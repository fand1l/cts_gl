"""The D-Bus entry point the KWin script talks to.

Interface ``io.github.fand1l.CircleToSearch`` on the session bus::

    Trigger(int32 x, int32 y, string screen)               -> ()
    TriggerShake(int32 x, int32 y, string screen)          -> ()
    OverlayGeometry(int32 x, int32 y, int32 w, int32 h)    -> ()
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

    @pyqtSlot(result=str)
    def Ping(self) -> str:
        """Return the running version — the quickest liveness check."""
        return __version__


class ServiceObject(QObject):
    """Qt-side signals produced by incoming D-Bus calls."""

    triggered = pyqtSignal(int, int, str)
    shake_triggered = pyqtSignal(int, int, str)
    overlay_geometry = pyqtSignal(int, int, int, int)
    triggered_current = pyqtSignal()
    settings_requested = pyqtSignal()

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
