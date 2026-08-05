"""Saying a colour, in the two forms anybody actually pastes.

The overlay has already frozen the screen and is already magnifying it under the
pointer, so the pixel's colour is on screen and being thrown away.  Keeping it
needs no new capability — only the two ways it gets written down, which are
worth having on their own away from any Qt object so they can be checked
against a table of known values.
"""

from __future__ import annotations

#: Where black and white give exactly the same contrast against a background,
#: in relative luminance: ``sqrt(1.05 × 0.05) − 0.05``.  Above it black wins,
#: below it white does.
_CROSSOVER = 0.1791


def hex_of(red: int, green: int, blue: int) -> str:
    """``#rrggbb``, lower case, the form every colour field on the machine takes."""
    return "#{:02x}{:02x}{:02x}".format(*(_clamp(part) for part in (red, green, blue)))


def css_of(red: int, green: int, blue: int) -> str:
    """``rgb(r, g, b)`` — what a stylesheet wants, and what *Shift* copies."""
    return "rgb({}, {}, {})".format(*(_clamp(part) for part in (red, green, blue)))


def readable_on(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """Black or white, whichever can be read on top of this colour.

    The swatch is the colour itself, so the label over it cannot be a fixed
    shade: ``#ffffff`` and ``#000000`` are both perfectly ordinary pixels to
    pick, and one of them would swallow the text.

    Decided on *linear* luminance rather than the cheap weighted sum of the
    channels as they are stored.  The shortcut is wrong exactly where it is
    most visible: a mid green like ``#00c800`` comes out "dark" on it and gets
    white text, when black is more than four times as readable on it.
    """
    return (0, 0, 0) if relative_luminance(red, green, blue) > _CROSSOVER else (255, 255, 255)


def relative_luminance(red: int, green: int, blue: int) -> float:
    """How bright a colour is to the eye, 0 to 1, the way sRGB defines it."""
    return (
        0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)
    )


def _linear(channel: int) -> float:
    """One channel, undone from the sRGB transfer curve."""
    value = _clamp(channel) / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _clamp(value: int) -> int:
    return max(0, min(255, int(value)))
