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

There is also a **text layer**, when text recognition is switched on: the words
found on the frozen screen become selectable, and a drag that starts on one of
them takes text rather than an area — the way a browser tells text and pictures
apart, so nothing has to be switched over first.  Recognition runs while the
overlay is already up, so the words arrive a moment later; until they do, a
small badge says the screen is being read.

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
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
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
from .hidpi import ScreenMetrics, logical_rect_to_physical, physical_rect_to_logical
from .i18n import tr
from .logging_setup import get_logger
from .ocr import Word, words_to_text

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

#: How often the "reading the screen…" badge ticks, and how many dots it has.
_SCAN_TICK_MS = 130
_SCAN_DOTS = 4

#: Grabbing a word needs a lot more slack than the glyphs occupy: text is small,
#: pointers are not, and being made to hit a five-pixel-tall word exactly is the
#: difference between "I can take this text" and "it keeps dragging a box".
_WORD_SLACK = 4

#: A press anywhere on a line of text — including the gaps between its words —
#: is a press on that line.  This is how every text field on the machine
#: behaves, and the alternative is aiming at individual words.
_LINE_SLACK = 5

#: What the user asked to do with the selection.
ACTION_SEARCH = "search"
ACTION_COPY = "copy"
ACTION_SAVE = "save"
#: Only the tray's recent list uses this one now: on the overlay the text is
#: taken by dragging across it, not by asking for a whole region to be read.
ACTION_TEXT = "text"


