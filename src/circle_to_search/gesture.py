"""Show the gesture instead of describing it.

The welcome window explains a physical movement in three lines of prose, and the
README has a placeholder for a screenshot of it that does not exist.  Both are
the same admission: a movement is very hard to write down and very easy to show.

So this draws it — but not as a fixed picture of "the" gesture, which would be
another thing that can drift out of step with the code.  The path here is built
from :func:`~circle_to_search.config.read_detection`:

* ``reversals`` decides how many strokes there are,
* ``minAmplitudePx`` how long each one is,
* ``minSpeedPxPerSec`` how fast the dot travels,
* ``angleTolerance`` how far off the 45° diagonal the strokes lean,
* ``windowMs`` whether all of that fits in the time the detector allows.

So it is not a drawing of the gesture, it is *the current settings*, animated.
After a calibration it shows your own gesture; a threshold set to something
absurd is visible as absurd movement, which is the fastest way to see that a
number is wrong that does not involve shaking the mouse and guessing.

The geometry is a plain function with no Qt in it, so all of the above can be
checked without a screen.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget

from .config import DetectionSettings
from .i18n import tr

#: How far each stroke leans off the 45° diagonal, as a fraction of the angle
#: tolerance and never more than this many degrees.  The detector does not need
#: any lean — two perfectly retraced strokes would satisfy it — but a real hand
#: never lands twice on the same pixel, and with no separation the animation is
#: a dot sliding along a single line segment.  Taking it out of the tolerance
#: rather than inventing a number keeps the drawing inside what is accepted.
_LEAN_OF_TOLERANCE = 0.45
_MAX_LEAN_DEG = 16.0

#: The animation runs at this rate and rests this long between repetitions.
TICK_MS = 33
REST_MS = 900


@dataclass(frozen=True)
class Gesture:
    """One demonstration of the movement the current settings ask for."""

    #: The corners of the zigzag, in gesture pixels, starting at (0, 0).
    points: tuple[tuple[float, float], ...]
    #: How long one stroke takes at the required minimum speed, in ms.
    swing_ms: float
    #: How long the whole thing takes.
    total_ms: float
    width: float
    height: float
    #: False when the strokes, made at the *slowest* speed the detector accepts,
    #: would not all land inside ``windowMs``.  Not an impossibility — shaking
    #: faster than the speed threshold fixes it — but worth saying out loud,
    #: because otherwise the two numbers look independent and are not.
    fits_window: bool

    @property
    def swings(self) -> int:
        return max(1, len(self.points) - 1)

    def at(self, elapsed_ms: float) -> tuple[float, float]:
        """Where the dot is, ``elapsed_ms`` into the gesture."""
        if self.total_ms <= 0 or len(self.points) < 2:
            return self.points[0] if self.points else (0.0, 0.0)
        clamped = max(0.0, min(elapsed_ms, self.total_ms))
        index = min(int(clamped // self.swing_ms), self.swings - 1)
        along = (clamped - index * self.swing_ms) / self.swing_ms
        start = self.points[index]
        end = self.points[index + 1]
        return (
            start[0] + (end[0] - start[0]) * along,
            start[1] + (end[1] - start[1]) * along,
        )


def build_gesture(detection: DetectionSettings) -> Gesture:
    """The movement these settings are asking for, as a path and a timing."""
    amplitude = max(1.0, float(detection.minAmplitudePx))
    # A reversal is a change of direction, so n reversals need n + 1 strokes.
    swings = max(1, int(detection.reversals) + 1)
    speed = max(1.0, float(detection.minSpeedPxPerSec))
    swing_ms = amplitude / speed * 1000.0
    total_ms = swing_ms * swings

    # Every stroke is exactly `amplitude` long, and every stroke is within the
    # angle tolerance of the 45° diagonal — those are the two things the
    # detector actually measures, so the drawing has to satisfy both.  The lean
    # alternates by a fraction of the tolerance, which is what separates the
    # strokes: going out at 45°+δ and back at 45°−δ leaves a small perpendicular
    # drift per pair, and no distortion of either stroke.
    lean = math.radians(min(float(detection.angleTolerance), _MAX_LEAN_DEG) * _LEAN_OF_TOLERANCE)
    forward = math.pi / 4 + lean
    backward = math.pi / 4 - lean

    points: list[tuple[float, float]] = [(0.0, 0.0)]
    for index in range(swings):
        angle = forward if index % 2 == 0 else backward
        direction = 1.0 if index % 2 == 0 else -1.0
        x, y = points[-1]
        points.append(
            (
                x + math.cos(angle) * amplitude * direction,
                y + math.sin(angle) * amplitude * direction,
            )
        )

    left = min(x for x, _ in points)
    top = min(y for _, y in points)
    shifted = tuple((x - left, y - top) for x, y in points)
    return Gesture(
        points=shifted,
        swing_ms=swing_ms,
        total_ms=total_ms,
        width=max(x for x, _ in shifted),
        height=max(y for _, y in shifted),
        # The detector counts reversals inside a sliding window, so the strokes
        # after the first have to fit in it.
        fits_window=(total_ms - swing_ms) <= float(detection.windowMs),
    )


class GesturePreview(QWidget):
    """A dot travelling the path the current settings describe."""

    def __init__(self, detection: DetectionSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gesture = build_gesture(detection)
        self._elapsed = 0.0
        self._resting = 0.0
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_detection(self, detection: DetectionSettings) -> None:
        """Follow a change made in the settings, or a fresh calibration."""
        self._gesture = build_gesture(detection)
        self._elapsed = 0.0
        self._resting = 0.0
        self.update()

    # The timer only runs while the panel is on screen: a settings window left
    # open in the background must not repaint anything thirty times a second.
    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        if self._resting > 0:
            self._resting -= TICK_MS
            if self._resting <= 0:
                self._elapsed = 0.0
            self.update()
            return
        self._elapsed += TICK_MS
        if self._elapsed >= self._gesture.total_ms:
            self._elapsed = self._gesture.total_ms
            self._resting = REST_MS
        self.update()

    # ------------------------------------------------------------- drawing

    def _warning_height(self) -> float:
        """How much room the warning line needs, wrapped, or nothing."""
        if self._gesture.fits_window:
            return 0.0
        bold = QFont(self.font())
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        box = metrics.boundingRect(
            0, 0, max(1, self.width() - 20), 0,
            int(Qt.TextFlag.TextWordWrap),
            tr("gesture.too_slow"),
        )
        return box.height() + 8.0

    def _scale(self) -> tuple[float, float, float]:
        """Fit the path into the panel: (scale, x offset, y offset)."""
        margin = 18.0
        # The warning, when there is one, gets a band of its own at the top
        # rather than being written across the movement it is complaining about.
        top = margin + self._warning_height()
        usable_w = max(1.0, self.width() - 2 * margin)
        usable_h = max(1.0, self.height() - top - margin)
        scale = min(
            usable_w / max(1.0, self._gesture.width),
            usable_h / max(1.0, self._gesture.height),
        )
        drawn_w = self._gesture.width * scale
        drawn_h = self._gesture.height * scale
        return (
            scale,
            (self.width() - drawn_w) / 2,
            top + (usable_h - drawn_h) / 2,
        )

    def _placed(self) -> list[QPointF]:
        scale, dx, dy = self._scale()
        return [QPointF(x * scale + dx, y * scale + dy) for x, y in self._gesture.points]

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        palette = QApplication.palette()
        accent = palette.highlight().color()
        ink = palette.windowText().color()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 14))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        points = self._placed()
        if len(points) < 2:
            painter.end()
            return

        # The whole path, faint: the shape is as much of the answer as the
        # movement is, and a dot on its own never shows a shape.
        ghost = QPen(QColor(ink.red(), ink.green(), ink.blue(), 70))
        ghost.setWidth(2)
        ghost.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(ghost)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for start, end in itertools.pairwise(points):
            painter.drawLine(start, end)

        # How far the dot has come, solid.
        travelled = self._travelled(points)
        if len(travelled) >= 2:
            trail = QPen(accent)
            trail.setWidth(3)
            trail.setCapStyle(Qt.PenCapStyle.RoundCap)
            trail.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(trail)
            for start, end in itertools.pairwise(travelled):
                painter.drawLine(start, end)

        scale, dx, dy = self._scale()
        x, y = self._gesture.at(self._elapsed)
        head = QPointF(x * scale + dx, y * scale + dy)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(head, 7, 7)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawEllipse(head, 3, 3)

        if not self._gesture.fits_window:
            # Something a picture of the movement can say that the numbers
            # cannot.  Amber, not red: nothing here is impossible — it means the
            # shake has to be quicker than the speed threshold for the reversals
            # to land inside the window, which is worth knowing before you spend
            # ten minutes wondering why nothing opens.
            warning = QFont(self.font())
            warning.setBold(True)
            painter.setFont(warning)
            painter.setPen(QPen(QColor(176, 112, 20)))
            painter.drawText(
                QRectF(self.rect()).adjusted(10, 6, -10, -10),
                int(
                    Qt.AlignmentFlag.AlignTop
                    | Qt.AlignmentFlag.AlignHCenter
                    | Qt.TextFlag.TextWordWrap
                ),
                tr("gesture.too_slow"),
            )
        painter.end()

    def _travelled(self, points: list[QPointF]) -> list[QPointF]:
        """The part of the path the dot has already covered."""
        gesture = self._gesture
        if gesture.total_ms <= 0:
            return []
        done = min(int(self._elapsed // gesture.swing_ms), gesture.swings)
        scale, dx, dy = self._scale()
        x, y = gesture.at(self._elapsed)
        return [*points[: done + 1], QPointF(x * scale + dx, y * scale + dy)]
