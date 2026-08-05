"""Small image helpers shared by the overlay and the upload path."""

from __future__ import annotations

from PIL import Image, ImageDraw
from PyQt6.QtGui import QImage, QPolygon

from .hidpi import ScreenMetrics
from .logging_setup import get_logger

log = get_logger("imageops")


def scaled_size(width: int, height: int, max_side: int = 1000) -> tuple[int, int]:
    """The size :func:`lens.prepare_image` will produce for a crop this big.

    Here rather than beside the resize it describes, so that the overlay can say
    what is really going to be sent without importing the upload path to find
    out — and so that what it says cannot drift away from what happens, because
    this *is* the arithmetic that happens.  Zero or a negative ``max_side``
    means "do not resize", which is how the setting switches resizing off.
    """
    longest = max(width, height)
    if max_side <= 0 or longest <= max_side:
        return max(1, width), max(1, height)
    factor = max_side / longest
    return max(1, round(width * factor)), max(1, round(height * factor))


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


def polygon_to_crop_space(
    polygon: QPolygon,
    crop_x: int,
    crop_y: int,
    metrics: ScreenMetrics,
) -> list[tuple[int, int]]:
    """Lasso outline (widget-logical px) → coordinates inside the cropped image.

    Two transformations at once: logical → physical pixels (the same scale the
    crop box was computed with), and global-to-crop by subtracting the crop's
    top-left corner.
    """
    return [
        (
            round(polygon.point(index).x() * metrics.scale_x) - crop_x,
            round(polygon.point(index).y() * metrics.scale_y) - crop_y,
        )
        for index in range(polygon.count())
    ]


def mask_outside_polygon(
    image: Image.Image,
    points: list[tuple[int, int]],
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Paint everything outside the lasso with ``background``.

    Lens is given a rectangle either way — this is what makes a lasso feel like
    a lasso instead of "the bounding box of a lasso": only what was circled
    survives, the rest becomes clean white that Lens ignores.
    """
    if len(points) < 3:
        return image

    rgb = image if image.mode == "RGB" else image.convert("RGB")
    mask = Image.new("L", rgb.size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    if not mask.getbbox():
        # Degenerate outline (all points on one line): keep the plain crop.
        log.debug("lasso mask is empty, using the bounding box instead")
        return rgb
    return Image.composite(rgb, Image.new("RGB", rgb.size, background), mask)
