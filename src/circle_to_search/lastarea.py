"""Where you selected last time, so you can select there again.

Comparing something that changes — a build log, a download counter, a dashboard
number — means taking the *same* rectangle twice.  Drawing it by hand each time
gets it slightly different each time, which is precisely what makes the two
results hard to compare.

So the last selection is remembered as four numbers and the screen it was on,
and the overlay offers it back.  In **logical** pixels rather than physical
ones: a logical rectangle still means the same place on the screen after the
scale factor changes, and it is the space the overlay itself works in.

Two lines of text in the settings file, and a pure pair of functions here, so
the parsing of something a person can open and edit is testable on its own and
cannot throw into the capture path.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRect

#: What a selection that spanned several monitors is filed under.  Its numbers
#: are global logical pixels — the coordinates a group of overlays agrees in —
#: and no single screen can claim them.
EVERY_SCREEN = "*"

#: A rectangle smaller than this in either direction is not worth offering
#: again; it is far more likely to have been a slip than a selection.
MIN_SIDE = 8


@dataclass(frozen=True)
class LastArea:
    """The previous selection: which screen, and where on it."""

    screen: str
    rect: QRect

    def matches(self, screen: str) -> bool:
        """Whether this can be offered to an overlay covering ``screen``."""
        return self.screen == screen


def format_area(screen: str, rect: QRect) -> str:
    """``"eDP-1 100 200 400 300"``.  Empty when there is nothing worth keeping.

    A screen with no name is one of those: there would be nothing to check the
    rectangle against next time, and offering it to whatever screen came along
    would put the selection somewhere arbitrary.
    """
    # A name with a space in it would make the field ambiguous.  KWin connector
    # names never have one, but the file is meant to be readable by hand.
    name = "".join(screen.split())
    if not name or rect.width() < MIN_SIDE or rect.height() < MIN_SIDE:
        return ""
    return f"{name} {rect.x()} {rect.y()} {rect.width()} {rect.height()}"


def parse_area(value: str) -> LastArea | None:
    """Read one back.  ``None`` for anything that is not exactly the five fields.

    Deliberately strict and deliberately silent: this comes out of a settings
    file, the only cost of rejecting it is that a convenience is not offered,
    and guessing at a half-parsed rectangle would put the selection somewhere
    nobody asked for.
    """
    parts = value.split()
    if len(parts) != 5:
        return None
    name, numbers = parts[0], parts[1:]
    try:
        x, y, width, height = (int(number) for number in numbers)
    except ValueError:
        return None
    if width < MIN_SIDE or height < MIN_SIDE:
        return None
    return LastArea(screen=name, rect=QRect(x, y, width, height))
