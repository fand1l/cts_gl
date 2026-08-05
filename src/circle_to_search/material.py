"""Material Design 3, as far as it reaches on a KDE desktop.

The reason for it is not fashion: the gesture this program imitates is Google's
*Circle to Search*, which on a phone is drawn in MD3, so somebody who knows what
that looks like arrives with an expectation.  The rules followed here are the
ones at <https://m3.material.io/>.

**What transfers is the part that is platform-neutral**, because the MD3
reference is Compose-first and none of its three implementation targets is
PyQt6: the colour *roles* and the rule that colours only ever pair as ``X`` and
``on-X``; the type *scale*; the shape corners; elevation expressed as tone
rather than shadow; the motion easings, which are plain cubic-béziers; and the
8 dp spacing grid.  What does not transfer is every line of example code, the
``dp`` table, spring physics, and Roboto.

Three places where MD3 and this program disagree, and the answer taken in each:

* **Dynamic colour is already done, differently.**  MD3 grows a scheme from the
  wallpaper.  This program's accent already comes from the Qt palette's
  Highlight, which is the KDE accent the user chose in System Settings — the
  same idea, answered better for this desktop, because it agrees with the rest
  of it.  So: **the MD3 roles, on the KDE seed.**
* **Tonal elevation needs a surface, and there is not one.**  Everything the
  overlay draws sits on a frozen screenshot of somebody else's screen, which is
  any colour at all.  So **the dimming becomes the surface**: it is no longer
  flat black but the dark scheme's ``surface`` — a near-black carrying the
  accent's own hue — and everything above it is a lighter tone of the same
  neutral palette.  That is exactly what MD3 means by elevation, and it is the
  one reading under which the elevation system applies here at all.
* **Roboto would be wrong.**  A KDE application that overrides the font chosen
  in System Settings is a badly behaved KDE application.  So the *scale* is
  taken and the *typeface* is not: every role is a ratio against MD3's own
  ``body-medium``, applied to whatever ``QFont`` the desktop hands over.

**Where this departs from the specification, and why.**  MD3 builds its tonal
palettes in HCT, Google's own space, whose *tone* is exactly CIE L\\* and whose
hue and chroma are CAM16's.  The tones here are computed in CIELAB, so **the
tone axis — and therefore every contrast ratio the ``X``/``on-X`` pairs promise
— is exact**, and only the hue and chroma of a generated tone differ slightly
from CAM16's, most visibly on strongly saturated blues.  The alternative was
three hundred lines of colour appearance model to move a few generated shades by
an amount nobody can name, in a program whose seed colour is somebody's theme
setting.  The tests check the *property* the spec is really promising — that
every role pair is readable — rather than a table of Google's exact bytes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QFont, QPalette, QResizeEvent
from PyQt6.QtWidgets import QApplication, QLabel, QSizePolicy, QWidget

from .logging_setup import get_logger

log = get_logger("material")

# --------------------------------------------------------------------- shape

#: The corner radii, in logical pixels.  One small set instead of the seven
#: separate numbers this program used to carry.  ``FULL`` is not a radius but an
#: instruction — half of whatever it is applied to — which is what MD3 means by
#: a pill.
SHAPE_NONE = 0
SHAPE_EXTRA_SMALL = 4
SHAPE_SMALL = 8
SHAPE_MEDIUM = 12
SHAPE_LARGE = 16
SHAPE_EXTRA_LARGE = 28
SHAPE_FULL = -1


def corner(radius: int, height: int) -> float:
    """Resolve a shape token against the thing it is being applied to."""
    if radius == SHAPE_FULL:
        return min(SHAPE_EXTRA_LARGE, max(0, height) / 2)
    return float(radius)


# ------------------------------------------------------------------- spacing

#: The 8 dp grid.  Half steps are allowed — MD3 uses 4 dp for the gap inside a
#: dense component — and nothing else is.
SPACE = 8


def space(units: float) -> int:
    """``space(0.5)`` is 4, ``space(2)`` is 16.  Nothing off the grid."""
    return round(SPACE * units)


# --------------------------------------------------------------------- motion

#: The easings, as the cubic-bézier control points the specification gives.
#: Qt has no spring, and MD3's springs have no honest approximation here, so the
#: béziers are the part that crosses over.
#:
#: **Nothing is animated with them yet, deliberately.**  They are here because
#: motion is one of the parts of the specification that transfers, and because a
#: design system with a hole where its timing should be invites the next person
#: to invent a number.  But this program has exactly two animations and neither
#: wants one: the gesture preview is a *depiction of a real movement*, whose
#: whole claim is that it travels at the speed the thresholds actually demand —
#: easing it would make it lie — and the lasso's trail fade belongs to
#: ``docs/STROKE.md``, which derived that look from photographs of the real
#: thing.  A hover fade on the action bar is the obvious third, and it would put
#: a timer on the overlay's repaint path to buy a subtlety; that trade has gone
#: badly here before.  So: written down, ready, and unused until something
#: genuinely needs to move.
EASING_STANDARD = (0.2, 0.0, 0.0, 1.0)
EASING_STANDARD_ACCELERATE = (0.3, 0.0, 1.0, 1.0)
EASING_STANDARD_DECELERATE = (0.0, 0.0, 0.0, 1.0)
EASING_EMPHASIZED = (0.2, 0.0, 0.0, 1.0)
EASING_EMPHASIZED_ACCELERATE = (0.3, 0.0, 0.8, 0.15)
EASING_EMPHASIZED_DECELERATE = (0.05, 0.7, 0.1, 1.0)

#: The durations, in milliseconds.
DURATION_SHORT_1 = 50
DURATION_SHORT_2 = 100
DURATION_SHORT_3 = 150
DURATION_SHORT_4 = 200
DURATION_MEDIUM_1 = 250
DURATION_MEDIUM_2 = 300
DURATION_MEDIUM_3 = 350
DURATION_MEDIUM_4 = 400
DURATION_LONG_1 = 450
DURATION_LONG_2 = 500


def ease(curve: tuple[float, float, float, float], t: float) -> float:
    """Where a cubic-bézier easing has got to at ``t`` in 0…1.

    Newton on x(t) with a bisection fallback, which is what every browser does
    for exactly this curve; the control points are ``(x1, y1, x2, y2)`` with the
    ends pinned at 0 and 1, as CSS defines them.
    """
    x1, y1, x2, y2 = curve
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0

    def bezier(a: float, b: float, u: float) -> float:
        v = 1 - u
        return 3 * v * v * u * a + 3 * v * u * u * b + u * u * u

    low, high, guess = 0.0, 1.0, t
    for _ in range(24):
        x = bezier(x1, x2, guess)
        if abs(x - t) < 1e-6:
            break
        if x < t:
            low = guess
        else:
            high = guess
        guess = (low + high) / 2
    return bezier(y1, y2, guess)


# ------------------------------------------------------------------ type scale

#: MD3's own ``body-medium`` is the anchor, because it is the role a desktop's
#: ordinary interface font already plays.  Every other role is stated as its
#: ratio to that, so the whole scale grows and shrinks with whatever the user
#: set in System Settings instead of pinning anything to Google's pixels.
_ANCHOR_PX = 14.0

#: role → (size in MD3 px, weight, tracking in MD3 px at that size).
_TYPE_SCALE: dict[str, tuple[float, int, float]] = {
    "display-large": (57, 400, -0.25),
    "display-medium": (45, 400, 0.0),
    "display-small": (36, 400, 0.0),
    "headline-large": (32, 400, 0.0),
    "headline-medium": (28, 400, 0.0),
    "headline-small": (24, 400, 0.0),
    "title-large": (22, 400, 0.0),
    "title-medium": (16, 500, 0.15),
    "title-small": (14, 500, 0.1),
    "body-large": (16, 400, 0.5),
    "body-medium": (14, 400, 0.25),
    "body-small": (12, 400, 0.4),
    "label-large": (14, 500, 0.1),
    "label-medium": (12, 500, 0.5),
    "label-small": (11, 500, 0.5),
}

TYPE_ROLES = tuple(_TYPE_SCALE)


def type_ratio(role: str) -> float:
    """How much bigger than the desktop's own font this role is."""
    return _TYPE_SCALE[role][0] / _ANCHOR_PX


