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

from PyQt6.QtCore import (
    QElapsedTimer,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygon,
    QRegion,
    QResizeEvent,
    QScreen,
)
from PyQt6.QtWidgets import QApplication, QWidget

from . import OVERLAY_WINDOW_TITLE
from .hidpi import ScreenMetrics, logical_rect_to_physical, physical_rect_to_logical
from .i18n import tr
from .logging_setup import get_logger
from .ocr import Word, words_to_text
from .stroke import (
    GLOW_RADIUS,
    LINE_WIDTH,
    SHADOW_EXTRA,
    TICK_MS,
    Trail,
    glow_blob,
    glow_colour,
)
from .websearch import looks_like_url
from .windows import window_at

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

#: Side of the grip in the middle of a confirmed box that moves the whole thing.
#: Big enough to aim at, small enough to leave the box redrawable around it.
_GRIP = 34

#: How many points of the lasso stay "live" — re-stroked on every frame — before
#: the rest is baked into a layer.  See :meth:`SelectionOverlay._freeze_stroke`:
#: this is the number that decides whether a long scribble runs at 60 fps or at
#: 4, and it wants to be small.
_STROKE_CHUNK = 48

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

#: How often the badge ticks, and how many dots it has.
_SCAN_TICK_MS = 130
_SCAN_DOTS = 4

#: The things the badge can be saying.  Only one is ever true at a time, which
#: is why they share the one badge.
BADGE_NONE = ""
BADGE_SCANNING = "scanning"
BADGE_SENDING = "sending"
#: A text search: no upload, so saying "sending it to Google Lens" would be a
#: lie.  The wait is the browser's, and that is what this one says.
BADGE_OPENING = "opening"

#: How long the overlay may stay up saying "sending" before it takes itself
#: down.  A dead-man's switch and nothing else: whatever goes wrong between the
#: release and the browser, a frozen screen that never lifts is worse.
SENDING_LIMIT_MS = 5000

#: The loupe: how wide it is on screen, how much it magnifies, and how big the
#: gap in the middle of its crosshair is.  Four times is enough to separate two
#: adjacent pixels without turning the surroundings into abstract art.
_LOUPE_SIZE = 132
_LOUPE_ZOOM = 4
_LOUPE_GAP = 5

#: Grabbing a word needs a lot more slack than the glyphs occupy: text is small,
#: pointers are not, and being made to hit a five-pixel-tall word exactly is the
#: difference between "I can take this text" and "it keeps dragging a box".
_WORD_SLACK = 4

#: A press anywhere on a line of text — including the gaps between its words —
#: is a press on that line.  This is how every text field on the machine
#: behaves, and the alternative is aiming at individual words.
_LINE_SLACK = 5

#: The action bar: a pill of real buttons under the selection.  Everything it
#: offers also has a key, and the keys keep working — but a gesture that is mouse
#: work from the shake to the release should not demand the keyboard at the end
#: of it.
_BAR_RADIUS = 11
_BAR_MARGIN = 6          # inside the pill, around the row
_BAR_GAP = 3             # between buttons
_BUTTON_PAD_X = 13
_BUTTON_PAD_Y = 8
_KEY_GAP = 8             # between a label and its key

#: Buttons only respond to a press *and* a release on the same one, the way
#: buttons everywhere do, so sliding off one is a way to change your mind.
BAR_CANCEL = "bar-cancel"
BAR_TEXT_SEARCH = "bar-text-search"
BAR_TEXT_COPY = "bar-text-copy"
BAR_TEXT_ALL = "bar-text-all"
BAR_TEXT_BACK = "bar-text-back"
#: Before anything is drawn the bar offers the choice the *next* drag will use.
#: Holding Shift has always swapped it for one selection, and nothing on screen
#: ever said so.
BAR_MODE_LASSO = "bar-mode-lasso"
BAR_MODE_RECT = "bar-mode-rect"

#: What the user asked to do with the selection.
ACTION_SEARCH = "search"
ACTION_COPY = "copy"
ACTION_SAVE = "save"
#: Only the tray's recent list uses this one now: on the overlay the text is
#: taken by dragging across it, not by asking for a whole region to be read.
ACTION_TEXT = "text"


