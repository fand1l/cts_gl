"""Logical ⇄ physical pixel arithmetic.

This is *the* module to look at when the cropped region does not match what was
selected on screen.  Three different coordinate spaces meet here:

1. **Global logical coordinates.**  What KWin gives us in ``Trigger(x, y, …)``
   and what ``QScreen.geometry()`` returns.  The origin is the top-left corner
   of the whole desktop; every screen occupies a rectangle in it.  On a 4K
   panel scaled to 200 % a screen is 1920×1080 *logical* px.

2. **Screen-local logical coordinates.**  What the overlay widget uses: (0, 0)
   is the top-left corner of the screen the overlay covers.  Obtained by
   subtracting ``QScreen.geometry().topLeft()``.

3. **Physical (device) pixels.**  What the screenshot actually contains — 3840×
   2160 for that same panel.  The crop has to happen here, otherwise a 200 %
   screen would give a region half the intended size in the top-left quadrant.

The scale factor is *measured*, not assumed: we divide the real screenshot size
by the logical screen size.  ``QScreen.devicePixelRatio()`` is only used as a
fallback because Qt may report a rounded value under fractional scaling (a
150 % screen can be reported as 2.0), and because ``org.kde.KWin.ScreenShot2``
can hand back a non-native-resolution image depending on the options it honours.
Measuring covers all of those cases with the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect

#: Anything outside this range means we are not looking at the screen we think
#: we are (e.g. a whole-desktop screenshot instead of a single output).
_MIN_SCALE = 0.25
_MAX_SCALE = 8.0


@dataclass(frozen=True)
class ScreenMetrics:
    """Everything needed to translate a selection into a crop box."""

    name: str
    #: Position and size of the screen in global logical coordinates.
    logical_geometry: QRect
    #: Size of the screenshot in physical pixels.
    physical_width: int
    physical_height: int
    #: Physical pixels per logical pixel, measured separately per axis.
    scale_x: float
    scale_y: float
    #: True when the scale had to be taken from Qt instead of being measured.
    estimated: bool = False

    @property
    def scale(self) -> float:
        """Mean of both axes — good enough for rendering decisions."""
        return (self.scale_x + self.scale_y) / 2.0

    def __str__(self) -> str:  # pragma: no cover - debug helper
        geo = self.logical_geometry
        return (
            f"{self.name}: logical {geo.width()}x{geo.height()}+{geo.x()}+{geo.y()}, "
            f"physical {self.physical_width}x{self.physical_height}, "
            f"scale {self.scale_x:.3f}x{self.scale_y:.3f}"
            f"{' (estimated)' if self.estimated else ''}"
        )


def measure_screen(
    name: str,
    logical_geometry: QRect,
    physical_size: tuple[int, int],
    fallback_dpr: float = 1.0,
) -> ScreenMetrics:
    """Derive the scale factors from a screenshot that was just taken.

    ``physical_size`` is the real pixel size of the captured image and
    ``logical_geometry`` the geometry Qt reports for the same screen.
    """
    logical_w = max(1, logical_geometry.width())
    logical_h = max(1, logical_geometry.height())
    physical_w, physical_h = physical_size
    if physical_w <= 0 or physical_h <= 0:
        raise ValueError(f"invalid screenshot size {physical_size!r}")

    scale_x = physical_w / logical_w
    scale_y = physical_h / logical_h
    estimated = False

    if not (_MIN_SCALE <= scale_x <= _MAX_SCALE) or not (_MIN_SCALE <= scale_y <= _MAX_SCALE):
        # The image is not a plain capture of this screen (wrong output, or a
        # whole-desktop image that the caller forgot to crop).  Fall back to
        # what Qt believes so that we at least stay self-consistent.
        scale_x = scale_y = max(fallback_dpr, 0.25)
        estimated = True

    return ScreenMetrics(
        name=name,
        logical_geometry=QRect(logical_geometry),
        physical_width=physical_w,
        physical_height=physical_h,
        scale_x=scale_x,
        scale_y=scale_y,
        estimated=estimated,
    )


def global_to_local(point: QPoint, logical_geometry: QRect) -> QPoint:
    """Global logical point → logical point relative to the screen."""
    return QPoint(point.x() - logical_geometry.x(), point.y() - logical_geometry.y())


def logical_rect_to_physical(rect: QRect, metrics: ScreenMetrics) -> QRect:
    """Screen-local logical rectangle → crop box in physical pixels.

    The rectangle is rounded outwards-consistently (left/top rounded, size
    derived from the rounded edges) and then clamped to the image, so the crop
    can never leave the screenshot even if the widget reports a size one pixel
    larger than the screen (which happens with fractional scaling).
    """
    left = round(rect.x() * metrics.scale_x)
    top = round(rect.y() * metrics.scale_y)
    right = round((rect.x() + rect.width()) * metrics.scale_x)
    bottom = round((rect.y() + rect.height()) * metrics.scale_y)

    left = max(0, min(left, metrics.physical_width))
    top = max(0, min(top, metrics.physical_height))
    right = max(left, min(right, metrics.physical_width))
    bottom = max(top, min(bottom, metrics.physical_height))

    return QRect(left, top, right - left, bottom - top)


def physical_rect_to_logical(rect: QRect, metrics: ScreenMetrics) -> QRect:
    """Inverse of :func:`logical_rect_to_physical` (used for drawing)."""
    left = round(rect.x() / metrics.scale_x)
    top = round(rect.y() / metrics.scale_y)
    right = round((rect.x() + rect.width()) / metrics.scale_x)
    bottom = round((rect.y() + rect.height()) / metrics.scale_y)
    return QRect(left, top, max(0, right - left), max(0, bottom - top))