def typeface(base: QFont, role: str) -> QFont:
    """``base``, at the size, weight and tracking MD3 gives that role.

    The family is never touched.  Sizes are carried in points when the base font
    has points and in pixels when it does not, because a font that came out of
    ``QFont(pixelSize=…)`` reports ``pointSizeF() == -1`` and multiplying that
    produces a font Qt will not draw.
    """
    size, weight, tracking = _TYPE_SCALE[role]
    ratio = size / _ANCHOR_PX
    font = QFont(base)
    if base.pointSizeF() > 0:
        font.setPointSizeF(max(5.0, base.pointSizeF() * ratio))
        # Tracking is in MD3's pixels at MD3's size, so it is scaled by how much
        # the role itself was scaled — otherwise a 57 px display line keeps the
        # letter spacing of a 14 px one.
        spacing = tracking * (font.pointSizeF() / size) * (96 / 72)
    else:
        font.setPixelSize(max(6, round(max(1, base.pixelSize()) * ratio)))
        spacing = tracking * (font.pixelSize() / size)
    font.setWeight(QFont.Weight(weight))
    if abs(spacing) > 0.01:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


# ------------------------------------------------------- colour: sRGB ↔ CIELAB

_WHITE_D65 = (0.95047, 1.00000, 1.08883)


