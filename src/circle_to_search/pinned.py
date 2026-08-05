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

**And it is where a crop is dragged out of.**  The idea was to drag one out of
the overlay, and the overlay turned out to be the one window it cannot be done
from: a `QDrag` has to be started while the button is still down, which is
while a *fullscreen* surface is still covering every window the picture could
be dropped into, so the drop would land on the overlay itself.  A pin is a
small ordinary window with nothing underneath it, which makes the same drag an
ordinary one — *P* and then drag, rather than a gesture fighting the
compositor.

`QDrag.exec()` runs a nested event loop, and this program has a rule against
opening one from a slot, bought with a SIGSEGV — see the message-box teardown
in `app.py`.  The rule holds here: this is a widget's own event handler, which
is where Qt's drag API is meant to be called from, and `app.py` never learns
that any of it happened.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QDrag,
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
from .dragout import (
    DRAG_LIFETIME_MS,
    drag_payload,
    png_bytes,
    remove_drag_file,
    write_drag_file,
)
from .i18n import tr
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

#: How big the picture that travels under the cursor may be.  It is a label on
#: the gesture, not the payload, and a 4K crop dragged at its own size covers
#: the window you are aiming at.
DRAG_PIXMAP_MAX = 256


def run_drag(drag: QDrag) -> Qt.DropAction:
    """Hand the drag to the compositor and wait for it to end.

    A function of its own, and the only thing in this module that cannot be
    checked from a test: the ``offscreen`` platform has no drag and drop at all,
    so a suite that called this would either hang or prove nothing.  Everything
    that decides *whether* to drag and *what* it carries is either side of it
    and is tested; this line is the part that needs a real compositor.
    """
    return drag.exec(Qt.DropAction.CopyAction)


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
        #: Written on the first drag out and reused by every one after it, so a
        #: crop dropped in three places leaves one file rather than three.
        self._drag_file: Path | None = None
        self._dragging_out = False

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
        # The pin has no bar, no caption and no menu, so the tooltip is the only
        # place its gestures can be written down — and one of them is a modifier
        # nobody would guess at.
        self.setToolTip(tr("pin.tooltip"))

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
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl is the one press going spare: a plain one moves the window,
            # and on a frameless window the whole surface *is* the title bar, so
            # there is nowhere else for this to live.
            self.drag_out()
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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # The only thing that says the crop can be taken out of here.  Read off
        # the event rather than from key presses, because a pin opens without
        # the focus and never receives any: it can spend its whole life being
        # hovered by a pointer belonging to a window that is still typing.
        self._show_modifier(event.modifiers())

    def _show_modifier(self, modifiers: Qt.KeyboardModifier) -> None:
        if self._dragging_out:
            return
        self.setCursor(
            Qt.CursorShape.DragCopyCursor
            if modifiers & Qt.KeyboardModifier.ControlModifier
            else Qt.CursorShape.OpenHandCursor
        )

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ------------------------------------------------------------ dragging out

    def drag_out(self) -> Qt.DropAction:
        """Carry the crop to another window and let go of it there.

        Started on the press rather than after a few pixels of travel, the way a
        drag usually is.  A threshold exists to tell a drag from a click, and
        with a modifier held there is nothing to tell apart — this window's
        plain press has already been spoken for by the move.
        """
        if self._dragging_out:
            return Qt.DropAction.IgnoreAction
        # Encoded again on every drag rather than kept.  A pin already holds a
        # full-size pixmap, and a second copy of the same picture living beside
        # it for the pin's whole life is the memory this program spent an item
        # getting rid of in the overlay — where dragging one out twice is rare
        # and a re-encode is milliseconds.  The *file* is what is not redone.
        payload = png_bytes(self._pixmap)
        if self._drag_file is None or not self._drag_file.exists():
            self._drag_file = write_drag_file(payload)
            self._arm_removal(self._drag_file)
        mime = drag_payload(self._pixmap, self._drag_file, payload)
        return self._exec_drag(mime)

    def _exec_drag(self, mime: QMimeData) -> Qt.DropAction:
        drag = QDrag(self)
        drag.setMimeData(mime)
        thumbnail = self._pixmap
        if thumbnail.width() > DRAG_PIXMAP_MAX or thumbnail.height() > DRAG_PIXMAP_MAX:
            # Only ever down.  `scaled` would just as happily blow a 20-pixel
            # crop up to the limit, and a blurred enlargement under the cursor
            # is a worse label on the gesture than the small sharp thing itself.
            thumbnail = thumbnail.scaled(
                DRAG_PIXMAP_MAX,
                DRAG_PIXMAP_MAX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        drag.setPixmap(thumbnail)
        # Under the middle of what is being carried, which is where the pointer
        # was: a hot spot at 0,0 hangs the picture off the cursor by its corner
        # and hides the thing being aimed at.
        drag.setHotSpot(QPoint(thumbnail.width() // 2, thumbnail.height() // 2))

        self._dragging_out = True
        self.setCursor(Qt.CursorShape.DragCopyCursor)
        try:
            action = run_drag(drag)
        finally:
            # Whatever the drop did, and whatever it raised, this window is
            # still here and still has to be usable.
            self._dragging_out = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        log.info("a pinned crop was dragged out (%s)", action.name)
        return action

    @staticmethod
    def _arm_removal(path: Path | None) -> None:
        """Take the file back off the disk once its time is up.

        The lambda closes over the *path* and not over the window, deliberately:
        a pin can be shut a second after the drop while the target is still
        reading the file, so the timer has to outlive it — and a timer holding a
        reference to a dead widget is the other way this program has crashed.
        """
        if path is None:
            return
        QTimer.singleShot(DRAG_LIFETIME_MS, lambda: remove_drag_file(path))

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
