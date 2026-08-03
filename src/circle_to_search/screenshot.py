"""Screen capture with three back ends and an automatic fallback chain.

Order of preference:

1. ``org.kde.KWin.ScreenShot2`` — the native KWin API.  Fastest, no user
   interaction, captures exactly one output at its native resolution.  KWin
   only allows callers whose ``.desktop`` file declares
   ``X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2``; the file we
   install does, and its ``Exec=`` line starts with the *resolved* interpreter
   path (``/usr/bin/python3.13``) because KWin matches the caller by
   ``/proc/<pid>/exe``.  If the check fails we simply fall through.
2. ``spectacle -b -n -m -o FILE`` — Spectacle is part of every Plasma install.
3. ``org.freedesktop.portal.Screenshot`` — works everywhere but may show a
   confirmation dialog, so it is the last resort.

Everything returns a Pillow image in physical (device) pixels for exactly one
screen.  Back ends 2 and 3 may hand back the whole desktop; in that case the
image is cropped to the target output here.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from urllib.parse import unquote, urlparse

from PIL import Image
from PyQt6.QtCore import QEventLoop, QObject, QRect, QTimer, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusUnixFileDescriptor
from PyQt6.QtGui import QImage, QScreen

from .logging_setup import get_logger

log = get_logger("screenshot")

KWIN_SCREENSHOT_SERVICE = "org.kde.KWin.ScreenShot2"
KWIN_SCREENSHOT_PATH = "/org/kde/KWin/ScreenShot2"
KWIN_SCREENSHOT_INTERFACE = "org.kde.KWin.ScreenShot2"

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SCREENSHOT_INTERFACE = "org.freedesktop.portal.Screenshot"
PORTAL_REQUEST_INTERFACE = "org.freedesktop.portal.Request"

#: ``QImage::Format`` enum value → (Pillow mode, Pillow raw mode).  KWin sends
#: whatever format its compositing back end produced, so all the plausible
#: 8-bit-per-channel layouts are handled.
_QIMAGE_FORMATS: dict[int, tuple[str, str]] = {
    4: ("RGBX", "BGRX"),  # Format_RGB32 (0xffRRGGBB, little endian → B,G,R,X)
    5: ("RGBA", "BGRA"),  # Format_ARGB32
    6: ("RGBA", "BGRA"),  # Format_ARGB32_Premultiplied
    13: ("RGB", "RGB"),  # Format_RGB888
    16: ("RGBX", "RGBX"),  # Format_RGBX8888
    17: ("RGBA", "RGBA"),  # Format_RGBA8888
    18: ("RGBA", "RGBA"),  # Format_RGBA8888_Premultiplied
    29: ("RGB", "BGR"),  # Format_BGR888
}


class CaptureError(Exception):
    """No back end could produce an image."""


@dataclass
class Capture:
    """A screenshot of a single output."""

    image: Image.Image
    backend: str
    screen_name: str

    @property
    def size(self) -> tuple[int, int]:
        return self.image.width, self.image.height


def capture_screen(screen: QScreen, screen_name: str) -> Capture:
    """Capture ``screen``, trying every back end in order."""
    errors: list[str] = []
    for name, backend in (
        ("kwin-screenshot2", _capture_kwin),
        ("spectacle", _capture_spectacle),
        ("portal", _capture_portal),
    ):
        started = time.monotonic()
        try:
            image = backend(screen, screen_name)
        except Exception as exc:
            log.info("capture back end %s unavailable: %s", name, exc)
            errors.append(f"{name}: {exc}")
            continue
        elapsed = (time.monotonic() - started) * 1000.0
        log.info(
            "captured %dx%d px via %s in %.0f ms",
            image.width,
            image.height,
            name,
            elapsed,
        )
        return Capture(image=image, backend=name, screen_name=screen_name)
    raise CaptureError("; ".join(errors) if errors else "no capture back end available")


# --------------------------------------------------------------------------- #
# Back end 1: org.kde.KWin.ScreenShot2
# --------------------------------------------------------------------------- #


class _PipeReader(threading.Thread):
    """Drain a pipe in the background.

    KWin writes the raw image into the pipe *before* it answers the D-Bus call,
    and a pipe only buffers 64 KiB, so a 4K frame (≈33 MiB) deadlocks unless
    somebody is reading while the call is in flight.
    """

    def __init__(self, fd: int) -> None:
        super().__init__(daemon=True, name="cts-screenshot-pipe")
        self._fd = fd
        self._chunks: list[bytes] = []
        self.total = 0
        self.error: OSError | None = None

    def run(self) -> None:
        try:
            while True:
                chunk = os.read(self._fd, 1 << 20)
                if not chunk:
                    break
                self._chunks.append(chunk)
                self.total += len(chunk)
        except OSError as exc:
            self.error = exc
        finally:
            with contextlib.suppress(OSError):
                os.close(self._fd)

    def data(self) -> bytes:
        return b"".join(self._chunks)


def _capture_kwin(screen: QScreen, screen_name: str) -> Image.Image:
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise CaptureError("no session bus")

    interface = QDBusInterface(
        KWIN_SCREENSHOT_SERVICE,
        KWIN_SCREENSHOT_PATH,
        KWIN_SCREENSHOT_INTERFACE,
        bus,
    )
    if not interface.isValid():
        raise CaptureError("org.kde.KWin.ScreenShot2 is not available")
    interface.setTimeout(15000)

    read_fd, write_fd = os.pipe()
    # QDBusUnixFileDescriptor duplicates the descriptor, so our own copy of the
    # write end must be closed immediately — otherwise the reader never sees EOF.
    qt_fd: QDBusUnixFileDescriptor | None = QDBusUnixFileDescriptor(write_fd)
    os.close(write_fd)

    reader = _PipeReader(read_fd)
    reader.start()

    try:
        options = {"native-resolution": True, "include-cursor": False}
        reply = interface.call("CaptureScreen", screen_name, options, qt_fd)

        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            raise CaptureError(f"{reply.errorName() or 'error'}: {reply.errorMessage()}")

        arguments = reply.arguments()
        if not arguments or not isinstance(arguments[0], dict):
            raise CaptureError("unexpected reply from ScreenShot2")

        results = {key: _unwrap(value) for key, value in arguments[0].items()}
        log.debug("ScreenShot2 metadata: %s", results)

        try:
            width = int(results["width"])
            height = int(results["height"])
            stride = int(results["stride"])
            image_format = int(results["format"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CaptureError(f"incomplete ScreenShot2 metadata: {exc}") from exc

        # KWin answers only after it has written the frame, but wait for the
        # bytes to actually arrive in our buffer before releasing the write end.
        expected = stride * height
        deadline = time.monotonic() + 10.0
        while reader.total < expected and reader.is_alive() and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        # Dropping the last reference closes our duplicate of the write end,
        # which is what lets the reader thread see EOF and finish.
        qt_fd = None
        reader.join(timeout=2.0)

    if reader.error is not None:
        raise CaptureError(f"reading the screenshot pipe failed: {reader.error}")

    payload = reader.data()
    if len(payload) < expected:
        raise CaptureError(f"short read: got {len(payload)} of {expected} bytes")

    image = _decode_raw(payload, width, height, stride, image_format)
    if image.size != (width, height):  # pragma: no cover - defensive
        raise CaptureError("decoded image has the wrong size")
    return _sanity_crop(image, screen)


def _unwrap(value: object) -> object:
    """Unwrap ``QDBusVariant``-style values into plain Python objects."""
    variant = getattr(value, "variant", None)
    if callable(variant):
        return variant()
    return value


def _decode_raw(
    data: bytes, width: int, height: int, stride: int, image_format: int
) -> Image.Image:
    """Turn KWin's raw buffer into an RGB Pillow image."""
    try:
        mode, raw_mode = _QIMAGE_FORMATS[image_format]
    except KeyError as exc:
        raise CaptureError(f"unsupported QImage format {image_format}") from exc

    image = Image.frombuffer(mode, (width, height), data, "raw", raw_mode, stride, 1)
    return image.convert("RGB")


