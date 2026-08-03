"""The frozen-screen selection overlay.

A frameless, always-on-top, full-screen widget that shows the screenshot that
was just taken, dims it, and lets the user drag a rectangle.  The un-dimmed
screenshot shows through inside the rectangle, the same way Spectacle's region
mode looks.

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

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
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

_LABEL_MARGIN = 8
_LABEL_PADDING = 6


class SelectionOverlay(QWidget):
    """Full-screen selection surface.

    Emits :attr:`selected` with the crop box in *physical* pixels of the
    screenshot, or :attr:`cancelled`.  Exactly one of the two is emitted.
    """

    selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(
        self,
        pixmap: QPixmap,
        metrics: ScreenMetrics,
        screen: QScreen,
        dim_percent: int = 40,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._target_screen = screen
        self._finished = False
        self._dragging = False
        self._origin = QPoint()
        self._current = QPoint()
        self._show_hint = True

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

    def show_on_screen(self) -> None:
        """Map the overlay full-screen on the target output."""
        geometry = self._target_screen.geometry()
        self.create()
        handle = self.windowHandle()
        if handle is not None:
            # On Wayland a client cannot position itself; telling Qt which
            # QScreen the window belongs to is what makes KWin full-screen it on
            # the right output.
            handle.setScreen(self._target_screen)
        self.setGeometry(geometry)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        log.debug("overlay mapped on %s at %s", self._target_screen.name(), geometry)

    # ---------------------------------------------------------------- events

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._dimmed)

        selection = self._selection_rect()
        if selection.isNull() or selection.width() < 1 or selection.height() < 1:
            if self._show_hint:
                self._draw_hint(painter)
            painter.end()
            return

        # Cut the "hole": redraw the untouched screenshot inside the selection.
        scale = self._metrics.scale
        source = QRectF(
            selection.x() * scale,
            selection.y() * scale,
            selection.width() * scale,
            selection.height() * scale,
        )
        painter.drawPixmap(QRectF(selection), self._sharp, source)

        pen = QPen(self._accent)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(selection.adjusted(0, 0, -1, -1))

        self._draw_size_label(painter, selection)
        painter.end()

    def _draw_hint(self, painter: QPainter) -> None:
        text = tr("overlay.hint")
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 2 * _LABEL_PADDING * 2
        height = metrics.height() + 2 * _LABEL_PADDING
        box = QRect(
            (self.width() - width) // 2,
            max(24, self.height() // 12),
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
        x = self._current.x() + _LABEL_MARGIN * 2
        y = self._current.y() + _LABEL_MARGIN * 2
        if x + width > self.width() - _LABEL_MARGIN:
            x = self._current.x() - width - _LABEL_MARGIN * 2
        if y + height > self.height() - _LABEL_MARGIN:
            y = self._current.y() - height - _LABEL_MARGIN * 2
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
        self._dragging = True
        self._show_hint = False
        self._origin = event.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._current = event.position().toPoint()
        if self._dragging or self._show_hint:
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        self._current = event.position().toPoint()
        selection = self._selection_rect()

        if selection.width() < MIN_SELECTION or selection.height() < MIN_SELECTION:
            # A stray click, not a selection: keep the overlay open so the user
            # can try again instead of silently doing nothing.
            log.debug("ignoring %dx%d selection", selection.width(), selection.height())
            self._origin = QPoint()
            self._current = QPoint()
            self._show_hint = True
            self.update()
            return

        physical = logical_rect_to_physical(selection, self._metrics)
        if physical.width() < 1 or physical.height() < 1:
            self._cancel()
            return

        log.info(
            "selection %dx%d logical → %dx%d physical at %d,%d",
            selection.width(),
            selection.height(),
            physical.width(),
            physical.height(),
            physical.x(),
            physical.y(),
        )
        self._finish(lambda: self.selected.emit(physical))

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

    def _selection_rect(self) -> QRect:
        """Drag rectangle in widget-logical pixels.

        Built from the two corners by hand rather than with
        ``QRect(topLeft, bottomRight)``: that constructor is inclusive on both
        ends, so dragging from x=100 to x=300 would come out 201 px wide and the
        size label would disagree with the crop the user asked for.
        """
        if self._origin.isNull() and self._current.isNull():
            return QRect()
        left = min(self._origin.x(), self._current.x())
        top = min(self._origin.y(), self._current.y())
        width = abs(self._current.x() - self._origin.x())
        height = abs(self._current.y() - self._origin.y())
        return QRect(left, top, width, height).intersected(self.rect())

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
