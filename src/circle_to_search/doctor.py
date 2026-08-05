"""``circle-to-search --doctor`` — check every joint, and say what to do.

This program has six moving parts in four processes: a KWin script inside the
compositor, a daemon of its own, a screen-capture back end that belongs to
somebody else, and a browser.  When one of them is wrong the symptom is
*silence* — you shake the pointer and nothing happens — and the six causes look
identical from the outside.

Three things in this tree already admit that.  The technical documentation has
twenty failure modes with the reasoning behind each; ``install.sh`` ends with a
verification pass; and :mod:`circle_to_search.traystate` exists because "the
toggle does not work" cost two rounds of debugging, twice, and both times the
answer was that KWin was running an older copy of the script than the one on
disk.  This is those three in one command, printed rather than reasoned about.

Almost nothing here is new.  It calls the same functions the daemon calls and
prints what they say; the point is that they are all called *at once*, in a
fixed order, with the fix written next to whichever one failed.

The report is in English while the rest of the interface follows the system
language, deliberately and for one reason: it is written to be pasted into a
bug report, and so are the two other diagnostic surfaces here — ``install.sh``
and ``docs/TECHNICAL.md``.  A report nobody in the thread can read is not a
better report for being in the right language.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QMetaType
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusInterface
from PyQt6.QtGui import QScreen

from . import APP_ID, DBUS_SERVICE, version_label
from .config import (
    KGLOBALACCEL_INTERFACE,
    KGLOBALACCEL_PATH,
    KGLOBALACCEL_SERVICE,
    KWIN_COMPONENT,
    SERVICE_NAME,
    SHORTCUT_ACTION,
    AppSettings,
    kwin_script_enabled,
    kwin_script_installed,
    kwin_script_version,
    read_detection,
    service_state,
)
from .logging_setup import get_logger

log = get_logger("doctor")

#: Everything about this line is as it should be.
OK = "ok"
#: Not wrong, but not doing anything either — an optional part switched off.
NOTE = "note"
#: Works, but something about it will bite later.
WARN = "warn"
#: Broken.  This is why nothing happens when you shake the pointer.
FAIL = "fail"

_MARK = {OK: "✓", NOTE: "·", WARN: "!", FAIL: "✗"}
_COLOUR = {OK: "\033[32m", NOTE: "\033[2m", WARN: "\033[33m", FAIL: "\033[31m"}
_RESET = "\033[0m"

#: How wide the label column is.  Long enough for the longest label below.
_LABEL = 18


@dataclass(frozen=True)
class Finding:
    """One line of the report, and the fix if that line is bad news."""

    label: str
    status: str
    detail: str
    #: What to do about it.  Only ever read for WARN and FAIL, because a fix
    #: printed under something that works is noise pretending to be help.
    fix: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bad(self) -> bool:
        return self.status == FAIL


# --------------------------------------------------------------------------- #
# The session the whole thing sits in
# --------------------------------------------------------------------------- #
def check_session() -> Finding:
    """Wayland, and Plasma 6."""
    kind = os.environ.get("XDG_SESSION_TYPE", "")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    wayland = kind == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    plasma = "KDE" in desktop.upper()

    if wayland and plasma:
        return Finding("session", OK, f"Wayland, {desktop or 'KDE'}")
    if not wayland:
        return Finding(
            "session",
            FAIL,
            f"not a Wayland session (XDG_SESSION_TYPE={kind or 'unset'})",
            (
                "Log in to “Plasma (Wayland)”. The capture path and the overlay",
                "stacking were only ever written for it.",
            ),
        )
    return Finding(
        "session",
        WARN,
        f"Wayland, but XDG_CURRENT_DESKTOP={desktop or 'unset'}",
        ("The KWin script needs KWin. Outside Plasma only the shortcut works.",),
    )


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #
def _bus_names() -> list[str] | None:
    """Every name on the session bus, or None if the bus is unreachable."""
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    interface = QDBusInterface(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", bus
    )
    if not interface.isValid():
        return None
    reply = interface.call("ListNames")
    if reply.errorName():
        log.debug("ListNames failed: %s", reply.errorName())
        return None
    arguments = reply.arguments()
    if not arguments or not isinstance(arguments[0], list):
        return None
    return [str(name) for name in arguments[0]]


def check_daemon(names: list[str] | None) -> Finding:
    """Is the thing that answers the KWin script actually running?"""
    if names is None:
        return Finding(
            "daemon",
            FAIL,
            "no session bus",
            ("Nothing here can work without one. Check DBUS_SESSION_BUS_ADDRESS.",),
        )
    if DBUS_SERVICE in names:
        return Finding("daemon", OK, f"{DBUS_SERVICE} is on the bus")
    return Finding(
        "daemon",
        FAIL,
        f"{DBUS_SERVICE} is not on the bus",
        (
            "The daemon is not running, so nothing will answer the gesture.",
            f"  systemctl --user status {SERVICE_NAME}",
            f"  journalctl --user -u {SERVICE_NAME} -n 50",
        ),
    )


def check_service(state: tuple[str, str] | None) -> Finding:
    """The unit that is supposed to start it with the session."""
    if state is None:
        return Finding("service", NOTE, "no systemctl — started some other way?")

    active, enabled = state
    if active == "active" and enabled == "enabled":
        return Finding("service", OK, f"{SERVICE_NAME} is active and enabled")
    if active == "active":
        return Finding(
            "service",
            WARN,
            f"{SERVICE_NAME} is running but {enabled or 'not enabled'}",
            ("It will not come back at the next login:",
             f"  systemctl --user enable {SERVICE_NAME}"),
        )
    return Finding(
        "service",
        FAIL,
        f"{SERVICE_NAME} is {active or 'not running'}",
        (f"  systemctl --user enable --now {SERVICE_NAME}",
         f"  journalctl --user -u {SERVICE_NAME} -n 50"),
    )


# --------------------------------------------------------------------------- #
# The compositor half
# --------------------------------------------------------------------------- #
def check_kwin_script(running: str = "") -> Finding:
    """Installed, switched on, and — the one that costs days — *current*.

    ``running`` is whatever version the script last announced over D-Bus.  The
    daemon knows it because the script tells it at startup; a doctor run has
    nobody to ask, so it reads the journal instead, and an empty string means
    "could not tell", which is never treated as a fault on its own.
    """
    package = kwin_script_installed()
    if package is None:
        return Finding(
            "KWin script",
            FAIL,
            "not installed",
            ("Run ./install.sh again. Until then only the shortcut works.",),
        )
    if not kwin_script_enabled():
        return Finding(
            "KWin script",
            FAIL,
            "installed but switched off in kwinrc",
            ("System Settings → Window Management → KWin Scripts → tick",
             "“Circle to Search”."),
        )

    installed = kwin_script_version()
    if running and installed and running != installed:
        return Finding(
            "KWin script",
            FAIL,
            f"KWin is running v{running}, but v{installed} is installed",
            ("KWin loads a script once, at login, and keeps running that copy.",
             "Toggle it off and on in System Settings → Window Management →",
             "KWin Scripts, or log out and back in."),
        )
    if not running:
        return Finding(
            "KWin script",
            NOTE,
            f"v{installed or '?'} installed and enabled; KWin has not said what it runs",
            ("Nothing is necessarily wrong — the announcement is only made at",
             "startup and the journal may have rotated past it. To be sure:",
             "  journalctl --user -u plasma-kwin_wayland | grep 'script started'"),
        )
    return Finding("KWin script", OK, f"v{running}, matching the installed copy")


def running_script_version() -> str:
    """Ask the journal which version KWin actually has in memory."""
    binary = shutil.which("journalctl")
    if binary is None:
        return ""
    try:
        done = subprocess.run(
            [binary, "--user", "-u", "plasma-kwin_wayland", "-o", "cat", "-n", "2000"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("journalctl failed: %s", exc)
        return ""
    version = ""
    for line in done.stdout.splitlines():
        marker = "KWin script started (v"
        if marker in line:
            version = line.split(marker, 1)[1].split(")", 1)[0]
    return version


def check_shortcut(names: list[str] | None) -> Finding:
    """kglobalaccel, which is what "Capture now" and Meta+Shift+L go through."""
    shortcut = read_detection().shortcut
    if names is None or "org.kde.kglobalaccel" not in names:
        return Finding(
            "shortcut",
            WARN,
            "kglobalaccel is not on the bus",
            ("The keyboard shortcut and “Capture now” both go through it;",
             "shaking the pointer does not, so that half may still work."),
        )
    known = _shortcut_actions()
    if known is None:
        return Finding("shortcut", NOTE, f"kglobalaccel is there; {shortcut} not verified")
    if SHORTCUT_ACTION in known:
        return Finding("shortcut", OK, f"{KWIN_COMPONENT}/{SHORTCUT_ACTION} — {shortcut}")
    return Finding(
        "shortcut",
        FAIL,
        f"kglobalaccel does not know {KWIN_COMPONENT}/{SHORTCUT_ACTION}",
        ("The KWin script registers it when it loads, so this usually means",
         "the script is not running. Log out and back in."),
    )


def _shortcut_actions() -> list[str] | None:
    """Every action kglobalaccel has for KWin, or None if it would not say.

    ``allActionsForComponent`` is declared ``as`` → ``aas``, and a plain Python
    list goes out as ``av`` — the same signature mismatch ``notify.py`` was
    written around, which fails with ``UnknownMethod`` and looks from here
    exactly like "the shortcut is not registered".  None is "could not tell",
    and the caller reports that rather than a fault.
    """
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    interface = QDBusInterface(
        KGLOBALACCEL_SERVICE, KGLOBALACCEL_PATH, KGLOBALACCEL_INTERFACE, bus
    )
    if not interface.isValid():
        return None
    interface.setTimeout(3000)
    identifier = QDBusArgument(
        [KWIN_COMPONENT, "", "", ""], QMetaType.Type.QStringList.value
    )
    reply = interface.call("allActionsForComponent", identifier)
    if reply.errorName():
        log.debug("allActionsForComponent failed: %s", reply.errorName())
        return None
    arguments = reply.arguments()
    if not arguments or not isinstance(arguments[0], list):
        return None
    # Each entry is a componentUnique/actionUnique/componentFriendly/…
    # quadruple, and the second field is the one the script registers.
    return [
        str(entry[1])
        for entry in arguments[0]
        if isinstance(entry, list) and len(entry) >= 2
    ]


# --------------------------------------------------------------------------- #
# Taking the picture
# --------------------------------------------------------------------------- #
def check_capture(screen: QScreen | None) -> Finding:
    """Really take one, because "should work" is what this command is for.

    The back ends are tried in the order the daemon tries them and the first
    one that answers wins — the portal is not reached while something above it
    works, deliberately: it is the one that can raise a permission dialog, and
    provoking that to confirm a path nothing will take is not a diagnostic.
    """
    if screen is None:
        return Finding(
            "screen capture",
            FAIL,
            "no screen to capture",
            ("Qt found no display at all, so nothing below this could run.",),
        )

    from .screenshot import CaptureError, capture_screen

    name = screen.name() or ""
    started = time.monotonic()
    try:
        capture = capture_screen(screen, name)
    except CaptureError as exc:
        return Finding(
            "screen capture",
            FAIL,
            f"every back end failed — {exc}",
            ("Nothing can be selected without a picture of the screen.",
             "See “Screen capture fails” in docs/TECHNICAL.md."),
        )
    elapsed = (time.monotonic() - started) * 1000.0
    detail = (
        f"{capture.backend}, {capture.image.width}×{capture.image.height} px"
        f" in {elapsed:.0f} ms"
    )
    if elapsed > 1500.0:
        return Finding(
            "screen capture",
            WARN,
            detail,
            ("That is slow enough to feel. The overlay cannot open until it is",
             "done, so the gesture will seem unresponsive."),
        )
    return Finding("screen capture", OK, detail)


# --------------------------------------------------------------------------- #
# What happens to the result
# --------------------------------------------------------------------------- #
def check_browser() -> Finding:
    """Something has to open the page the upload ends on."""
    if shutil.which("xdg-open") is None:
        return Finding(
            "result page",
            FAIL,
            "xdg-open is not installed",
            ("The daemon hands the page to xdg-open and nothing else.",
             "Install xdg-utils."),
        )
    handler = _default_handler("text/html")
    if not handler:
        return Finding(
            "result page",
            FAIL,
            "nothing is registered for text/html",
            ("xdg-open will have nowhere to send the result.",
             "  xdg-settings set default-web-browser firefox.desktop"),
        )
    return Finding("result page", OK, f"text/html opens in {handler}")


def _default_handler(mime: str) -> str:
    binary = shutil.which("xdg-mime")
    if binary is None:
        return ""
    try:
        done = subprocess.run(
            [binary, "query", "default", mime],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


# --------------------------------------------------------------------------- #
# The optional half
# --------------------------------------------------------------------------- #
def check_ocr(enabled: bool) -> Finding:
    """Text recognition, which is off until it is asked for."""
    from . import ocr

    if not ocr.is_available():
        if not enabled:
            return Finding("text recognition", NOTE, "off, and tesseract is not installed")
        return Finding(
            "text recognition",
            FAIL,
            "switched on, but tesseract is not installed",
            (f"  {ocr.install_hint()}",),
        )
    languages = ocr.installed_languages()
    have = ", ".join(languages) if languages else "no language data"
    if not enabled:
        return Finding("text recognition", NOTE, f"tesseract is there ({have}); switched off")
    if not languages:
        return Finding(
            "text recognition",
            FAIL,
            "tesseract is installed with no language data",
            ("It will fail on every image. Install a language pack:",
             f"  {ocr.install_hint()}"),
        )
    return Finding("text recognition", OK, f"tesseract, {have}")


# --------------------------------------------------------------------------- #
# Putting it together
# --------------------------------------------------------------------------- #
def run_checks(screen: QScreen | None = None) -> list[Finding]:
    """Every check, in the order a failure cascades.

    Session first, then the two processes, then the compositor, then the things
    that are only reached once all of that works — so reading it top to bottom
    finds the first real cause rather than the loudest symptom.
    """
    settings = AppSettings()
    names = _bus_names()
    return [
        check_session(),
        check_daemon(names),
        check_service(service_state()),
        check_kwin_script(running_script_version()),
        check_shortcut(names),
        check_capture(screen),
        check_browser(),
        check_ocr(settings.ocr_enabled),
    ]


def format_report(findings: list[Finding], *, colour: bool = True) -> str:
    """The whole report as text, so a test can read what a person would."""

    def paint(text: str, status: str) -> str:
        return f"{_COLOUR[status]}{text}{_RESET}" if colour else text

    lines = [f"Circle to Search {version_label()}", ""]
    for finding in findings:
        mark = paint(_MARK[finding.status], finding.status)
        lines.append(f"  {mark}  {finding.label.ljust(_LABEL)}{finding.detail}")
        if finding.status in (WARN, FAIL):
            lines.extend(f"     {' ' * _LABEL}{line}" for line in finding.fix)

    broken = sum(1 for finding in findings if finding.bad)
    warned = sum(1 for finding in findings if finding.status == WARN)
    lines.append("")
    if broken:
        lines.append(paint(f"{broken} of {len(findings)} broken. Start at the first ✗.", FAIL))
    elif warned:
        lines.append(paint(f"Nothing is broken; {warned} worth a look.", WARN))
    else:
        lines.append(paint("Everything is working.", OK))
    return "\n".join(lines)


def run() -> int:
    """The entry point behind ``--doctor``.  Non-zero when something is broken.

    A QApplication is needed for the screen list and for QtDBus, and building
    one is itself a check: if it cannot be built there is no display, and that
    is worth saying rather than crashing about.
    """
    screen: QScreen | None = None
    # Held in a name for as long as the checks run.  A QApplication nothing
    # refers to is collected on the next line, and a collected one has no
    # screens — which reads exactly like a display that is not there.
    application = None
    try:
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([APP_ID])
        screen = QGuiApplication.primaryScreen()
    except Exception as exc:  # a broken display must still get a report out
        log.debug("could not start Qt: %s", exc)

    findings = run_checks(screen)
    del application  # every check that needed it has run
    print(format_report(findings, colour=os.isatty(1)))
    return 1 if any(finding.bad for finding in findings) else 0


__all__ = [
    "FAIL",
    "NOTE",
    "OK",
    "WARN",
    "Finding",
    "format_report",
    "run",
    "run_checks",
]
