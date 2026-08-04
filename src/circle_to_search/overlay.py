"""The frozen-screen selection overlay.

A frameless, always-on-top, full-screen widget that shows the screenshot that
was just taken, dims it, and lets the user draw a selection.  The un-dimmed
screenshot shows through inside the selection, the same way Spectacle's region
mode looks.

Two selection modes:

* **lasso** (default) — draw freehand around whatever you want, like Android's
  Circle to Search.  The loop only *marks out the edges*: what gets uploaded is
  the plain rectangular crop around it, exactly as it looks on screen.  Turning
  on :class:`AppSettings.lasso_mask` additionally whitens everything outside the
  loop, which is occasionally useful for isolating one object but is not what
  Circle to Search does.
* **rectangle** — the classic drag.  Hold *Shift* while starting a lasso drag to
  get a rectangle for that one selection (and vice versa).

Releasing the button does not send anything.  The selection stays on screen with
handles on its edges, and the keyboard decides what happens to it: *Enter*
searches, *C* copies, *S* saves to a file, *Esc* cancels.  A selection is easy to
get slightly wrong and impossible to take back once it has been uploaded, which
is the whole reason for the pause; :class:`AppSettings.confirm_selection` turns
it off for anyone who prefers the older send-on-release behaviour.

Why not layer-shell?  There are no Python bindings for ``layer-shell-qt`` (it is
a C++ library without GObject introspection), so the only thing reachable from
Python is its Qt *shell integration plugin* via
``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` — see :mod:`circle_to_search.app`.
Without the C++ API the surface cannot be anchored or given an exclusive zone,
which is why the plugin is opt-in and the default is this plain full-screen
window, raised above the panels by the KWin script (``keepAbove``/``fullScreen``/
``noBorder``).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygon,
    QResizeEvent,
    QScreen,
)
from PyQt6.QtWidgets import QApplication, QWidget

from . import OVERLAY_WINDOW_TITLE
from .hidpi import ScreenMetrics, logical_rect_to_physical
from .i18n import tr
from .logging_setup import get_logger

log = get_logger("overlay")

#: Selections smaller than this (in logical pixels) are treated as a stray click.
MIN_SELECTION = 10

#: Freehand points closer together than this are dropped: it keeps the polygon
#: small without any visible difference.
_LASSO_MIN_STEP = 3

MODE_LASSO = "lasso"
MODE_RECTANGLE = "rectangle"

_LABEL_MARGIN = 8
_LABEL_PADDING = 6

#: Side of a resize handle, and how far from an edge a press still grabs it.
_HANDLE = 14

#: Arrow-key step, and the bigger one with Ctrl held.
_NUDGE = 1
_NUDGE_FAST = 10

#: The eight handles, as (name, x factor, y factor) of the box.
_HANDLES = (
    ("nw", 0.0, 0.0),
    ("n", 0.5, 0.0),
    ("ne", 1.0, 0.0),
    ("e", 1.0, 0.5),
    ("se", 1.0, 1.0),
    ("s", 0.5, 1.0),
    ("sw", 0.0, 1.0),
    ("w", 0.0, 0.5),
)

_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}

#: What the user asked to do with the selection.
ACTION_SEARCH = "search"
ACTION_COPY = "copy"
ACTION_SAVE = "save"


class SelectionOverlay(QWidget):
    """Full-screen selection surface.

    Emits exactly one of :attr:`selected`, :attr:`copy_requested`,
    :attr:`save_requested` or :attr:`cancelled`.  The first three carry the crop
    box in *physical* pixels of the screenshot plus the lasso outline in
    screen-logical pixels (empty for a rectangle selection).
    """

    selected = pyqtSignal(QRect, QPolygon)
    copy_requested = pyqtSignal(QRect, QPolygon)
    save_requested = pyqtSignal(QRect, QPolygon)
    cancelled = pyqtSignal()

    def __init__(
        self,
        pixmap: QPixmap,
        metrics: ScreenMetrics,
        screen: QScreen,
        dim_percent: int = 40,
        mode: str = MODE_LASSO,
        mask_outside: bool = False,
        confirm: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._target_screen = screen
        self._mode = mode if mode in (MODE_LASSO, MODE_RECTANGLE) else MODE_LASSO
        #: Whether the upload will keep only the inside of the loop.  It decides
        #: what the overlay un-dims, so that the bright area is always exactly
        #: what Google is going to receive.
        self._mask_outside = mask_outside
        self._drag_mode = self._mode
        #: Stop after the drag and let the user check and adjust the selection.
        self._confirm = confirm
        self._finished = False
        self._fullscreen_attempts = 0

        #: Where this window sits inside its screen, in logical pixels.  It is
        #: (0, 0) for a proper full-screen overlay; when KWin leaves the window
        #: in the work area the KWin script reports the real offset and
        #: everything — the screenshot, the selection, the crop — shifts by it,
        #: so what is drawn still lines up with the actual screen.
        self._offset = QPoint(0, 0)

        self._dragging = False
        #: False until the first press: without it the selection would be drawn
        #: from the widget origin to the pointer before anything was clicked.
        self._has_selection = False
        self._origin = QPoint()
        self._current = QPoint()
        self._points: list[QPoint] = []

        #: Confirmation state.  ``_box`` is in *screen* coordinates, like
        #: everything the painter draws, and becomes the selection once the drag
        #: is over; ``_box_edited`` records that it no longer matches the lasso,
        #: so the outline is dropped rather than quietly sent as a wrong mask.
        self._confirming = False
        self._box = QRect()
        self._box_edited = False
        self._grab: str | None = None
        self._grab_origin = QPoint()
        self._grab_box = QRect()

        self.setWindowTitle(OVERLAY_WINDOW_TITLE)
        self.setObjectName("CircleToSearchOverlay")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # The screenshot is kept at native resolution; the device pixel ratio
        # makes Qt blit it 1:1 instead of rescaling it on every repaint.  The
        # dimmed copy is rendered while both pixmaps still have a ratio of 1, so
        # the blit inside _make_dimmed is a plain pixel-for-pixel copy.
        pixmap.setDevicePixelRatio(1.0)
        self._dimmed = self._make_dimmed(pixmap, dim_percent)
        self._sharp = pixmap
        self._sharp.setDevicePixelRatio(metrics.scale)
        self._dimmed.setDevicePixelRatio(metrics.scale)

        self._accent = self._accent_colour()

        # Wayland likes to send a spurious deactivation right after mapping the
        # surface; ignore "focus lost" for a moment so the overlay does not
        # close itself immediately.
        self._accept_deactivation = False
        QTimer.singleShot(600, self._enable_deactivation)

    # ----------------------------------------------------------------- setup

    @staticmethod
    def _make_dimmed(pixmap: QPixmap, dim_percent: int) -> QPixmap:
        """Pre-render the darkened copy once instead of blending every frame."""
        dimmed = QPixmap(pixmap.size())
        dimmed.setDevicePixelRatio(1.0)
        painter = QPainter(dimmed)
        painter.drawPixmap(0, 0, pixmap)
        alpha = max(0, min(90, dim_percent)) * 255 // 100
        painter.fillRect(dimmed.rect(), QColor(0, 0, 0, alpha))
        painter.end()
        return dimmed

    @staticmethod
    def _accent_colour() -> QColor:
        """Plasma's accent colour, as Qt reports it through the palette."""
        colour = QApplication.palette().highlight().color()
        if not colour.isValid():
            return QColor(61, 174, 233)  # Breeze blue
        return colour

    def _enable_deactivation(self) -> None:
        self._accept_deactivation = True

    def covers_screen(self) -> bool:
        """True when the window really did get the whole output."""
        return self.size() == self._target_screen.geometry().size()

    def set_window_offset(self, x: int, y: int) -> None:
        """Told by the KWin script where the window really is."""
        offset = QPoint(x, y)
        if not offset.isNull() and self.covers_screen():
            # The report raced with the window becoming full screen.  A window
            # the size of the output is at its corner by definition, so trust
            # that over a message that was true a moment ago — a stale offset
            # would shift the drawing the other way and break a window that is
            # now perfectly fine.
            log.debug("ignoring offset %d,%d: the overlay already covers the screen", x, y)
            offset = QPoint(0, 0)
        if offset == self._offset:
            return
        self._offset = offset
        if not offset.isNull():
            log.warning(
                "the overlay is at +%d+%d inside its screen instead of the corner; "
                "compensating so the screenshot is not drawn shifted",
                x,
                y,
            )
        self.update()

    def show_on_screen(self) -> None:
        """Map the overlay full-screen on the target output.

        The order matters more than it looks.  Creating the platform window
        first and *then* moving it to another QScreen makes QtWayland tear the
        surface down and build a new one, and the pending full-screen state does
        not always survive that — the window comes up as an ordinary one inside
        the work area, below the panel.  So: pick the screen while the window is
        still virtual, ask for the full-screen state, and only then show it.
        """
        geometry = self._target_screen.geometry()

        # Qt 6.3+; on anything older the window simply opens on the screen Qt
        # picks, which is right in the single-monitor case.
        if hasattr(self, "setScreen"):
            self.setScreen(self._target_screen)
        self.setGeometry(geometry)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.show()

        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        log.debug(
            "overlay mapped on %s at %s in %s mode",
            self._target_screen.name(),
            geometry,
            self._mode,
        )
        self._fullscreen_attempts = 0
        QTimer.singleShot(250, self._ensure_fullscreen)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Drop the offset the moment the window does cover the screen.

        The KWin script reports the geometry it sees, but the window can grow
        into full screen a moment later — through the retry below, or because
        the compositor got round to it.  Keeping the old offset then would shift
        everything in the opposite direction, so the size decides.
        """
        super().resizeEvent(event)
        if self.covers_screen() and not self._offset.isNull():
            log.info("the overlay now covers %s, dropping the offset", self._target_screen.name())
            self._offset = QPoint(0, 0)
            self.update()

    def _ensure_fullscreen(self) -> None:
        """Re-ask for full screen if the compositor gave us less than the output.

        Some sequences leave the window sized to the work area; asking again
        after the first configure round-trip is usually enough, and it costs
        nothing when the window is already right.
        """
        if self._finished or not self.isVisible():
            return
        expected = self._target_screen.geometry().size()
        if self.size() == expected:
            if self._fullscreen_attempts:
                log.info(
                    "the overlay is full screen after %d retr%s",
                    self._fullscreen_attempts,
                    "y" if self._fullscreen_attempts == 1 else "ies",
                )
            return

        self._fullscreen_attempts += 1
        if self._fullscreen_attempts > 3:
            log.warning(
                "the overlay is still %dx%d instead of %dx%d; the drawing is being "
                "offset to compensate, but part of the screen cannot be selected",
                self.size().width(),
                self.size().height(),
                expected.width(),
                expected.height(),
            )
            return

        log.info(
            "the overlay came up %dx%d instead of %dx%d, asking for full screen again (%d)",
            self.size().width(),
            self.size().height(),
            expected.width(),
            expected.height(),
            self._fullscreen_attempts,
        )
        # A plain repeat of the state request is ignored when Qt thinks the
        # state is already set, so drop it and set it again.
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.setGeometry(self._target_screen.geometry())
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        QTimer.singleShot(250, self._ensure_fullscreen)

    # ---------------------------------------------------------------- events

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        # Everything is drawn in *screen* coordinates; the translation makes the
        # window's own corner line up with the screen's, even when the window
        # was not given the whole output.
        painter.translate(-self._offset)
        painter.drawPixmap(0, 0, self._dimmed)

        if not self._has_selection:
            self._draw_hint(painter)
            painter.end()
            return

        selection = self._selection_rect()
        if selection.width() < 1 or selection.height() < 1:
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Cut the "hole" showing exactly what will be uploaded: the bounding box
        # (the loop only marks out the edges, like Circle to Search on Android)
        # or the loop itself when the outside is going to be whitened.
        # Clipping is used instead of a source rectangle so that the lasso and
        # the rectangle take the same code path.
        painter.save()
        painter.setClipPath(self._reveal_path())
        painter.drawPixmap(0, 0, self._sharp)
        painter.restore()

        pen = QPen(self._accent)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._drag_mode == MODE_LASSO and not self._mask_outside and not self._box_edited:
            # Show both: the stroke follows the hand, the dashed box is the crop.
            outline = QColor(self._accent)
            outline.setAlpha(150)
            dashed = QPen(outline)
            dashed.setWidth(1)
            dashed.setCosmetic(True)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.drawRect(selection.adjusted(0, 0, -1, -1))

        painter.setPen(pen)
        painter.drawPath(self._selection_path())

        if self._confirming:
            self._draw_handles(painter)
            self._draw_confirm_hint(painter)
        self._draw_size_label(painter, selection)
        painter.end()

    def _reveal_path(self) -> QPainterPath:
        """The area to un-dim: what the upload will actually contain."""
        if self._drag_mode == MODE_LASSO and self._mask_outside and not self._box_edited:
            return self._selection_path()
        rect = self._selection_rect()
        path = QPainterPath()
        path.addRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
        return path

    def _selection_path(self) -> QPainterPath:
        """The selection outline: a polygon for the lasso, a rect otherwise."""
        path = QPainterPath()
        if self._drag_mode == MODE_LASSO and len(self._points) >= 3 and not self._box_edited:
            # QPainterPath only takes floating point coordinates in PyQt6, and
            # the painter draws in screen coordinates.
            points = [point + self._offset for point in self._points]
            path.moveTo(float(points[0].x()), float(points[0].y()))
            for point in points[1:]:
                path.lineTo(float(point.x()), float(point.y()))
            path.closeSubpath()
            return path
        rect = self._selection_rect()
        path.addRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
        return path

    def _draw_hint(self, painter: QPainter) -> None:
        text = tr("overlay.hint.lasso" if self._mode == MODE_LASSO else "overlay.hint.rect")
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 4 * _LABEL_PADDING
        height = metrics.height() + 2 * _LABEL_PADDING
        box = QRect(
            self._offset.x() + (self.width() - width) // 2,
            self._offset.y() + max(24, self.height() // 12),
            width,
            height,
        )
        self._draw_box(painter, box, text)

    def _draw_handles(self, painter: QPainter) -> None:
        """The grab squares on the edges and corners of the confirmed box."""
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.setBrush(self._accent)
        for rect in self._handle_rects().values():
            painter.drawRect(rect.adjusted(2, 2, -2, -2))

    def _draw_confirm_hint(self, painter: QPainter) -> None:
        """The one line that says the selection has not been sent yet."""
        text = tr("overlay.confirm")
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 4 * _LABEL_PADDING
        height = metrics.height() + 2 * _LABEL_PADDING

        # Under the selection when there is room, above it otherwise, so the
        # thing being described is never covered by its own caption.
        x = self._offset.x() + (self.width() - width) // 2
        y = self._box.bottom() + _LABEL_MARGIN * 2
        if y + height > self._offset.y() + self.height() - _LABEL_MARGIN:
            y = self._box.top() - height - _LABEL_MARGIN * 2
        y = max(self._offset.y() + _LABEL_MARGIN, y)
        self._draw_box(painter, QRect(x, y, width, height), text)

    def _draw_size_label(self, painter: QPainter, selection: QRect) -> None:
        physical = logical_rect_to_physical(selection, self._metrics)
        text = f"{physical.width()} × {physical.height()} px"
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 2 * _LABEL_PADDING
        height = metrics.height() + _LABEL_PADDING

        # Below/right of the cursor, flipped when there is no room.
        cursor = self._current + self._offset
        x = cursor.x() + _LABEL_MARGIN * 2
        y = cursor.y() + _LABEL_MARGIN * 2
        if x + width > self._offset.x() + self.width() - _LABEL_MARGIN:
            x = cursor.x() - width - _LABEL_MARGIN * 2
        if y + height > self._offset.y() + self.height() - _LABEL_MARGIN:
            y = cursor.y() - height - _LABEL_MARGIN * 2
        x = max(_LABEL_MARGIN, x)
        y = max(_LABEL_MARGIN, y)

        self._draw_box(painter, QRect(x, y, width, height), text)

    def _draw_box(self, painter: QPainter, box: QRect, text: str) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._confirming:
            where = event.position().toPoint() + self._offset
            grab = self._handle_at(where)
            if grab is not None:
                self._grab = grab
                self._grab_origin = where
                self._grab_box = QRect(self._box)
                return
            # A press outside the selection means "no, that one" — start again.
            self._confirming = False
            self._reset_selection()

        # Shift swaps the mode for this one selection.
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shifted:
            self._drag_mode = MODE_RECTANGLE if self._mode == MODE_LASSO else MODE_LASSO
        else:
            self._drag_mode = self._mode

        position = event.position().toPoint()
        self._dragging = True
        self._has_selection = True
        self._origin = position
        self._current = position
        self._points = [position]
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position().toPoint()
        self._current = position

        if self._confirming:
            where = position + self._offset
            if self._grab is not None:
                self._resize_to(where)
                return
            hovered = self._handle_at(where)
            self.setCursor(_CURSORS.get(hovered or "", Qt.CursorShape.CrossCursor))
            return

        if not self._dragging:
            return
        if self._drag_mode == MODE_LASSO:
            last = self._points[-1] if self._points else None
            if (
                last is None
                or abs(position.x() - last.x()) >= _LASSO_MIN_STEP
                or abs(position.y() - last.y()) >= _LASSO_MIN_STEP
            ):
                self._points.append(position)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._grab is not None:
            self._grab = None
            self.update()
            return
        if not self._dragging:
            return
        self._dragging = False
        self._current = event.position().toPoint()
        if self._drag_mode == MODE_LASSO:
            self._points.append(self._current)
        selection = self._selection_rect()

        if selection.width() < MIN_SELECTION or selection.height() < MIN_SELECTION:
            # A stray click, not a selection: keep the overlay open so the user
            # can try again instead of silently doing nothing.
            log.debug("ignoring %dx%d selection", selection.width(), selection.height())
            self._reset_selection()
            return

        physical = logical_rect_to_physical(selection, self._metrics)
        if physical.width() < 1 or physical.height() < 1:
            self._cancel()
            return

        polygon = self.selection_polygon()
        log.info(
            "%s selection %dx%d logical → %dx%d physical at %d,%d (%d outline points)",
            self._drag_mode,
            selection.width(),
            selection.height(),
            physical.width(),
            physical.height(),
            physical.x(),
            physical.y(),
            polygon.count(),
        )
        if not self._confirm:
            self._finish(lambda: self.selected.emit(physical, polygon))
            return

        # Nothing is sent yet: hold the selection, let it be adjusted, and wait
        # for the key that says what to do with it.
        self._confirming = True
        self._box = selection
        self._box_edited = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self._cancel()
            return

        if not self._confirming:
            super().keyPressEvent(event)
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._commit(ACTION_SEARCH)
            return
        if key == Qt.Key.Key_C:
            self._commit(ACTION_COPY)
            return
        if key == Qt.Key.Key_S:
            self._commit(ACTION_SAVE)
            return

        arrows = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if key in arrows:
            fast = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            step = _NUDGE_FAST if fast else _NUDGE
            dx, dy = arrows[key]
            # Shift grows or shrinks the far edge; on its own the whole box moves.
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._apply_box(
                    self._box.adjusted(0, 0, dx * step, dy * step), edited=True
                )
            else:
                self._apply_box(self._box.translated(dx * step, dy * step), edited=True)
            return

        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type() == QEvent.Type.ActivationChange
            and self._accept_deactivation
            and not self.isActiveWindow()
            and not self._dragging
            and self._grab is None
            and not self._finished
        ):
            log.debug("overlay lost focus, cancelling")
            self._cancel()
            return
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._finished:
            self._finished = True
            self.releaseKeyboard()
            self.cancelled.emit()
        event.accept()

    # --------------------------------------------------------------- helpers

    def selection_polygon(self) -> QPolygon:
        """The lasso outline in screen-logical pixels (empty for a rectangle)."""
        if self._drag_mode != MODE_LASSO or len(self._points) < 3:
            return QPolygon()
        if self._box_edited:
            # The box was adjusted by hand, so the loop no longer describes it.
            # Returning it anyway would mask the crop against the wrong shape.
            return QPolygon()
        return QPolygon(self._points).translated(self._offset)

    def _selection_rect(self) -> QRect:
        """Selection bounding box in widget-logical pixels.

        Returns an empty rectangle until the user actually presses the button —
        otherwise plain pointer movement would paint a selection anchored at the
        widget origin.

        The rectangle is built from its two corners by hand rather than with
        ``QRect(topLeft, bottomRight)``: that constructor is inclusive on both
        ends, so dragging from x=100 to x=300 would come out 201 px wide and the
        size label would disagree with the crop the user asked for.
        """
        if not self._has_selection:
            return QRect()
        if self._confirming:
            return self._box

        # Points come from mouse events, i.e. widget coordinates; the crop and
        # the drawing both work in screen coordinates.
        if self._drag_mode == MODE_LASSO:
            if len(self._points) < 2:
                return QRect()
            xs = [point.x() for point in self._points]
            ys = [point.y() for point in self._points]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            rect = QRect(left, top, right - left, bottom - top)
            return rect.intersected(self.rect()).translated(self._offset)

        left = min(self._origin.x(), self._current.x())
        top = min(self._origin.y(), self._current.y())
        width = abs(self._current.x() - self._origin.x())
        height = abs(self._current.y() - self._origin.y())
        rect = QRect(left, top, width, height)
        return rect.intersected(self.rect()).translated(self._offset)

    # --------------------------------------------------- adjusting the box

    def _handle_rects(self) -> dict[str, QRect]:
        """The eight grab areas, in screen coordinates."""
        box = self._box
        half = _HANDLE // 2
        return {
            name: QRect(
                int(box.x() + box.width() * fx) - half,
                int(box.y() + box.height() * fy) - half,
                _HANDLE,
                _HANDLE,
            )
            for name, fx, fy in _HANDLES
        }

    def _handle_at(self, position: QPoint) -> str | None:
        """Which handle is under the pointer — ``"move"`` inside the box."""
        for name, rect in self._handle_rects().items():
            if rect.contains(position):
                return name
        if self._box.contains(position):
            return "move"
        return None

    def _resize_to(self, position: QPoint) -> None:
        delta = position - self._grab_origin
        box = QRect(self._grab_box)
        grab = self._grab or ""
        if grab == "move":
            box.translate(delta)
        else:
            if "n" in grab:
                box.setTop(box.top() + delta.y())
            if "s" in grab:
                box.setBottom(box.bottom() + delta.y())
            if "w" in grab:
                box.setLeft(box.left() + delta.x())
            if "e" in grab:
                box.setRight(box.right() + delta.x())
        self._apply_box(box.normalized(), edited=True)

    def _apply_box(self, box: QRect, *, edited: bool) -> None:
        """Clamp a proposed box to the screen and keep it usable."""
        bounds = self.rect().translated(self._offset)
        box = box.normalized()
        if box.width() < MIN_SELECTION:
            box.setWidth(MIN_SELECTION)
        if box.height() < MIN_SELECTION:
            box.setHeight(MIN_SELECTION)
        # Moving must not push the box off screen, and resizing must not pull an
        # edge past the far side of it.
        if box.right() > bounds.right():
            box.moveRight(bounds.right())
        if box.bottom() > bounds.bottom():
            box.moveBottom(bounds.bottom())
        if box.left() < bounds.left():
            box.moveLeft(bounds.left())
        if box.top() < bounds.top():
            box.moveTop(bounds.top())
        box = box.intersected(bounds)
        if box.width() < MIN_SELECTION or box.height() < MIN_SELECTION:
            return
        if box == self._box:
            return
        self._box = box
        if edited:
            self._box_edited = True
        self.update()

    def _commit(self, action: str) -> None:
        """Send the confirmed selection off as whatever the user asked for."""
        physical = logical_rect_to_physical(self._box, self._metrics)
        if physical.width() < 1 or physical.height() < 1:
            self._cancel()
            return
        polygon = self.selection_polygon()
        log.info(
            "%s %dx%d physical at %d,%d",
            action,
            physical.width(),
            physical.height(),
            physical.x(),
            physical.y(),
        )
        signals = {
            ACTION_COPY: self.copy_requested,
            ACTION_SAVE: self.save_requested,
        }
        signal = signals.get(action, self.selected)
        self._finish(lambda: signal.emit(physical, polygon))

    def _reset_selection(self) -> None:
        self._has_selection = False
        self._confirming = False
        self._box = QRect()
        self._box_edited = False
        self._grab = None
        self._origin = QPoint()
        self._points = []
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def _cancel(self) -> None:
        self._finish(self.cancelled.emit)

    def _finish(self, emit: Callable[[], None]) -> None:
        if self._finished:
            return
        self._finished = True
        self.releaseKeyboard()
        self.hide()
        emit()
        self.close()