@dataclass(frozen=True)
class BarButton:
    """One button of the action bar, already placed in screen coordinates."""

    action: str
    label: str
    key: str
    rect: QRect
    primary: bool = False


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
    #: The same words, but to be searched for as *text* rather than copied.  The
    #: overlay has already read them, so a picture of them would be a round trip
    #: through the network to answer a question that is already answered.
    text_search_requested = pyqtSignal(str)
    cancelled = pyqtSignal()
    #: The mode chip was clicked.  It is a setting, shown where it is wanted, so
    #: the choice is kept rather than lasting for one capture.
    mode_changed = pyqtSignal(str)
    #: A "sending" overlay took itself down.  Whoever kept it alive can let go.
    dismissed = pyqtSignal()

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
        magnifier: bool = True,
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
        #: A loupe beside the pointer while a drag or a handle is being moved.
        self._magnifier = magnifier
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
        #: these arrive late; until then the badge below says so, because
        #: several seconds of nothing looks like a hang.
        self._words: list[PlacedWord] = []
        #: One rectangle per recognised line.  Both the lit-up backdrop and the
        #: "am I over text?" test work on these: a line is what a person aims
        #: at, and there are ten times fewer of them than there are words.
        self._lines: list[tuple[tuple[int, int, int], QRect]] = []
        #: Everything *except* the text, cached because it is filled on every
        #: repaint and a drag repaints constantly.
        self._text_dim_path: QPainterPath | None = None
        #: The badge: what it says, which frame of the dots it is on, and the
        #: timer driving it.  One mechanism for both the reading and the sending
        #: message, because only one of them is ever true at a time.
        self._badge = BADGE_NONE
        self._scan_phase = 0
        self._scan_timer: QTimer | None = None
        #: Set once the selection has gone off to be uploaded.  The overlay
        #: stays on screen saying so instead of vanishing into a second of
        #: nothing, and `_dismiss_timer` guarantees it comes down again.
        self._dismiss_timer: QTimer | None = None
        #: Indices into `_words`; the range between them is what is selected.
        self._text_anchor: int | None = None
        self._text_focus: int | None = None
        self._selecting_text = False

        #: The action bar.  Laid out from the current state whenever it is
        #: needed — six text measurements, cheaper than keeping it in step.
        self._hovered_button: str | None = None
        self._pressed_button: str | None = None

        #: The lasso stroke.  The trail is the last handful of head positions,
        #: each keeping the colour its height gave it; the elapsed timer is the
        #: one clock they are all measured against, so no wall clock is read in
        #: the paint path.
        self._trail = Trail()
        #: The settled part of the ribbon, already drawn, and how much of
        #: `_points` is in it.  Allocated only once a loop grows past a chunk,
        #: and given back the moment the drag ends.
        self._stroke_layer: QPixmap | None = None
        self._frozen_upto = 0
        self._stroke_started = QElapsedTimer()
        self._stroke_started.start()
        self._stroke_timer: QTimer | None = None

        #: Where the other windows are, in this screen's coordinates, front-most
        #: first.  From the compositor: a Wayland client cannot see anybody
        #: else's geometry.  Empty until the KWin script says otherwise, and
        #: everything that uses it degrades to "no outline".
        self._window_rects: list[QRect] = []
        self._window_under: QRect | None = None

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

    # ------------------------------------------------------------ action bar

    def _bar_fonts(self) -> tuple[QFont, QFont]:
        """The label font and the smaller one the keys and the caption use."""
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF() + 0.5))
        small = QFont(font)
        small.setPointSizeF(max(8.0, font.pointSizeF() - 1.5))
        return font, small

    def _showing_hint(self) -> bool:
        """Nothing taken yet: the bar offers the mode the next drag will use.

        The same condition :meth:`paintEvent` uses to draw the hint, because a
        button that is hit-tested but not drawn is a dead patch of screen.
        """
        return (
            not self._has_selection
            and not self._dragging
            and not self.has_text_selection()
            and self._preview.isNull()
        )

    def _bar_caption(self) -> str:
        """The line under the buttons: what they cannot say by themselves.

        For a waiting selection that is the arrow keys — the handles show that
        the box can be dragged, and nothing at all shows that it can be nudged.
        For the mode chips it is the sentence that used to be the whole hint.
        """
        if self.has_text_selection():
            return ""
        if self._confirming and self._has_selection:
            return tr("overlay.adjust")
        if self._showing_hint():
            return tr("overlay.hint.lasso" if self._mode == MODE_LASSO else "overlay.hint.rect")
        return ""

    def _bar_entries(self) -> list[tuple[str, str, str, bool]]:
        """(action, label, key, primary) for the state the overlay is in."""
        if self._finished:
            # The selection has gone; the window is only still here to say so.
            return []
        if self._showing_hint():
            # The current mode is the lit one; the other carries *Shift*, which
            # is what swapping to it for a single drag has always been.
            lasso = self._mode == MODE_LASSO
            return [
                (BAR_MODE_LASSO, tr("bar.mode.lasso"), "" if lasso else "Shift", lasso),
                (BAR_MODE_RECT, tr("bar.mode.rect"), "Shift" if lasso else "", not lasso),
            ]
        if self.has_text_selection():
            # Searching leads, as it does for a region: the whole program is a
            # way to search for what is on the screen, and by this point the
            # words are already in hand — so this search costs no upload at all.
            # The count goes on the copy button rather than into a sentence
            # beside it: it is the one fact about a text selection worth
            # stating, and a label that carries it needs no prose underneath.
            count = len(self.selected_words())
            # Only one word can ever be a link — anything with a space in it is
            # a sentence — and this runs on every hover, so ask no further.
            link = looks_like_url(self.selected_text()) if count == 1 else None
            return [
                (
                    BAR_TEXT_SEARCH,
                    tr("bar.open_link") if link else tr("bar.search_text"),
                    "Enter",
                    True,
                ),
                (BAR_TEXT_COPY, tr("bar.copy_text", count=count), "C", False),
                (BAR_TEXT_ALL, tr("bar.all_text"), "T", False),
                (BAR_TEXT_BACK, tr("bar.back"), "Esc", False),
            ]
        if self._confirming and self._has_selection:
            return [
                (ACTION_SEARCH, tr("bar.search"), "Enter", True),
                (ACTION_COPY, tr("bar.copy"), "C", False),
                (ACTION_SAVE, tr("bar.save"), "S", False),
                (BAR_CANCEL, tr("bar.cancel"), "Esc", False),
            ]
        return []

    def _layout_buttons(self) -> list[BarButton]:
        """Place the bar under the selection, in screen coordinates."""
        entries = self._bar_entries()
        if not entries:
            return []

        font, small = self._bar_fonts()
        metrics = QFontMetrics(font)
        key_metrics = QFontMetrics(small)

        height = metrics.height() + 2 * _BUTTON_PAD_Y
        widths = [
            metrics.horizontalAdvance(label)
            + (_KEY_GAP + key_metrics.horizontalAdvance(key) if key else 0)
            + 2 * _BUTTON_PAD_X
            for _action, label, key, _primary in entries
        ]
        total = sum(widths) + _BAR_GAP * (len(widths) - 1)

        # The whole pill, caption row included, because that is what has to fit
        # — and a caption can be wider than every button put together.
        caption = self._bar_caption()
        caption_height = key_metrics.height() if caption else 0
        pill_height = height + caption_height + 2 * _BAR_MARGIN
        pill_width = max(total, key_metrics.horizontalAdvance(caption) + 2 * _BUTTON_PAD_X)

        anchor = self._bar_anchor()
        bounds = self._visible_area().toRect()
        left = anchor.center().x() - pill_width // 2
        left = max(
            bounds.left() + _LABEL_MARGIN,
            min(left, bounds.right() - pill_width - _LABEL_MARGIN),
        )
        # The row is centred inside the pill, not left-aligned in it.
        left += (pill_width - total) // 2

        top = anchor.bottom() + _LABEL_MARGIN * 2 + _BAR_MARGIN
        if top - _BAR_MARGIN + pill_height > bounds.bottom() - _LABEL_MARGIN:
            top = anchor.top() - pill_height - _LABEL_MARGIN * 2 + _BAR_MARGIN
        top = max(bounds.top() + _LABEL_MARGIN + _BAR_MARGIN, top)

        placed: list[BarButton] = []
        x = left
        for (action, label, key, primary), width in zip(entries, widths, strict=True):
            placed.append(
                BarButton(
                    action=action,
                    label=label,
                    key=key,
                    rect=QRect(x, top, width, height),
                    primary=primary,
                )
            )
            x += width + _BAR_GAP
        return placed

    def _bar_anchor(self) -> QRect:
        """What the bar hangs off: the selection, the words, or the top edge."""
        selected = self.selected_words()
        if selected:
            bounds = selected[0].rect
            for word in selected[1:]:
                bounds = bounds.united(word.rect)
            return bounds
        if self._showing_hint():
            # Where the prose hint used to sit, high enough to stay out of the
            # way of whatever is about to be circled.
            return QRect(
                self._offset.x() + self.width() // 2,
                self._offset.y() + max(24, self.height() // 12),
                0,
                0,
            )
        return self._selection_rect()

    def _button_at(self, position: QPoint) -> str | None:
        for button in self._layout_buttons():
            if button.rect.contains(position):
                return button.action
        return None

    def _bar_rect(self) -> QRect:
        """The pill: the row of buttons, its margins, and the caption row."""
        buttons = self._layout_buttons()
        if not buttons:
            return QRect()
        rect = buttons[0].rect
        for button in buttons[1:]:
            rect = rect.united(button.rect)
        rect = rect.adjusted(-_BAR_MARGIN, -_BAR_MARGIN, _BAR_MARGIN, _BAR_MARGIN)
        caption = self._bar_caption()
        if caption:
            metrics = QFontMetrics(self._bar_fonts()[1])
            rect.setBottom(rect.bottom() + metrics.height())
            needed = metrics.horizontalAdvance(caption) + 2 * _BUTTON_PAD_X
            if needed > rect.width():
                grow = (needed - rect.width() + 1) // 2
                rect.adjust(-grow, 0, grow, 0)
        return rect

    def _repaint_bar(self) -> None:
        """Repaint the bar and nothing else.

        Hover has to be cheap: the alternative is a full repaint of a 4K
        screenshot every time the pointer crosses a button.
        """
        rect = self._bar_rect()
        if rect.isNull():
            return
        self.update(rect.translated(-self._offset).adjusted(-2, -2, 2, 2))

    def _update_hover(self, where: QPoint) -> bool:
        """Track which button the pointer is over.  True if it is over one."""
        hovered = self._button_at(where)
        if hovered != self._hovered_button:
            self._hovered_button = hovered
            self._repaint_bar()
        if hovered is None:
            return False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        return True

    def _activate_button(self, action: str) -> None:
        if action in (BAR_MODE_LASSO, BAR_MODE_RECT):
            mode = MODE_LASSO if action == BAR_MODE_LASSO else MODE_RECTANGLE
            if mode != self._mode:
                log.info("selection mode switched to %s from the overlay", mode)
                self._mode = mode
                self._drag_mode = mode
                self.mode_changed.emit(mode)
            self.update()
        elif action == BAR_CANCEL:
            self._cancel()
        elif action == BAR_TEXT_BACK:
            self._clear_text_selection()
        elif action == BAR_TEXT_ALL:
            self.select_all_text()
        elif action == BAR_TEXT_SEARCH:
            self._search_text()
        elif action == BAR_TEXT_COPY:
            self._copy_text()
        else:
            self._commit(action)

    def _copy_text(self) -> None:
        text = self.selected_text()
        if not text:
            return
        log.info("copying %d character(s) of recognised text", len(text))
        self._finish(lambda: self.text_selected.emit(text))

    def _search_text(self) -> None:
        """Search for the words themselves.  Nothing is uploaded to do it."""
        text = self.selected_text()
        if not text:
            return
        log.info("searching for %d character(s) of recognised text", len(text))
        # The badge, because the wait here is the browser's cold start and an
        # overlay that vanished into a second of nothing would look like a drop.
        self._finish(
            lambda: self.text_search_requested.emit(text), badge=BADGE_OPENING
        )

    # ------------------------------------------------------------ text layer

    def _set_badge(self, badge: str) -> None:
        if badge == self._badge:
            return
        self._badge = badge
        if badge and self._scan_timer is None:
            timer = QTimer(self)
            timer.setInterval(_SCAN_TICK_MS)
            timer.timeout.connect(self._tick_scan)
            timer.start()
            self._scan_timer = timer
        elif not badge and self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
        self.update()

    def set_scanning(self, scanning: bool) -> None:
        """Show (or stop showing) that the screen is being read."""
        if self._badge == BADGE_SENDING:
            # Already on its way.  What the recogniser has to say about a screen
            # nobody is looking at any more is not worth taking the badge for.
            return
        self._set_badge(BADGE_SCANNING if scanning else BADGE_NONE)

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

    # --------------------------------------------------- the windows below

    def set_window_rects(self, rects: list[QRect]) -> None:
        """Hand over the window layout, in this screen's logical pixels."""
        self._window_rects = list(rects)
        log.info("%d window(s) can be picked out on %s", len(rects), self._metrics.name)
        self.update()

    def _window_outline(self) -> QRect | None:
        """The window a click would take, or ``None``.

        Only before a drag has started.  Once the pointer is down the user is
        drawing, and an outline that kept following them would be arguing with
        the selection they are making.
        """
        if not self._window_rects or self._finished:
            return None
        if self._dragging or self._has_selection or self._selecting_text:
            return None
        if self.has_text_selection() or not self._preview.isNull():
            return None
        # Text wins: a press on a word takes the words, so offering the whole
        # window there would promise something that will not happen.
        where = self._current + self._offset
        if self._word_at(where) is not None:
            return None
        return window_at(self._window_rects, where)

    def _draw_window_outline(self, painter: QPainter) -> None:
        """Outline the window under the pointer, and say it can be clicked.

        The compositor knows where every window is and the user has to draw
        around one by hand — the screenshot of a lasso laboriously circling a
        rectangular panel is the case this removes.
        """
        outline = self._window_outline()
        if outline is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Lift it out of the dimming, so the outline is a preview of the crop
        # rather than a line drawn on a dark rectangle.
        if self._dim.alpha():
            wash = QColor(255, 255, 255, min(60, self._dim.alpha()))
            painter.fillRect(outline, wash)
        pen = QPen(self._accent)
        pen.setWidth(2)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(outline.adjusted(1, 1, -1, -1))

        # A dashed rectangle is not an affordance on its own: nothing else in
        # the overlay answers a plain click, so it has to be said once.
        text = tr("overlay.take_window")
        painter.setFont(self.font())
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 3 * _LABEL_PADDING
        height = metrics.height() + _LABEL_PADDING
        if outline.width() > width + 2 * _LABEL_MARGIN and outline.height() > height * 3:
            self._draw_box(
                painter,
                QRect(outline.left() + _LABEL_MARGIN, outline.top() + _LABEL_MARGIN,
                      width, height),
                text,
            )
        painter.restore()

    def set_mode(self, mode: str) -> None:
        """Follow a mode change made on another overlay of the same group."""
        if mode == self._mode:
            return
        self._mode = mode
        self._drag_mode = mode
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
        # The layer is the size of the widget, so it does not survive one.
        self._drop_stroke_layer()
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
            self._draw_action_bar(painter)
            self._draw_badge(painter)
            painter.end()
            return

        if not self._has_selection and self._preview.isNull():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._dim_around_text(painter)
            self._draw_window_outline(painter)
            self._draw_word_hints(painter)
            self._draw_action_bar(painter)
            self._draw_badge(painter)
            painter.end()
            return

        selection = self._selection_rect()
        if selection.width() < 1 or selection.height() < 1:
            # A stroke drawn along one axis has a bounding box with no area, and
            # this used to leave the screen blank while the user was drawing it.
            self._dim_everything(painter)
            if self._stroke_showing():
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                self._draw_stroke(painter)
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

        # The text is still takeable while an area is drawn, so the marks that
        # say so have to stay: without them the affordance vanished the moment
        # the first rectangle appeared.
        self._draw_word_hints(painter)

        if self._stroke_showing():
            # The lasso is a ribbon, not a hairline.  No dashed box around it:
            # with lasso_mask off — the default — the un-dimmed area *is* the
            # bounding box already, so the rectangle was a second drawing of the
            # same fact, and next to a stroke this wide it is noise.
            self._draw_stroke(painter)
        elif self._outline_showing():
            pen = QPen(self._accent)
            pen.setWidth(1)
            pen.setCosmetic(True)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            painter.drawPath(self._selection_path())

        if self._confirming and self._has_selection and not self._finished:
            self._draw_handles(painter)
            self._draw_action_bar(painter)
        if not self._finished:
            self._draw_loupe(painter)
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

    # ------------------------------------------------------------ the stroke

    def _stroke_showing(self) -> bool:
        """True only *while the loop is being drawn*.

        The ribbon is the hand's own line, and once the hand has stopped there
        is a selection instead: the un-dimmed box, its handles and the bar say
        everything the line was saying, and a twelve-pixel stroke left lying
        across the result is in the way of reading it.  Android does the same —
        the stroke is part of the gesture, not part of the answer.

        Also only for a real freehand loop: a box adjusted by hand is no longer
        described by the loop, and a rectangle drawn as a ribbon would be
        claiming a shape that is not there.
        """
        return (
            self._dragging
            and self._has_selection
            and self._drag_mode == MODE_LASSO
            and len(self._points) >= 2
            and not self._box_edited
        )

    def _outline_showing(self) -> bool:
        """Whether the thin selection outline is worth drawing at all.

        A rectangle: always — it *is* the selection.  A finished lasso: only
        when ``lasso_mask`` is on, because then the loop is the shape that will
        be cut out and has to be visible.  With the mask off the crop is the
        bounding box, the un-dimmed area already is that box, and one more line
        tracing the loop is the same fact drawn twice.
        """
        if self._drag_mode != MODE_LASSO or self._box_edited:
            return True
        return self._mask_outside

    def _stroke_path(self) -> QPainterPath:
        """The visible lasso line: open, unlike the one used for masking.

        ``_selection_path()`` has to keep closing, because ``_reveal_path()``
        uses it when ``lasso_mask`` is on and a mask needs a closed shape.  At
        one pixel that closing line was a hint; at twelve it is a bar across the
        middle of whatever is being circled.  In the photographs the two ends
        simply pass each other without joining, so the drawing gets its own
        path — which is what these two things always were.
        """
        path = QPainterPath()
        if len(self._points) < 2:
            return path
        points = [point + self._offset for point in self._points]
        path.moveTo(float(points[0].x()), float(points[0].y()))
        for point in points[1:]:
            path.lineTo(float(point.x()), float(point.y()))
        return path

    def _ribbon_pen(self, width: int, colour: QColor) -> QPen:
        pen = QPen(colour)
        pen.setWidth(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    @staticmethod
    def _polyline(points: list[QPoint], offset: QPoint) -> QPainterPath:
        path = QPainterPath()
        if len(points) < 2:
            return path
        first = points[0] + offset
        path.moveTo(float(first.x()), float(first.y()))
        for point in points[1:]:
            moved = point + offset
            path.lineTo(float(moved.x()), float(moved.y()))
        return path

    def _freeze_stroke(self) -> None:
        """Bake the finished part of the ribbon into a layer.

        Re-stroking the whole path every frame is what made a long scribble
        unusable, and by a wide margin: Qt's raster engine takes **88 ms** to
        stroke a 1500-point antialiased twelve-pixel ribbon, and there are two
        passes of it in a frame.  The cost is linear in the number of points, so
        it got worse the longer you drew — a 4 fps drag by the time the loop was
        interesting.  Clipping the painter does not help at all, because the
        path is rasterised in full before anything is clipped away.

        So the settled part of the stroke is drawn once, into a pixmap, and only
        the last :data:`_STROKE_CHUNK` points are re-stroked per frame.  That
        makes the per-frame cost constant no matter how long the line gets.
        """
        spare = len(self._points) - self._frozen_upto
        if spare <= _STROKE_CHUNK:
            return

        layer = self._stroke_layer
        if layer is None:
            ratio = self.devicePixelRatioF()
            layer = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            layer.setDevicePixelRatio(ratio)
            layer.fill(Qt.GlobalColor.transparent)
            self._stroke_layer = layer

        # Overlap by one segment so the round caps of the frozen part and the
        # live tail meet on a shared point instead of leaving a gap.
        start = max(0, self._frozen_upto - 1)
        end = len(self._points) - 1
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # The layer is in widget coordinates, so no offset here.
        chunk = self._polyline(self._points[start:end], QPoint())
        painter.setPen(self._ribbon_pen(LINE_WIDTH + SHADOW_EXTRA, QColor(0, 0, 0, 90)))
        painter.drawPath(chunk)
        painter.setPen(self._ribbon_pen(LINE_WIDTH, QColor(255, 255, 255)))
        painter.drawPath(chunk)
        painter.end()
        self._frozen_upto = end

    def _drop_stroke_layer(self) -> None:
        """Give the layer back.  It is only wanted while a loop is being drawn."""
        self._stroke_layer = None
        self._frozen_upto = 0

    def _draw_stroke(self, painter: QPainter) -> None:
        """The glow, then the shadow, then the white line — in that order.

        The cap sits on top of its own glow, which is how it looks in every
        photograph, and the shadow is under both because a white line on a white
        page would otherwise be invisible.

        The shadow of the live tail goes down *before* the frozen layer, so that
        where the two meet the frozen white covers it rather than a dark ring
        being drawn across a line that is already there.
        """
        self._freeze_stroke()
        live = self._points[max(0, self._frozen_upto - 1) :]
        tail = self._polyline(live, self._offset)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_glow(painter)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.setPen(self._ribbon_pen(LINE_WIDTH + SHADOW_EXTRA, QColor(0, 0, 0, 90)))
        painter.drawPath(tail)
        if self._stroke_layer is not None:
            painter.drawPixmap(self._offset, self._stroke_layer)
        painter.setPen(self._ribbon_pen(LINE_WIDTH, QColor(255, 255, 255)))
        painter.drawPath(tail)
        painter.restore()

    def _draw_glow(self, painter: QPainter) -> None:
        """One soft blob per remembered head position, fading with age.

        Plain source-over, not additive: a stationary glow saturates towards its
        own colour instead of blowing out to white, which is what the
        photographs show.

        The blobs are pre-rendered and blitted rather than rasterised here — see
        :func:`~circle_to_search.stroke.glow_blob`.  Drawing the gradient forty
        times a frame was thirty milliseconds of it.
        """
        alive = self._trail.alive(self._stroke_clock())
        if not alive:
            return
        painter.save()
        for blob, left in alive:
            painter.setOpacity(left)
            painter.drawPixmap(
                blob.point.x() - GLOW_RADIUS,
                blob.point.y() - GLOW_RADIUS,
                glow_blob(blob.colour),
            )
        painter.restore()

    def _stroke_clock(self) -> float:
        """Milliseconds since the overlay was built.  One clock for the trail."""
        return float(self._stroke_started.elapsed())

    def _remember_head(self, position: QPoint) -> None:
        """Take one more head position, in the colour that height gives it."""
        head = position + self._offset
        # Height on *this screen*, not on the virtual desktop: otherwise the
        # same gesture would come out a different colour on a second monitor.
        colour = glow_colour(float(position.y()), float(max(1, self.height())))
        self._trail.add(head, colour, self._stroke_clock())

    def _stroke_damage(self) -> QRect:
        """What the fade needs repainted, in widget coordinates.

        A few hundred pixels square, not the whole 4K screenshot.  This is the
        number that matters most: the cursor glow that had to be taken back out
        repainted a large area at about ten frames a second.
        """
        bounds = self._trail.bounds()
        if bounds.isNull():
            return QRect()
        return bounds.translated(-self._offset).intersected(self.rect())

    def _start_stroke_animation(self) -> None:
        timer = self._stroke_timer
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(TICK_MS)
            timer.timeout.connect(self._tick_stroke)
            self._stroke_timer = timer
        if not timer.isActive():
            timer.start()

    def _stop_stroke_animation(self) -> None:
        if self._stroke_timer is not None:
            self._stroke_timer.stop()

    def _tick_stroke(self) -> None:
        """Fade the tail, and repaint only where the tail is.

        Runs while the loop is being drawn.  A pointer held still long enough
        for the whole trail to age out stops it; the next move starts it again.
        """
        damaged = self._stroke_damage()
        self._trail.prune(self._stroke_clock())
        if damaged.isValid():
            self.update(damaged)
        if not self._trail:
            self._stop_stroke_animation()

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
        if not self._lines or self._finished:
            # Once the selection has gone, nothing on the frozen screen can be
            # taken any more, so nothing should still be marked as takeable.
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

        # One rectangle per line, not per word: a selection with a gap at every
        # space is not what selected text looks like anywhere else.
        runs: list[QRect] = []
        previous: tuple[int, int, int] | None = None
        for word in selected:
            if previous == word.line and runs:
                runs[-1] = runs[-1].united(word.rect)
            else:
                runs.append(QRect(word.rect))
            previous = word.line

        area = QPainterPath()
        for run in runs:
            area.addRoundedRect(QRectF(run.adjusted(-3, -2, 3, 2)), 4, 4)

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

    def _badge_text(self) -> str:
        if self._badge == BADGE_SENDING:
            return tr("overlay.sending")
        if self._badge == BADGE_OPENING:
            return tr("overlay.opening")
        if self._badge == BADGE_SCANNING:
            return tr("overlay.scanning")
        return ""

    def _badge_rect(self) -> QRect:
        """Where the badge sits, in screen coordinates."""
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self._badge_text()) + 8 * _LABEL_PADDING
        height = metrics.height() + 2 * _LABEL_PADDING
        return QRect(
            self._offset.x() + (self.width() - width) // 2,
            self._offset.y() + self.height() - height - _LABEL_MARGIN * 4,
            width,
            height,
        )

    def _draw_badge(self, painter: QPainter) -> None:
        """Say what is being waited for, and keep saying it.

        Several seconds of nothing happening looks like a hang; a line that
        moves says "working" without asking for attention.  The same badge does
        the reading of the screen and the sending of the selection, because only
        one of the two is ever true at a time.
        """
        if not self._badge:
            return
        dots = "." * (self._scan_phase + 1)
        painter.setFont(self.font())
        box = self._badge_rect()
        if self._badge in (BADGE_SENDING, BADGE_OPENING):
            # In the accent colour: this one is not a note about the screen, it
            # is the last thing the overlay does before it goes away.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent)
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self._badge_text() + dots)
            return
        self._draw_box(painter, box, self._badge_text() + dots)

    def _draw_handles(self, painter: QPainter) -> None:
        """The grab squares on the edges and corners of the confirmed box."""
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.setBrush(self._accent)
        for rect in self._handle_rects().values():
            painter.drawRect(rect.adjusted(2, 2, -2, -2))

        # The move grip, in the middle.  It has to be visible, because pressing
        # anywhere else inside the box now starts a new selection and there
        # would otherwise be nothing to say where moving lives.
        grip = self._move_grip()
        if grip.isNull():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        fill = QColor(self._accent)
        fill.setAlpha(180)
        painter.setBrush(fill)
        painter.drawEllipse(grip)
        # A four-way arrow — the move cursor's own shape.  A bare cross reads as
        # "add", which is not what pressing this does.
        ink = QColor(255, 255, 255, 235)
        pen = QPen(ink)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(ink)
        centre = grip.center()
        reach = max(5, grip.width() // 3)
        head = max(3, reach // 3)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tip_x = centre.x() + dx * reach
            tip_y = centre.y() + dy * reach
            painter.drawLine(centre.x(), centre.y(), tip_x - dx * head, tip_y - dy * head)
            # The arrowhead: the tip, and two corners square to the direction.
            painter.drawPolygon(
                QPolygon(
                    [
                        QPoint(tip_x, tip_y),
                        QPoint(tip_x - dx * head + dy * head, tip_y - dy * head + dx * head),
                        QPoint(tip_x - dx * head - dy * head, tip_y - dy * head - dx * head),
                    ]
                )
            )
        painter.restore()

    def _draw_action_bar(self, painter: QPainter) -> None:
        """The pill of buttons under the selection.

        Painted rather than made of child widgets: real widgets would swallow
        the presses and moves that the drag, the resize handles and the text
        layer all need, and the whole overlay is one custom-painted surface
        anyway.
        """
        buttons = self._layout_buttons()
        if not buttons:
            return

        pill = self._bar_rect()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 205))
        painter.drawRoundedRect(pill, _BAR_RADIUS, _BAR_RADIUS)

        font, small = self._bar_fonts()
        key_metrics = QFontMetrics(small)

        for button in buttons:
            hovered = button.action == self._hovered_button
            # Armed but slid off: no longer drawn as held down, still armed, so
            # coming back and letting go works.
            pressed = hovered and button.action == self._pressed_button
            if button.primary or hovered:
                fill = QColor(self._accent)
                if pressed:
                    fill = fill.darker(120)
                elif not button.primary:
                    fill.setAlpha(95)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(button.rect, _BAR_RADIUS - 3, _BAR_RADIUS - 3)

            key_width = key_metrics.horizontalAdvance(button.key) if button.key else 0
            trim = _BUTTON_PAD_X + (key_width + _KEY_GAP if button.key else 0)
            label_rect = button.rect.adjusted(_BUTTON_PAD_X, 0, -trim, 0)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                button.label,
            )

            # The key stays visible next to its button: the keyboard is still
            # the faster way once you know it, and this is how you learn it.
            if button.key:
                key_rect = QRect(
                    label_rect.right() + _KEY_GAP,
                    button.rect.top(),
                    key_width,
                    button.rect.height(),
                )
                painter.setFont(small)
                painter.setPen(QColor(255, 255, 255, 150))
                painter.drawText(
                    key_rect,
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    button.key,
                )

        caption = self._bar_caption()
        if caption:
            # Inside the pill, not under it: the same dim grey that reads as a
            # footnote on black is unreadable on whatever the screenshot has.
            painter.setFont(small)
            painter.setPen(QColor(255, 255, 255, 140))
            row = QRect(
                pill.left(),
                buttons[0].rect.bottom(),
                pill.width(),
                pill.bottom() - buttons[0].rect.bottom(),
            )
            painter.drawText(row, int(Qt.AlignmentFlag.AlignCenter), caption)

        painter.restore()

    # --------------------------------------------------------------- the loupe

    def _loupe_showing(self) -> bool:
        """Only while something is actually being aimed.

        Not on hover: a magnifier that follows the pointer around a frozen
        screen is the cursor glow all over again, and the thing being solved
        here is putting an *edge* in the right place.
        """
        return (
            self._magnifier
            and not self._finished
            and (self._dragging or self._grab is not None)
            and not self._selecting_text
        )

    def _loupe_rect(self) -> QRect:
        """Where the loupe goes: beside the pointer, flipped when there is no room."""
        cursor = self._current + self._offset
        bounds = self._visible_area().toRect()
        x = cursor.x() + _LABEL_MARGIN * 2
        y = cursor.y() + _LABEL_MARGIN * 2
        if x + _LOUPE_SIZE > bounds.right() - _LABEL_MARGIN:
            x = cursor.x() - _LOUPE_SIZE - _LABEL_MARGIN * 2
        if y + _LOUPE_SIZE > bounds.bottom() - _LABEL_MARGIN:
            y = cursor.y() - _LOUPE_SIZE - _LABEL_MARGIN * 2
        x = max(bounds.left() + _LABEL_MARGIN, x)
        y = max(bounds.top() + _LABEL_MARGIN, y)
        return QRect(x, y, _LOUPE_SIZE, _LOUPE_SIZE)

    def _draw_loupe(self, painter: QPainter) -> None:
        """A 4× circle of the frozen screen, with a crosshair on the pointer.

        The arrow-key nudging exists because precision was missing — but it only
        helps *after* the miss.  This is the same problem answered before it
        happens: the pixel the edge will land on is visible while the edge is
        still being placed.

        Drawn from the screenshot that is already resident, so it costs one
        scaled blit of a few thousand pixels; nothing is captured again.
        """
        if not self._loupe_showing():
            return

        target = self._loupe_rect()
        cursor = self._current + self._offset
        side = max(2, _LOUPE_SIZE // _LOUPE_ZOOM)
        # The source in *physical* pixels of the screenshot, unclamped: clamping
        # it would slide the magnified image sideways near the edges of the
        # screen, which is exactly where careful aiming happens.
        source = QRectF(
            (cursor.x() - side / 2) * self._metrics.scale_x,
            (cursor.y() - side / 2) * self._metrics.scale_y,
            side * self._metrics.scale_x,
            side * self._metrics.scale_y,
        )

        painter.save()
        circle = QPainterPath()
        circle.addEllipse(QRectF(target))
        painter.setClipPath(circle)
        painter.fillRect(target, QColor(0, 0, 0))

        whole = QRectF(0.0, 0.0, float(self._sharp.width()), float(self._sharp.height()))
        visible = source.intersected(whole)
        if not visible.isEmpty():
            # Past the edge of the screenshot there is nothing to show, so the
            # part that does exist is placed where it belongs and the rest stays
            # black rather than being stretched to fill the circle.
            fx = target.width() / source.width()
            fy = target.height() / source.height()
            painter.drawPixmap(
                QRectF(
                    target.x() + (visible.x() - source.x()) * fx,
                    target.y() + (visible.y() - source.y()) * fy,
                    visible.width() * fx,
                    visible.height() * fy,
                ),
                self._sharp,
                visible,
            )

        centre = QRectF(target).center()
        arms = (
            (QPointF(target.left(), centre.y()), QPointF(centre.x() - _LOUPE_GAP, centre.y())),
            (QPointF(centre.x() + _LOUPE_GAP, centre.y()), QPointF(target.right(), centre.y())),
            (QPointF(centre.x(), target.top()), QPointF(centre.x(), centre.y() - _LOUPE_GAP)),
            (QPointF(centre.x(), centre.y() + _LOUPE_GAP), QPointF(centre.x(), target.bottom())),
        )
        # A dark line under a light one: the loupe shows whatever the screen had
        # there, so a crosshair in one colour is invisible against something.
        for width, colour in ((3, QColor(0, 0, 0, 110)), (1, self._accent)):
            pen = QPen(colour)
            pen.setWidth(width)
            painter.setPen(pen)
            for start, end in arms:
                painter.drawLine(start, end)

        painter.setClipping(False)
        ring = QPen(QColor(255, 255, 255, 220))
        ring.setWidth(2)
        painter.setPen(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(target).adjusted(1, 1, -1, -1))
        painter.restore()

    def _size_label(self, selection: QRect) -> tuple[str, QFont, QRect]:
        """The pixel readout: what it says, in what, and where it goes."""
        physical = logical_rect_to_physical(selection, self._metrics)
        text = f"{physical.width()} × {physical.height()} px"
        font = QFont(self.font())
        font.setBold(True)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 2 * _LABEL_PADDING
        height = metrics.height() + _LABEL_PADDING

        if self._confirming and self._grab is None:
            # Pinned above the box rather than to the pointer.  Once the drag is
            # over the pointer wanders off, and a readout that follows it sat
            # straight on top of the action bar.
            x = selection.left()
            y = selection.top() - height - _LABEL_MARGIN
            if y < self._offset.y() + _LABEL_MARGIN:
                y = selection.top() + _LABEL_MARGIN
        else:
            # Below/right of the cursor, flipped when there is no room.
            cursor = self._current + self._offset
            x = cursor.x() + _LABEL_MARGIN * 2
            y = cursor.y() + _LABEL_MARGIN * 2
            if x + width > self._offset.x() + self.width() - _LABEL_MARGIN:
                x = cursor.x() - width - _LABEL_MARGIN * 2
            if y + height > self._offset.y() + self.height() - _LABEL_MARGIN:
                y = cursor.y() - height - _LABEL_MARGIN * 2
        x = max(self._offset.x() + _LABEL_MARGIN, x)
        y = max(self._offset.y() + _LABEL_MARGIN, y)

        box = QRect(x, y, width, height)
        bounds = self._visible_area().toRect()
        bar = self._bar_rect()
        if not bar.isNull() and box.intersects(bar):
            # Resizing by a bottom handle can still bring the two together.
            box.moveBottom(bar.top() - _LABEL_MARGIN)
        if self._loupe_showing():
            # Both want the space just below and right of the pointer, and the
            # loupe is the one that has to be there.
            loupe = self._loupe_rect()
            if box.intersects(loupe):
                box.moveTop(loupe.bottom() + _LABEL_MARGIN)
                if box.bottom() > bounds.bottom() - _LABEL_MARGIN:
                    box.moveBottom(loupe.top() - _LABEL_MARGIN)
                box.moveLeft(loupe.left())
        box.moveTop(max(bounds.top() + _LABEL_MARGIN, box.top()))
        return text, font, box

    def _draw_size_label(self, painter: QPainter, selection: QRect) -> None:
        text, font, box = self._size_label(selection)
        painter.setFont(font)
        self._draw_box(painter, box, text)

    def _draw_box(self, painter: QPainter, box: QRect, text: str) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._finished:
            # Nothing left to select: the selection has gone and the window is
            # only still here to say so.  A press takes the badge away early.
            self.dismiss()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        where = event.position().toPoint() + self._offset

        # The bar is painted on top of everything, so it is hit-tested before
        # everything: a press on a button must never also start a drag or move
        # the box it is hanging off.
        button = self._button_at(where)
        if button is not None:
            self._pressed_button = button
            self._hovered_button = button
            self._repaint_bar()
            return

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
                # Anywhere else — inside the box included — means "no, that
                # one": start again.  It used to be that the whole inside was a
                # move grab, which left a selection covering most of the screen
                # impossible to redraw, because there was nowhere left to press
                # that did not move it.  Moving has its own grip now.
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
        self._trail.clear()
        self._drop_stroke_layer()
        if self._drag_mode == MODE_LASSO:
            self._remember_head(position)
            self._start_stroke_animation()
        self._changed()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._finished:
            return
        position = event.position().toPoint()
        # Snapshotted before anything moves, so the repaint can be told exactly
        # what changed instead of the whole screen.
        was_box = self._selection_rect() if self._dragging else QRect()
        was_floating = self._floating_rects() if self._dragging else []
        was_head = self._points[-1] if self._points else position
        self._current = position

        # Hovering is only ever asked while nothing is being dragged: during a
        # drag the bar is not on screen at all, and a press that armed a button
        # keeps tracking so that sliding off it un-highlights, the way a real
        # button does.
        idle = not (self._dragging or self._selecting_text)
        if (self._pressed_button is not None or idle) and self._update_hover(
            position + self._offset
        ):
            return

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
            # The outline follows the pointer from window to window, and only
            # the two rectangles involved are repainted — the alternative is a
            # full repaint of a 4K screenshot on every pointer move.
            outline = self._window_outline()
            if outline != self._window_under:
                for rect in (self._window_under, outline):
                    if rect is not None:
                        self.update(rect.translated(-self._offset).adjusted(-3, -3, 3, 3))
                self._window_under = outline
            return
        if self._drag_mode == MODE_LASSO:
            last = self._points[-1] if self._points else None
            if (
                last is None
                or abs(position.x() - last.x()) >= _LASSO_MIN_STEP
                or abs(position.y() - last.y()) >= _LASSO_MIN_STEP
            ):
                self._points.append(position)
            # Every move, not every kept point: the glow follows the hand even
            # while the polygon is dropping samples that are too close together.
            self._remember_head(position)
            # A pointer held still long enough for the trail to age out stopped
            # the frames; moving again is what starts them.
            self._start_stroke_animation()
        self._changed(self._drag_damage(was_box, was_floating, was_head))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._finished or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._pressed_button is not None:
            armed = self._pressed_button
            self._pressed_button = None
            # Released somewhere else: the press is taken back, which is what
            # every button everywhere does and the only way out of a misclick.
            if self._button_at(event.position().toPoint() + self._offset) == armed:
                self._activate_button(armed)
                return
            self._repaint_bar()
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
            # The stroke ends with the gesture.  The design had the tail fading
            # for a fifth of a second after the button came up, which was right
            # while the line stayed on screen; now that the line goes, a glow
            # left behind on its own is a coloured smudge with nothing under it.
            self._stop_stroke_animation()
            self._trail.clear()
            self._drop_stroke_layer()
        selection = self._selection_rect()

        if selection.width() < MIN_SELECTION or selection.height() < MIN_SELECTION:
            # Too small to be a drag — but the compositor told us where the
            # windows are, and a click on one of them is not a mistake, it is
            # the fastest possible way to say "that panel".
            window = window_at(self._window_rects, self._anchor + self._offset)
            if window is not None:
                log.info("taking the window under the click: %dx%d",
                         window.width(), window.height())
                self._take_window(window)
                return
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
                self._finish(
                    lambda: self.committed.emit(global_box, ACTION_SEARCH),
                    badge=BADGE_SENDING,
                )
                return
            self._finish(lambda: self.selected.emit(physical, polygon), badge=BADGE_SENDING)
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
        if self._finished:
            # Only the badge is left.  Any key takes it away — it is a progress
            # note, not something to be answered.
            self.dismiss()
            return
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

        if self.has_text_selection():
            # The same keys as the region bar below, doing the same two things:
            # Enter is whatever the lit button says, C is the clipboard.
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self._search_text()
                return
            if key == Qt.Key.Key_C:
                self._copy_text()
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
            and self.is_sending()
            and not self.isActiveWindow()
        ):
            # The browser came up first.  Its window is the thing that says the
            # sending worked, so the badge has nothing left to add.
            log.debug("something else took the focus, the badge is done")
            self.dismiss()
            return
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

    def _floating_rects(self) -> list[QRect]:
        """What moves with the pointer during a drag, apart from the box itself."""
        rects = [self._trail.bounds()]
        if self._loupe_showing():
            rects.append(self._loupe_rect())
        selection = self._selection_rect()
        if selection.width() >= 1 and selection.height() >= 1:
            rects.append(self._size_label(selection)[2])
        return [rect for rect in rects if not rect.isNull()]

    def _drag_damage(self, was_box: QRect, was_floating: list[QRect], was_head: QPoint) -> QRegion:
        """What actually changed since the frame before, in widget coordinates.

        On a 4K screen the whole overlay is thirty-three megabytes, and repainting
        all of it on every pointer move means handing the compositor that much
        again sixty times a second.  The raster cost is survivable; the upload is
        what made a big screen crawl while a small one did not.

        The box contributes a *ring*, never its filled inside: a loop that already
        covers most of the screen would otherwise damage most of the screen every
        time it grew by five pixels.
        """
        if self._mask_outside:
            # The un-dimmed area is the loop itself, so closing the loop can
            # light up a large region nowhere near the pointer.  Not worth
            # tracking; this is off by default anyway.
            return QRegion()

        damage = QRegion()
        box = self._selection_rect()
        if not (box.isNull() and was_box.isNull()):
            damage += QRegion(box.united(was_box).adjusted(-4, -4, 4, 4))
            shared = box.intersected(was_box).adjusted(4, 4, -4, -4)
            if shared.width() > 0 and shared.height() > 0:
                damage -= QRegion(shared)

        reach = (LINE_WIDTH + SHADOW_EXTRA) // 2 + 3
        head = self._points[-1] if self._points else was_head
        segment = QRect(was_head, head).normalized().adjusted(-reach, -reach, reach, reach)
        damage += QRegion(segment)

        for rect in (*was_floating, *self._floating_rects()):
            damage += QRegion(rect.adjusted(-3, -3, 3, 3))

        damage.translate(-self._offset)
        # No guard on how big this can get: every piece of it is either a thin
        # ring or a few hundred pixels square, and in the one case where the ring
        # really is the whole window the region simply *is* a full repaint.
        return damage.intersected(QRegion(self.rect()))

    def _changed(self, damage: QRegion | None = None) -> None:
        """Repaint, and in a group tell the other overlays what to draw."""
        if damage is not None and not damage.isEmpty():
            self.update(damage)
        else:
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

    def _move_grip(self) -> QRect:
        """The grab square in the middle of the box, for moving the whole thing.

        The inside of the box used to *be* the move grab, which meant a
        selection covering most of the screen could never be redrawn: there was
        nowhere left to press that did not move it.  So moving got a grip of its
        own and the rest of the inside went back to starting a new selection.
        """
        if self._box.isNull():
            return QRect()
        side = min(_GRIP, max(_HANDLE, min(self._box.width(), self._box.height()) // 3))
        centre = self._box.center()
        return QRect(centre.x() - side // 2, centre.y() - side // 2, side, side)

    def _handle_at(self, position: QPoint) -> str | None:
        """Which grab area is under the pointer, if any.

        Deliberately not "anywhere inside the box is a move": see
        :meth:`_move_grip`.
        """
        for name, rect in self._handle_rects().items():
            if rect.contains(position):
                return name
        if self._move_grip().contains(position):
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
        was_box = self._selection_rect()
        was_floating = self._floating_rects()
        self._box = box
        if edited:
            self._box_edited = True
        # Dragging a handle across a 4K screen repaints as much as drawing does.
        head = self._points[-1] if self._points else self._current
        self._changed(self._drag_damage(was_box, was_floating, head))

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
        # Only searching goes anywhere: copying and saving are done by the time
        # the overlay would have closed, and a badge for them would be a lie.
        badge = BADGE_SENDING if action == ACTION_SEARCH else BADGE_NONE

        if self.in_group:
            # The app cannot use a per-screen crop box here: the selection may
            # cover parts of two screenshots at two different scales, so it is
            # handed over in global logical pixels and stitched afterwards.
            global_box = self._box.translated(self._origin)
            self._finish(lambda: self.committed.emit(global_box, action), badge=badge)
            return

        signals = {
            ACTION_COPY: self.copy_requested,
            ACTION_SAVE: self.save_requested,
        }
        signal = signals.get(action, self.selected)
        self._finish(lambda: signal.emit(physical, polygon), badge=badge)

    def _take_window(self, window: QRect) -> None:
        """Make one window the selection, as if it had been dragged around.

        A rectangle, always: whatever the mode is, what the compositor handed
        over is a box, and a lasso outline around it would be a fiction.
        """
        self._drag_mode = MODE_RECTANGLE
        self._points = []
        self._has_selection = True
        self._window_under = None
        self._box = QRect(window)
        self._box_edited = False

        if not self._confirm:
            physical = logical_rect_to_physical(window, self._metrics)
            if physical.width() < 1 or physical.height() < 1:
                self._reset_selection()
                return
            if self.in_group:
                global_box = window.translated(self._origin)
                self._finish(
                    lambda: self.committed.emit(global_box, ACTION_SEARCH),
                    badge=BADGE_SENDING,
                )
                return
            self._confirming = True  # so _selection_rect() reads _box
            self._finish(
                lambda: self.selected.emit(physical, QPolygon()), badge=BADGE_SENDING
            )
            return

        self._confirming = True
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._changed()

    def _reset_selection(self) -> None:
        self._stop_stroke_animation()
        self._trail.clear()
        self._drop_stroke_layer()
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

    def _finish(self, emit: Callable[[], None], *, badge: str = BADGE_NONE) -> None:
        if self._finished:
            return
        self._finished = True
        self.releaseKeyboard()
        self._stop_stroke_animation()

        if badge:
            # Do not vanish into a second of nothing.  Preparing the image and
            # writing the launcher page take a moment, the browser takes longer,
            # and an overlay that disappears before anything appears is
            # indistinguishable from one that threw the selection away.  The
            # same argument as for the reading badge, which the user made first.
            # The selection stays drawn, undimmed, under the badge: "this is
            # what is on its way" is more use than an empty frozen screen.  What
            # goes is everything that invites another click.
            self._set_badge(badge)
            self.setCursor(Qt.CursorShape.BusyCursor)
            self.update()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.dismiss)
            timer.start(SENDING_LIMIT_MS)
            self._dismiss_timer = timer
            emit()
            return

        self._set_badge(BADGE_NONE)
        self.hide()
        emit()
        self.close()

    def is_sending(self) -> bool:
        """True while the overlay is up only to say the result is on its way."""
        return self._badge in (BADGE_SENDING, BADGE_OPENING)

    def dismiss(self) -> None:
        """Take a sending overlay down: the launcher is up, or time is up."""
        if not self.is_sending():
            return
        if self._dismiss_timer is not None:
            self._dismiss_timer.stop()
            self._dismiss_timer = None
        self._set_badge(BADGE_NONE)
        self.hide()
        self.close()
        self.dismissed.emit()
