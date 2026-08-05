"""Selecting across more than one monitor.

The overlay is a plain full-screen window, and on Wayland a window belongs to one
output — there is no such thing as a surface stretched across two.  So "all
screens" means one overlay per screen, each holding its own screenshot, plus the
arithmetic in this module to make them behave like a single surface:

* every selection is expressed in **global logical pixels**, the coordinate space
  KWin lays the outputs out in, so the overlays can agree about one rectangle;
* :class:`VirtualDesktop` stitches the crop back out of the individual
  screenshots afterwards, resizing the parts that came from a screen with a
  different scale factor.

Whether a drag can actually *cross* an edge is up to the compositor: Wayland
gives the surface where the button went down an implicit grab, so KWin keeps
sending it pointer motion with surface-local coordinates that run negative or
past the edge, and those map straight back to global ones.  If a compositor
declines to do that, the drag simply stops at the edge of the screen it started
on and everything else here still works — which is why this is safe to ship as
an option rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PyQt6.QtCore import QObject, QPoint, QRect, pyqtSignal, pyqtSlot

from .hidpi import ScreenMetrics
from .logging_setup import get_logger
from .overlay import SelectionOverlay

log = get_logger("multiscreen")

#: Never build an image larger than this many pixels on a side.  A three-monitor
#: desktop at 200 % is 11520 px wide, and a full-desktop selection at that size
#: is nobody's intent — but it must not become a gigabyte of RAM either.
MAX_COMPOSED_SIDE = 16000


@dataclass(frozen=True)
class ScreenShot:
    """One screen's capture, with everything needed to place it."""

    metrics: ScreenMetrics
    image: Image.Image

    @property
    def name(self) -> str:
        return self.metrics.name

    @property
    def geometry(self) -> QRect:
        """Where this screen sits on the virtual desktop, in logical pixels."""
        return self.metrics.logical_geometry


class VirtualDesktop:
    """All the captured screens, and the crop that spans them."""

    def __init__(self, shots: list[ScreenShot]) -> None:
        if not shots:
            raise ValueError("a virtual desktop needs at least one screen")
        self._shots = list(shots)

    @property
    def shots(self) -> list[ScreenShot]:
        return list(self._shots)

    @property
    def bounds(self) -> QRect:
        """The union of every screen, in global logical pixels."""
        union = QRect(self._shots[0].geometry)
        for shot in self._shots[1:]:
            union = union.united(shot.geometry)
        return union

    def scale_for(self, rect: QRect) -> float:
        """Physical pixels per logical pixel for a crop covering ``rect``.

        The highest scale of the screens it touches: upscaling the coarser part
        keeps the sharp screen sharp, while the other way round would throw away
        half the detail of a 4K panel because a 1080p one is next to it.
        """
        scales = [
            shot.metrics.scale for shot in self._shots if shot.geometry.intersects(rect)
        ]
        return max(scales) if scales else 1.0

    def screen_at(self, point: QPoint) -> ScreenShot | None:
        for shot in self._shots:
            if shot.geometry.contains(point):
                return shot
        return None

    def compose(self, rect: QRect) -> Image.Image:
        """Cut ``rect`` (global logical) out of the desktop as one image.

        Areas the rectangle covers but no screen does — the gap in an L-shaped
        arrangement — come out black, which is what is actually there.
        """
        rect = rect.intersected(self.bounds)
        if rect.width() < 1 or rect.height() < 1:
            raise ValueError("the selection does not overlap any screen")

        scale = self.scale_for(rect)
        width = min(MAX_COMPOSED_SIDE, max(1, round(rect.width() * scale)))
        height = min(MAX_COMPOSED_SIDE, max(1, round(rect.height() * scale)))
        canvas = Image.new("RGB", (width, height), (0, 0, 0))

        for shot in self._shots:
            piece = shot.geometry.intersected(rect)
            if piece.width() < 1 or piece.height() < 1:
                continue

            # Where the piece is inside that screen's own screenshot.
            local = piece.translated(-shot.geometry.topLeft())
            source = (
                max(0, round(local.x() * shot.metrics.scale_x)),
                max(0, round(local.y() * shot.metrics.scale_y)),
                min(shot.image.width, round((local.x() + local.width()) * shot.metrics.scale_x)),
                min(shot.image.height, round((local.y() + local.height()) * shot.metrics.scale_y)),
            )
            if source[2] - source[0] < 1 or source[3] - source[1] < 1:
                continue
            part = shot.image.crop(source)

            # ...and where it belongs on the composed image.
            target_x = round((piece.x() - rect.x()) * scale)
            target_y = round((piece.y() - rect.y()) * scale)
            target_w = max(1, min(width - target_x, round(piece.width() * scale)))
            target_h = max(1, min(height - target_y, round(piece.height() * scale)))
            if target_w < 1 or target_h < 1:
                continue
            if part.size != (target_w, target_h):
                part = part.resize((target_w, target_h), Image.Resampling.LANCZOS)
            canvas.paste(part, (target_x, target_y))
            log.debug(
                "%s contributes %dx%d at %d,%d", shot.name, target_w, target_h, target_x, target_y
            )

        return canvas