# --------------------------------------------------------------------------- #
# Back end 2: spectacle
# --------------------------------------------------------------------------- #


def _capture_spectacle(screen: QScreen, screen_name: str) -> Image.Image:
    binary = shutil.which("spectacle")
    if binary is None:
        raise CaptureError("spectacle is not installed")

    with tempfile.TemporaryDirectory(prefix="circle-to-search-") as tmp:
        target = Path(tmp) / "shot.png"
        # -b background (no GUI), -n no notification, -m current monitor
        # (the monitor the pointer is on, which is exactly the one we want).
        _run_spectacle([binary, "-b", "-n", "-m", "-o", str(target)], target)
        image = Image.open(target)
        image.load()

    image = image.convert("RGB")
    expected = _expected_physical_size(screen)
    if _sizes_differ(image.size, expected):
        log.debug(
            "spectacle -m returned %sx%s, expected ~%sx%s — retrying with a full capture",
            image.width,
            image.height,
            expected[0],
            expected[1],
        )
        image = _capture_spectacle_full(binary, screen)
    log.debug("spectacle captured %s for %s", image.size, screen_name)
    return image


def _capture_spectacle_full(binary: str, screen: QScreen) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="circle-to-search-") as tmp:
        target = Path(tmp) / "shot.png"
        _run_spectacle([binary, "-b", "-n", "-f", "-o", str(target)], target)
        image = Image.open(target)
        image.load()
    return _crop_desktop_to_screen(image.convert("RGB"), screen)


