"""Cursor glow shown while a shake is being recognised.

The gesture is invisible until it fires, which makes it hard to learn and hard
to tell "I am shaking wrong" from "the thresholds are wrong".  So as soon as the
KWin script has accepted one swing it starts sending the pointer position
(``GestureProgress``) and this window lights the cursor up: a soft halo plus a
ring that closes as the remaining swings are made.

Two things make it harmless to have on screen:

* it never takes input — ``Qt.WindowTransparentForInput`` makes QtWayland send an
  empty ``wl_surface.set_input_region``, so clicks land in whatever is
  underneath;
* it never takes focus — ``WA_ShowWithoutActivating`` plus
  ``WindowDoesNotAcceptFocus``, so the window you were typing in keeps the
  keyboard.

Placement is the KWin script's job (the same promotion pass that raises the
selection overlay): a Wayland client cannot position itself, and this window has
to sit exactly over one output.  Without the script there is no gesture
detection either, so the glow simply never appears.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPaintEvent,
    QRadialGradient,
    QScreen,
)
from PyQt6.QtWidgets import QApplication, QWidget

from .logging_setup import get_logger

log = get_logger("glow")

GLOW_WINDOW_TITLE = "Circle to Search Glow"

#: Logical pixels.  The halo is deliberately larger than a cursor so it reads as
#: "the system noticed you" rather than as a cursor theme change.
_HALO_RADIUS = 52.0
_RING_RADIUS = 26.0
_RING_WIDTH = 3.0

_FRAME_MS = 16
_FADE_IN_MS = 120
_FADE_OUT_MS = 220

#: Hide when the script stops reporting (it also sends GestureEnded, this is the
#: safety net for a KWin that was restarted mid-gesture).
_STALE_MS = 900


class GestureGlow(QWidget):
    """A click-through, focus-free halo that follows the pointer."""

    def __init__(self, screen: QScreen, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._screen = screen
        self._position = QPoint(-1000, -1000)
        self._progress = 0.0
        self._opacity = 0.0
        self._target_opacity = 0.0
        self._phase = 0.0

        self.setWindowTitle(GLOW_WINDOW_TITLE)
        self.setObjectName("CircleToSearchGlow")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._accent = QApplication.palette().highlight().color()
        if not self._accent.isValid():
            self._accent = QColor(61, 174, 233)

        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

        self._stale = QTimer(self)
        self._stale.setInterval(_STALE_MS)
        self._stale.setSingleShot(True)
        self._stale.timeout.connect(self.end)

    # ------------------------------------------------------------------ API

    @property
    def screen_name(self) -> str:
        return self._screen.name()

    def update_gesture(self, position: QPoint, count: int, needed: int) -> None:
        """Move the halo and set how far along the gesture is."""
        previous = self._position
        self._position = position
        self._progress = min(1.0, count / max(1, needed))
        self._target_opacity = 1.0
        self._stale.start()

        if not self.isVisible():
            self._show()
        if not self._timer.isActive():
            self._timer.start()

        # Repaint only around the old and new positions: a full-screen
        # translucent repaint at 60 Hz would be a silly price for a halo.
        self.update(self._damage(previous).united(self._damage(position)))

    def end(self) -> None:
        """Start fading out; the window hides itself when it is done."""
        self._stale.stop()
        self._target_opacity = 0.0
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def dismiss(self) -> None:
        """Disappear at once (the selection overlay is taking over)."""
        self._stale.stop()
        self._timer.stop()
        self._opacity = 0.0
        self._target_opacity = 0.0
        self.hide()

    # --------------------------------------------------------------- internals

    def _show(self) -> None:
        self.setGeometry(self._screen.geometry())
        self.create()
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(self._screen)
            # Belt and braces for the input region: on Wayland this is what
            # actually makes the surface click-through.
            handle.setFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.show()
        self.raise_()
        log.debug("glow shown on %s", self._screen.name())

    def _damage(self, position: QPoint) -> QRect:
        reach = int(_HALO_RADIUS) + 6
        return QRect(
            position.x() - reach,
            position.y() - reach,
            reach * 2,
            reach * 2,
        )

    def _tick(self) -> None:
        step = _FRAME_MS / (_FADE_IN_MS if self._target_opacity > self._opacity else _FADE_OUT_MS)
        if self._target_opacity > self._opacity:
            self._opacity = min(self._target_opacity, self._opacity + step)
        else:
            self._opacity = max(self._target_opacity, self._opacity - step)

        self._phase = (self._phase + 0.09) % 6.28318

        if self._opacity <= 0.001 and self._target_opacity <= 0.0:
            self._timer.stop()
            self.hide()
            return
        self.update(self._damage(self._position))

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        # The widget is translucent: partial updates have to clear their own
        # rectangle, otherwise every frame is drawn on top of the previous one.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if self._opacity <= 0.001:
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        centre = self._position

        # A gentle breath so it reads as alive rather than as a static artifact.
        pulse = 1.0 + 0.06 * math.sin(self._phase)
        radius = _HALO_RADIUS * pulse * (0.75 + 0.25 * self._progress)

        gradient = QRadialGradient(float(centre.x()), float(centre.y()), radius)
        inner = QColor(self._accent)
        inner.setAlphaF(min(1.0, 0.55 * self._opacity * (0.6 + 0.4 * self._progress)))
        middle = QColor(self._accent)
        middle.setAlphaF(min(1.0, 0.22 * self._opacity))
        outer = QColor(self._accent)
        outer.setAlphaF(0.0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(0.45, middle)
        gradient.setColorAt(1.0, outer)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(centre, int(radius), int(radius))

        # The ring closes as the swings are counted: one more and it fires.
        if self._progress > 0.0:
            ring = QColor(self._accent)
            ring.setAlphaF(min(1.0, 0.9 * self._opacity))
            pen = painter.pen()
            pen.setColor(ring)
            pen.setWidthF(_RING_WIDTH)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            box = QRect(
                centre.x() - int(_RING_RADIUS),
                centre.y() - int(_RING_RADIUS),
                int(_RING_RADIUS * 2),
                int(_RING_RADIUS * 2),
            )
            # Qt angles are in 1/16th of a degree, counter-clockwise from 3 o'clock.
            painter.drawArc(box, 90 * 16, -int(360 * 16 * self._progress))

        painter.end()
