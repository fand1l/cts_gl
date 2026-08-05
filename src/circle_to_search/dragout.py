"""What a dragged-out crop carries, and where the file it points at lives.

Copying to the clipboard already exists and this is the same picture by another
route — but the route is the whole point: dropping a crop into a chat is one
gesture where copy, switch, click, paste is four.

**The payload carries the crop twice, and neither half is a fallback.**  A drop
lands in *somebody else's* application and what that application will accept is
not ours to decide, so both of the two answers in circulation are offered and
the target picks whichever it understands:

* ``text/uri-list``, pointing at a real PNG on disk.  A chat window, a file
  manager and a mail composer want a *file*; most of them will not look at raw
  image bytes at all, and every one of them takes this.
* ``image/png``, plus Qt's own ``application/x-qt-image``.  An image editor, a
  word processor or a canvas in a browser tab takes the bytes — and so does
  anything sandboxed away from the directory below, which is the case the file
  cannot serve.  It is also all that is left when the file could not be
  written, so a full disk costs the feature nothing.

``image/png`` is named explicitly rather than left to Qt's conversion of
:meth:`QMimeData.setImageData`, because the offer a non-Qt client sees over the
Wayland data device is the list of format names — and a format that is not
named is a format that is not there.  The bytes are encoded once and used for
both halves.

**Where the file goes** is the argument :func:`lens.write_browser_launcher`
already had to settle, and it comes out the same way: ``~/.cache`` rather than
``$XDG_RUNTIME_DIR`` or ``/tmp``, because a sandboxed target can read neither
of those.  It cannot necessarily read ``~/.cache`` either — that is exactly
what the second half of the payload is for.

**It is named like a saved capture** (``circle-to-search-20260805-143012.png``)
and not like a temporary file, because the name is not ours: it is what lands
in the chat, and what the person on the other end sees.

**It does not stay.**  A dragged crop is a piece of your screen sitting on
disk, and this program deletes the browser launcher for the same reason.  Two
mechanisms, because they cover different failures: the pin arms a timer for the
ordinary case, and :func:`write_drag_file` sweeps anything older than the
lifetime on its way past, which is what collects the files of a session that
was killed before its timers ran.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from tempfile import gettempdir

from PyQt6.QtCore import QBuffer, QIODevice, QMimeData, QUrl
from PyQt6.QtGui import QPixmap

from .logging_setup import get_logger

log = get_logger("dragout")

#: How long a dragged-out file may sit on disk.  Long enough for a slow target
#: — a chat that does not read the path until the message is actually sent —
#: and short enough that a piece of somebody's screen is not left lying about.
#: The same number and the same argument as the browser launcher's.
DRAG_LIFETIME_MS = 10 * 60 * 1000

#: The formats offered, in the order a target sees them.  The file leads: it is
#: the one that works nearly everywhere, and a target that takes the first
#: thing it recognises should get the useful one.
URI_FORMAT = "text/uri-list"
PNG_FORMAT = "image/png"


def cache_directory() -> Path:
    """The private directory the file is written into, made if it is missing.

    Falls back to the temporary directory when the cache cannot be made at all,
    which is worse for a sandboxed target and better than nothing.
    """
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    directory = Path(cache) / "circle-to-search"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        directory = Path(gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def png_bytes(pixmap: QPixmap) -> bytes:
    """Encode the crop once, for the file and for the ``image/png`` offer."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        log.warning("the crop could not be encoded as a PNG")
        return b""
    data = bytes(buffer.data())
    buffer.close()
    return data


def drag_filename(when: datetime | None = None) -> str:
    """``circle-to-search-20260805-143012.png`` — the same shape as a save.

    The recipient sees this, so it says what the thing is; a hex token would
    say only that a program made it.
    """
    return f"circle-to-search-{when or datetime.now():%Y%m%d-%H%M%S}.png"


def _free_path(directory: Path, name: str) -> Path:
    """``name``, or ``name-2``, ``name-3``… if it is taken.

    Two drags inside one second are rare and overwriting the first one's file
    while its target is still reading it would be a real bug, so the readable
    name is kept and a counter is added rather than a token replacing it.
    """
    stem, suffix = name.rsplit(".", 1)
    path = directory / name
    attempt = 2
    while path.exists():
        path = directory / f"{stem}-{attempt}.{suffix}"
        attempt += 1
    return path


def sweep(directory: Path, lifetime_ms: int = DRAG_LIFETIME_MS, now: float | None = None) -> int:
    """Delete drag files older than the lifetime.  Returns how many went.

    The backstop, not the ordinary path: a timer removes each file when its
    time is up, and this is what collects the ones whose timer died with the
    process.  Only the names this module writes are touched — the directory is
    shared with the browser launcher, which looks after its own.
    """
    if not directory.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - lifetime_ms / 1000
    gone = 0
    for path in directory.glob("circle-to-search-*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                gone += 1
        except OSError as exc:  # pragma: no cover - a racing sweep, or a stale mount
            log.debug("could not sweep %s: %s", path, exc)
    if gone:
        log.info("swept %d dragged-out file(s) that outlived their timers", gone)
    return gone


def write_drag_file(payload: bytes, directory: Path | None = None) -> Path | None:
    """Write the PNG the drop will point at, and return where it went.

    ``None`` when it could not be written — a read-only home, a full disk — in
    which case the drag still carries the bytes and only the file half is lost.
    """
    if not payload:
        return None
    if directory is None:
        directory = cache_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        sweep(directory)
        path = _free_path(directory, drag_filename())
        path.write_bytes(payload)
        path.chmod(0o600)
    except OSError as exc:
        log.warning("could not write the file for the drag: %s", exc)
        return None
    log.info("wrote %s (%d KiB) for a drag", path, len(payload) // 1024)
    return path


def drag_payload(pixmap: QPixmap, path: Path | None, payload: bytes | None = None) -> QMimeData:
    """The crop as both a file and a picture, for whoever catches it."""
    if payload is None:
        payload = png_bytes(pixmap)
    mime = QMimeData()
    if path is not None:
        mime.setUrls([QUrl.fromLocalFile(str(path))])
    if payload:
        mime.setData(PNG_FORMAT, payload)
    # Last, and unconditional: Qt's own flavour is the one a Qt target reads
    # without decoding anything, and it is the only half left if the encode
    # above failed.
    mime.setImageData(pixmap.toImage())
    return mime


def remove_drag_file(path: Path | None) -> None:
    """Take a dragged-out file back off the disk."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
        log.debug("removed %s", path)
    except OSError as exc:
        log.debug("could not remove %s: %s", path, exc)
