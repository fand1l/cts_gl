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


class SelectionOverlay(QWidget):
    """Full-screen selection surface.

    Emits :attr:`selected` with the crop box in *physical* pixels of the
    screenshot plus the lasso outline in widget-logical pixels (empty for a
    rectangle selection), or :attr:`cancelled`.  Exactly one of the two is
    emitted.
    """

    selected = pyqtSignal(QRect, QPolygon)
    cancelled = pyqtSignal()

    def __init__(
        self,
        pixmap: QPixmap,
        metrics: ScreenMetrics,
        screen: QScreen,
        dim_percent: int = 40,
        mode: str = MODE_LASSO,
        mask_outside: bool = False,
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

    def set_window_offset(self, x: int, y: int) -> None:
        """Told by the KWin script where the window really is."""
        offset = QPoint(x, y)
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
                log.info("the overlay is full screen after %d retries", self._fullscreen_attempts)
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

        if self._drag_mode == MODE_LASSO and not self._mask_outside:
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

        self._draw_size_label(painter, selection)
        painter.end()

    def _reveal_path(self) -> QPainterPath:
        """The area to un-dim: what the upload will actually contain."""
        if self._drag_mode == MODE_LASSO and self._mask_outside:
            return self._selection_path()
        rect = self._selection_rect()
        path = QPainterPath()
        path.addRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
        return path

    def _selection_path(self) -> QPainterPath:
        """The selection outline: a polygon for the lasso, a rect otherwise."""
        path = QPainterPath()
        if self._drag_mode == MODE_LASSO and len(self._points) >= 3:
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
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
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
        self._finish(lambda: self.selected.emit(physical, polygon))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self._cancel()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type() == QEvent.Type.ActivationChange
            and self._accept_deactivation
            and not self.isActiveWindow()
            and not self._dragging
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

    def _reset_selection(self) -> None:
        self._has_selection = False
        self._origin = QPoint()
        self._points = []
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
