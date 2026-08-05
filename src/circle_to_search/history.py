"""The last few selections, kept so they can be used again.

Redoing the gesture, re-drawing the loop and hoping the screen has not changed
is a poor answer to "actually, search that again" or "I meant to copy it".  The
last :data:`LIMIT` crops therefore stay on disk and are one click away in the
tray.

On disk, not in memory: five 4K crops would be a lot of resident memory for a
daemon that is idle most of the time, and a restart would lose them anyway.  It
does mean the selections are files under the user's data directory until they
are pushed out by newer ones — which is why it can be switched off, and why
"Forget them" is next to the list.

Whatever text was recognised inside a crop is kept beside it in a plain ``.txt``
file, so asking for it again from the tray costs nothing.  Plain text rather
than a format: it is the kind of thing a person may well want to open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from .logging_setup import get_logger

log = get_logger("history")

#: How many to keep.  Enough to cover "no, the one before that", small enough
#: that the directory never becomes an archive.
LIMIT = 5

#: Where they live.
RECENT_DIR = Path.home() / ".local/share/circle-to-search/recent"

#: ``capture-YYYYmmdd-HHMMSS-ffffff.png``.  The microseconds are not decoration:
#: two selections a second apart are ordinary, two in the same second are quite
#: possible, and without them the "newest" in the list would be a guess.  The
#: older second-resolution form is still read, in case one is lying around.
_NAME = re.compile(r"^capture-(\d{8}-\d{6}(?:-\d{6})?)\.png$")
_STAMP = "%Y%m%d-%H%M%S-%f"
_STAMP_COARSE = "%Y%m%d-%H%M%S"


def matches(query: str, *fields: str) -> bool:
    """Whether every word of ``query`` appears somewhere in ``fields``.

    Words rather than the whole string, so "connection refused" finds a capture
    whose recognised text wrapped between the two, and "error db" finds one that
    has both a long way apart.  Case-insensitive, anywhere inside a word: this
    is a box above a dozen thumbnails, not a search engine, and somebody typing
    "conn" wants the connection error.
    """
    words = query.lower().split()
    if not words:
        return True
    haystack = "\n".join(fields).lower()
    return all(word in haystack for word in words)


@dataclass(frozen=True)
class RecentCapture:
    """One kept selection."""

    path: Path
    taken: datetime
    width: int
    height: int
    #: True when text was recognised inside it and kept beside it.
    has_text: bool = False

    @property
    def label(self) -> str:
        mark = "  ·  ¶" if self.has_text else ""
        return f"{self.taken:%H:%M:%S}  ·  {self.width} × {self.height}{mark}"


class RecentCaptures:
    """The directory of kept selections, newest first."""

    def __init__(self, directory: Path | None = None, limit: int = LIMIT) -> None:
        self._directory = directory if directory is not None else RECENT_DIR
        self._limit = max(1, limit)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> None:
        """Change how many are kept, and enforce it on the ones already there.

        Changed in place rather than by making a new one: the history window
        holds this object, and swapping it would leave that window reading a
        directory through a stale copy of these two fields.
        """
        self._limit = max(1, limit)
        self.prune()

    @staticmethod
    def _text_path(path: Path) -> Path:
        return path.with_suffix(".txt")

    def text_of(self, path: Path) -> str:
        """The text kept beside a capture, or an empty string."""
        try:
            return self._text_path(path).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return ""

    def add(self, image: Image.Image, text: str = "") -> Path | None:
        """Keep a copy of ``image``.  Returns its path, or ``None`` on failure.

        Failing to remember a capture must never break the capture itself, so
        every error here is logged and swallowed.
        """
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            path = self._directory / f"capture-{datetime.now().strftime(_STAMP)}.png"
            while path.exists():
                # Microseconds make this all but impossible; handle it anyway
                # rather than silently overwrite somebody's capture.
                path = self._directory / f"capture-{datetime.now().strftime(_STAMP)}.png"
            image.save(path, "PNG")
        except (OSError, ValueError) as exc:
            log.warning("could not keep the capture: %s", exc)
            return None
        if text:
            try:
                self._text_path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                # The picture is what matters; losing the text only means it has
                # to be recognised again.
                log.warning("could not keep the text of %s: %s", path.name, exc)
        log.debug("kept %s", path)
        self.prune()
        return path

    def entries(self) -> list[RecentCapture]:
        """The kept captures, newest first.  Never raises."""
        found: list[RecentCapture] = []
        try:
            candidates = sorted(self._directory.glob("capture-*.png"))
        except OSError:
            return []
        for path in candidates:
            match = _NAME.match(path.name)
            if match is None:
                continue
            try:
                stamp = match.group(1)
                taken = datetime.strptime(
                    stamp, _STAMP if len(stamp) > len("00000000-000000") else _STAMP_COARSE
                )
                with Image.open(path) as image:
                    width, height = image.size
            except (OSError, ValueError) as exc:
                log.debug("ignoring %s: %s", path, exc)
                continue
            found.append(
                RecentCapture(
                    path=path,
                    taken=taken,
                    width=width,
                    height=height,
                    has_text=self._text_path(path).is_file(),
                )
            )
        found.sort(key=lambda entry: (entry.taken, entry.path.name), reverse=True)
        return found

    def load(self, path: Path) -> Image.Image | None:
        """Read one back.  ``None`` when it is gone or unreadable."""
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except (OSError, ValueError) as exc:
            log.warning("could not read %s: %s", path, exc)
            return None

    def forget(self, path: Path) -> None:
        for target in (path, self._text_path(path)):
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not remove %s: %s", target, exc)

    def clear(self) -> int:
        """Drop all of them.  Returns how many were removed."""
        removed = 0
        for entry in self.entries():
            self.forget(entry.path)
            removed += 1
        log.info("forgot %d kept capture(s)", removed)
        return removed

    def prune(self) -> None:
        """Keep only the newest :data:`LIMIT`."""
        for entry in self.entries()[self._limit :]:
            self.forget(entry.path)
