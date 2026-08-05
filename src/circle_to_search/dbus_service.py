"""The D-Bus entry point the KWin script talks to.

Interface ``io.github.fand1l.CircleToSearch`` on the session bus::

    Trigger(int32 x, int32 y, string screen)               -> ()
    TriggerShake(int32 x, int32 y, string screen)          -> ()
    OverlayGeometry(int32 x, int32 y, int32 w, int32 h)    -> ()
    CalibrationSample(int32 length, int32 speed, int32 curvature,
                      int32 diagonal, int32 turn, int32 duration) -> ()
    GestureTrace(string points)                            -> ()
    TriggerCurrentScreen()                                 -> ()
    ShowSettings()                                         -> ()
    Ping()                                                 -> string

Manual test::

    busctl --user call io.github.fand1l.CircleToSearch \\
        /io/github/fand1l/CircleToSearch \\
        io.github.fand1l.CircleToSearch Trigger iis 1920 1080 eDP-1

``PyQt6.QtDBus`` is used rather than dbus-python/dbus-next so that D-Bus and the
GUI share one Qt event loop.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

from . import DBUS_INTERFACE, DBUS_PATH, DBUS_SERVICE, __version__
from .logging_setup import get_logger

log = get_logger("dbus")


@pyqtClassInfo("D-Bus Interface", DBUS_INTERFACE)
class CircleToSearchAdaptor(QDBusAbstractAdaptor):
    """Exports :class:`ServiceObject` on the bus."""

    def __init__(self, parent: ServiceObject) -> None:
        super().__init__(parent)
        self._service = parent

    @pyqtSlot(int, int, str)
    def Trigger(self, x: int, y: int, screen: str) -> None:
        """Open the selection overlay on ``screen`` (cursor was at ``x``/``y``)."""
        log.info("Trigger(%d, %d, %r)", x, y, screen)
        self._service.triggered.emit(x, y, screen)

    @pyqtSlot()
    def TriggerCurrentScreen(self) -> None:
        """Open the overlay on the screen Qt believes the pointer is on."""
        log.info("TriggerCurrentScreen()")
        self._service.triggered_current.emit()

    @pyqtSlot(int, int, str)
    def TriggerShake(self, x: int, y: int, screen: str) -> None:
        """Same as :meth:`Trigger`, but the KWin script says it was a shake.

        The daemon can then honour "Detect cursor shake" itself instead of
        trusting that the script picked the setting up — the checkbox failing to
        take effect is the kind of bug a user notices immediately and cannot
        work around.  The plain Trigger stays unconditional, because the global
        shortcut and busctl calls are deliberate actions.
        """
        log.info("TriggerShake(%d, %d, %r)", x, y, screen)
        self._service.shake_triggered.emit(x, y, screen)

    @pyqtSlot(int, int, int, int, int, int)
    def CalibrationSample(
        self,
        length: int,
        speed: int,
        curvature_pct: int,
        diagonal_deg: int,
        turn_deg: int,
        duration_ms: int,
    ) -> None:
        """One measured swing, sent only while the calibration dialog is open."""
        self._service.calibration_sample.emit(
            length, speed, curvature_pct, diagonal_deg, turn_deg, duration_ms
        )

    @pyqtSlot(str)
    def GestureTrace(self, points: str) -> None:
        """The pointer movement that is about to trigger, as ``"x,y,t;…"``.

        Sent immediately before the trigger it belongs to, and only while the
        daemon has asked the script to collect it.  It exists so a misfire the
        user disowns can be replayed offline instead of being described.
        """
        log.debug("GestureTrace(%d bytes)", len(points))
        self._service.gesture_trace.emit(points)

    @pyqtSlot(str)
    def WindowRects(self, rects: str) -> None:
        """Where every window is, as ``"x,y,w,h;…"`` in global logical pixels.

        Front-most first, our own overlay excluded.  Sent immediately before the
        trigger it belongs to, so the overlay can outline the window under the
        pointer and let a click take exactly it — a Wayland client cannot see
        anybody else's geometry, so this is the only place it can come from.
        """
        log.debug("WindowRects(%d bytes)", len(rects))
        self._service.window_rects.emit(rects)

    @pyqtSlot(int, int, int, int)
    def OverlayGeometry(self, x: int, y: int, width: int, height: int) -> None:
        """Where the KWin script left the overlay window, relative to its output.

        A Wayland client cannot ask for its own position; without this the
        overlay would keep painting the screenshot from its own corner even when
        the window was placed below a panel.
        """
        log.debug("OverlayGeometry(%d, %d, %d, %d)", x, y, width, height)
        self._service.overlay_geometry.emit(x, y, width, height)

    @pyqtSlot()
    def ShowSettings(self) -> None:
        """Raise the settings dialog (used when a second instance is started)."""
        log.info("ShowSettings()")
        self._service.settings_requested.emit()

    @pyqtSlot(str)
    def ScriptReady(self, version: str) -> None:
        """The version of the KWin script KWin is *actually running*.

        KWin loads a script once, at login, and ``reconfigure`` re-reads its
        settings without re-reading its code, so after an upgrade the file on
        disk and the code in the compositor can be two different things — and
        every symptom of that looks like a setting that does not work.  The
        script sends this at start-up and again on every configuration change,
        which is what makes it reach a daemon that was restarted since login.
        """
        log.info("ScriptReady(%s)", version)
        self._service.script_ready.emit(version)

    @pyqtSlot(result=str)
    def Ping(self) -> str:
        """Return the running version — the quickest liveness check."""
        return __version__


class ServiceObject(QObject):
    """Qt-side signals produced by incoming D-Bus calls."""

    triggered = pyqtSignal(int, int, str)
    shake_triggered = pyqtSignal(int, int, str)
    overlay_geometry = pyqtSignal(int, int, int, int)
    calibration_sample = pyqtSignal(int, int, int, int, int, int)
    gesture_trace = pyqtSignal(str)
    triggered_current = pyqtSignal()
    settings_requested = pyqtSignal()
    #: The version string of the KWin script now running inside KWin.
    script_ready = pyqtSignal(str)
    #: The window layout at the moment of the trigger, encoded.
    window_rects = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._adaptor = CircleToSearchAdaptor(self)


class DBusServiceError(Exception):
    """The service name or object could not be registered."""


def register_service(service: ServiceObject) -> QDBusConnection:
    """Claim the well-known name and export the object."""
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise DBusServiceError("no session bus")

    if not bus.registerService(DBUS_SERVICE):
        raise DBusServiceError(f"the name {DBUS_SERVICE} is already taken")

    if not bus.registerObject(
        DBUS_PATH,
        service,
        QDBusConnection.RegisterOption.ExportAdaptors,
    ):
        bus.unregisterService(DBUS_SERVICE)
        raise DBusServiceError(f"cannot export {DBUS_PATH}")

    log.info("listening on %s %s", DBUS_SERVICE, DBUS_PATH)
    return bus


def unregister_service() -> None:
    """Release the name (called on shutdown)."""
    bus = QDBusConnection.sessionBus()
    if bus.isConnected():
        bus.unregisterObject(DBUS_PATH)
        bus.unregisterService(DBUS_SERVICE)


def call_running_instance(method: str) -> bool:
    """Invoke ``method`` on an already running instance.  True when it answered."""
    from PyQt6.QtDBus import QDBusInterface, QDBusMessage

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    interface = QDBusInterface(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, bus)
    if not interface.isValid():
        return False
    interface.setTimeout(5000)
    reply = interface.call(method)
    return reply.type() == QDBusMessage.MessageType.ReplyMessage
