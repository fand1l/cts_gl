"""Leaving a piece of the screen on the screen.

The commonest reason to capture something is to *look at it* while typing
somewhere else, and every other thing this program does with a selection takes
it away: searching sends it, copying hides it in the clipboard, saving buries it
in a folder.  The case is always the same shape — the thing you need to read is
in one window, the place you have to type it is in another, and the second
covers the first.  Today that is alt-tab, forget, alt-tab, forget.

So a pinned crop is a small frameless window holding exactly that image, above
everything, until you close it.

Two Wayland facts shape all of it.  A client **cannot place its own window** —
`move()` does nothing — so dragging one around has to be handed to the
compositor with `startSystemMove()`, and the window has to be *born* wherever
KWin decides.  And a client cannot make itself stay above other windows either;
the KWin script does that, by caption, the same way it promotes the overlay.
It follows that neither of the two things that make a pin a pin is done here.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtWidgets import QWidget

from . import PINNED_WINDOW_TITLE
from .logging_setup import get_logger

log = get_logger("pinned")

#: How far the image may be scaled, and by how much per notch of the wheel.
MIN_SCALE = 0.15
MAX_SCALE = 6.0
ZOOM_STEP = 1.1

#: Never open one smaller than this in either direction: a pin you cannot see
#: is a pin you cannot close.
MIN_SIDE = 24

#: Nor larger than this share of the screen it lands on.  A full-screen crop
#: pinned at its own size *is* a second copy of the screen, on top of the first.
MAX_SHARE = 0.8

#: The border: light over dark, because this is drawn on top of somebody else's
#: window and either one alone disappears against something.
_RIM_LIGHT = QColor(255, 255, 255, 210)
_RIM_DARK = QColor(0, 0, 0, 160)


class PinnedCrop(QWidget):
    """One crop, frameless and above everything, until it is closed."""

    #: Ctrl+C on a pinned window: a pin can become the other actions without
    #: the screen having to be captured again.
    copy_requested = pyqtSignal(QPixmap)
    #: It went away — by Esc, by a middle click, or by being closed.
    closed = pyqtSignal()

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._scale = 1.0

        self.setWindowTitle(PINNED_WINDOW_TITLE)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            # A Tool window keeps it out of the task switcher even when the KWin
            # script is not loaded, so an unpromoted pin is still not clutter.
            | Qt.WindowType.Tool
        )
        # Shown without taking the focus: pinning something is not a request to
        # stop typing where you were typing.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(PINNED_WINDOW_TITLE)

        self.resize(self._wanted_size())
        log.info("pinned a %dx%d crop", pixmap.width(), pixmap.height())

    # ------------------------------------------------------------- geometry

    def _wanted_size(self) -> QSize:
        """The size for the current scale, kept sane at both ends."""
        width = max(MIN_SIDE, round(self._pixmap.width() * self._scale))
        height = max(MIN_SIDE, round(self._pixmap.height() * self._scale))
        return QSize(width, height)

    def fit_to_screen(self, available: QRect) -> None:
        """Shrink an oversized crop so it opens as a pin and not as a wall.

        Called before showing, with the geometry of the screen it will land on:
        a selection can be the whole 4K panel, and pinning that at its own size
        would cover the thing it was taken from.
        """
        if self._pixmap.isNull() or available.isEmpty():
            return
        limit = min(
            available.width() * MAX_SHARE / max(1, self._pixmap.width()),
            available.height() * MAX_SHARE / max(1, self._pixmap.height()),
            1.0,
        )
        if limit < 1.0:
            self._scale = max(MIN_SCALE, limit)
            log.info("scaled the pin to %d%% so it fits the screen", round(self._scale * 100))
            self.resize(self._wanted_size())

    def scale(self) -> float:
        return self._scale

    def _zoom(self, factor: float) -> None:
        wanted = max(MIN_SCALE, min(MAX_SCALE, self._scale * factor))
        if abs(wanted - self._scale) < 1e-9:
            return
        self._scale = wanted
        self.resize(self._wanted_size())
        self.update()

    # --------------------------------------------------------------- events

    def paintEvent(self, _event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._pixmap)

        # Two rims rather than one: on top of an unknown window a single-colour
        # edge is invisible against something, and without an edge a pinned crop
        # of a white dialog on a white background has no shape at all.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for inset, colour in ((0, _RIM_DARK), (1, _RIM_LIGHT)):
            pen = QPen(colour)
            pen.setWidth(1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(self.rect().adjusted(inset, inset, -inset - 1, -inset - 1))

        if abs(self._scale - 1.0) > 0.01:
            # Only when it is not life size, because that is the only time the
            # number tells you anything you cannot see.
            self._draw_scale(painter)
        painter.end()

    def _draw_scale(self, painter: QPainter) -> None:
        text = f"{round(self._scale * 100)} %"
        metrics = painter.fontMetrics()
        box = QRect(
            4, 4, metrics.horizontalAdvance(text) + 12, metrics.height() + 4
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            # Closes without needing the focus first, which is the point: a pin
            # is usually in the way of the window you are actually working in.
            self.dismiss()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        window = self.windowHandle()
        if window is None:
            return
        # Wayland does not let a client place its own window, so the drag is
        # handed to the compositor and it is KWin that moves it from here on.
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if not window.startSystemMove():
            log.debug("the compositor would not start a system move")
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Proportional to how far the wheel actually turned, not one step per
        # event: 120 units is one notch of a mouse wheel, and a high-resolution
        # touchpad sends a stream of much smaller ones.  Treating those as a
        # notch each would make a gentle two-finger scroll leap through the
        # whole zoom range.
        notches = event.angleDelta().y() / 120
        if notches == 0:
            return
        self._zoom(ZOOM_STEP ** notches)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.dismiss()
            return
        if key == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_requested.emit(self._pixmap)
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom(ZOOM_STEP)
            return
        if key == Qt.Key.Key_Minus:
            self._zoom(1 / ZOOM_STEP)
            return
        if key == Qt.Key.Key_0:
            self._scale = 1.0
            self.resize(self._wanted_size())
            self.update()
            return
        super().keyPressEvent(event)

    def dismiss(self) -> None:
        log.info("a pinned crop was closed")
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.closed.emit()
        event.accept()


def open_pin(pixmap: QPixmap, near: QPoint | None = None) -> PinnedCrop:
    """Make a pin, size it for the screen it will land on, and show it."""
    pin = PinnedCrop(pixmap)
    screen = None
    if near is not None:
        screen = QGuiApplication.screenAt(near)
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is not None:
        pin.fit_to_screen(screen.availableGeometry())
    pin.show()
    return pin
