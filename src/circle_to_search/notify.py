"""Desktop notifications through ``org.freedesktop.Notifications``.

Nothing here may fail loudly: if the notification daemon is missing we still
want the message in the journal.  Call these from the GUI thread only.

The argument types are spelled out rather than left to Qt.  ``Notify`` is
declared ``susssasa{sv}i``, and PyQt turns a plain Python ``int`` into ``i``
and a plain ``list[str]`` into ``av`` — so a call built the obvious way is
``sisssava{sv}i`` and every strict server (Plasma's included) answers
``UnknownMethod``.  tests/test_dbus_surface.py exists because that failure is
invisible from this side: the log line is written either way.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QMetaType, QObject, pyqtSlot
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusInterface, QDBusMessage

from . import APP_ID
from .i18n import tr
from .logging_setup import get_logger

log = get_logger("notify")

NOTIFICATIONS_SERVICE = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS_INTERFACE = "org.freedesktop.Notifications"

URGENCY_LOW = 0
URGENCY_NORMAL = 1
URGENCY_CRITICAL = 2

_last_id = 0


def _uint32(value: int) -> QDBusArgument:
    """A ``u``, which is what the id arguments are declared as."""
    return QDBusArgument(int(value), QMetaType.Type.UInt.value)


def _string_list(values: Sequence[str]) -> QDBusArgument:
    """An ``as``; a bare Python list would go out as ``av``."""
    return QDBusArgument(list(values), QMetaType.Type.QStringList.value)


class _ActionRouter(QObject):
    """Delivers ``ActionInvoked`` back to whoever showed the notification.

    The signal carries only the notification id, so the callbacks are kept in a
    table keyed by it.  Slots take the whole :class:`QDBusMessage` on purpose:
    the id is a D-Bus ``uint32`` and matching that against a Python ``int`` slot
    is exactly the kind of signature mismatch that fails silently at run time.
    """

    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[int, Callable[[str], None]] = {}
        bus = QDBusConnection.sessionBus()
        for name, slot in (
            ("ActionInvoked", self._on_action),
            ("NotificationClosed", self._on_closed),
        ):
            if not bus.connect(
                NOTIFICATIONS_SERVICE,
                NOTIFICATIONS_PATH,
                NOTIFICATIONS_INTERFACE,
                name,
                slot,
            ):
                log.debug("could not subscribe to %s", name)

    def watch(self, notification_id: int, handler: Callable[[str], None]) -> None:
        self._handlers[notification_id] = handler

    @staticmethod
    def _first_int(message: QDBusMessage) -> int | None:
        arguments = message.arguments()
        if not arguments:
            return None
        try:
            return int(arguments[0])
        except (TypeError, ValueError):
            return None

    @pyqtSlot(QDBusMessage)
    def _on_action(self, message: QDBusMessage) -> None:
        notification_id = self._first_int(message)
        if notification_id is None:
            return
        handler = self._handlers.pop(notification_id, None)
        if handler is None:
            return
        arguments = message.arguments()
        key = str(arguments[1]) if len(arguments) > 1 else ""
        log.debug("notification %d: action %r", notification_id, key)
        handler(key)

    @pyqtSlot(QDBusMessage)
    def _on_closed(self, message: QDBusMessage) -> None:
        notification_id = self._first_int(message)
        if notification_id is not None:
            # Dismissed or timed out: drop the callback so the table cannot grow
            # for the whole life of the daemon.
            self._handlers.pop(notification_id, None)


_router: _ActionRouter | None = None
_capabilities: frozenset[str] | None = None


def _interface() -> QDBusInterface | None:
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    interface = QDBusInterface(
        NOTIFICATIONS_SERVICE, NOTIFICATIONS_PATH, NOTIFICATIONS_INTERFACE, bus
    )
    if not interface.isValid():
        log.debug("no notification service on the bus")
        return None
    interface.setTimeout(5000)
    return interface


def supports_actions() -> bool:
    """Can the notification server show buttons?  Asked once, then cached.

    Without buttons a question would be a message the user cannot answer, so the
    caller is expected to stay quiet instead.
    """
    global _capabilities
    if _capabilities is None:
        interface = _interface()
        if interface is None:
            return False
        reply = interface.call("GetCapabilities")
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return False
        arguments = reply.arguments()
        values = arguments[0] if arguments and isinstance(arguments[0], list) else []
        _capabilities = frozenset(str(value) for value in values)
        log.debug("notification capabilities: %s", ", ".join(sorted(_capabilities)) or "none")
    return "actions" in _capabilities


def notify(
    summary: str,
    body: str = "",
    *,
    urgency: int = URGENCY_NORMAL,
    timeout_ms: int = 6000,
    transient: bool = False,
    replace_previous: bool = False,
    actions: Sequence[tuple[str, str]] = (),
    on_action: Callable[[str], None] | None = None,
) -> int:
    """Show a notification and return its id (0 when it could not be shown).

    ``actions`` are ``(key, label)`` pairs shown as buttons; ``on_action`` is
    called with the key of the one that was pressed, and never called at all
    when the notification is dismissed or times out.
    """
    global _last_id, _router

    log_line = f"{summary}: {body}" if body else summary
    if urgency >= URGENCY_CRITICAL:
        log.error("%s", log_line)
    else:
        log.info("%s", log_line)

    interface = _interface()
    if interface is None:
        return 0

    hints: dict[str, object] = {
        "urgency": urgency,
        "desktop-entry": APP_ID,
        "x-kde-origin-name": tr("app.name"),
    }
    if transient:
        hints["transient"] = True

    flat_actions: list[str] = []
    for key, label in actions:
        flat_actions += [key, label]

    reply = interface.call(
        "Notify",
        tr("app.name"),
        _uint32(_last_id if replace_previous else 0),
        APP_ID,
        summary,
        body,
        _string_list(flat_actions),
        hints,
        timeout_ms,
    )
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        log.debug("Notify failed: %s %s", reply.errorName(), reply.errorMessage())
        return 0

    arguments = reply.arguments()
    try:
        notification_id = int(arguments[0]) if arguments else 0
    except (TypeError, ValueError):
        notification_id = 0

    if not flat_actions:
        # A notification the user is answering must not be replaced by the next
        # "uploading…" message, so only plain ones become the replace target.
        _last_id = notification_id
    if notification_id and on_action is not None:
        if _router is None:
            _router = _ActionRouter()
        _router.watch(notification_id, on_action)
    return notification_id


def notify_error(summary: str, body: str = "") -> int:
    """Shorthand for an error notification."""
    return notify(summary, body, urgency=URGENCY_CRITICAL, timeout_ms=10000)