def _run_spectacle(command: list[str], target: Path) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureError(f"spectacle failed: {exc}") from exc
    # Spectacle exits 0 even for some failures, so the file is the real test.
    deadline = time.monotonic() + 5.0
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not target.exists() or target.stat().st_size == 0:
        raise CaptureError(f"spectacle produced no file ({result.stderr.strip() or 'no output'})")


# --------------------------------------------------------------------------- #
# Back end 3: xdg-desktop-portal
# --------------------------------------------------------------------------- #


class _PortalResponse(QObject):
    """Receives the ``Response`` signal of a portal request."""

    def __init__(self) -> None:
        super().__init__()
        self.code: int | None = None
        self.results: dict[str, object] = {}
        self.loop = QEventLoop()

    @pyqtSlot(QDBusMessage)
    def on_response(self, message: QDBusMessage) -> None:
        arguments = message.arguments()
        if arguments:
            try:
                self.code = int(arguments[0])
            except (TypeError, ValueError):
                self.code = 2
        if len(arguments) > 1 and isinstance(arguments[1], dict):
            self.results = {key: _unwrap(value) for key, value in arguments[1].items()}
        self.loop.quit()


def _capture_portal(screen: QScreen, screen_name: str) -> Image.Image:
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise CaptureError("no session bus")

    token = f"cts_{token_hex(8)}"
    sender = bus.baseService().removeprefix(":").replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    receiver = _PortalResponse()
    connected = bus.connect(
        PORTAL_SERVICE,
        request_path,
        PORTAL_REQUEST_INTERFACE,
        "Response",
        receiver.on_response,
    )
    if not connected:
        raise CaptureError("cannot subscribe to the portal Response signal")

    try:
        interface = QDBusInterface(
            PORTAL_SERVICE, PORTAL_PATH, PORTAL_SCREENSHOT_INTERFACE, bus
        )
        if not interface.isValid():
            raise CaptureError("xdg-desktop-portal is not available")
        interface.setTimeout(30000)
        reply = interface.call(
            "Screenshot",
            "",
            {"handle_token": token, "interactive": False, "modal": False},
        )
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            raise CaptureError(f"{reply.errorName() or 'error'}: {reply.errorMessage()}")

        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(receiver.loop.quit)
        timeout.start(30000)
        receiver.loop.exec()
        timeout.stop()
    finally:
        bus.disconnect(
            PORTAL_SERVICE,
            request_path,
            PORTAL_REQUEST_INTERFACE,
            "Response",
            receiver.on_response,
        )

    if receiver.code is None:
        raise CaptureError("the portal did not answer in time")
    if receiver.code != 0:
        raise CaptureError(f"the portal request was cancelled (code {receiver.code})")

    uri = str(receiver.results.get("uri", ""))
    if not uri:
        raise CaptureError("the portal returned no image URI")

    path = Path(unquote(urlparse(uri).path))
    if not path.is_file():
        raise CaptureError(f"the portal image is missing: {path}")

    try:
        image = Image.open(path)
        image.load()
        image = image.convert("RGB")
    finally:
        _remove_temporary(path)

    log.debug("portal captured %s for %s", image.size, screen_name)
    return _crop_desktop_to_screen(image, screen)


