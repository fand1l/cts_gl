"""Desktop notifications through ``org.freedesktop.Notifications``.

Nothing here may fail loudly: if the notification daemon is missing we still
want the message in the journal.  Call these from the GUI thread only.
"""

from __future__ import annotations

from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

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


def notify(
    summary: str,
    body: str = "",
    *,
    urgency: int = URGENCY_NORMAL,
    timeout_ms: int = 6000,
    transient: bool = False,
    replace_previous: bool = False,
) -> int:
    """Show a notification and return its id (0 when it could not be shown)."""
    global _last_id

    log_line = f"{summary}: {body}" if body else summary
    if urgency >= URGENCY_CRITICAL:
        log.error("%s", log_line)
    else:
        log.info("%s", log_line)

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return 0

    interface = QDBusInterface(
        NOTIFICATIONS_SERVICE, NOTIFICATIONS_PATH, NOTIFICATIONS_INTERFACE, bus
    )
    if not interface.isValid():
        log.debug("no notification service on the bus")
        return 0
    interface.setTimeout(5000)

    hints: dict[str, object] = {
        "urgency": urgency,
        "desktop-entry": APP_ID,
        "x-kde-origin-name": tr("app.name"),
    }
    if transient:
        hints["transient"] = True

    reply = interface.call(
        "Notify",
        tr("app.name"),
        _last_id if replace_previous else 0,
        APP_ID,
        summary,
        body,
        [],
        hints,
        timeout_ms,
    )
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        log.debug("Notify failed: %s %s", reply.errorName(), reply.errorMessage())
        return 0

    arguments = reply.arguments()
    if arguments:
        try:
            _last_id = int(arguments[0])
        except (TypeError, ValueError):
            _last_id = 0
    return _last_id


def notify_error(summary: str, body: str = "") -> int:
    """Shorthand for an error notification."""
    return notify(summary, body, urgency=URGENCY_CRITICAL, timeout_ms=10000)