@dataclass(frozen=True)
class PlacedWord:
    """A recognised word, moved into the coordinates the overlay draws in."""

    text: str
    rect: QRect
    line: tuple[int, int, int]


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
    #: A run of recognised words the user dragged across.
    text_selected = pyqtSignal(str)
    cancelled = pyqtSignal()

    #: Group mode only (see multiscreen.py): the selection in *global logical*
    #: pixels, which is the only space several overlays can agree about.
    committed = pyqtSignal(QRect, str)
    preview_changed = pyqtSignal(QRect)

    def __init__(
        self,
        pixmap: QPixmap,
        metrics: ScreenMetrics,
        screen: QScreen,
        dim_percent: int = 40,
        mode: str = MODE_LASSO,
        mask_outside: bool = False,
        confirm: bool = True,
        group_bounds: QRect | None = None,
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
        #: The point the drag started from, in widget coordinates.  Not to be
        #: confused with _origin below, which is where this screen starts.
        self._anchor = QPoint()
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

        #: Group mode.  ``_group_bounds`` is the whole virtual desktop in global
        #: logical pixels; a selection may reach anywhere inside it instead of
        #: being clamped to this one screen.  ``_preview`` is the part of another
        #: overlay's selection that falls on this screen, so all of them draw the
        #: same rectangle.
        self._group_bounds = QRect(group_bounds) if group_bounds is not None else QRect()
        self._origin = metrics.logical_geometry.topLeft()
        self._preview = QRect()
        self._deactivation_guard: Callable[[], bool] | None = None

        #: The text layer.  Recognition runs while the overlay is already up, so
        #: these arrive late; until then `_scanning` drives a small badge saying
        #: so, because several seconds of nothing looks like a hang.
        self._words: list[PlacedWord] = []
        #: One rectangle per recognised line.  Both the lit-up backdrop and the
        #: "am I over text?" test work on these: a line is what a person aims
        #: at, and there are ten times fewer of them than there are words.
        self._lines: list[tuple[tuple[int, int, int], QRect]] = []
        #: Everything *except* the text, cached because it is filled on every
        #: repaint and a drag repaints constantly.
        self._text_dim_path: QPainterPath | None = None
        self._scanning = False
        self._scan_phase = 0
        self._scan_timer: QTimer | None = None
        #: Indices into `_words`; the range between them is what is selected.
        self._text_anchor: int | None = None
        self._text_focus: int | None = None
        self._selecting_text = False

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

        # One copy of the screenshot, not two.  A pre-dimmed second pixmap used
        # to be kept so that dimming cost nothing per frame, but at 3840x2160
        # that is another ~33 MB resident for a window that is on screen for a
        # couple of seconds — and with an overlay per monitor it multiplies.
        # Painting the dim over the parts that are not selected is a fill and a
        # path subtraction per repaint, which is nothing.
        self._sharp = pixmap
        self._sharp.setDevicePixelRatio(metrics.scale)
        self._dim = QColor(0, 0, 0, max(0, min(90, dim_percent)) * 255 // 100)

        self._accent = self._accent_colour()

        # Wayland likes to send a spurious deactivation right after mapping the
        # surface; ignore "focus lost" for a moment so the overlay does not
        # close itself immediately.
        self._accept_deactivation = False
        QTimer.singleShot(600, self._enable_deactivation)

    # ----------------------------------------------------------------- setup

    @staticmethod
    def _accent_colour() -> QColor:
        """Plasma's accent colour, as Qt reports it through the palette."""
        colour = QApplication.palette().highlight().color()
        if not colour.isValid():
            return QColor(61, 174, 233)  # Breeze blue
        return colour

    def _enable_deactivation(self) -> None:
        self._accept_deactivation = True

    # ------------------------------------------------------------ text layer

    def set_scanning(self, scanning: bool) -> None:
        """Show (or stop showing) that the screen is being read."""
        if scanning == self._scanning:
            return
        self._scanning = scanning
        if scanning:
            timer = QTimer(self)
            timer.setInterval(_SCAN_TICK_MS)
            timer.timeout.connect(self._tick_scan)
            timer.start()
            self._scan_timer = timer
        elif self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
        self.update()

    def _tick_scan(self) -> None:
        self._scan_phase = (self._scan_phase + 1) % _SCAN_DOTS
        # Only the badge, not the whole 4K screenshot: repainting all of it ten
        # times a second is exactly what made the old cursor glow unusable.
        self.update(self._badge_rect().translated(-self._offset))

    def set_words(self, words: list[Word]) -> None:
        """Hand over what was recognised, in physical pixels of the screenshot."""
        placed: list[PlacedWord] = []
        for word in words:
            rect = physical_rect_to_logical(
                QRect(word.left, word.top, word.width, word.height), self._metrics
            )
            if rect.width() < 1 or rect.height() < 1:
                continue
            placed.append(PlacedWord(text=word.text, rect=rect, line=word.line))
        self._words = placed
        self._rebuild_lines()
        self.set_scanning(False)
        log.info("text layer: %d word(s) on %d line(s)", len(placed), len(self._lines))
        self.update()

    def _rebuild_lines(self) -> None:
        """Group the words into the lines they were read from."""
        lines: list[tuple[tuple[int, int, int], QRect]] = []
        for word in self._words:
            if lines and lines[-1][0] == word.line:
                lines[-1] = (word.line, lines[-1][1].united(word.rect))
            else:
                lines.append((word.line, QRect(word.rect)))
        self._lines = lines
        self._text_dim_path = None

    def _text_backdrop(self) -> QPainterPath:
        """Everything the dimming still covers once the text is lit up."""
        cached = self._text_dim_path
        if cached is not None:
            return cached
        lit = QPainterPath()
        for _key, rect in self._lines:
            lit.addRoundedRect(QRectF(rect.adjusted(-3, -2, 3, 2)), 4, 4)
        path = QPainterPath()
        path.addRect(self._visible_area())
        path = path.subtracted(lit)
        self._text_dim_path = path
        return path

    @property
    def has_words(self) -> bool:
        return bool(self._words)

    def has_text_selection(self) -> bool:
        return self._text_anchor is not None and self._text_focus is not None

    def selected_words(self) -> list[PlacedWord]:
        if self._text_anchor is None or self._text_focus is None:
            return []
        first, last = sorted((self._text_anchor, self._text_focus))
        return self._words[first : last + 1]

    def selected_text(self) -> str:
        return words_to_text(
            [
                Word(
                    text=word.text,
                    left=word.rect.x(),
                    top=word.rect.y(),
                    width=word.rect.width(),
                    height=word.rect.height(),
                    confidence=100.0,
                    order=(*word.line, index),
                )
                for index, word in enumerate(self.selected_words())
            ]
        )

    def _word_at(self, position: QPoint) -> int | None:
        """Index of the word a press lands on, generously.

        A direct hit first, then anywhere on the *line* — including the spaces
        between its words, where a press plainly still means "this text".
        Without the second pass, taking text means aiming at glyphs a few pixels
        tall and missing turns into dragging a rectangle instead.
        """
        for index, word in enumerate(self._words):
            if word.rect.adjusted(-_WORD_SLACK, -_WORD_SLACK, _WORD_SLACK, _WORD_SLACK).contains(
                position
            ):
                return index

        for key, rect in self._lines:
            if not rect.adjusted(-_LINE_SLACK, -_LINE_SLACK, _LINE_SLACK, _LINE_SLACK).contains(
                position
            ):
                continue
            # The word on that line nearest the press, horizontally: on a line,
            # left and right is the only direction that means anything.
            best: int | None = None
            best_distance = 0
            for index, word in enumerate(self._words):
                if word.line != key:
                    continue
                distance = abs(word.rect.center().x() - position.x())
                if best is None or distance < best_distance:
                    best = index
                    best_distance = distance
            if best is not None:
                return best
        return None

    def _nearest_word(self, position: QPoint) -> int | None:
        """The word a drag has reached, even when it is between two of them.

        Without this, dragging through the gap between words or past the end of
        a line would keep dropping the selection back to where it started.
        """
        direct = self._word_at(position)
        if direct is not None:
            return direct
        best: int | None = None
        best_distance = 0
        for index, word in enumerate(self._words):
            centre = word.rect.center()
            # Vertical distance counts for more: the word on the line you are on
            # beats one that happens to be nearer in a straight line.
            distance = (centre.x() - position.x()) ** 2 + 4 * (centre.y() - position.y()) ** 2
            if best is None or distance < best_distance:
                best = index
                best_distance = distance
        return best

    def _clear_text_selection(self) -> None:
        if self._text_anchor is None and self._text_focus is None:
            return
        self._text_anchor = None
        self._text_focus = None
        self._selecting_text = False
        self.update()

    def select_all_text(self) -> None:
        if not self._words:
            return
        self._reset_selection()
        self._text_anchor = 0
        self._text_focus = len(self._words) - 1
        self.update()

    @property
    def in_group(self) -> bool:
        """True when this overlay is one of several covering all screens."""
        return not self._group_bounds.isNull()

    def set_deactivation_guard(self, guard: Callable[[], bool] | None) -> None:
        """Let the group veto "the overlay lost focus, cancel".

        Focus moving from one overlay to its neighbour is not the user walking
        away from the selection, and without this the group would close itself
        the first time the pointer crossed an edge.
        """
        self._deactivation_guard = guard

    def set_preview(self, rect: QRect) -> None:
        """Draw the part of another overlay's selection that lands here."""
        if self._has_selection:
            return
        local = rect.translated(-self._origin) if not rect.isNull() else QRect()
        if local == self._preview:
            return
        self._preview = local
        self.update()

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
        self._text_dim_path = None
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
        if not self.in_group:
            # One keyboard grab per group would be one too many: the overlays
            # would fight over it and only the last one mapped would ever see a
            # key press.
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
        self._text_dim_path = None
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
        painter.drawPixmap(0, 0, self._sharp)

        if self.has_text_selection():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_text_selection(painter)
            self._draw_badge(painter)
            painter.end()
            return

        if not self._has_selection and self._preview.isNull():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._dim_around_text(painter)
            self._draw_word_hints(painter)
            self._draw_hint(painter)
            self._draw_badge(painter)
            painter.end()
            return

        selection = self._selection_rect()
        if selection.width() < 1 or selection.height() < 1:
            self._dim_everything(painter)
            self._draw_badge(painter)
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Darken everything except what will actually be uploaded: the bounding
        # box (the loop only marks out the edges, like Circle to Search on
        # Android) or the loop itself when the outside is going to be whitened.
        # The subtraction gives the lasso and the rectangle one code path.
        if self._dim.alpha():
            outside = QPainterPath()
            outside.addRect(self._visible_area())
            painter.fillPath(outside.subtracted(self._reveal_path()), self._dim)

        pen = QPen(self._accent)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if (
            self._has_selection
            and self._drag_mode == MODE_LASSO
            and not self._mask_outside
            and not self._box_edited
        ):
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

        if self._confirming and self._has_selection:
            self._draw_handles(painter)
            self._draw_confirm_hint(painter)
        self._draw_size_label(painter, selection)
        self._draw_badge(painter)
        painter.end()

    def _visible_area(self) -> QRectF:
        """The part of the screen this window actually covers, in screen px."""
        area = self.rect().translated(self._offset)
        return QRectF(area)

    def _dim_everything(self, painter: QPainter) -> None:
        if self._dim.alpha():
            painter.fillRect(self._visible_area(), self._dim)

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
        if (
            self._has_selection
            and self._drag_mode == MODE_LASSO
            and len(self._points) >= 3
            and not self._box_edited
        ):
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

    # ------------------------------------------------------ the text layer

    def _dim_around_text(self, painter: QPainter) -> None:
        """Dim the screen, but leave the readable text lit.

        This is the whole affordance.  Everything else on the frozen screen goes
        dark and the sentences stay bright, which says "these are live" before
        the pointer has been anywhere near them — a faint tint under the words
        said it far too quietly to notice.
        """
        if not self._dim.alpha():
            return
        if not self._lines:
            self._dim_everything(painter)
            return
        painter.fillPath(self._text_backdrop(), self._dim)

    def _draw_word_hints(self, painter: QPainter) -> None:
        """Highlighter under the lines of text, and a rule beneath each.

        Drawn per *line* rather than per word: that is the shape a person reads
        and the shape they aim at, and there are ten times fewer of them.
        """
        if not self._lines:
            return

        wash = QColor(255, 255, 255, 26)
        rule = QColor(self._accent)
        rule.setAlpha(150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(wash)
        for _key, rect in self._lines:
            painter.drawRoundedRect(rect.adjusted(-3, -2, 3, 2), 4, 4)

        pen = QPen(rule)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for _key, rect in self._lines:
            baseline = rect.bottom() + 3
            painter.drawLine(rect.left() - 2, baseline, rect.right() + 2, baseline)

    def _draw_text_selection(self, painter: QPainter) -> None:
        """The selected run: undimmed, so it reads, with the accent over it."""
        selected = self.selected_words()
        if not selected:
            return

        area = QPainterPath()
        for word in selected:
            area.addRoundedRect(QRectF(word.rect.adjusted(-2, -1, 2, 1)), 3, 3)

        if self._dim.alpha():
            outside = QPainterPath()
            outside.addRect(self._visible_area())
            painter.fillPath(outside.subtracted(area), self._dim)

        tint = QColor(self._accent)
        tint.setAlpha(90)
        painter.fillPath(area, tint)

        pen = QPen(self._accent)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(area)

        self._draw_text_hint(painter, selected)

    def _draw_text_hint(self, painter: QPainter, selected: list[PlacedWord]) -> None:
        text = tr("overlay.text_hint", count=len(selected))
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 4 * _LABEL_PADDING
        height = metrics.height() + 2 * _LABEL_PADDING

        bounds = selected[0].rect
        for word in selected[1:]:
            bounds = bounds.united(word.rect)
        x = self._offset.x() + (self.width() - width) // 2
        y = bounds.bottom() + _LABEL_MARGIN * 2
        if y + height > self._offset.y() + self.height() - _LABEL_MARGIN:
            y = bounds.top() - height - _LABEL_MARGIN * 2
        y = max(self._offset.y() + _LABEL_MARGIN, y)
        self._draw_box(painter, QRect(x, y, width, height), text)

    def _badge_rect(self) -> QRect:
        """Where the "reading the screen" badge sits, in screen coordinates."""
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(tr("overlay.scanning")) + 8 * _LABEL_PADDING
        height = metrics.height() + 2 * _LABEL_PADDING
        return QRect(
            self._offset.x() + (self.width() - width) // 2,
            self._offset.y() + self.height() - height - _LABEL_MARGIN * 4,
            width,
            height,
        )

    def _draw_badge(self, painter: QPainter) -> None:
        """Say that the screen is being read, and keep saying it.

        Several seconds of nothing happening looks like a hang; a line that
        moves says "working" without asking for attention.
        """
        if not self._scanning:
            return
        dots = "." * (self._scan_phase + 1)
        painter.setFont(self.font())
        self._draw_box(painter, self._badge_rect(), tr("overlay.scanning") + dots)

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

        where = event.position().toPoint() + self._offset

        # Text first, everywhere.  The one thing that outranks it is a resize
        # handle, which is a few pixels at the edge of a box the user put there
        # on purpose; everything else — including the inside of that box, which
        # used to grab it and drag it — gives way to the words underneath.
        word = self._word_at(where)

        if self._confirming:
            grab = self._handle_at(where)
            if grab is not None and grab != "move":
                self._grab = grab
                self._grab_origin = where
                self._grab_box = QRect(self._box)
                return
            if word is None:
                if grab == "move":
                    self._grab = grab
                    self._grab_origin = where
                    self._grab_box = QRect(self._box)
                    return
                # A press on empty screen means "no, that one" — start again.
                self._confirming = False
                self._reset_selection()

        # A press on a word takes text instead of an area, the same way a
        # browser tells text and image apart: nothing has to be switched on.
        if word is not None:
            self._reset_selection()
            self._selecting_text = True
            self._text_anchor = word
            self._text_focus = word
            self.setCursor(Qt.CursorShape.IBeamCursor)
            self.update()
            return
        self._clear_text_selection()

        # Shift swaps the mode for this one selection.
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shifted:
            self._drag_mode = MODE_RECTANGLE if self._mode == MODE_LASSO else MODE_LASSO
        else:
            self._drag_mode = self._mode

        position = event.position().toPoint()
        self._dragging = True
        self._has_selection = True
        self._preview = QRect()
        self._anchor = position
        self._current = position
        self._points = [position]
        self._changed()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position().toPoint()
        self._current = position

        if self._selecting_text:
            focus = self._nearest_word(position + self._offset)
            if focus is not None and focus != self._text_focus:
                self._text_focus = focus
                self.update()
            return

        if self._confirming:
            where = position + self._offset
            if self._grab is not None:
                self._resize_to(where)
                return
            hovered = self._handle_at(where)
            self.setCursor(_CURSORS.get(hovered or "", Qt.CursorShape.CrossCursor))
            return

        if not self._dragging:
            # An I-beam is the whole hint that the text can be taken.
            if self._words:
                over_word = self._word_at(position + self._offset) is not None
                self.setCursor(
                    Qt.CursorShape.IBeamCursor if over_word else Qt.CursorShape.CrossCursor
                )
            return
        if self._drag_mode == MODE_LASSO:
            last = self._points[-1] if self._points else None
            if (
                last is None
                or abs(position.x() - last.x()) >= _LASSO_MIN_STEP
                or abs(position.y() - last.y()) >= _LASSO_MIN_STEP
            ):
                self._points.append(position)
        self._changed()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._selecting_text:
            self._selecting_text = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
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
            if self.in_group:
                global_box = selection.translated(self._origin)
                self._finish(lambda: self.committed.emit(global_box, ACTION_SEARCH))
                return
            self._finish(lambda: self.selected.emit(physical, polygon))
            return

        # Nothing is sent yet: hold the selection, let it be adjusted, and wait
        # for the key that says what to do with it.
        self._confirming = True
        self._box = selection
        self._box_edited = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._changed()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        word = self._word_at(event.position().toPoint() + self._offset)
        if word is None:
            return
        self._reset_selection()
        self._text_anchor = word
        self._text_focus = word
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            # One Esc drops the text selection, so a mis-drag does not throw the
            # whole capture away; the next one closes the overlay.
            if self.has_text_selection():
                self._clear_text_selection()
                return
            self._cancel()
            return

        if key == Qt.Key.Key_T and self._words:
            self.select_all_text()
            return

        if self.has_text_selection() and key in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_C,
            Qt.Key.Key_Space,
        ):
            text = self.selected_text()
            if text:
                log.info("copying %d character(s) of recognised text", len(text))
                self._finish(lambda: self.text_selected.emit(text))
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
            and not self._selecting_text
            and self._grab is None
            and not self._finished
        ):
            if self._deactivation_guard is not None:
                # Focus crossing from one overlay of a group to the next is not
                # the user walking away.  Ask again in a moment, once the other
                # window has had time to become the active one.
                QTimer.singleShot(200, self._cancel_if_really_deactivated)
                return
            log.debug("overlay lost focus, cancelling")
            self._cancel()
            return
        super().changeEvent(event)

    def _cancel_if_really_deactivated(self) -> None:
        if self._finished or self.isActiveWindow() or self._dragging:
            return
        guard = self._deactivation_guard
        if guard is not None and not guard():
            return
        log.debug("the whole overlay group lost focus, cancelling")
        self._cancel()

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

    def _changed(self) -> None:
        """Repaint, and in a group tell the other overlays what to draw."""
        self.update()
        if self.in_group:
            rect = self._selection_rect()
            self.preview_changed.emit(
                rect.translated(self._origin) if not rect.isEmpty() else QRect()
            )

    def _clamp_bounds(self) -> QRect:
        """Where a selection may reach, in this screen's coordinates.

        One screen on its own: the window.  In a group: the whole virtual
        desktop, so a drag can carry on past the edge onto the next monitor —
        Wayland keeps sending the motion to the surface the button went down on,
        with coordinates that simply run negative or past the far side.
        """
        if self.in_group:
            return self._group_bounds.translated(-self._origin)
        return self.rect().translated(self._offset)

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
            # A group member showing someone else's selection: only the part
            # that lands on this screen, so the seam falls exactly on the edge.
            if not self._preview.isNull():
                return self._preview.intersected(self.rect().translated(self._offset))
            return QRect()
        if self._confirming:
            return self._box

        # Points come from mouse events, i.e. widget coordinates; the crop and
        # the drawing both work in screen coordinates.
        bounds = self._clamp_bounds()
        if self._drag_mode == MODE_LASSO:
            if len(self._points) < 2:
                return QRect()
            xs = [point.x() for point in self._points]
            ys = [point.y() for point in self._points]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            rect = QRect(left, top, right - left, bottom - top)
            return rect.translated(self._offset).intersected(bounds)

        left = min(self._anchor.x(), self._current.x())
        top = min(self._anchor.y(), self._current.y())
        width = abs(self._current.x() - self._anchor.x())
        height = abs(self._current.y() - self._anchor.y())
        rect = QRect(left, top, width, height)
        return rect.translated(self._offset).intersected(bounds)

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
        bounds = self._clamp_bounds()
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
        self._changed()

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
        if self.in_group:
            # The app cannot use a per-screen crop box here: the selection may
            # cover parts of two screenshots at two different scales, so it is
            # handed over in global logical pixels and stitched afterwards.
            global_box = self._box.translated(self._origin)
            self._finish(lambda: self.committed.emit(global_box, action))
            return

        signals = {
            ACTION_COPY: self.copy_requested,
            ACTION_SAVE: self.save_requested,
        }
        signal = signals.get(action, self.selected)
        self._finish(lambda: signal.emit(physical, polygon))

    def _reset_selection(self) -> None:
        self._text_anchor = None
        self._text_focus = None
        self._selecting_text = False
        self._has_selection = False
        self._confirming = False
        self._box = QRect()
        self._box_edited = False
        self._grab = None
        self._anchor = QPoint()
        self._points = []
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._changed()

    def _cancel(self) -> None:
        self._finish(self.cancelled.emit)

    def _finish(self, emit: Callable[[], None]) -> None:
        if self._finished:
            return
        self._finished = True
        self.set_scanning(False)
        self.releaseKeyboard()
        self.hide()
        emit()
        self.close()
