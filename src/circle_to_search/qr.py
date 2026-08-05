"""Reading a QR code off the frozen screen, instead of uploading a picture of it.

Circling a QR code and sending it to Google is a network round trip for
something that decodes locally in about a millisecond — and it hands a picture
of the code to a third party on the way, which for a code that is a Wi-Fi
password or a payment link is worse than useless.

Same shape as :mod:`circle_to_search.ocr`, and for the same reasons: an outside
program called through a pipe rather than a Python dependency, optional, and
never a hard requirement.  ``zbarimg`` ships in every distribution this project
targets and pulls in nothing; the alternatives are a compiled Python binding
(`pyzbar`) or OpenCV, and neither is worth putting in ``requirements.txt`` for
one button.

Unlike text recognition this is **not** asked about before it is used.  There
is nothing to ask: it costs nothing, it uploads nothing, and what it does is
*prevent* a picture from going to Google.  It is on when the decoder is there
and there is a setting to turn it off, which is the other way round from OCR
because the trade is the other way round.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .logging_setup import get_logger

log = get_logger("qr")

BINARY = "zbarimg"

#: What to tell the user when the binary is not there, per package manager.  A
#: dnf command shown to somebody on Arch looks like an answer and is not one.
_INSTALL_HINTS = {
    "dnf": "sudo dnf install zbar",
    "apt": "sudo apt install zbar-tools",
    "pacman": "sudo pacman -S zbar",
    "zypper": "sudo zypper install zbar",
}

#: In the order they are looked for; apt-get rather than apt because the latter
#: is a front end that is not always installed.
_PACKAGE_MANAGERS = (("dnf", "dnf"), ("apt", "apt-get"), ("pacman", "pacman"), ("zypper", "zypper"))

#: A crop is small and zbar is fast.  Anything past this is not slow, it is
#: stuck, and the answer is wanted while the selection is still on screen.
_TIMEOUT_S = 10

#: zbarimg exits 4 when it read the image and found nothing in it, which is the
#: ordinary case here — most selections are not QR codes.
_NOTHING_FOUND = 4

#: Below this the code is a few pixels across and whatever came out of it is
#: more likely to be noise than a payload.
MIN_SIDE = 24


class QrError(Exception):
    """The decoder could not be run, or would not answer."""


@dataclass(frozen=True)
class Code:
    """One symbol, and where in the image it was found."""

    #: ``QR-Code``, ``EAN-13``, … — zbar's own name for the symbology.
    kind: str
    payload: str
    #: The corners zbar reported, in pixels of the image it was given, in the
    #: order it reported them.  Empty when the decoder is too old to say — the
    #: payload is still worth having, it just cannot be pointed at.
    points: tuple[tuple[int, int], ...] = ()

    @property
    def is_qr(self) -> bool:
        return self.kind.upper().startswith("QR")

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """``(left, top, width, height)`` around the corners, or all zeroes."""
        if not self.points:
            return (0, 0, 0, 0)
        xs = [x for x, _ in self.points]
        ys = [y for _, y in self.points]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    @property
    def located(self) -> bool:
        width, height = self.bounds[2], self.bounds[3]
        return width >= MIN_SIDE and height >= MIN_SIDE


def install_hint() -> str:
    """The command that would install the decoder on *this* machine."""
    for name, binary in _PACKAGE_MANAGERS:
        if shutil.which(binary):
            return _INSTALL_HINTS[name]
    return "install zbar (the zbarimg command)"


def binary_path() -> str | None:
    """Where ``zbarimg`` is, or ``None``."""
    return shutil.which(BINARY)


def is_available() -> bool:
    return binary_path() is not None


def _points(field: str) -> tuple[tuple[int, int], ...]:
    """``+68,+67 +68,+230 …`` — zbar's SVG-shaped polygon, or nothing.

    Returns an empty tuple for anything that is not one, which is how the
    parser tells ``TYPE:POLYGON:payload`` from a plain ``TYPE:payload``: the
    second field of the first is always a polygon and the second field of the
    second is the start of the data.
    """
    pairs = field.split()
    if len(pairs) < 3:
        # Three corners is the fewest that encloses anything.  Two would also
        # be a plausible misreading of a payload that happens to look numeric.
        return ()
    corners: list[tuple[int, int]] = []
    for pair in pairs:
        x, _, y = pair.partition(",")
        try:
            corners.append((int(x), int(y)))
        except ValueError:
            return ()
    return tuple(corners)


def parse(raw: str) -> list[Code]:
    """Turn zbarimg's output lines into codes.

    Two shapes, because ``--polygon`` is not in every zbar:

        QR-Code:https://example.com/
        QR-Code:+68,+67 +68,+230 +233,+232 +231,+67:https://example.com/

    The payload is whatever is left after the fields in front of it, taken in
    one piece: it is very often a URL and full of colons of its own, and
    splitting on all of them would hand back a hostname.
    """
    codes: list[Code] = []
    for line in raw.splitlines():
        kind, _, rest = line.partition(":")
        kind = kind.strip()
        if not kind or not rest:
            continue
        maybe, _, tail = rest.partition(":")
        corners = _points(maybe)
        payload = tail if corners else rest
        if not payload:
            continue
        codes.append(Code(kind=kind, payload=payload, points=corners))
    return codes


def decode(image: Image.Image) -> list[Code]:
    """Every symbol in ``image``, in the order zbar reports them.

    Call this off the GUI thread.  It is fast, but it is a process, and the
    thing it is holding up is a window drawn over the whole screen.
    """
    binary = binary_path()
    if binary is None:
        raise QrError(f"{BINARY} is not installed")
    if image.width < MIN_SIDE or image.height < MIN_SIDE:
        return []

    with tempfile.TemporaryDirectory(prefix="circle-to-search-qr-") as tmp:
        source = Path(tmp) / "selection.png"
        try:
            # PNG: a QR code is a grid of hard edges and JPEG ringing around
            # them is exactly what stops it decoding.
            image.save(source, "PNG")
        except (OSError, ValueError) as exc:
            raise QrError(f"cannot write the image for decoding: {exc}") from exc

        # --polygon is what makes the code point-at-able rather than just
        # readable; an older zbar without it still answers, with no corners.
        command = [binary, "-q", "--nodbus", "--polygon", str(source)]
        log.debug("running %s", " ".join(command))
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise QrError(f"{BINARY} did not finish within {_TIMEOUT_S} s") from exc
        except OSError as exc:
            raise QrError(str(exc)) from exc

    if result.returncode == _NOTHING_FOUND:
        # Not an error: most selections are not codes, and this is asked about
        # every one of them.
        return []
    if result.returncode != 0:
        message = result.stderr.strip().splitlines()
        raise QrError(message[-1] if message else f"{BINARY} exited with {result.returncode}")
    codes = parse(result.stdout)
    if codes:
        log.info("decoded %d symbol(s): %s", len(codes), ", ".join(code.kind for code in codes))
    return codes


def best(codes: list[Code]) -> Code | None:
    """The one worth offering, when there is more than one.

    A QR code beats a barcode: somebody who circled a shelf label with both on
    it meant the square one, and a 13-digit EAN is not something anybody wants
    opened.  Otherwise the first, which is zbar's own order.
    """
    for code in codes:
        if code.is_qr:
            return code
    return codes[0] if codes else None