class OverlayGroup(QObject):
    """One overlay per screen, behaving like a single surface.

    Whichever overlay the drag started on owns the selection; the others are
    told what it is and draw their share of it, so the rectangle looks
    continuous across the seam.  Exactly one of :attr:`committed` and
    :attr:`cancelled` is emitted, no matter which member produced it.
    """

    #: The selection in global logical pixels, plus what to do with it.
    committed = pyqtSignal(QRect, str)
    cancelled = pyqtSignal()

    def __init__(self, overlays: list[SelectionOverlay], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._overlays = list(overlays)
        self._done = False
        for overlay in self._overlays:
            overlay.preview_changed.connect(self._on_preview)
            overlay.committed.connect(self._on_committed)
            overlay.cancelled.connect(self._on_cancelled)
            overlay.set_deactivation_guard(self._nothing_is_active)

    @property
    def overlays(self) -> list[SelectionOverlay]:
        return list(self._overlays)

    def show(self) -> None:
        for overlay in self._overlays:
            overlay.show_on_screen()

    def _nothing_is_active(self) -> bool:
        """True when the user really did leave — not just crossed an edge."""
        return not any(overlay.isActiveWindow() for overlay in self._overlays)

    @pyqtSlot(QRect)
    def _on_preview(self, rect: QRect) -> None:
        source = self.sender()
        for overlay in self._overlays:
            if overlay is not source:
                overlay.set_preview(rect)

    @pyqtSlot(QRect, str)
    def _on_committed(self, rect: QRect, action: str) -> None:
        if self._done:
            return
        self._done = True
        self._close_others()
        log.info("group selection %dx%d at %d,%d (%s)",
                 rect.width(), rect.height(), rect.x(), rect.y(), action)
        self.committed.emit(rect, action)

    @pyqtSlot()
    def _on_cancelled(self) -> None:
        if self._done:
            return
        self._done = True
        self._close_others()
        self.cancelled.emit()

    def _close_others(self) -> None:
        """Take the rest of the group down with the one that finished.

        The one that finished may still be up, showing that its selection is on
        its way; that one takes itself down when the sending is under way.
        """
        for overlay in self._overlays:
            overlay.set_deactivation_guard(None)
            if not overlay.is_sending():
                overlay.close()

    def release(self) -> None:
        """Drop every overlay.  Safe after either outcome.

        An overlay still saying "sending" is left on screen and left alive: the
        caller has taken it over by then (see ``_hold_if_sending``), and closing
        it here would take the badge away the moment the selection left.
        """
        self._done = True
        for overlay in self._overlays:
            overlay.set_deactivation_guard(None)
            if overlay.is_sending():
                continue
            overlay.close()
            overlay.deleteLater()
        self._overlays = []
