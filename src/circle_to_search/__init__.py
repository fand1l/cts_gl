"""Circle to Search — Google Lens region search for KDE Plasma 6 on Wayland.

The package is split into small modules so that every piece can be tested and
debugged on its own:

* :mod:`circle_to_search.config`    — settings shared with the KWin script
* :mod:`circle_to_search.dbus_service` — the ``Trigger`` D-Bus entry point
* :mod:`circle_to_search.screenshot`   — three screen-capture back ends
* :mod:`circle_to_search.hidpi`        — logical/physical pixel arithmetic
* :mod:`circle_to_search.overlay`      — the frozen-screen selection overlay
* :mod:`circle_to_search.lens`         — the Google Lens upload
"""

from __future__ import annotations

__version__ = "1.0.0"

APP_NAME = "circle-to-search"
APP_ID = "io.github.fand1l.CircleToSearch"

DBUS_SERVICE = "io.github.fand1l.CircleToSearch"
DBUS_PATH = "/io/github/fand1l/CircleToSearch"
DBUS_INTERFACE = "io.github.fand1l.CircleToSearch"

#: The KWin script matches the overlay window by this exact caption in order to
#: force ``keepAbove``/``fullScreen``/``noBorder`` on it.  Keep the two in sync.
OVERLAY_WINDOW_TITLE = "Circle to Search Overlay"

#: And this one for a pinned crop, which wants the opposite treatment: kept
#: above everything, but at its own small size and never full screen.  A caption
#: of its own rather than a flag, because a KWin script has nothing else to go
#: on — it sees windows, not the program that made them.
PINNED_WINDOW_TITLE = "Circle to Search Pin"

#: KPackage plugin id of the KWin script (``kwinrc`` uses it for the
#: ``<id>Enabled`` key in ``[Plugins]`` and for the ``[Script-<id>]`` group).
KWIN_SCRIPT_ID = "circletosearch"

__all__ = [
    "APP_ID",
    "APP_NAME",
    "DBUS_INTERFACE",
    "DBUS_PATH",
    "DBUS_SERVICE",
    "KWIN_SCRIPT_ID",
    "OVERLAY_WINDOW_TITLE",
    "PINNED_WINDOW_TITLE",
    "__version__",
]
