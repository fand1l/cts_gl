"""What the tray icon is actually saying.

The icon used to be one picture that never changed, so the only way to find out
whether the gesture would work was to shake the pointer and see.  That is fine
until it does not work, and then there are four quite different reasons and no
way to tell them apart:

* the user switched detection off;
* the KWin script is not installed at all;
* it is installed but not enabled in ``kwinrc``;
* it is enabled, and KWin is running an *older copy* of it.

The last one is the reason this module exists.  KWin loads a script once, at
login, and ``reconfigure`` re-reads its settings without re-reading its code —
so after an upgrade the file on disk and the code in the compositor are two
different things, and every symptom of that looks like "the checkbox does not
work".  It came up twice during development and cost two rounds of debugging
each time.  An icon that had said so would have ended it in seconds.

Everything here is read **fresh** on every call.  Caching would reintroduce
exactly the bug being reported on: a disagreement between what is written down
and what is running.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import (
    kwin_script_enabled,
    kwin_script_installed,
    kwin_script_version,
    read_detection,
)
from .i18n import tr

#: Everything works: the gesture and the shortcut both do what they say.
STATE_OK = "ok"
#: Deliberately switched off.  Not a fault, and the shortcut still works.
STATE_OFF = "off"
#: The compositor half is missing, so only the shortcut works.
STATE_NO_SCRIPT = "no-script"
#: Installed but not enabled in ``kwinrc``.
STATE_DISABLED = "disabled"
#: Enabled, but KWin is running a different version than the one on disk.
STATE_STALE = "stale"


@dataclass(frozen=True)
class TrayState:
    """The one thing the icon has to convey, and the words for the tooltip."""

    state: str
    #: The version KWin reported, and the version of the file on disk.  Both
    #: empty when the script has never announced itself.
    running: str = ""
    installed: str = ""

    @property
    def working(self) -> bool:
        """True only when shaking the pointer will really open the overlay."""
        return self.state == STATE_OK

    @property
    def tooltip(self) -> str:
        if self.state == STATE_STALE:
            return tr("tray.state.stale", running=self.running, installed=self.installed)
        return tr(f"tray.state.{self.state}")


def read_tray_state(reported_version: str = "") -> TrayState:
    """Work out what the icon should be saying, from scratch.

    ``reported_version`` is whatever the KWin script last announced over D-Bus,
    or ``""`` if it never has — which is the normal case for a daemon started
    after login, and is not treated as a fault.  The version is only ever used
    to *contradict* the file on disk, never to claim something is wrong on the
    strength of not having heard anything.
    """
    if kwin_script_installed() is None:
        return TrayState(STATE_NO_SCRIPT)
    if not kwin_script_enabled():
        return TrayState(STATE_DISABLED)

    installed = kwin_script_version()
    if reported_version and installed and reported_version != installed:
        return TrayState(STATE_STALE, running=reported_version, installed=installed)

    if not read_detection().enabled:
        return TrayState(STATE_OFF)
    return TrayState(STATE_OK)