def _remove_temporary(path: Path) -> None:
    """Delete the portal's file, but only from locations that are clearly temporary."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    temporary_roots = [Path(tempfile.gettempdir())]
    if runtime_dir:
        temporary_roots.append(Path(runtime_dir))
    temporary_roots.append(Path.home() / ".cache")
    for root in temporary_roots:
        if path.is_relative_to(root):
            try:
                path.unlink()
            except OSError as exc:
                log.debug("could not remove %s: %s", path, exc)
            return


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _expected_physical_size(screen: QScreen) -> tuple[int, int]:
    geometry = screen.geometry()
    ratio = screen.devicePixelRatio() or 1.0
    return round(geometry.width() * ratio), round(geometry.height() * ratio)


def _sizes_differ(actual: tuple[int, int], expected: tuple[int, int], tolerance: int = 4) -> bool:
    return (
        abs(actual[0] - expected[0]) > tolerance or abs(actual[1] - expected[1]) > tolerance
    )


def _virtual_geometry(screen: QScreen) -> QRect:
    """Logical bounding box of every screen (the whole desktop)."""
    union = QRect()
    for candidate in screen.virtualSiblings() or [screen]:
        union = union.united(candidate.geometry())
    return union if not union.isEmpty() else screen.geometry()


def _aspect_error(size: tuple[int, int], rect: QRect) -> float:
    """How far the image's aspect ratio is from the rectangle's, relatively."""
    if size[1] <= 0 or rect.height() <= 0 or rect.width() <= 0:
        return float("inf")
    wanted = rect.width() / rect.height()
    return abs(size[0] / size[1] - wanted) / wanted


def _sanity_crop(image: Image.Image, screen: QScreen) -> Image.Image:
    """If a back end handed back more than one screen, cut out ours.

    The decision is made on the *aspect ratio*, not the pixel size: with
    fractional scaling ``QScreen.devicePixelRatio()`` can be rounded (a 150 %
    screen reported as 2.0), so a perfectly good single-screen capture may be
    half the "expected" size.  Cropping it as if it were the whole desktop would
    then destroy a correct image, which is much worse than doing nothing.
    """
    union = _virtual_geometry(screen)
    if union == screen.geometry():
        return image  # only one screen: there is nothing else in the picture
    if _aspect_error(image.size, union) < _aspect_error(image.size, screen.geometry()):
        return _crop_desktop_to_screen(image, screen)
    return image


def _crop_desktop_to_screen(image: Image.Image, screen: QScreen) -> Image.Image:
    """Crop a whole-desktop screenshot down to one output.

    The desktop image covers the union of all screens.  The ratio between its
    pixel size and the logical size of that union gives the scale that was used
    when it was composed; with mixed-DPI monitors this is an approximation, but
    the ScreenShot2 back end (which never needs this) is the normal path.
    """
    union = _virtual_geometry(screen)
    scale_x = image.width / max(1, union.width())
    scale_y = image.height / max(1, union.height())

    geometry = screen.geometry()
    left = round((geometry.x() - union.x()) * scale_x)
    top = round((geometry.y() - union.y()) * scale_y)
    right = round((geometry.x() - union.x() + geometry.width()) * scale_x)
    bottom = round((geometry.y() - union.y() + geometry.height()) * scale_y)

    left = max(0, min(left, image.width))
    top = max(0, min(top, image.height))
    right = max(left + 1, min(right, image.width))
    bottom = max(top + 1, min(bottom, image.height))

    if (left, top, right, bottom) == (0, 0, image.width, image.height):
        return image
    log.debug("cropping desktop image %s to %s", image.size, (left, top, right, bottom))
    return image.crop((left, top, right, bottom))


def pil_to_qimage(image: Image.Image) -> QImage:
    """Convert a Pillow image into a standalone ``QImage`` (owns its memory)."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    payload = rgb.tobytes("raw", "RGB")
    qimage = QImage(
        payload,
        rgb.width,
        rgb.height,
        rgb.width * 3,
        QImage.Format.Format_RGB888,
    )
    # copy() detaches from the Python buffer, which is about to be garbage
    # collected; without it the QImage would point at freed memory.
    return qimage.copy()
