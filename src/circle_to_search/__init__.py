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

__version__ = "1.3.0"

#: What this release is *called*.  The number says what changed in relation to
#: the last one; the name says which one it is, which is the question somebody
#: running `install.sh update` is actually asking.
#:
#: Kept beside the number rather than inside it, and joined only for display.
#: "1.3.0-another-way" is not a version pip will take — under PEP 440
#: a hyphen introduces a *pre-release*, so that string sorts *below* 1.3.0 — and
#: RPM will not take it at all, because its Version field uses the hyphen to
#: separate the version from the release.
RELEASE_NAME = "another-way"

#: A parallel count, and the only one a machine compares.
#:
#: Five digits, flat, and up by one on every push — it says nothing about what
#: changed, only which of two copies is the later one.  ``__version__`` cannot
#: answer that on its own: dev and deploy sit on the same version for as long as
#: the work takes, so "is this older than what I have" has no answer there, and
#: that is exactly the moment somebody is about to install the wrong one.
#:
#: Deliberately not derived from ``__version__`` for that reason.  It starts at
#: 10000 so it is five digits from the first one, and 89,999 of them is more
#: pushes than this will ever see.
BUILD = 10003


def version_label() -> str:
    """``1.3.0 “another-way” (build 10003)``, for people to read."""
    named = f"{__version__} “{RELEASE_NAME}”" if RELEASE_NAME else __version__
    return f"{named} (build {BUILD})"

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
    "BUILD",
    "DBUS_INTERFACE",
    "DBUS_PATH",
    "DBUS_SERVICE",
    "KWIN_SCRIPT_ID",
    "OVERLAY_WINDOW_TITLE",
    "PINNED_WINDOW_TITLE",
    "RELEASE_NAME",
    "__version__",
    "version_label",
]
