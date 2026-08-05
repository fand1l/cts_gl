"""The lasso stroke: a wide white line with a coloured glow at the pointer.

Designed in ``docs/STROKE.md`` from photographs of the real thing, which
corrected the first two drafts:

* **The line carries no colour at all.**  It is white, end to end, at full
  opacity.  An earlier draft ran a four-colour gradient along it; the
  photographs settled that.
* **The colour is a glow under the head** of the line — a soft round blob about
  ten times the line's width across, drawn *before* the line so the cap sits on
  top of it.
* **The colour is a function of height on screen**, not of a clock.  Tested
  directly on a phone: blue at the top, red across the middle, yellow a little
  below that, green at the bottom.  A fast swipe from one corner to the other
  lays all four out at once, in that order.
* **The stretch is a fading trail**, not a shape computed from velocity.  Keep
  the last handful of head positions and draw the glow at every one of them,
  fading with age: moving fast they are far apart and the glow *is* a smear;
  nearly still they pile up and it is a bright round circle.  No velocity is
  ever calculated, and nothing snaps when the direction changes.

Everything here is arithmetic — the ramp, the trail, the fade, the damaged
rectangle — so all of it can be checked without a screen.  The drawing itself
lives in :mod:`circle_to_search.overlay`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QColor

#: The line: wide, white, opaque, with round caps and joins so it reads as one
#: ribbon.  The shadow under it is a few pixels wider — Android draws over
#: photographs and does not need one, but a white line on a white page is
#: invisible and we are drawing over whatever the user had on screen.
LINE_WIDTH = 12
SHADOW_EXTRA = 4

#: How far the glow reaches, in logical pixels.  Far bigger than a first guess
#: suggests: on a fast swipe the colour washes out over a good fraction of the
#: screen, brightest along the line and falling away for a hundred pixels more.
GLOW_RADIUS = 140

#: How long a remembered head position keeps glowing, and how often new ones are
#: taken.  The cap matters: a gaming mouse reports at 1000 Hz and every sample
#: would otherwise become a blob to draw.
TRAIL_LIFETIME_MS = 500
TRAIL_MIN_GAP_MS = 1000 // 90

#: Frames while a drag is in progress, and how long the tail keeps fading after
#: the button comes up, so the smear is seen settling into a circle rather than
#: disappearing.
TICK_MS = 16
SETTLE_MS = 200

#: The vertical ramp, as (fraction of screen height, colour).  Read off the real
#: thing rather than invented: blue at the top, red at about two fifths, yellow
#: a little below that, green at the bottom.
RAMP: tuple[tuple[float, str], ...] = (
    (0.00, "#4285F4"),
    (0.40, "#EA4335"),
    (0.62, "#FBBC05"),
    (1.00, "#34A853"),
)


def glow_colour(y: float, screen_height: float) -> QColor:
    """The colour of a glow at height ``y``, on a screen ``screen_height`` tall.

    One pure function of one number.  This is what explains the earlier
    photographs I had read as a time cycle: one showed orange and the other
    yellow because *both* heads happened to be in the lower middle of the
    screen, where the ramp runs from red through orange into yellow.  Nothing
    was drifting; they were simply at similar heights.

    Normalised against the height of **the screen the overlay covers**, not the
    whole virtual desktop — otherwise the same gesture would come out a
    different colour depending on which monitor it happened on.  Clamped rather
    than wrapped: above the top is blue, below the bottom is green.
    """
    stops = [(position, QColor(name)) for position, name in RAMP]
    if screen_height <= 0:
        return stops[0][1]
    fraction = max(0.0, min(1.0, y / screen_height))

    for index in range(len(stops) - 1):
        start_at, start = stops[index]
        end_at, end = stops[index + 1]
        if fraction > end_at:
            continue
        span = end_at - start_at
        along = 0.0 if span <= 0 else (fraction - start_at) / span
        return QColor(
            round(start.red() + (end.red() - start.red()) * along),
            round(start.green() + (end.green() - start.green()) * along),
            round(start.blue() + (end.blue() - start.blue()) * along),
        )
    return stops[-1][1]


@dataclass(frozen=True)
class Blob:
    """One remembered head position, with the colour it had when it was there."""

    point: QPoint
    colour: QColor
    at_ms: float


@dataclass
class Trail:
    """The last few head positions, oldest first.

    The obvious way to elongate a glow is to compute a shape from the velocity.
    That means differentiating a noisy pointer signal, and it produces something
    that snaps around when the direction changes.  This keeps positions instead:
    moving fast they lie far apart and the blobs lay out along the path; nearly
    still they stack on one spot and the glow is round and brighter.  Lifting
    the button lets the tail age out, so the smear settles into a circle by
    itself.
    """

    lifetime_ms: float = TRAIL_LIFETIME_MS
    min_gap_ms: float = TRAIL_MIN_GAP_MS
    blobs: list[Blob] = field(default_factory=list)

    def add(self, point: QPoint, colour: QColor, at_ms: float) -> bool:
        """Remember a head position.  False when it arrived too soon after the last."""
        if self.blobs and at_ms - self.blobs[-1].at_ms < self.min_gap_ms:
            return False
        self.blobs.append(Blob(QPoint(point), QColor(colour), at_ms))
        self.prune(at_ms)
        return True

    def prune(self, now_ms: float) -> None:
        """Drop everything older than the lifetime, keeping the order."""
        cutoff = now_ms - self.lifetime_ms
        if self.blobs and self.blobs[0].at_ms >= cutoff:
            return
        self.blobs = [blob for blob in self.blobs if blob.at_ms >= cutoff]

    def clear(self) -> None:
        self.blobs = []

    def alive(self, now_ms: float) -> list[tuple[Blob, float]]:
        """Every blob still glowing, with how much of it is left (1 → 0)."""
        cutoff = now_ms - self.lifetime_ms
        out: list[tuple[Blob, float]] = []
        for blob in self.blobs:
            if blob.at_ms < cutoff:
                continue
            age = now_ms - blob.at_ms
            out.append((blob, max(0.0, 1.0 - age / self.lifetime_ms)))
        return out

    def bounds(self, radius: int = GLOW_RADIUS) -> QRect:
        """The rectangle the glow can possibly touch.  Empty when nothing is left.

        This is the number that matters: it is what gets repainted on every
        frame, and the last thing wearing this description had to be taken back
        out because it repainted a large area at ten frames a second.
        """
        if not self.blobs:
            return QRect()
        xs = [blob.point.x() for blob in self.blobs]
        ys = [blob.point.y() for blob in self.blobs]
        return QRect(
            min(xs) - radius,
            min(ys) - radius,
            max(xs) - min(xs) + 2 * radius,
            max(ys) - min(ys) + 2 * radius,
        )

    def __len__(self) -> int:
        return len(self.blobs)
