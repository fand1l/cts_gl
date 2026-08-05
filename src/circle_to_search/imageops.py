"""Small image helpers shared by the overlay and the upload path."""

from __future__ import annotations

from PIL import Image, ImageDraw
from PyQt6.QtCore import QRect
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


def rects_to_crop_space(
    rects: list[QRect], crop: QRect, scale: float = 1.0
) -> list[tuple[int, int, int, int]]:
    """Absolute rectangles → ``(left, top, right, bottom)`` inside the crop.

    One function for both spaces the selection is ever described in.  On a
    single screen everything is already physical pixels and ``scale`` is 1; in a
    group the rectangles and the crop are global *logical* pixels and the
    composed image was built at ``scale`` physical pixels to each of them.
    """
    return [
        (
            round((rect.x() - crop.x()) * scale),
            round((rect.y() - crop.y()) * scale),
            round((rect.x() + rect.width() - crop.x()) * scale),
            round((rect.y() + rect.height() - crop.y()) * scale),
        )
        for rect in rects
    ]


def black_out(
    image: Image.Image,
    boxes: list[tuple[int, int, int, int]],
    fill: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Fill each ``(left, top, right, bottom)`` box solid.

    Baked into the pixels, not drawn on top of them: the whole point is that
    there is no version of this image with the covered part still in it, and
    the copy, the saved PNG, the kept capture and the JPEG the browser posts are
    all made from what this returns.

    Boxes are clipped to the image, and ones that fall outside it are dropped —
    the caller works in screen coordinates and the crop moved under them.
    """
    inside = [
        (max(0, left), max(0, top), min(image.width, right), min(image.height, bottom))
        for left, top, right, bottom in boxes
    ]
    inside = [box for box in inside if box[2] > box[0] and box[3] > box[1]]
    if not inside:
        return image

    rgb = image if image.mode == "RGB" else image.convert("RGB")
    # A copy either way: the source is the screenshot the overlay is still
    # showing, and painting on it would black out what is on screen too.
    painted = rgb.copy()
    draw = ImageDraw.Draw(painted)
    for left, top, right, bottom in inside:
        draw.rectangle((left, top, right - 1, bottom - 1), fill=fill)
    log.info("blacked out %d area(s) before anything left the machine", len(inside))
    return painted
