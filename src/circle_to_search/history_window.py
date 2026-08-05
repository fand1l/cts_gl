"""Everything you have looked up, in a window you can search.

The kept captures were only ever reachable through a tray submenu, which meant
the number of them had to stay at five: a menu of thirty is unusable.  That was
a statement about the menu, not about the right number — and the whole time,
whatever text was recognised inside each crop was sitting on disk beside it.

So there was already a small searchable corpus of everything you have looked up,
and no way to search it.  This is the window, and with it the limit becomes a
setting rather than a consequence.

A ``QListWidget`` in icon mode rather than hand-drawn tiles: it brings a grid
that reflows, keyboard navigation, selection and scrolling with it, and every
one of those is something a hand-rolled version would get subtly wrong.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeyEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .history import RecentCaptures, matches
from .i18n import tr
from .logging_setup import get_logger
from .overlay import ACTION_COPY, ACTION_SAVE, ACTION_SEARCH, ACTION_TEXT

log = get_logger("history-window")

#: The thumbnails.  Big enough to recognise an error message by its shape,
#: small enough that a dozen fit without scrolling.
THUMBNAIL = QSize(200, 130)
#: Room under each for two lines of label.
CELL = QSize(THUMBNAIL.width() + 24, THUMBNAIL.height() + 52)

#: Where a capture's path is kept on its item.
_PATH_ROLE = int(Qt.ItemDataRole.UserRole)
#: And the text it is searched by, loaded once when the window is filled rather
#: than read off disk on every keystroke.
_HAYSTACK_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class HistoryWindow(QDialog):
    """The kept captures: a grid of them, and a box to search their text."""

    #: Do one of the four things to a kept capture.  The application owns what
    #: those mean, exactly as it does for the tray's version of this menu.
    reuse_requested = pyqtSignal(Path, str)

    def __init__(self, recent: RecentCaptures, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recent = recent

        self.setWindowTitle(tr("history.title"))
        self.resize(760, 560)

        layout = QVBoxLayout(self)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("history.search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(THUMBNAIL)
        self.list.setGridSize(CELL)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemActivated.connect(lambda _item: self._act(ACTION_SEARCH))
        self.list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.list, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self._buttons: list[tuple[QPushButton, str]] = []
        for label, action in (
            (tr("tray.recent.search"), ACTION_SEARCH),
            (tr("tray.recent.copy"), ACTION_COPY),
            (tr("tray.recent.save"), ACTION_SAVE),
            (tr("tray.recent.text"), ACTION_TEXT),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, chosen=action: self._act(chosen))
            buttons.addWidget(button)
            self._buttons.append((button, action))
        buttons.addStretch(1)
        self.forget = QPushButton(tr("tray.recent.forget"))
        self.forget.clicked.connect(self._forget)
        buttons.addWidget(self.forget)
        layout.addLayout(buttons)

        self.reload()

    # ---------------------------------------------------------------- filling

    def reload(self) -> None:
        """Read the directory again and rebuild the grid.

        The text of each capture is read once, here, and kept on its item: with
        a couple of hundred of them, going back to disk on every keystroke would
        make typing in the search box feel like the disk it is hitting.
        """
        selected = self.selected_path()
        self.list.clear()
        for entry in self._recent.entries():
            item = QListWidgetItem(self._caption(entry))
            pixmap = QPixmap(str(entry.path))
            if not pixmap.isNull():
                item.setIcon(QIcon(self._thumbnail(pixmap)))
            item.setData(_PATH_ROLE, str(entry.path))
            item.setData(
                _HAYSTACK_ROLE,
                "\n".join((entry.label, entry.path.name, self._recent.text_of(entry.path))),
            )
            item.setToolTip(self._tooltip(entry.label, self._recent.text_of(entry.path)))
            self.list.addItem(item)
            if selected is not None and entry.path == selected:
                item.setSelected(True)
        self._filter(self.search.text())
        log.info("the history window is showing %d capture(s)", self.list.count())

    @staticmethod
    def _thumbnail(pixmap: QPixmap) -> QPixmap:
        """The crop, centred on a tile of one fixed size.

        Every icon has to be the same size or the grid stops being a grid: with
        icons of their own shapes, a wide crop leaves room for two lines of
        caption and a tall one leaves room for none, so the captions land at
        different heights and the elided ones lose the size.

        A thin rim around the image, for the same reason the pinned window has
        one: a crop of a white dialog on a pale background is otherwise an
        invisible rectangle in the middle of a tile.
        """
        scaled = pixmap.scaled(
            THUMBNAIL,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        tile = QPixmap(THUMBNAIL)
        tile.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tile)
        left = (THUMBNAIL.width() - scaled.width()) // 2
        top = (THUMBNAIL.height() - scaled.height()) // 2
        painter.drawPixmap(left, top, scaled)
        painter.setPen(QPen(QColor(0, 0, 0, 60)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(left, top, scaled.width() - 1, scaled.height() - 1)
        painter.end()
        return tile

    @staticmethod
    def _caption(entry) -> str:
        """Two lines under the thumbnail: when, and how big.

        Not `entry.label`, which is one line built for a menu — in a grid that
        either gets elided or wraps at whichever separator happens to fall near
        the edge, which put the ¶ on a line of its own.
        """
        mark = "  ¶" if entry.has_text else ""
        return f"{entry.taken:%H:%M:%S}\n{entry.width} × {entry.height}{mark}"

    @staticmethod
    def _tooltip(label: str, text: str) -> str:
        if not text:
            return label
        head = text.strip().splitlines()
        return label + "\n\n" + "\n".join(head[:8]) + ("\n…" if len(head) > 8 else "")

    # --------------------------------------------------------------- filtering

    def _filter(self, query: str) -> None:
        shown = 0
        for index in range(self.list.count()):
            item = self.list.item(index)
            hit = matches(query, str(item.data(_HAYSTACK_ROLE) or ""))
            item.setHidden(not hit)
            if hit:
                shown += 1
            elif item.isSelected():
                item.setSelected(False)
        self._say(shown, query)
        self._update_buttons()

    def _say(self, shown: int, query: str) -> None:
        total = self.list.count()
        if total == 0:
            self.status.setText(tr("history.empty"))
        elif query.strip() and shown == 0:
            # Naming the thing that was searched, because the commonest reason
            # for no results is that the text was never recognised: the optional
            # OCR is off until it is turned on, and then there is nothing to
            # match against however much was captured.
            self.status.setText(tr("history.no_match", query=query.strip()))
        elif query.strip():
            self.status.setText(tr("history.found", shown=shown, total=total))
        else:
            self.status.setText(tr("history.total", total=total))

    # ----------------------------------------------------------------- actions

    def selected_path(self) -> Path | None:
        items = [item for item in self.list.selectedItems() if not item.isHidden()]
        if not items:
            return None
        return Path(str(items[0].data(_PATH_ROLE)))

    def _update_buttons(self) -> None:
        has = self.selected_path() is not None
        for button, _action in self._buttons:
            button.setEnabled(has)
        self.forget.setEnabled(has)

    def _act(self, action: str) -> None:
        path = self.selected_path()
        if path is None:
            return
        log.info("reusing %s from the history window (%s)", path.name, action)
        self.reuse_requested.emit(path, action)

    def _forget(self) -> None:
        path = self.selected_path()
        if path is None:
            return
        self._recent.forget(path)
        self.reload()

    def _context_menu(self, where) -> None:
        item = self.list.itemAt(where)
        if item is None:
            return
        item.setSelected(True)
        menu = QMenu(self)
        for label, action in (
            (tr("tray.recent.search"), ACTION_SEARCH),
            (tr("tray.recent.copy"), ACTION_COPY),
            (tr("tray.recent.save"), ACTION_SAVE),
            (tr("tray.recent.text"), ACTION_TEXT),
        ):
            entry = menu.addAction(label)
            entry.triggered.connect(lambda _checked=False, chosen=action: self._act(chosen))
        menu.addSeparator()
        forget = menu.addAction(tr("tray.recent.forget"))
        forget.triggered.connect(self._forget)
        menu.exec(self.list.viewport().mapToGlobal(where))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.search.text():
            # The first Esc clears the search, the second closes the window:
            # the same bargain the overlay makes with a text selection.
            self.search.clear()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.list.hasFocus():
            self._forget()
            return
        super().keyPressEvent(event)
