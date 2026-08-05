"""Where the other windows are, at the moment the screen froze.

A Wayland client cannot see anybody else's geometry, so this can only come from
the compositor: the KWin script already walks ``workspace.windowList()`` in
``checkForOverlay()``, and it sends the layout with the trigger as
``"x,y,w,h;…"`` in global logical pixels, front-most first.

What the overlay does with it: outline the window under the pointer before a
drag starts, and let a plain click take exactly that rectangle.  The case this
removes is the one in the screenshots — a lasso drawn laboriously around a
rectangular panel that the compositor could have named in one number.

Two rough edges are handled here rather than in the drawing:

* **Windows partly off screen.**  A window may hang off the left edge or across
  two monitors; the rectangle is clipped to what is actually visible before it
  is offered, so a click can never ask for a crop outside the screenshot.
* **Shadows are not part of the frame.**  KWin's ``frameGeometry`` excludes the
  drop shadow, so the outline sits on the window and not on its blur.  That is
  the behaviour we want, and it is worth writing down because the first bug
  report about a one-pixel gap will be about exactly this.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect

from .logging_setup import get_logger

log = get_logger("windows")

#: More than this and they are stacked so deep that the ones underneath cannot
#: be pointed at anyway.  Matches MAX_WINDOW_RECTS in the KWin script.
MAX_RECTS = 64

#: A window smaller than this in either direction is a tooltip, a shadow helper
#: or something equally unclickable.
MIN_SIDE = 8


def parse_rects(encoded: str) -> list[QRect]:
    """``"x,y,w,h;…"`` from the KWin script → rectangles, front-most first.

    Malformed entries are skipped rather than raising: this arrives over D-Bus
    from another process, and a partial list is still worth having — the worst
    case is one window that cannot be clicked, not a capture that fails.
    """
    rects: list[QRect] = []
    for chunk in encoded.split(";"):
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 4:
            continue
        try:
            x, y, width, height = (int(part) for part in parts)
        except ValueError:
            continue
        if width < MIN_SIDE or height < MIN_SIDE:
            continue
        rects.append(QRect(x, y, width, height))
        if len(rects) >= MAX_RECTS:
            break
    return rects


def visible_on(rects: list[QRect], screen: QRect) -> list[QRect]:
    """The parts of those windows that fall on one screen, in its coordinates.

    A window may hang off an edge or straddle two monitors, so it is clipped
    first; what is left is moved into the screen's own coordinates, which is
    what everything the overlay draws is measured in.
    """
    on_screen: list[QRect] = []
    for rect in rects:
        clipped = rect.intersected(screen)
        if clipped.width() < MIN_SIDE or clipped.height() < MIN_SIDE:
            continue
        on_screen.append(clipped.translated(-screen.topLeft()))
    return on_screen


def window_at(rects: list[QRect], point: QPoint) -> QRect | None:
    """The front-most window under a point, or ``None``.

    The list is already in stacking order, so this is the first hit — no
    area comparison, no guessing at which of two overlapping windows was meant.
    Whichever one KWin would have given the click is the one outlined.
    """
    for rect in rects:
        if rect.contains(point):
            return rect
    return None