def _to_linear(channel: float) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _from_linear(value: float) -> float:
    value = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    return value * 255


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    red, green, blue = (_to_linear(part) for part in rgb)
    x = 0.4124564 * red + 0.3575761 * green + 0.1804375 * blue
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = 0.0193339 * red + 0.1191920 * green + 0.9503041 * blue

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = (f(v / w) for v, w in zip((x, y, z), _WHITE_D65, strict=True))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _lab_to_rgb(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unclamped on purpose: the caller needs to know when it left the gamut."""
    lightness, a_star, b_star = lab
    fy = (lightness + 16) / 116
    fx = fy + a_star / 500
    fz = fy - b_star / 200

    def g(t: float) -> float:
        return t**3 if t > 6 / 29 else 3 * (6 / 29) ** 2 * (t - 4 / 29)

    x, y, z = (g(v) * w for v, w in zip((fx, fy, fz), _WHITE_D65, strict=True))
    return (
        _from_linear(3.2404542 * x - 1.5371385 * y - 0.4985314 * z),
        _from_linear(-0.9692660 * x + 1.8760108 * y + 0.0415560 * z),
        _from_linear(0.0556434 * x - 0.2040259 * y + 1.0572252 * z),
    )


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-0.5 <= part <= 255.5 for part in rgb)


def tone_of(rgb: tuple[int, int, int]) -> float:
    """How light a colour is, on MD3's 0…100 tone axis."""
    return _rgb_to_lab(rgb)[0]


def hue_chroma_of(rgb: tuple[int, int, int]) -> tuple[float, float]:
    """The seed's hue in degrees and its chroma, which set its whole palette."""
    _lightness, a_star, b_star = _rgb_to_lab(rgb)
    hue = math.degrees(math.atan2(b_star, a_star)) % 360
    return hue, math.hypot(a_star, b_star)


@lru_cache(maxsize=4096)
def tonal(hue: float, chroma: float, tone: float) -> tuple[int, int, int]:
    """One step of a tonal palette: this hue, at this lightness.

    The chroma asked for is the most that will be used, not a promise — a tone
    near either end of the axis has very little room for colour and asking for
    it anyway would put the answer outside sRGB, where it would be clipped
    channel by channel and come back a *different hue*.  So the chroma is
    searched down to whatever actually fits, which keeps the hue and keeps the
    tone, and those are the two the roles are built on.
    """
    tone = max(0.0, min(100.0, tone))
    radians = math.radians(hue)

    def at(c: float) -> tuple[float, float, float]:
        return _lab_to_rgb((tone, c * math.cos(radians), c * math.sin(radians)))

    wanted = at(chroma)
    if not _in_gamut(wanted):
        low, high = 0.0, chroma
        for _ in range(20):
            middle = (low + high) / 2
            if _in_gamut(at(middle)):
                low = middle
            else:
                high = middle
        wanted = at(low)
    return tuple(max(0, min(255, round(part))) for part in wanted)  # type: ignore[return-value]


@dataclass(frozen=True)
class TonalPalette:
    """One hue at one chroma, readable at any tone from 0 to 100."""

    hue: float
    chroma: float

    def tone(self, tone: float) -> QColor:
        return QColor(*tonal(self.hue, self.chroma, tone))

    def rgb(self, tone: float) -> tuple[int, int, int]:
        return tonal(self.hue, self.chroma, tone)


# ------------------------------------------------------------------- the roles

#: The chroma each palette is drawn at, straight out of the specification's own
#: core palette: the accent keeps at least 48 so a grey theme still produces a
#: usable primary, the neutrals are nearly grey but *not* grey — 4 and 8 chroma
#: is what makes a tinted surface tinted — and the tertiary is a 60° turn away.
_CHROMA_PRIMARY_MIN = 48.0
_CHROMA_SECONDARY = 16.0
_CHROMA_TERTIARY = 24.0
_HUE_TERTIARY_TURN = 60.0
_CHROMA_NEUTRAL = 4.0
_CHROMA_NEUTRAL_VARIANT = 8.0
_HUE_ERROR = 25.0
_CHROMA_ERROR = 84.0

#: Breeze blue, for when the palette has nothing to say.
FALLBACK_SEED = (61, 174, 233)

#: Below this much chroma a seed has no hue worth reading.  MD3's own core
#: palette forces at least 48 chroma onto whatever hue the seed reports, which is
#: right for the wallpaper it was designed to read and wrong here: a seed of pure
#: grey reports a hue made of rounding noise, and the whole program would come
#: out a colour nobody chose — green for one grey, pink for another.  A theme
#: with no accent colour in it gets Breeze's hue instead of a coin toss.  The one
#: place this departs from the specification's arithmetic, and it departs only
#: where the specification's input assumption does not hold.
_CHROMA_ACHROMATIC = 2.0


@dataclass(frozen=True)
class Scheme:
    """Every colour role, resolved.  Colours only ever pair as ``X``/``on-X``."""

    dark: bool
    primary: QColor
    on_primary: QColor
    primary_container: QColor
    on_primary_container: QColor
    secondary: QColor
    on_secondary: QColor
    secondary_container: QColor
    on_secondary_container: QColor
    tertiary: QColor
    on_tertiary: QColor
    error: QColor
    on_error: QColor
    surface: QColor
    on_surface: QColor
    surface_variant: QColor
    on_surface_variant: QColor
    surface_container_lowest: QColor
    surface_container_low: QColor
    surface_container: QColor
    surface_container_high: QColor
    surface_container_highest: QColor
    inverse_surface: QColor
    inverse_on_surface: QColor
    outline: QColor
    outline_variant: QColor
    scrim: QColor

    def state_layer(self, over: QColor, opacity: float) -> QColor:
        """MD3 says a pressed or hovered thing gets a film of its own ink.

        Not a lighter or darker version of the fill: the same ``on-`` colour at
        a low opacity, which is why a state layer looks right over every fill
        instead of only over the ones somebody checked.
        """
        film = QColor(over)
        film.setAlphaF(max(0.0, min(1.0, opacity)))
        return film


#: The state layer opacities, from the specification.
STATE_HOVER = 0.08
STATE_FOCUS = 0.10
STATE_PRESSED = 0.10
STATE_DRAGGED = 0.16


def scheme_for(seed: tuple[int, int, int], *, dark: bool) -> Scheme:
    """Build every role from one seed colour, light or dark.

    The tones are the specification's, unchanged.  Reading them as a table is
    the point: this is the only place in the program where a colour decision is
    made, and it is made by naming a palette and a tone.
    """
    hue, chroma = hue_chroma_of(seed)
    if chroma < _CHROMA_ACHROMATIC:
        hue = hue_chroma_of(FALLBACK_SEED)[0]
    accent = TonalPalette(hue, max(_CHROMA_PRIMARY_MIN, chroma))
    second = TonalPalette(hue, _CHROMA_SECONDARY)
    third = TonalPalette((hue + _HUE_TERTIARY_TURN) % 360, _CHROMA_TERTIARY)
    neutral = TonalPalette(hue, _CHROMA_NEUTRAL)
    variant = TonalPalette(hue, _CHROMA_NEUTRAL_VARIANT)
    wrong = TonalPalette(_HUE_ERROR, _CHROMA_ERROR)

    if dark:
        return Scheme(
            dark=True,
            primary=accent.tone(80),
            on_primary=accent.tone(20),
            primary_container=accent.tone(30),
            on_primary_container=accent.tone(90),
            secondary=second.tone(80),
            on_secondary=second.tone(20),
            secondary_container=second.tone(30),
            on_secondary_container=second.tone(90),
            tertiary=third.tone(80),
            on_tertiary=third.tone(20),
            error=wrong.tone(80),
            on_error=wrong.tone(20),
            surface=neutral.tone(6),
            on_surface=neutral.tone(90),
            surface_variant=variant.tone(30),
            on_surface_variant=variant.tone(80),
            surface_container_lowest=neutral.tone(4),
            surface_container_low=neutral.tone(10),
            surface_container=neutral.tone(12),
            surface_container_high=neutral.tone(17),
            surface_container_highest=neutral.tone(22),
            inverse_surface=neutral.tone(90),
            inverse_on_surface=neutral.tone(20),
            outline=variant.tone(60),
            outline_variant=variant.tone(30),
            scrim=neutral.tone(0),
        )
    return Scheme(
        dark=False,
        primary=accent.tone(40),
        on_primary=accent.tone(100),
        primary_container=accent.tone(90),
        on_primary_container=accent.tone(10),
        secondary=second.tone(40),
        on_secondary=second.tone(100),
        secondary_container=second.tone(90),
        on_secondary_container=second.tone(10),
        tertiary=third.tone(40),
        on_tertiary=third.tone(100),
        error=wrong.tone(40),
        on_error=wrong.tone(100),
        surface=neutral.tone(98),
        on_surface=neutral.tone(10),
        surface_variant=variant.tone(90),
        on_surface_variant=variant.tone(30),
        surface_container_lowest=neutral.tone(100),
        surface_container_low=neutral.tone(96),
        surface_container=neutral.tone(94),
        surface_container_high=neutral.tone(92),
        surface_container_highest=neutral.tone(90),
        inverse_surface=neutral.tone(20),
        inverse_on_surface=neutral.tone(95),
        outline=variant.tone(50),
        outline_variant=variant.tone(80),
        scrim=neutral.tone(0),
    )


def scheme_for_palette(palette: QPalette | None = None) -> Scheme:
    """The scheme for an ordinary window: seeded and lit by the desktop.

    The seed is the Highlight colour — the accent chosen in System Settings —
    and light or dark is *read* off the Window colour rather than assumed, so a
    Plasma in its dark theme gets MD3's dark scheme without anything having to
    be configured twice.  The overlay does not use this: it dims the screen, so
    it is dark by construction whatever the desktop is doing.
    """
    if palette is None:
        application = QApplication.instance()
        palette = application.palette() if application is not None else QPalette()
    seed = seed_of(palette.highlight().color())
    return scheme_for(seed, dark=tone_of(seed_of(palette.window().color())) < 50)


#: How long a line of prose should be allowed to get, in characters.  MD3 gives
#: body text a readable measure rather than the width of whatever is holding it,
#: and it is what stops a hint from being laid out as a narrow tall column.
READABLE_MEASURE = 64


class SupportingText(QLabel):
    """A wrapped remark under the thing it is about, that keeps its own height.

    MD3's name for the sentence under a control, and a fix for a Qt trap that
    has been in both dialogs all along: a word-wrapped ``QLabel`` reports a
    minimum height of *one line*, because it is entitled to assume it might be
    given unlimited width.  A layout that believes it hands over one line and
    the rest of the paragraph is drawn on top of whatever comes next.  It was
    survivable while the hints were body-sized and clipped at the right edge;
    at *body-small* they wrap sooner, so the same bug started overlapping the
    row below instead of running off the side of it.

    The fix is to answer the question the layout is actually asking — how tall
    at *this* width — which is what ``heightForWidth`` is for.

    Asked for from ``resizeEvent`` rather than by overriding ``sizeHint``.
    That was the first attempt and it is a trap of its own: with no width yet,
    the only width available to ask about is ``sizeHint().width()``, which for a
    wrapped label is Qt's guess at a *pleasantly shaped block* rather than the
    width the layout will really give — asking how tall the text is at that
    width answers with a tall thin column, and the window opens around it.  Once
    the widget has been laid out there is no guessing left to do.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        # The flag is the whole fix, and it is the one Qt provides for exactly
        # this: with it set, a layout asks `heightForWidth` at the width it is
        # really about to hand over, instead of believing `sizeHint`.  Left off,
        # a wrapped QLabel's hint is Qt's guess at a pleasant *block* — narrow
        # and several lines tall — and a column of hints makes the window as
        # tall as the sum of those guesses.  Smaller text wraps sooner, so
        # moving these to body-small made a long-standing squeeze into a window
        # half as tall again.
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def sizeHint(self) -> QSize:
        """As wide as a line of prose should be, and as tall as that makes it.

        ``show()`` sizes a window from its ``sizeHint``, and a wrapped label's
        own hint is Qt's guess at a block — which comes out narrow, and
        therefore tall, and therefore makes the window tall.  A paragraph has a
        width that is *right* rather than a width that is convenient, so this
        says what it is: MD3's readable measure, and then the honest height at
        it.
        """
        metrics = self.fontMetrics()
        measure = max(1, metrics.averageCharWidth()) * READABLE_MEASURE
        width = max(1, min(metrics.horizontalAdvance(self.text()), measure))
        return QSize(width, self.heightForWidth(width))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # And the belt to that pair of braces: a layout that ignores the flag
        # still cannot make the label shorter than the text needs.
        self.setMinimumHeight(self.heightForWidth(max(1, self.width())))


def supporting(text: str, scheme: Scheme | None = None) -> SupportingText:
    """The supporting-text role, ready to drop into a layout."""
    label = SupportingText(text)
    restyle(label, "body-small", (scheme or scheme_for_palette()).on_surface_variant)
    return label


def restyle(widget: QWidget, role: str, colour: QColor | None = None) -> QWidget:
    """Put a widget on the type scale, and optionally on a colour role.

    The typeface is still the desktop's and so is the widget: this sets a size,
    a weight and an ink, which is the part of MD3 that belongs to a program
    rather than to a platform style.  Anything more would be a KDE application
    wearing somebody else's buttons.
    """
    widget.setFont(typeface(widget.font(), role))
    if colour is not None:
        palette = widget.palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            palette.setColor(group, QPalette.ColorRole.WindowText, colour)
            palette.setColor(group, QPalette.ColorRole.Text, colour)
        widget.setPalette(palette)
    return widget


def seed_of(colour: QColor) -> tuple[int, int, int]:
    """A QColor as the plain triple the arithmetic above works in."""
    if not colour.isValid():
        return FALLBACK_SEED
    return colour.red(), colour.green(), colour.blue()


def contrast(one: QColor, two: QColor) -> float:
    """The WCAG ratio between two colours, 1 to 21.

    Here rather than in ``colours.py`` because that module answers a different
    question — which of black and white to put on a swatch — and this one is how
    the ``X``/``on-X`` pairs above are checked to be worth their name.
    """
    lights = sorted(
        (
            0.2126 * _to_linear(part.red())
            + 0.7152 * _to_linear(part.green())
            + 0.0722 * _to_linear(part.blue())
        )
        for part in (one, two)
    )
    return (lights[1] + 0.05) / (lights[0] + 0.05)
