"""Optional text recognition, through the ``tesseract`` binary.

This is deliberately the one part of the application that is allowed to be
missing.  Everything else works without it, it is **off until the user turns it
on**, and it adds no Python dependency at all: the work is done by calling
``tesseract``, which Fedora packages, rather than by pulling in a machine
learning stack that would dwarf the rest of the program.

Two ways to ask:

* :func:`recognise` returns the text and nothing else — used for a crop that is
  already on disk, where there is nothing to point at.
* :func:`recognise_words` returns every word **with the box it sits in**, which
  is what lets the overlay put a text layer over the frozen screen so a sentence
  can be dragged across and copied instead of the whole page arriving in a
  notification.

Nothing here talks to a network.  The image never leaves the machine, which is
the point of having a local option next to "upload it to Google".
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .logging_setup import get_logger

log = get_logger("ocr")

BINARY = "tesseract"

#: What to tell the user when the binary is not there.
INSTALL_HINT = "sudo dnf install tesseract tesseract-langpack-ukr tesseract-langpack-eng"

#: UI language → the tesseract language pack that goes with it.
_LANGUAGE_PACKS = {
    "uk": "ukr",
    "en": "eng",
}

#: Always worth adding: most screens have some English on them.
_FALLBACK = "eng"

#: Recognition of a whole 4K screen takes a few seconds; anything beyond this
#: means something is wrong, not slow.
_TIMEOUT_S = 60

#: Words tesseract is this unsure about are usually noise from a gradient or an
#: icon, and a text layer full of those is worse than a smaller one.
MIN_CONFIDENCE = 40.0

#: LSTM only (skips the slower legacy engine) and no second pass over an
#: inverted copy.  Both are pure speed on screenshots, which are already
#: dark-on-light or light-on-dark and never both.
_SPEED = ["--oem", "1", "-c", "tessedit_do_invert=0"]


class OcrError(Exception):
    """Recognition could not be run, or produced nothing usable."""


@dataclass(frozen=True)
class Word:
    """One recognised word and the box it occupies, in **physical** pixels.

    ``order`` is tesseract's own (block, paragraph, line, word) numbering, which
    is reading order — that is what makes "from this word to that one" mean the
    same thing as dragging across a paragraph.
    """

    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float
    order: tuple[int, int, int, int]

    @property
    def line(self) -> tuple[int, int, int]:
        """Everything but the word number: identifies the line it is on."""
        return self.order[:3]

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


def binary_path() -> str | None:
    """Where ``tesseract`` is, or ``None``."""
    return shutil.which(BINARY)


def is_available() -> bool:
    return binary_path() is not None


def installed_languages() -> list[str]:
    """The language packs tesseract can see.  Empty when it cannot be asked."""
    binary = binary_path()
    if binary is None:
        return []
    try:
        result = subprocess.run(
            [binary, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not list tesseract languages: %s", exc)
        return []
    # The first line is a header ("List of available languages (4):").
    lines = [line.strip() for line in result.stdout.splitlines()]
    return [line for line in lines if line and " " not in line and ":" not in line]


def pick_languages(ui_language: str, configured: str = "") -> str:
    """Choose the ``-l`` argument: what is configured, or what fits the UI.

    Only packs that are actually installed are asked for — naming a missing one
    makes tesseract fail outright instead of doing its best.
    """
    available = installed_languages()
    if configured:
        wanted = [part for part in configured.split("+") if part]
        usable = [part for part in wanted if not available or part in available]
        if usable:
            return "+".join(usable)
        log.warning("none of the configured languages (%s) are installed", configured)

    order = []
    pack = _LANGUAGE_PACKS.get(ui_language)
    if pack:
        order.append(pack)
    if _FALLBACK not in order:
        order.append(_FALLBACK)
    usable = [pack for pack in order if not available or pack in available]
    if usable:
        return "+".join(usable)
    # Nothing familiar is installed: use whatever there is rather than refuse.
    return available[0] if available else _FALLBACK


def _run(image: Image.Image, languages: str, extra: list[str]) -> str:
    """Run tesseract over ``image`` and return its standard output.

    Call this off the GUI thread: a whole 4K screen takes a few seconds.
    """
    binary = binary_path()
    if binary is None:
        raise OcrError(f"{BINARY} is not installed")

    with tempfile.TemporaryDirectory(prefix="circle-to-search-ocr-") as tmp:
        source = Path(tmp) / "selection.png"
        try:
            # PNG, not JPEG: recognition rates drop noticeably on text that has
            # been through a lossy encoder.
            image.save(source, "PNG")
        except (OSError, ValueError) as exc:
            raise OcrError(f"cannot write the image for recognition: {exc}") from exc

        command = [binary, str(source), "stdout", *_SPEED]
        if languages:
            command += ["-l", languages]
        command += extra
        log.debug("running %s", " ".join(command))
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError(f"{BINARY} did not finish within {_TIMEOUT_S} s") from exc
        except OSError as exc:
            raise OcrError(str(exc)) from exc

    if result.returncode != 0:
        message = result.stderr.strip().splitlines()
        raise OcrError(message[-1] if message else f"{BINARY} exited with {result.returncode}")
    return result.stdout


def recognise(image: Image.Image, languages: str = _FALLBACK) -> str:
    """Return the text in ``image``.  Raises :class:`OcrError` when it cannot."""
    text = clean(_run(image, languages, []))
    if not text:
        raise OcrError("no text was found in the selection")
    return text


def recognise_words(image: Image.Image, languages: str = _FALLBACK) -> list[Word]:
    """Every word in ``image``, with the box it sits in.

    Returns an empty list when the image simply has no text on it — that is an
    ordinary outcome for a photograph, not an error.  Only a *failure* to run
    raises.
    """
    return parse_tsv(_run(image, languages, ["tsv"]))


def parse_tsv(raw: str) -> list[Word]:
    """Turn tesseract's TSV into words, in reading order.

    Its columns are level, page, block, paragraph, line, word, left, top,
    width, height, conf, text.  Only level 5 rows are words; the rest describe
    the boxes those words are grouped into and carry no text.  Rows that cannot
    be parsed are skipped rather than raising: a text layer with a hole in it is
    still worth having.
    """
    words: list[Word] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        text = parts[11].strip()
        if not text:
            continue
        try:
            block, paragraph, line_no, word_no = (int(parts[index]) for index in (2, 3, 4, 5))
            left, top, width, height = (int(parts[index]) for index in (6, 7, 8, 9))
            confidence = float(parts[10])
        except ValueError:
            continue
        if confidence < MIN_CONFIDENCE or width < 1 or height < 1:
            continue
        words.append(
            Word(
                text=text,
                left=left,
                top=top,
                width=width,
                height=height,
                confidence=confidence,
                order=(block, paragraph, line_no, word_no),
            )
        )
    words.sort(key=lambda word: word.order)
    return words


def words_to_text(words: list[Word]) -> str:
    """Join words back into text, one line per recognised line.

    A blank line between paragraphs, because that is what the layout said and
    pasting a wall of text loses it.
    """
    pieces: list[str] = []
    previous: tuple[int, int, int] | None = None
    for word in words:
        if previous is None:
            pieces.append(word.text)
        elif word.line == previous:
            pieces.append(" " + word.text)
        elif word.line[:2] == previous[:2]:
            pieces.append("\n" + word.text)
        else:
            pieces.append("\n\n" + word.text)
        previous = word.line
    return "".join(pieces).strip()


def words_in(words: list[Word], left: int, top: int, right: int, bottom: int) -> list[Word]:
    """The words whose *middle* falls inside a box, in reading order.

    The middle rather than the whole box: a word clipped by the edge of the
    selection belongs to whichever side most of it is on, which is what a person
    drawing the rectangle meant.
    """
    inside = []
    for word in words:
        centre_x = word.left + word.width // 2
        centre_y = word.top + word.height // 2
        if left <= centre_x <= right and top <= centre_y <= bottom:
            inside.append(word)
    return inside


def clean(raw: str) -> str:
    """Tidy tesseract's output: it pads with blank lines and trailing spaces."""
    lines = [line.rstrip() for line in raw.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Collapse runs of blank lines; tesseract emits one per layout gap.
    tidied: list[str] = []
    for line in lines:
        if not line.strip() and (not tidied or not tidied[-1].strip()):
            continue
        tidied.append(line)
    return "\n".join(tidied).strip()


def summarise(text: str, limit: int = 160) -> str:
    """A one-glance version of the text, for a notification body."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"
