"""The D-Bus surface, on a private bus.

Reading the code is not enough here.  A method whose ``@pyqtSlot`` decorator is
missing, or whose signature does not match what the KWin script sends, exports
nothing at all and fails in complete silence — that has already happened once in
this project.  The same goes for the notification *actions*: a slot whose
argument types do not match the signal is connected happily and then never
called.

So this suite starts a private ``dbus-daemon``, exports the real service on it,
pokes it with the real ``busctl``, and checks that the Qt signals come out the
other end::

    python3 tests/test_dbus_surface.py

It re-executes itself under ``dbus-run-session`` and skips (exit 0) when D-Bus
or PyQt6 is missing, so it can sit next to the other suites without becoming a
hard dependency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ---------------------------------------------------------------- bootstrap

if os.environ.get("CTS_PRIVATE_BUS") != "1":
    if shutil.which("dbus-run-session") is None or shutil.which("busctl") is None:
        print("SKIP  dbus-run-session or busctl is not installed")
        raise SystemExit(0)
    environment = dict(os.environ, CTS_PRIVATE_BUS="1", QT_QPA_PLATFORM="offscreen")
    raise SystemExit(
        subprocess.run(
            ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve())],
            env=environment,
            check=False,
        ).returncode
    )

try:
    from PyQt6.QtCore import QCoreApplication
except ImportError as exc:  # pragma: no cover - depends on the machine
    print(f"SKIP  PyQt6 is not installed ({exc})")
    raise SystemExit(0) from None

from circle_to_search import DBUS_INTERFACE, DBUS_PATH, DBUS_SERVICE
from circle_to_search.dbus_service import ServiceObject, register_service
from circle_to_search.notify import notify

app = QCoreApplication(sys.argv[:1])

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name} {detail}")
    if not condition:
        failures.append(name)


def pump(seconds: float) -> None:
    """Run the Qt event loop for a while without blocking on it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def bus_call(args: list[str], timeout: float = 5.0) -> str:
    """Run busctl while still answering it — blocking here would deadlock."""
    # "--" or busctl reads a negative argument as an option of its own.
    process = subprocess.Popen(
        ["busctl", "--user", "--no-pager", "--", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    if process.poll() is None:
        process.kill()
        return "<timeout>"
    return process.communicate()[0]


# ------------------------------------------------------- the exported object

service = ServiceObject()
register_service(service)

seen: dict[str, object] = {}
service.triggered.connect(lambda x, y, name: seen.__setitem__("trigger", (x, y, name)))
service.shake_triggered.connect(lambda x, y, name: seen.__setitem__("shake", (x, y, name)))
service.gesture_trace.connect(lambda points: seen.__setitem__("trace", points))
service.calibration_sample.connect(lambda *values: seen.__setitem__("sample", values))
service.overlay_geometry.connect(lambda *values: seen.__setitem__("geometry", values))

introspection = bus_call(["introspect", DBUS_SERVICE, DBUS_PATH])

# Every method the KWin script calls, with the signature it calls it with.  A
# mismatch here is the bug that cannot be seen by reading either side alone.
for method, signature in (
    ("Trigger", "iis"),
    ("TriggerShake", "iis"),
    ("TriggerCurrentScreen", "-"),
    ("CalibrationSample", "iiiiii"),
    ("GestureTrace", "s"),
    ("OverlayGeometry", "iiii"),
    ("ShowSettings", "-"),
    ("Ping", "-"),
):
    line = next(
        (row for row in introspection.splitlines() if f".{method} " in row or row.endswith(method)),
        "",
    )
    exported = method in introspection
    check(f"{method} is exported", exported)
    if exported and signature != "-":
        check(f"{method}({signature})", signature in line, line.strip())

check(
    "the interface name is right",
    DBUS_INTERFACE in introspection,
    DBUS_INTERFACE,
)

# ------------------------------------------------------------- real calls

bus_call(["call", DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "TriggerShake", "iis",
          "1920", "1080", "eDP-1"])
check("TriggerShake arrives", seen.get("shake") == (1920, 1080, "eDP-1"), str(seen.get("shake")))

bus_call(["call", DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "Trigger", "iis",
          "10", "20", "HDMI-A-1"])
check("Trigger arrives", seen.get("trigger") == (10, 20, "HDMI-A-1"), str(seen.get("trigger")))

bus_call(["call", DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "GestureTrace", "s",
          "100,200,0;110,205,30"])
check("GestureTrace arrives", seen.get("trace") == "100,200,0;110,205,30", str(seen.get("trace")))

bus_call(["call", DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "CalibrationSample", "iiiiii",
          "354", "2946", "100", "0", "-1", "120"])
check(
    "CalibrationSample arrives",
    seen.get("sample") == (354, 2946, 100, 0, -1, 120),
    str(seen.get("sample")),
)

ping = bus_call(["call", DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "Ping"])
check("Ping answers with the version", ping.strip().startswith('s "'), ping.strip())

# --------------------------------------------- notification action buttons
#
# The stub notification server runs in its own process on purpose.  A blocking
# call to an object exported on the same connection never leaves the process,
# and the local-loop shortcut Qt takes then proves nothing about how the real
# server — which is always another process — is talked to.

STUB_SERVER = r"""
import sys
from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

app = QCoreApplication(sys.argv[:1])


@pyqtClassInfo("D-Bus Interface", "org.freedesktop.Notifications")
class Stub(QDBusAbstractAdaptor):
    # Declared here so QtDBus relays it with the uint32 id the spec uses --
    # which is the whole point: an int32 would be a different signal.
    ActionInvoked = pyqtSignal("uint", str)

    @pyqtSlot(result="QStringList")
    def GetCapabilities(self):
        return ["body", "actions", "persistence"]

    @pyqtSlot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(self, name, replaces, icon, summary, body, actions, hints, timeout):
        print("NOTIFY", summary, "|", ",".join(actions), flush=True)
        if actions:
            # The user presses the second button, then the notification is
            # gone: a repeat and a stray id must both be ignored by the client.
            QTimer.singleShot(150, lambda: self.ActionInvoked.emit(4242, "no"))
            QTimer.singleShot(300, lambda: self.ActionInvoked.emit(4242, "yes"))
            QTimer.singleShot(450, lambda: self.ActionInvoked.emit(9999, "no"))
        return 4242


holder = QObject()
stub = Stub(holder)
bus = QDBusConnection.sessionBus()
if not bus.registerService("org.freedesktop.Notifications"):
    print("CANNOT CLAIM THE NAME", flush=True)
    sys.exit(1)
bus.registerObject(
    "/org/freedesktop/Notifications", holder, QDBusConnection.RegisterOption.ExportAdaptors
)
print("READY", flush=True)
QTimer.singleShot(30000, app.quit)
sys.exit(app.exec())
"""

stub_path = Path(tempfile.mkdtemp()) / "stub_notifications.py"
stub_path.write_text(STUB_SERVER, encoding="utf-8")
server = subprocess.Popen(
    [sys.executable, str(stub_path)],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
)
try:
    ready = server.stdout is not None and server.stdout.readline().strip() == "READY"
    check("the stub notification server is up", ready)

    answers: list[str] = []
    notification = notify(
        "Did you mean to open this?",
        "body",
        actions=(("yes", "Yes"), ("no", "No, that was accidental")),
        on_action=answers.append,
    )
    check("the notification was shown", notification == 4242, str(notification))

    # It has to survive the round trip: an id that never comes back means the
    # question can be shown but never answered.
    deadline = time.monotonic() + 5
    while not answers and time.monotonic() < deadline:
        pump(0.05)
    pump(0.8)

    check("the answer reaches the caller", answers == ["no"], str(answers))

    line = server.stdout.readline().strip() if server.stdout is not None else ""
    check(
        "both buttons were sent to the server",
        line.endswith("yes,Yes,no,No, that was accidental"),
        line,
    )
finally:
    server.terminate()
    server.wait(timeout=5)

# The callback fires once and only for its own notification: the server also
# emitted a repeat and an id belonging to nobody, and neither may be delivered.
check("the callback is one-shot and id-checked", answers == ["no"], str(answers))

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    sys.exit(1)
print("all good")
