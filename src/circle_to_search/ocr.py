"""Optional text recognition, through the ``tesseract`` binary.

This is deliberately the one part of the application that is allowed to be
missing.  Everything else works without it, it is **off until the user turns it
on**, and it adds no Python dependency at all: the work is done by calling
``tesseract``, which Fedora packages, rather than by pulling in a machine
learning stack that would dwarf the rest of the program.

Nothing here talks to a network.  The image never leaves the machine, which is
the point of having a local option next to "upload it to Google".
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
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

#: Recognition of a screen-sized region takes well under a second; anything
#: beyond this means something is wrong, not slow.
_TIMEOUT_S = 60


class OcrError(Exception):
    """Recognition could not be run, or produced nothing usable."""


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


def recognise(image: Image.Image, languages: str = _FALLBACK) -> str:
    """Return the text in ``image``.  Raises :class:`OcrError` when it cannot.

    Call this off the GUI thread: tesseract takes a noticeable fraction of a
    second even on a small crop.
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

        command = [binary, str(source), "stdout"]
        if languages:
            command += ["-l", languages]
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

    text = clean(result.stdout)
    if not text:
        raise OcrError("no text was found in the selection")
    return text


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
