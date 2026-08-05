"""Offscreen tests for the parts that do not need a Plasma session.

Run with::

    python3 tests/test_logic.py

Covers the HiDPI crop arithmetic, the Lens request/redirect handling, the
raw ScreenShot2 buffer decoder, the translation table and the overlay's
selection geometry.  Qt runs on the "offscreen" platform, so no display is
needed.
"""

from __future__ import annotations

import itertools
import math
import os
import re
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])

import requests  # noqa: E402
from PIL import Image  # noqa: E402

from circle_to_search import hidpi, i18n, lens  # noqa: E402
from circle_to_search.imageops import (  # noqa: E402
    black_out,
    mask_outside_polygon,
    pil_to_qimage,
    polygon_to_crop_space,
    rects_to_crop_space,
)
from circle_to_search.overlay import (  # noqa: E402
    ACTION_PIN,
    MODE_LASSO,
    MODE_RECTANGLE,
    SelectionOverlay,
)
from circle_to_search.screenshot import _decode_raw  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name} {detail}")
    if not condition:
        failures.append(name)


# --- hidpi -----------------------------------------------------------------
# 4K panel at 200 %: logical 1920x1080, physical 3840x2160.
geo = QRect(0, 0, 1920, 1080)
m = hidpi.measure_screen("eDP-1", geo, (3840, 2160), fallback_dpr=2.0)
check("scale 200%", (m.scale_x, m.scale_y) == (2.0, 2.0), str(m))
crop = hidpi.logical_rect_to_physical(QRect(100, 50, 400, 300), m)
check("crop 200%", crop == QRect(200, 100, 800, 600), str(crop))

# 4K panel at 150 % (fractional): logical 2560x1440.
m15 = hidpi.measure_screen("eDP-1", QRect(0, 0, 2560, 1440), (3840, 2160), fallback_dpr=2.0)
check("scale 150%", abs(m15.scale_x - 1.5) < 1e-9, str(m15))
crop15 = hidpi.logical_rect_to_physical(QRect(10, 10, 100, 100), m15)
check("crop 150%", crop15 == QRect(15, 15, 150, 150), str(crop15))

# Second monitor at 1080p, offset to the right of the 4K one.
geo2 = QRect(1920, 0, 1920, 1080)
m2 = hidpi.measure_screen("HDMI-A-1", geo2, (1920, 1080), fallback_dpr=1.0)
check("scale 100%", m2.scale == 1.0, str(m2))
local = hidpi.global_to_local(QPoint(2000, 300), geo2)
check("global→local", local == QPoint(80, 300), str(local))

# Clamping: a selection that runs past the right edge must not leave the image.
clamped = hidpi.logical_rect_to_physical(QRect(1900, 1070, 100, 100), m)
check("clamped", clamped == QRect(3800, 2140, 40, 20), str(clamped))

# Nonsense sizes fall back to the reported DPR instead of exploding.
bad = hidpi.measure_screen("eDP-1", geo, (60, 40), fallback_dpr=2.0)
check("fallback dpr", bad.estimated and bad.scale_x == 2.0, str(bad))

# Round trip.
back = hidpi.physical_rect_to_logical(crop, m)
check("round trip", back == QRect(100, 50, 400, 300), str(back))

# --- lens ------------------------------------------------------------------
img = Image.new("RGB", (2400, 1200), (10, 120, 200))
prepared = lens.prepare_image(img, max_side=1000, quality=85)
check("resize", (prepared.width, prepared.height) == (1000, 500), str(prepared.dimensions))
check("jpeg magic", prepared.payload[:2] == b"\xff\xd8")
small = lens.prepare_image(Image.new("RGB", (50, 50)), max_side=1000)
check("no upscale", (small.width, small.height) == (50, 50))

# The real session must carry the consent cookie and a browser UA, otherwise
# Google answers EU clients with an interstitial instead of a result.
real_session = lens._session()
check("consent cookie", real_session.cookies.get("SOCS") == lens.CONSENT_COOKIES["SOCS"])
check("browser ua", "Chrome/" in real_session.headers["User-Agent"])


def _response(status: int, location: str = "", body: str = "", url: str = "https://x/") -> object:
    response = requests.Response()
    response.status_code = status
    response.url = url
    if location:
        response.headers["Location"] = location
    response._content = body.encode()
    return response


class _FakeSession:
    """Replays a scripted list of responses and records what was sent."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[tuple] = []

    def post(self, url, files=None, data=None, timeout=None, allow_redirects=None):
        self.calls.append(("post", url, files, data))
        return self.script.pop(0)

    def get(self, url, timeout=None, allow_redirects=None):
        self.calls.append(("get", url))
        return self.script.pop(0)


def with_session(script: list):
    fake = _FakeSession(script)
    lens._session = lambda: fake  # type: ignore[assignment]
    return fake


_real_session_factory = lens._session

# Happy path: one redirect straight to a Lens result page.
fake = with_session([_response(302, "https://lens.google.com/search?p=TOKEN")])
result = lens.upload_lens(prepared)
check("upload result", result == "https://lens.google.com/search?p=TOKEN", result)
method, posted_url, files, data = fake.calls[0]
check("upload url", posted_url.startswith("https://lens.google.com/v3/upload?ep=ccm&s=&st="),
      posted_url)
check("image part", files["encoded_image"][2] == "image/jpeg")
check("image bytes", files["encoded_image"][1][:2] == b"\xff\xd8")
# A plain form field, not a file part with an empty filename: sent the wrong
# way Google ignores the upload and the result page opens without a picture.
check("dims is a form field", data == {"processed_image_dimensions": "1000,500"}, str(data))

# The EU consent interstitial has to be unwrapped, not opened in the browser.
fake = with_session([
    _response(302, "https://consent.google.com/m?continue=https%3A%2F%2Flens.google.com"
                   "%2Fsearch%3Fp%3DTOK&gl=UA"),
])
check("consent unwrapped", lens.upload_lens(prepared) == "https://lens.google.com/search?p=TOK")

# 200 with the URL buried in the HTML.
with_session([_response(200, body='x <a href="https://lens.google.com/search?p=HTML">y</a>')])
check("html fallback", lens.upload_lens(prepared) == "https://lens.google.com/search?p=HTML")

# 200 with nothing usable must raise a descriptive error, not open a junk page.
with_session([_response(200, body="<html>sorry</html>")])
try:
    lens.upload_lens(prepared)
    check("junk raises", False)
except lens.LensError as exc:
    check("junk raises", "endpoint has most likely changed" in str(exc), str(exc)[:60])

# The exact answer a real Plasma 6 install got on 2026-08-03: the upload works
# (vsdim proves Google accepted the image) but the URL is bound to the uploading
# session, so the browser shows the Lens page with an empty image slot.
SESSION_BOUND = (
    "https://www.google.com/search?vsrid=CICYmeX6&vsint=CAIqDA&udm=26&lns_mode=un"
    "&source=lns.web.ccm&vsdim=1000,562&gsessionid=snYQ3ENZqCVs&lsessionid=KPaahsW9"
    "&lns_surface=26&hl=en"
)
check("session-bound detected", not lens.is_stateless_url(SESSION_BOUND))
check("stateless p= url", lens.is_stateless_url("https://lens.google.com/search?p=TOKEN"))
check("stateless sbi url", lens.is_stateless_url("https://www.google.com/search?tbs=sbi:X"))

# auto must not stop at a session-bound answer: it keeps trying and returns the
# first URL the browser can actually resolve on its own.
fake = with_session([
    _response(303, SESSION_BOUND),
    _response(303, SESSION_BOUND),
    _response(303, SESSION_BOUND),
    _response(303, SESSION_BOUND),
    _response(302, "https://www.google.com/search?tbs=sbi:GOOD"),
])
picked = lens.upload(prepared, backend=lens.BACKEND_AUTO)
check("skips session-bound", picked == "https://www.google.com/search?tbs=sbi:GOOD", picked)
check("tried every variant", len(fake.calls) == len(lens.VARIANTS), str(len(fake.calls)))

# When nothing is stateless the session-bound URL is still opened: a page with
# the Lens chrome beats a bare error message.
with_session([_response(303, SESSION_BOUND)] * len(lens.VARIANTS))
check("last resort", lens.upload(prepared, backend=lens.BACKEND_AUTO) == SESSION_BOUND)

# Pinning one variant by name must send exactly one request.
fake = with_session([_response(302, "https://lens.google.com/search?p=PINNED")])
check(
    "pinned variant",
    lens.upload(prepared, backend="lens-subb") == "https://lens.google.com/search?p=PINNED",
)
check("pinned url", "ep=subb" in fake.calls[0][1], fake.calls[0][1])
check("one call only", len(fake.calls) == 1)

# The probe reports every variant, working or not.
with_session([
    _response(303, SESSION_BOUND),
    _response(500),
    _response(302, "https://lens.google.com/search?p=OK"),
    _response(303, SESSION_BOUND),
    _response(302, "https://www.google.com/search?tbs=sbi:OK"),
])
rows = lens.probe(prepared)
check("probe rows", len(rows) == len(lens.VARIANTS), str(len(rows)))
check("probe marks failure", any(row.error for row in rows))
check("probe marks stateless", sum(row.stateless for row in rows) == 2,
      str([(r.variant, r.stateless) for r in rows]))
check("probe shape", rows[0].shape == "vsrid=…", rows[0].shape)

# auto: Lens fails, search-by-image takes over.
fake = with_session([
    _response(500),
    _response(500),
    _response(500),
    _response(500),
    _response(302, "https://www.google.com/search?tbs=sbi:FALLBACK"),
])
check(
    "auto falls back",
    lens.upload(prepared, backend=lens.BACKEND_AUTO)
    == "https://www.google.com/search?tbs=sbi:FALLBACK",
)
check(
    "fallback endpoint",
    fake.calls[-1][1].startswith(lens.SEARCH_BY_IMAGE_URL),
    str(fake.calls[-1][1]),
)

lens._session = _real_session_factory  # type: ignore[assignment]

html_body = '<meta http-equiv="refresh" content="0; url=https://lens.google.com/search?p=abc&amp;x=1">'
check(
    "extract meta",
    lens.extract_result_url(html_body) == "https://lens.google.com/search?p=abc&x=1",
    str(lens.extract_result_url(html_body)),
)
js_body = 'var a="https://lens.google.com/search?p=Zm9v\\u003d\\u003d&hl=en";'
check("extract js", (lens.extract_result_url(js_body) or "").endswith("&hl=en"),
      str(lens.extract_result_url(js_body)))
check("extract none", lens.extract_result_url("<html>nothing here</html>") is None)

# --- raw decode (KWin ScreenShot2 formats) ---------------------------------
w, h = 4, 2
# Format_ARGB32 (5) -> BGRA byte order on little endian, with padding in stride.
stride = w * 4 + 8
rows = []
for y in range(h):
    row = bytearray()
    for x in range(w):
        row += bytes((x * 10, y * 20, 200, 255))  # B, G, R, A
    row += b"\x00" * 8
    rows.append(bytes(row))
decoded = _decode_raw(b"".join(rows), w, h, stride, 5)
check("decode size", decoded.size == (w, h))
check("decode pixel", decoded.getpixel((2, 1)) == (200, 20, 20), str(decoded.getpixel((2, 1))))
check("decode rgb32", _decode_raw(b"".join(rows), w, h, stride, 4).size == (w, h))

qimg = pil_to_qimage(decoded)
check("pil→qimage", qimg.width() == w and not qimg.isNull())
check("qimage pixel", qimg.pixelColor(2, 1).getRgb()[:3] == (200, 20, 20))

# --- i18n ------------------------------------------------------------------
i18n.set_language("uk")
check("uk", i18n.tr("tray.capture") == "Зняти зараз", i18n.tr("tray.capture"))
i18n.set_language("en")
check("en", i18n.tr("tray.capture") == "Capture now")
i18n.set_language("klingon")
check("unknown lang", i18n.current_language() == "en")
check("missing key", i18n.tr("no.such.key") == "no.such.key")
check("format", "42" in i18n.tr("notify.lens_failed_body", error=42))

# --- lens redirect handling ------------------------------------------------
check("result url lens", lens.is_result_url("https://lens.google.com/search?p=abc"))
check("result url google", lens.is_result_url("https://www.google.com/search?tbs=sbi:xyz"))
check("result url consent", not lens.is_result_url("https://consent.google.com/m?continue=x"))
consent = "https://consent.google.com/m?continue=https%3A%2F%2Flens.google.com%2Fsearch%3Fp%3Dq"
check(
    "unwrap consent",
    lens.unwrap_interstitial(consent) == "https://lens.google.com/search?p=q",
    lens.unwrap_interstitial(consent),
)
plain = "https://lens.google.com/search?p=q"
check("unwrap passthrough", lens.unwrap_interstitial(plain) == plain)


# --- overlay ---------------------------------------------------------------
screen = app.primaryScreen()
shot = Image.new("RGB", (screen.geometry().width() * 2, screen.geometry().height() * 2), "green")
metrics = hidpi.measure_screen(screen.name(), screen.geometry(), shot.size, 2.0)


def make_overlay(mode: str, mask_outside: bool = False) -> SelectionOverlay:
    overlay = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(shot)),
        metrics,
        screen,
        dim_percent=40,
        mode=mode,
        mask_outside=mask_outside,
    )
    overlay.resize(screen.geometry().size())
    return overlay


# Nothing may be selected before the first press: this was a visible bug where
# a rectangle appeared anchored at the top-left corner as soon as the pointer
# moved over the freshly mapped overlay.
rect_overlay = make_overlay(MODE_RECTANGLE)
rect_overlay._current = QPoint(400, 300)  # pointer moved, no button pressed
check("no phantom selection", rect_overlay._selection_rect().isEmpty(),
      str(rect_overlay._selection_rect()))
rect_overlay.render(QPixmap(rect_overlay.size()))
check("hint paints", True)

rect_overlay._has_selection = True
rect_overlay._anchor = QPoint(100, 100)
rect_overlay._current = QPoint(300, 250)
check("rect selection", rect_overlay._selection_rect() == QRect(100, 100, 200, 150))
check("rect has no polygon", rect_overlay.selection_polygon().count() == 0)

got: list[tuple] = []
rect_overlay.selected.connect(lambda r, p: got.append((r, p)))
cancelled: list[bool] = []
rect_overlay.cancelled.connect(lambda: cancelled.append(True))
rect_overlay.render(QPixmap(rect_overlay.size()))
rect_overlay._finish(
    lambda: rect_overlay.selected.emit(
        hidpi.logical_rect_to_physical(rect_overlay._selection_rect(), metrics),
        rect_overlay.selection_polygon(),
    )
)
check("emitted once", len(got) == 1 and got[0][0] == QRect(200, 200, 400, 300), str(got))
check("no double cancel", not cancelled)

# Lasso: a diamond around (200, 200).
lasso = make_overlay(MODE_LASSO)
lasso._has_selection = True
lasso._drag_mode = MODE_LASSO
lasso._points = [QPoint(200, 100), QPoint(300, 200), QPoint(200, 300), QPoint(100, 200)]
lasso._current = QPoint(100, 200)
check("lasso bbox", lasso._selection_rect() == QRect(100, 100, 200, 200),
      str(lasso._selection_rect()))
check("lasso polygon", lasso.selection_polygon().count() == 4)
lasso.render(QPixmap(lasso.size()))
check("lasso paints", True)
check("lasso path closed", lasso._selection_path().elementCount() >= 4)

# The loop marks out the edges: what is un-dimmed — and therefore what will be
# uploaded — is the bounding box, not the shape.  With the optional mask on, the
# revealed area becomes the shape itself, so the preview never lies.
check(
    "reveal is the bbox",
    lasso._reveal_path().boundingRect().toRect() == QRect(100, 100, 200, 200),
    str(lasso._reveal_path().boundingRect()),
)
check("reveal is a rectangle", lasso._reveal_path().elementCount() == 5,
      str(lasso._reveal_path().elementCount()))

masking = make_overlay(MODE_LASSO, mask_outside=True)
masking._has_selection = True
masking._drag_mode = MODE_LASSO
masking._points = list(lasso._points)
masking._current = QPoint(100, 200)
check("mask mode reveals the shape",
      masking._reveal_path().elementCount() == lasso._selection_path().elementCount())
masking.render(QPixmap(masking.size()))
check("mask mode paints", True)

# --- lasso masking ---------------------------------------------------------
crop = hidpi.logical_rect_to_physical(lasso._selection_rect(), metrics)
points = polygon_to_crop_space(lasso.selection_polygon(), crop.x(), crop.y(), metrics)
check("crop space origin", points[0] == (200, 0), str(points))
check("crop space count", len(points) == 4)

source = Image.new("RGB", (crop.width(), crop.height()), (10, 200, 30))
masked = mask_outside_polygon(source, points)
check("mask size", masked.size == source.size)
check("mask centre kept", masked.getpixel((crop.width() // 2, crop.height() // 2)) == (10, 200, 30),
      str(masked.getpixel((crop.width() // 2, crop.height() // 2))))
check("mask corner white", masked.getpixel((1, 1)) == (255, 255, 255),
      str(masked.getpixel((1, 1))))
degenerate = mask_outside_polygon(source, [(0, 0), (5, 0)])
check("mask degenerate", degenerate.getpixel((1, 1)) == (10, 200, 30))

# --- blacking part of a crop out before it can leave -----------------------
# The confirmation step exists because an upload cannot be taken back, and it
# used to let you change only the *bounds* of the selection, never its content.
plain = Image.new("RGB", (100, 80), (10, 200, 30))
painted = black_out(plain, [(20, 10, 60, 40)])
check("the box is filled solid", painted.getpixel((30, 20)) == (0, 0, 0),
      str(painted.getpixel((30, 20))))
check("and nothing beside it is", painted.getpixel((70, 20)) == (10, 200, 30))
check("the edges are the ones asked for",
      painted.getpixel((20, 10)) == (0, 0, 0) and painted.getpixel((60, 40)) == (10, 200, 30),
      f"{painted.getpixel((20, 10))} {painted.getpixel((60, 40))}")
check("the original is left alone", plain.getpixel((30, 20)) == (10, 200, 30),
      "black_out must not paint on the screenshot the overlay is still showing")
check("nothing to do is not a copy", black_out(plain, []) is plain)
check("a box hanging over the edge is clipped",
      black_out(plain, [(80, 60, 500, 500)]).getpixel((99, 79)) == (0, 0, 0))
check("one entirely outside is dropped",
      black_out(plain, [(200, 200, 300, 300)]).getpixel((50, 40)) == (10, 200, 30))
check("and so is one with no area", black_out(plain, [(10, 10, 10, 40)]) is plain)

check("crop space is a translation",
      rects_to_crop_space([QRect(120, 90, 40, 30)], QRect(100, 50, 400, 300))
      == [(20, 40, 60, 70)],
      str(rects_to_crop_space([QRect(120, 90, 40, 30)], QRect(100, 50, 400, 300))))
check("and a scale when the crop was composed at one",
      rects_to_crop_space([QRect(120, 90, 40, 30)], QRect(100, 50, 400, 300), 2.0)
      == [(40, 80, 120, 140)],
      str(rects_to_crop_space([QRect(120, 90, 40, 30)], QRect(100, 50, 400, 300), 2.0)))

# --- the overlay compensates for a window that is not at the screen corner ---
# KWin sometimes leaves the window in the work area, below the panel.  A Wayland
# client cannot ask for its own position, so the KWin script reports it and
# everything shifts by that offset; without this the screenshot is painted from
# the window's corner and appears moved down by the panel's height.
offset_overlay = make_overlay(MODE_RECTANGLE)
# The window really is short in this situation — that is why it has an offset.
offset_overlay.resize(screen.geometry().width(), screen.geometry().height() - 35)
offset_overlay._has_selection = True
offset_overlay._anchor = QPoint(100, 100)
offset_overlay._current = QPoint(300, 250)
check("no offset by default", offset_overlay._selection_rect() == QRect(100, 100, 200, 150))

offset_overlay.set_window_offset(0, 35)
check(
    "selection shifts with the window",
    offset_overlay._selection_rect() == QRect(100, 135, 200, 150),
    str(offset_overlay._selection_rect()),
)
offset_overlay.render(QPixmap(offset_overlay.size()))
check("offset overlay paints", True)

lasso_offset = make_overlay(MODE_LASSO)
lasso_offset.resize(screen.geometry().width(), screen.geometry().height() - 35)
lasso_offset._has_selection = True
lasso_offset._drag_mode = MODE_LASSO
lasso_offset._points = [QPoint(200, 100), QPoint(300, 200), QPoint(200, 300), QPoint(100, 200)]
lasso_offset._current = QPoint(100, 200)
lasso_offset.set_window_offset(0, 35)
check("lasso bbox shifts", lasso_offset._selection_rect() == QRect(100, 135, 200, 200),
      str(lasso_offset._selection_rect()))
check("lasso polygon shifts", lasso_offset.selection_polygon().point(0) == QPoint(200, 135),
      str(lasso_offset.selection_polygon().point(0)))
lasso_offset.render(QPixmap(lasso_offset.size()))
check("offset lasso paints", True)

# The crop lands where the user pointed, in physical pixels of the screenshot.
shifted_crop = hidpi.logical_rect_to_physical(lasso_offset._selection_rect(), metrics)
check("offset crop", shifted_crop == QRect(200, 270, 400, 400), str(shifted_crop))

# --- the overlay insists on being full screen ------------------------------
# The compositor sometimes hands the window the work area instead of the whole
# output; asking again after the first configure round-trip usually fixes it.
retry_overlay = make_overlay(MODE_RECTANGLE)
retry_overlay.show()
retry_overlay.resize(screen.geometry().width(), screen.geometry().height() - 35)
check("smaller than the screen", retry_overlay.size() != screen.geometry().size())
retry_overlay._ensure_fullscreen()
check("a retry was attempted", retry_overlay._fullscreen_attempts == 1,
      str(retry_overlay._fullscreen_attempts))
check("full screen state requested",
      bool(retry_overlay.windowState() & Qt.WindowState.WindowFullScreen))

# The retry resized the widget, so a window that is right again is left alone —
# that is what the previous check just proved.  Force it small once more to see
# that it gives up after a few tries instead of looping forever.
retry_overlay.resize(screen.geometry().width(), screen.geometry().height() - 35)
retry_overlay._fullscreen_attempts = 3
retry_overlay._ensure_fullscreen()
check("stops retrying", retry_overlay._fullscreen_attempts == 4,
      str(retry_overlay._fullscreen_attempts))

# A window that is the right size must not be poked at all.
good_overlay = make_overlay(MODE_RECTANGLE)
good_overlay.show()
good_overlay.resize(screen.geometry().size())
good_overlay._ensure_fullscreen()
check("no retry when correct", good_overlay._fullscreen_attempts == 0)
good_overlay.close()
retry_overlay.close()

# A stale offset must never survive the window becoming full screen: the retry
# and the KWin report race, and shifting a window that is now correct would
# break it in the opposite direction.
race_overlay = make_overlay(MODE_RECTANGLE)
race_overlay.show()
race_overlay.resize(screen.geometry().width(), screen.geometry().height() - 36)
race_overlay.set_window_offset(0, 36)
check("offset applied while short", race_overlay._offset == QPoint(0, 36))
race_overlay.resize(screen.geometry().size())
check("offset dropped once full screen", race_overlay._offset == QPoint(0, 0),
      str(race_overlay._offset))
# A report that arrives after the window is already full screen is ignored.
race_overlay.set_window_offset(0, 36)
check("late report ignored", race_overlay._offset == QPoint(0, 0), str(race_overlay._offset))
race_overlay.close()

# --- calibration -----------------------------------------------------------
from circle_to_search.calibration import (  # noqa: E402
    MINIMUM_SAMPLES,
    Sample,
    describe_changes,
    suggest,
)
from circle_to_search.config import DetectionSettings  # noqa: E402


def swings(count: int, length: int, speed: int, curve: int = 105, diag: int = 8,
           turn: int = 176, duration: int = 120) -> list[Sample]:
    return [
        Sample(
            length=length,
            speed=speed,
            curvature_pct=curve,
            diagonal_deg=diag,
            turn_deg=turn,
            duration_ms=duration,
        )
        for _ in range(count)
    ]


base = DetectionSettings.defaults()

# Not enough to go on: the settings must come back untouched rather than being
# derived from two twitches.
check("too few samples", suggest(swings(MINIMUM_SAMPLES - 1, 300, 2000), base) == base)
check("twitches are dropped", suggest(swings(20, 12, 2000), base) == base)

# A brisk shaker: thresholds sit below what they actually do, with margin.
brisk = suggest(swings(12, 300, 2400), base)
check("brisk amplitude", brisk.minAmplitudePx == 180, str(brisk.minAmplitudePx))
check("brisk speed", brisk.minSpeedPxPerSec == 1320, str(brisk.minSpeedPxPerSec))
check("brisk speed is below the measured one", brisk.minSpeedPxPerSec < 2400)
check("brisk amplitude is below the measured one", brisk.minAmplitudePx < 300)

# A gentle shaker gets thresholds they can actually reach — this is the case
# the defaults were failing, so it matters most.
gentle = suggest(swings(12, 120, 420, duration=280), base)
check("gentle speed", gentle.minSpeedPxPerSec < base.minSpeedPxPerSec,
      f"{gentle.minSpeedPxPerSec} vs {base.minSpeedPxPerSec}")
check("gentle amplitude", gentle.minAmplitudePx < base.minAmplitudePx,
      f"{gentle.minAmplitudePx} vs {base.minAmplitudePx}")
check("gentle window widens", gentle.windowMs > base.windowMs,
      f"{gentle.windowMs} vs {base.windowMs}")

# A wobbly, off-diagonal shake loosens the angle and curvature limits.
wobbly = suggest(swings(12, 250, 1500, curve=130, diag=26, turn=150), base)
check("wobbly angle", wobbly.angleTolerance >= 36, str(wobbly.angleTolerance))
check("wobbly curvature", wobbly.maxCurvaturePct >= 165, str(wobbly.maxCurvaturePct))
check("wobbly turn tolerance", wobbly.reversalTolerance >= 45, str(wobbly.reversalTolerance))

# Everything stays inside the ranges the settings dialog allows.
extreme = suggest(swings(12, 5000, 50000, curve=900, diag=80, turn=95, duration=4000), base)
check("clamped amplitude", 20 <= extreme.minAmplitudePx <= 1000, str(extreme.minAmplitudePx))
check("clamped speed", 100 <= extreme.minSpeedPxPerSec <= 5000, str(extreme.minSpeedPxPerSec))
check("clamped curvature", 110 <= extreme.maxCurvaturePct <= 400, str(extreme.maxCurvaturePct))
check("clamped angle", 10 <= extreme.angleTolerance <= 44, str(extreme.angleTolerance))
check("clamped turn", 15 <= extreme.reversalTolerance <= 90, str(extreme.reversalTolerance))
check("clamped window", 300 <= extreme.windowMs <= 3000, str(extreme.windowMs))

# Gentle corners are not reversals, so they must not widen the turn tolerance.
corners = swings(12, 250, 1500, turn=60)
check("corners ignored for the turn stat",
      suggest(corners, base).reversalTolerance == 40,
      str(suggest(corners, base).reversalTolerance))

rows = describe_changes(base, brisk)
check("changes are listed", len(rows) >= 3, str(rows))
check("nothing listed when identical", describe_changes(base, base) == [])

# --- confirm and adjust before uploading -----------------------------------
from PyQt6.QtCore import QEvent, QPointF  # noqa: E402
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter, QRegion  # noqa: E402

from circle_to_search.ocr import Word  # noqa: E402
from circle_to_search.overlay import (  # noqa: E402
    ACTION_COPY,
    ACTION_SAVE,
    ACTION_SEARCH,
    ACTION_TEXT,
    BADGE_NONE,
    BAR_CANCEL,
    BAR_MODE_COLOUR,
    BAR_MODE_LASSO,
    BAR_MODE_RECT,
    BAR_REDACT,
    BAR_TEXT_ALL,
    BAR_TEXT_BACK,
    BAR_TEXT_COPY,
    BAR_TEXT_SEARCH,
    SENDING_LIMIT_MS,
)


def drag(overlay: SelectionOverlay, start: tuple[int, int], end: tuple[int, int]) -> None:
    """A real press-move-release through the overlay's own event handlers."""
    for kind, point, button in (
        (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton),
        (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton),
    ):
        position = QPointF(float(point[0]), float(point[1]))
        event = QMouseEvent(
            kind,
            position,
            position,
            button,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        if kind == QEvent.Type.MouseButtonPress:
            overlay.mousePressEvent(event)
        elif kind == QEvent.Type.MouseMove:
            overlay.mouseMoveEvent(event)
        else:
            overlay.mouseReleaseEvent(event)


def press_key(overlay: SelectionOverlay, key: Qt.Key, modifiers=Qt.KeyboardModifier.NoModifier):
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


def _mouse(overlay: SelectionOverlay, kind: QEvent.Type, point: tuple[int, int]) -> None:
    position = QPointF(float(point[0]), float(point[1]))
    button = (
        Qt.MouseButton.NoButton
        if kind == QEvent.Type.MouseMove
        else Qt.MouseButton.LeftButton
    )
    event = QMouseEvent(
        kind,
        position,
        position,
        button,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    if kind == QEvent.Type.MouseButtonPress:
        overlay.mousePressEvent(event)
    elif kind == QEvent.Type.MouseMove:
        overlay.mouseMoveEvent(event)
    else:
        overlay.mouseReleaseEvent(event)


def click(overlay: SelectionOverlay, point: tuple[int, int], release=None) -> None:
    """Press and let go, optionally somewhere else — that is how a click is taken back."""
    _mouse(overlay, QEvent.Type.MouseButtonPress, point)
    _mouse(overlay, QEvent.Type.MouseButtonRelease, release or point)


def hover(overlay: SelectionOverlay, point: tuple[int, int]) -> None:
    _mouse(overlay, QEvent.Type.MouseMove, point)


def button_named(overlay: SelectionOverlay, action: str):
    for placed in overlay._layout_buttons():
        if placed.action == action:
            return placed
    raise AssertionError(f"no {action} button in {[b.action for b in overlay._layout_buttons()]}")


def confirming(mode: str = MODE_RECTANGLE, mask_outside: bool = False) -> tuple:
    """An overlay with a finished 200x150 drag waiting to be confirmed."""
    overlay = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(shot)),
        metrics,
        screen,
        dim_percent=40,
        mode=mode,
        mask_outside=mask_outside,
        confirm=True,
    )
    overlay.resize(screen.geometry().size())
    results: dict[str, tuple] = {}
    overlay.selected.connect(lambda r, p: results.__setitem__(ACTION_SEARCH, (r, p)))
    overlay.copy_requested.connect(lambda r, p: results.__setitem__(ACTION_COPY, (r, p)))
    overlay.save_requested.connect(lambda r, p: results.__setitem__(ACTION_SAVE, (r, p)))
    overlay.pin_requested.connect(lambda r, p: results.__setitem__(ACTION_PIN, (r, p)))
    overlay.text_selected.connect(lambda text: results.__setitem__(ACTION_TEXT, text))
    overlay.cancelled.connect(lambda: results.__setitem__("cancelled", ()))
    drag(overlay, (100, 100), (300, 250))
    return overlay, results


# Releasing the button must not send anything any more: an upload cannot be
# taken back, and a selection is easy to get slightly wrong.
overlay, results = confirming()
check("release does not send", results == {}, str(results))
check("the selection is held", overlay._confirming and
      overlay._selection_rect() == QRect(100, 100, 200, 150), str(overlay._selection_rect()))
overlay.render(QPixmap(overlay.size()))
check("the confirmation paints", True)

press_key(overlay, Qt.Key.Key_Return)
check("Enter searches", ACTION_SEARCH in results, str(list(results)))
check("Enter sends the physical crop",
      results[ACTION_SEARCH][0] == QRect(200, 200, 400, 300), str(results.get(ACTION_SEARCH)))

overlay, results = confirming()
press_key(overlay, Qt.Key.Key_C)
check("C copies", list(results) == [ACTION_COPY], str(list(results)))

overlay, results = confirming()
press_key(overlay, Qt.Key.Key_S)
check("S saves", list(results) == [ACTION_SAVE], str(list(results)))

overlay, results = confirming()
press_key(overlay, Qt.Key.Key_Escape)
check("Esc cancels", list(results) == ["cancelled"], str(list(results)))

# The edges really move the crop, and only the edge that was grabbed.
overlay, results = confirming()
handles = overlay._handle_rects()
check("eight handles", len(handles) == 8, str(sorted(handles)))
check("the corner handle covers the corner",
      handles["nw"].contains(QPoint(100, 100)) and handles["se"].contains(QPoint(300, 250)),
      f'{handles["nw"]} {handles["se"]}')

overlay._grab = "se"
overlay._grab_origin = QPoint(300, 250)
overlay._grab_box = QRect(overlay._box)
overlay._resize_to(QPoint(340, 280))
check("dragging the corner resizes", overlay._selection_rect() == QRect(100, 100, 240, 180),
      str(overlay._selection_rect()))

overlay._grab = "w"
overlay._grab_origin = QPoint(100, 175)
overlay._grab_box = QRect(overlay._box)
overlay._resize_to(QPoint(150, 400))
check("a side handle moves one edge only",
      overlay._selection_rect() == QRect(150, 100, 190, 180), str(overlay._selection_rect()))

overlay._grab = None
press_key(overlay, Qt.Key.Key_Right)
check("arrows nudge", overlay._selection_rect().x() == 151, str(overlay._selection_rect()))
press_key(overlay, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
check("Ctrl nudges further", overlay._selection_rect().y() == 110, str(overlay._selection_rect()))
press_key(overlay, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
check("Shift resizes", overlay._selection_rect().width() == 191,
      str(overlay._selection_rect()))

# The box cannot be pushed off the screen, or the crop would fall outside the
# screenshot.
overlay, results = confirming()
for _ in range(400):
    press_key(overlay, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
check("nudging stops at the edge", overlay._selection_rect().x() == 0,
      str(overlay._selection_rect()))

# A lasso whose box was adjusted is no longer described by the loop, so the
# outline is dropped instead of being used as a mask that no longer fits.
overlay, results = confirming(MODE_LASSO)
check("the lasso outline survives an untouched box",
      overlay.selection_polygon().count() > 0, str(overlay.selection_polygon().count()))
press_key(overlay, Qt.Key.Key_Right)
check("an edited box drops the outline", overlay.selection_polygon().count() == 0)
press_key(overlay, Qt.Key.Key_Return)
check("the edited lasso still searches", ACTION_SEARCH in results, str(list(results)))

# Pressing outside the selection starts again rather than adjusting it.  Well
# clear of the action bar, which hangs below the box and is wider than it.
overlay, results = confirming()
drag(overlay, (500, 500), (600, 600))
check("a second drag replaces the first",
      overlay._selection_rect() == QRect(500, 500, 100, 100), str(overlay._selection_rect()))
check("and still sends nothing by itself", results == {}, str(results))

# Reported from a real desktop: the whole inside of the box was a move grab, so
# a selection covering most of the screen could never be redrawn — there was
# nowhere left to press that did not move it.  Moving has its own grip now, and
# everything else inside starts again.
overlay, results = confirming()
inside = QPoint(150, 140)                     # inside the box, away from its middle
check("that really is inside the selection", overlay._selection_rect().contains(inside))
check("and it is not the move grip", not overlay._move_grip().contains(inside),
      str(overlay._move_grip()))
drag(overlay, (inside.x(), inside.y()), (260, 230))
check("a drag from inside redraws instead of moving",
      overlay._selection_rect() == QRect(150, 140, 110, 90), str(overlay._selection_rect()))

# The grip still moves the whole thing, and it is inside the box where it can
# be aimed at.
overlay, results = confirming()
before = QRect(overlay._selection_rect())
grip = overlay._move_grip()
check("the grip is in the middle of the box", before.contains(grip.center()), str(grip))
check("and it is small enough to leave room around it",
      grip.width() < before.width() // 2 and grip.height() < before.height() // 2, str(grip))
check("pressing it grabs a move", overlay._handle_at(grip.center()) == "move")
drag(overlay, (grip.center().x(), grip.center().y()),
     (grip.center().x() + 40, grip.center().y() + 30))
check("dragging the grip moves the box, unchanged in size",
      overlay._selection_rect().size() == before.size()
      and overlay._selection_rect().topLeft() == before.topLeft() + QPoint(40, 30),
      str(overlay._selection_rect()))

# Reported from a real desktop: moving the box smeared the handles, the grip and
# the action bar across the screen in stripes.  The repaint is a *region* — the
# whole point is not to hand a 4K compositor thirty-three megabytes per frame —
# and the box contributes only a ring around its outline.  Anything drawn near
# the box but not on that ring has to name itself, or it is never erased from
# where it was.  A one-pixel step is the case that matters: the ring is then
# eight pixels wide and the handles hang seven pixels either side of it.


def repainted(damage, rect: QRect) -> bool:
    """Whether every pixel of ``rect`` is in ``damage``.

    Not ``QRegion.contains(QRect)``: that one is true when the rectangle merely
    *touches* the region, which is exactly the wrong question here and would
    have passed happily against the bug this pins.
    """
    return QRegion(rect).subtracted(damage).isEmpty()


moving, _ = confirming()
was_box = QRect(moving._selection_rect())
was_floating = moving._floating_rects()
was_furniture = {
    **{f"the {name} handle": rect for name, rect in moving._handle_rects().items()},
    "the grip": QRect(moving._move_grip()),
    "the action bar": QRect(moving._bar_rect()),
}
moving._grab = "move"
moving._apply_box(was_box.translated(1, 0), edited=True)
damage = moving._drag_damage(was_box, was_floating, moving._current)
left_behind = [
    name
    for name, rect in was_furniture.items()
    if not repainted(damage, rect.translated(-moving._offset))
]
check("moving the box erases everything drawn around it", not left_behind, str(left_behind))
check("and draws it where it is now",
      all(repainted(damage, rect.translated(-moving._offset))
          for rect in (*moving._handle_rects().values(),
                       moving._move_grip(), moving._bar_rect())))
check("without repainting the whole window",
      not repainted(damage, moving.rect()), str(damage.boundingRect()))
moving._grab = None


def raw_pixels(image) -> bytes:
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    return bytes(bits)


def replay(grab, change, steps: int = 20, magnifier: bool = False) -> int:
    """Change the box repeatedly, letting only the damaged pixels through.

    The regions are what the screen really gets, so the only honest question is
    whether the result differs from a full repaint — and by how much.
    """
    view = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(shot)),
        metrics,
        screen,
        dim_percent=40,
        mode=MODE_RECTANGLE,
        confirm=True,
        magnifier=magnifier,
    )
    view.resize(screen.geometry().size())
    view._has_selection = True
    view._confirming = True
    view._box = QRect(300, 300, 200, 150)
    view._current = QPoint(400, 375)
    view._grab = grab

    canvas, frame = QPixmap(view.size()), QPixmap(view.size())
    view.render(canvas)
    for _ in range(steps):
        was = QRect(view._selection_rect())
        floating = view._floating_rects()
        view._apply_box(change(was), edited=True)
        view.render(frame)
        painter = QPainter(canvas)
        painter.setClipRegion(view._drag_damage(was, floating, view._current))
        painter.drawPixmap(0, 0, frame)
        painter.end()

    honest = QPixmap(view.size())
    view.render(honest)
    left, right = canvas.toImage(), honest.toImage()
    return sum(1 for a, b in zip(raw_pixels(left), raw_pixels(right), strict=True) if a != b)


for what, grab, change in (
    ("moving it", "move", lambda r: r.translated(-3, -3)),
    ("resizing by a corner", "se", lambda r: r.adjusted(0, 0, 4, 3)),
    ("nudging it with the keys", None, lambda r: r.translated(2, 0)),
):
    smeared = replay(grab, change)
    check(f"{what} leaves nothing behind at all", smeared == 0, f"{smeared} bytes differ")

# The corners still resize, which is what they were always for.
overlay, results = confirming()
corner = overlay._handle_rects()["se"].center()
check("a corner is still a resize, not a redraw",
      overlay._handle_at(corner) == "se", str(overlay._handle_at(corner)))

# --- blacking something out of the selection --------------------------------
# The confirmation step was written because an upload cannot be taken back, and
# it only ever let the *bounds* be changed.  If a token happens to sit next to
# the thing you want to look up, reframing the crop was the only answer.
hiding, results = confirming()
check("the bar offers it", button_named(hiding, BAR_REDACT).key == "B")
check("and is not lit yet", not button_named(hiding, BAR_REDACT).primary)
target = button_named(hiding, BAR_REDACT)
click(hiding, (target.rect.center().x(), target.rect.center().y()))
check("the button turns it on", hiding._redacting)
check("and lights up to say so", button_named(hiding, BAR_REDACT).primary)
check("the caption says what to do now", hiding._bar_caption() == i18n.tr("overlay.redact"),
      hiding._bar_caption())

# The whole box belongs to the drag while it is on: what has to be covered is
# usually in the middle of what was selected, which is where the grip lives.
before = QRect(hiding._selection_rect())
grip = hiding._move_grip().center()
drag(hiding, (grip.x() - 20, grip.y() - 5), (grip.x() + 20, grip.y() + 5))
check("a drag over the grip covers instead of moving",
      hiding._selection_rect() == before, str(hiding._selection_rect()))
check("and one rectangle was placed", len(hiding._redactions) == 1, str(hiding._redactions))
check("the count goes on the button", "1" in button_named(hiding, BAR_REDACT).label,
      button_named(hiding, BAR_REDACT).label)

# It is what will be sent, so it can only ever be part of what is being sent.
drag(hiding, (before.right() - 10, before.bottom() - 10),
     (before.right() + 400, before.bottom() + 400))
check("a rectangle is clipped to the selection",
      before.contains(hiding._redactions[-1]), str(hiding._redactions[-1]))
drag(hiding, (before.right() + 50, before.top()), (before.right() + 90, before.top() + 20))
check("and one entirely outside it is not kept", len(hiding._redactions) == 2,
      str(hiding._redactions))

press_key(hiding, Qt.Key.Key_Backspace)
check("Backspace undoes the last one", len(hiding._redactions) == 1)
press_key(hiding, Qt.Key.Key_Escape)
check("Esc leaves the mode", not hiding._redacting)
check("without throwing the capture away", "cancelled" not in results and not hiding._finished)
check("and keeps what was already covered", len(hiding._redactions) == 1)
press_key(hiding, Qt.Key.Key_Escape)
check("the next Esc does cancel it", "cancelled" in results, str(list(results)))

# What the application is handed: physical pixels of the screenshot, the same
# space as the crop rectangle it gets beside them.
handing, _ = confirming()
handing._set_redacting(True)
handing._add_redaction(QRect(120, 120, 60, 40))
check("handed over in the crop's own space",
      handing.redactions() == [QRect(240, 240, 120, 80)], str(handing.redactions()))

# Drawn solid black over the un-dimmed area, because the picture being checked
# has to be the picture that is sent.
canvas = QPixmap(handing.size())
handing.render(canvas)
painted = canvas.toImage()
check("it really is black on screen", painted.pixelColor(150, 140).value() == 0,
      painted.pixelColor(150, 140).name())
check("and only there", painted.pixelColor(250, 140).value() != 0,
      painted.pixelColor(250, 140).name())

# The handles sit on the outline and the grip in the middle, which is exactly
# where the things that need covering are, so they go while covering.
furniture, _ = confirming()
spot = furniture._move_grip().center()
shown = QPixmap(furniture.size())
furniture.render(shown)
furniture._set_redacting(True)
gone = QPixmap(furniture.size())
furniture.render(gone)
check("the grip is out of the way while covering",
      shown.toImage().pixelColor(spot) != gone.toImage().pixelColor(spot),
      f"{shown.toImage().pixelColor(spot).name()} vs {gone.toImage().pixelColor(spot).name()}")
furniture._set_redacting(False)
back = QPixmap(furniture.size())
furniture.render(back)
check("and comes back afterwards",
      back.toImage().pixelColor(spot) == shown.toImage().pixelColor(spot))

# Starting a new selection starts with nothing covered: the rectangles belonged
# to the crop that was just thrown away.
handing._reset_selection()
check("a new selection is not full of holes",
      handing._redactions == [] and not handing._redacting)

# A box too small to hold a full-size grip still gets one, so a small selection
# is not one you can only nudge with the arrow keys.
overlay, results = confirming()
overlay._apply_box(QRect(400, 400, 40, 30), edited=True)
tiny = overlay._move_grip()
check("even a small box has a grip", not tiny.isNull() and tiny.width() >= 10, str(tiny))
check("and it fits inside it", overlay._selection_rect().contains(tiny), str(tiny))

# With confirmation switched off the old behaviour is exactly as it was.
immediate = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(shot)),
    metrics,
    screen,
    dim_percent=40,
    mode=MODE_RECTANGLE,
    confirm=False,
)
immediate.resize(screen.geometry().size())
sent: list[tuple] = []
immediate.selected.connect(lambda r, p: sent.append((r, p)))
drag(immediate, (100, 100), (300, 250))
check("without confirmation the release sends", len(sent) == 1, str(sent))
check("and sends the same crop", sent and sent[0][0] == QRect(200, 200, 400, 300), str(sent))

# A stray click is still not a selection, confirmation or not.
overlay, results = confirming()
overlay._reset_selection()
drag(overlay, (500, 500), (503, 502))
check("a stray click selects nothing",
      not overlay._confirming and results == {}, str(results))

# --- the action bar --------------------------------------------------------
# The whole gesture is mouse work and then it used to demand the keyboard: the
# bottom of the screen said "Enter — search · C — copy · …", which is
# documentation, not an interface.  These are real buttons now.
# Before anything is drawn the bar carries the choice the next drag will use.
# Holding Shift has always swapped lasso and rectangle for one selection, and
# nothing on screen ever said so.
bare = make_overlay(MODE_RECTANGLE)
actions = [placed.action for placed in bare._layout_buttons()]
check("the idle bar offers the two shapes and the picker",
      actions == [BAR_MODE_LASSO, BAR_MODE_RECT, BAR_MODE_COLOUR], str(actions))
check("the current one is lit", button_named(bare, BAR_MODE_RECT).primary
      and not button_named(bare, BAR_MODE_LASSO).primary)
check("and Shift is shown against the other one",
      button_named(bare, BAR_MODE_LASSO).key == "Shift"
      and button_named(bare, BAR_MODE_RECT).key == "", str(bare._layout_buttons()))
check("the hint is the caption, not a second box",
      "Esc" in bare._bar_caption(), bare._bar_caption())

switched: list[str] = []
bare.mode_changed.connect(switched.append)
target = button_named(bare, BAR_MODE_LASSO)
click(bare, (target.rect.center().x(), target.rect.center().y()))
check("clicking a chip switches the mode", bare._mode == MODE_LASSO, bare._mode)
check("and says so, because it is a setting", switched == [MODE_LASSO], str(switched))
check("the chips swap over", button_named(bare, BAR_MODE_LASSO).primary
      and button_named(bare, BAR_MODE_RECT).key == "Shift")
click(bare, (target.rect.center().x(), target.rect.center().y()))
check("clicking the lit one changes nothing", switched == [MODE_LASSO], str(switched))
check("and does not start a drag", not bare._dragging and not bare._has_selection)
bare.set_mode(MODE_RECTANGLE)
check("a group can follow along", bare._mode == MODE_RECTANGLE and switched == [MODE_LASSO])

# A drag that starts anywhere else is still a drag.
away = make_overlay(MODE_RECTANGLE)
drag(away, (60, 400), (260, 520))
check("the chips do not swallow the rest of the screen",
      away._selection_rect() == QRect(60, 400, 200, 120), str(away._selection_rect()))
check("and they are gone once there is a selection",
      [placed.action for placed in away._layout_buttons()] != [BAR_MODE_LASSO, BAR_MODE_RECT],
      str([placed.action for placed in away._layout_buttons()]))

overlay, results = confirming()
actions = [placed.action for placed in overlay._layout_buttons()]
check("six buttons once a selection is waiting",
      actions == [ACTION_SEARCH, ACTION_COPY, ACTION_SAVE, ACTION_PIN, BAR_REDACT, BAR_CANCEL],
      str(actions))
check("search is the primary one", button_named(overlay, ACTION_SEARCH).primary)
check("the keys are still shown",
      [placed.key for placed in overlay._layout_buttons()] == ["Enter", "C", "S", "P", "B", "Esc"],
      str([placed.key for placed in overlay._layout_buttons()]))
placed_bar = overlay._layout_buttons()
check("the buttons do not overlap",
      all(a.rect.right() < b.rect.left()
          for a, b in itertools.pairwise(placed_bar)))
check("the bar hangs under the selection",
      overlay._bar_rect().top() > overlay._selection_rect().bottom(),
      f"{overlay._bar_rect()} under {overlay._selection_rect()}")
check("and stays on the screen",
      overlay._visible_area().toRect().contains(overlay._bar_rect()), str(overlay._bar_rect()))

for action, expected in (
    (ACTION_SEARCH, ACTION_SEARCH),
    (ACTION_COPY, ACTION_COPY),
    (ACTION_SAVE, ACTION_SAVE),
    (BAR_CANCEL, "cancelled"),
):
    overlay, results = confirming()
    target = button_named(overlay, action)
    click(overlay, (target.rect.center().x(), target.rect.center().y()))
    check(f"clicking {action} does it", list(results) == [expected], f"{action}: {list(results)}")

# Pressing a button must not also start a drag underneath it, or every click
# would replace the selection it was meant to act on.
overlay, results = confirming()
before = QRect(overlay._selection_rect())
target = button_named(overlay, ACTION_COPY)
_mouse(overlay, QEvent.Type.MouseButtonPress, (target.rect.center().x(), target.rect.center().y()))
check("a press on a button starts no drag", not overlay._dragging and results == {}, str(results))
check("and leaves the selection alone", overlay._selection_rect() == before,
      str(overlay._selection_rect()))
_mouse(overlay, QEvent.Type.MouseButtonRelease,
       (target.rect.center().x(), target.rect.center().y()))
check("letting go on it does the thing", list(results) == [ACTION_COPY], str(list(results)))

# Sliding off before letting go takes the press back — the only way out of a
# misclick on a button that uploads something.
overlay, results = confirming()
target = button_named(overlay, ACTION_SEARCH)
click(overlay, (target.rect.center().x(), target.rect.center().y()), release=(700, 650))
check("released elsewhere, nothing happens", results == {}, str(results))
check("and the selection is still there", overlay._confirming and overlay._has_selection)

# Hover, and the cost of it: repainting a 4K screenshot every time the pointer
# crosses a button is exactly what made the old cursor glow unusable.
overlay, results = confirming()
target = button_named(overlay, ACTION_SAVE)
hover(overlay, (target.rect.center().x(), target.rect.center().y()))
check("hovering marks the button", overlay._hovered_button == ACTION_SAVE,
      str(overlay._hovered_button))
check("and asks for a hand cursor", overlay.cursor().shape() == Qt.CursorShape.PointingHandCursor,
      str(overlay.cursor().shape()))
bar_area = overlay._bar_rect().width() * overlay._bar_rect().height()
screen_area = overlay.width() * overlay.height()
check("the bar is a small part of the screen", bar_area * 12 < screen_area,
      f"{bar_area} of {screen_area}")
hover(overlay, (700, 650))
check("moving away clears it", overlay._hovered_button is None, str(overlay._hovered_button))
check("and the cursor goes back", overlay.cursor().shape() != Qt.CursorShape.PointingHandCursor)

# The bar is painted on top, so it is hit-tested on top: a button sitting over a
# word must act as a button and not start selecting text.
over_text = confirming()[0]
first = button_named(over_text, ACTION_SEARCH).rect.center()
over_text.set_words([
    Word("under", (first.x() - 40) * 2, (first.y() - 7) * 2, 80, 28, 90.0, (9, 1, 1, 1)),
])
check("the word really is under the button", over_text._word_at(first) is not None, str(first))
searched: list[str] = []
over_text.selected.connect(lambda r, p: searched.append(ACTION_SEARCH))
click(over_text, (first.x(), first.y()))
check("the button wins over the text under it",
      searched == [ACTION_SEARCH] and not over_text.has_text_selection(), str(searched))

# A selection at the very bottom has no room under it for the bar.
low = confirming()[0]
low._apply_box(QRect(100, low.height() - 60, 200, 50), edited=True)
check("the bar flips above a selection at the bottom",
      low._bar_rect().bottom() < low._selection_rect().top(),
      f"{low._bar_rect()} vs {low._selection_rect()}")
check("and is still on the screen",
      low._visible_area().toRect().contains(low._bar_rect()), str(low._bar_rect()))

# A selection in the corner would put a centred bar off the side of the screen.
corner = confirming()[0]
corner._apply_box(QRect(0, 200, 60, 50), edited=True)
check("the bar is pushed back onto the screen", corner._bar_rect().left() >= 0,
      str(corner._bar_rect()))

# Every button is reachable: nothing may end up outside the pill that is drawn
# around them.
overlay, results = confirming()
for placed in overlay._layout_buttons():
    check(f"{placed.action} is inside the pill", overlay._bar_rect().contains(placed.rect),
          f"{placed.rect} in {overlay._bar_rect()}")
    check(f"{placed.action} answers to a hit test",
          overlay._button_at(placed.rect.center()) == placed.action)

# The arrow keys are the one thing no button can announce, so the caption stays
# — inside the pill, where it has something to be read against, and only where
# arrow keys actually do anything.
check("the caption says what the buttons cannot", overlay._bar_caption() != "",
      overlay._bar_caption())
check("and it is inside the pill, not under it",
      overlay._bar_rect().bottom() > overlay._layout_buttons()[0].rect.bottom() + 8,
      f"{overlay._bar_rect()} vs {overlay._layout_buttons()[0].rect}")
caption_bar = make_overlay(MODE_RECTANGLE)
caption_bar.set_words([Word("word", 200, 200, 100, 30, 95.0, (1, 1, 1, 1))])
caption_bar.select_all_text()
check("no caption where the arrow keys do nothing", caption_bar._bar_caption() == "",
      caption_bar._bar_caption())

# The pixel readout used to follow the pointer, which put it straight on top of
# the bar as soon as one existed.
overlay, results = confirming()
_text, _font, readout = overlay._size_label(overlay._selection_rect())
check("the size readout keeps clear of the bar", not readout.intersects(overlay._bar_rect()),
      f"{readout} vs {overlay._bar_rect()}")
overlay._current = QPoint(overlay._bar_rect().center().x(), overlay._bar_rect().top() - 20)
_text, _font, readout = overlay._size_label(overlay._selection_rect())
check("wherever the pointer has wandered off to",
      not readout.intersects(overlay._bar_rect()), f"{readout} vs {overlay._bar_rect()}")
overlay._grab = "s"
overlay._current = QPoint(200, overlay._selection_rect().bottom())
_text, _font, readout = overlay._size_label(overlay._selection_rect())
check("and while a bottom edge is being dragged",
      not readout.intersects(overlay._bar_rect()), f"{readout} vs {overlay._bar_rect()}")
overlay._grab = None

# --- saying what will actually be sent -------------------------------------
# The crop size is not what leaves: prepare_image resizes to "Longest side" and
# re-encodes at "JPEG quality", and neither setting could be judged from a
# readout that only ever showed the number the user does not control.
from circle_to_search.imageops import scaled_size  # noqa: E402

check("no resize below the limit", scaled_size(800, 600, 1000) == (800, 600))
check("the long edge decides", scaled_size(4000, 2000, 1000) == (1000, 500),
      str(scaled_size(4000, 2000, 1000)))
check("portrait too", scaled_size(2000, 4000, 1000) == (500, 1000),
      str(scaled_size(2000, 4000, 1000)))
check("nothing rounds away to nothing", scaled_size(4000, 3, 1000) == (1000, 1),
      str(scaled_size(4000, 3, 1000)))
check("zero means do not resize", scaled_size(4000, 2000, 0) == (4000, 2000))


def sending(max_side: int = 1000):
    """A confirmed 200x150 selection on an overlay that knows about the upload."""
    view = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(shot)),
        metrics,
        screen,
        dim_percent=40,
        mode=MODE_RECTANGLE,
        confirm=True,
        max_side=max_side,
    )
    view.resize(screen.geometry().size())
    drag(view, (100, 100), (300, 250))
    return view


# The screenshot is 2x, so a 200x150 drag is a 400x300 crop.
sized = sending()
check("the crop size is still the first line",
      sized._size_label(sized._selection_rect())[0].startswith("400 × 300 px"),
      sized._size_label(sized._selection_rect())[0])
check("nothing is claimed about a crop that is not resized",
      "→" not in sized._size_label(sized._selection_rect())[0],
      sized._size_label(sized._selection_rect())[0])

asked: list[QRect] = []
sized.estimate_requested.connect(asked.append)
sized._ask_for_upload_size()
check("it asks about the physical crop", asked == [QRect(200, 200, 400, 300)], str(asked))

sized.set_upload_size(QRect(200, 200, 400, 300), 114688)
check("and says what it weighs once told",
      sized._size_label(sized._selection_rect())[0] == "400 × 300 px\n→ ~112 KB JPEG",
      repr(sized._size_label(sized._selection_rect())[0]))

# An answer about a box that has since moved is worse than no answer at all.
sized.set_upload_size(QRect(0, 0, 10, 10), 999999)
check("a stale answer is dropped",
      "112 KB" in sized._size_label(sized._selection_rect())[0],
      repr(sized._size_label(sized._selection_rect())[0]))
sized._apply_box(QRect(100, 100, 240, 180), edited=True)
check("moving the box drops the measurement", sized._upload_bytes == 0,
      str(sized._upload_bytes))

# A crop bigger than the limit gets the resized dimensions immediately, with no
# encoding needed — that half of the answer is pure arithmetic.
big = sending(max_side=200)
big._ask_for_upload_size()
check("the resized size is there at once",
      big._size_label(big._selection_rect())[0] == "400 × 300 px\n→ 200 × 150",
      repr(big._size_label(big._selection_rect())[0]))
big.set_upload_size(QRect(200, 200, 400, 300), 8 * 1024)
check("and the weight joins it",
      big._size_label(big._selection_rect())[0] == "400 × 300 px\n→ 200 × 150, ~8 KB JPEG",
      repr(big._size_label(big._selection_rect())[0]))

# Nothing at all while a handle is being dragged: the box is not where it is
# going to end up, so neither number would be about anything.
big._grab = "se"
check("no upload line mid-resize", "→" not in big._size_label(big._selection_rect())[0],
      repr(big._size_label(big._selection_rect())[0]))
big._grab = None

# Off entirely for an overlay that is not sending anywhere.
plain = make_overlay(MODE_RECTANGLE)
drag(plain, (100, 100), (300, 250))
check("no upload line without a limit to apply",
      plain._size_label(plain._selection_rect())[0] == "400 × 300 px",
      repr(plain._size_label(plain._selection_rect())[0]))
quiet: list[QRect] = []
plain.estimate_requested.connect(quiet.append)
plain._changed()
check("and nothing is measured for it", plain._estimate_timer is None and quiet == [])

# The wider label still has to fit on screen: a box against the right edge used
# to push it off, and the second line made it wider.
edge = sending(max_side=200)
edge._apply_box(
    QRect(screen.geometry().width() - 60, 200, 50, 40), edited=True
)
edge.set_upload_size(
    hidpi.logical_rect_to_physical(edge._selection_rect(), metrics), 4096
)
_text, _font, readout = edge._size_label(edge._selection_rect())
check("the readout stays on screen at the right edge",
      edge._visible_area().toRect().contains(readout),
      f"{readout} in {edge._visible_area().toRect()}")

# --- the loupe already knows the colour -------------------------------------
# On Wayland no client can read a pixel off another's window.  This one has
# already frozen the screen and is already magnifying it under the pointer, so
# the colour is on screen and was being thrown away after being drawn.
from circle_to_search.colours import css_of, hex_of, readable_on  # noqa: E402
from circle_to_search.overlay import BAR_MODE_COLOUR  # noqa: E402

check("hex is lower case and padded", hex_of(233, 30, 99) == "#e91e63", hex_of(233, 30, 99))
check("black and white come out right",
      hex_of(0, 0, 0) == "#000000" and hex_of(255, 255, 255) == "#ffffff")
check("css is what a stylesheet takes", css_of(233, 30, 99) == "rgb(233, 30, 99)",
      css_of(233, 30, 99))
check("out of range is clamped, not wrapped",
      hex_of(-5, 300, 99) == "#00ff63", hex_of(-5, 300, 99))
check("white text on a dark swatch", readable_on(20, 20, 20) == (255, 255, 255))
check("and black on a light one", readable_on(240, 240, 240) == (0, 0, 0))
check("a mid green takes black, which the cheap formula gets wrong",
      readable_on(0, 200, 0) == (0, 0, 0), str(readable_on(0, 200, 0)))
check("and a pure blue takes white", readable_on(0, 0, 255) == (255, 255, 255),
      str(readable_on(0, 0, 255)))
check("mid grey is above the crossover", readable_on(128, 128, 128) == (0, 0, 0),
      str(readable_on(128, 128, 128)))

# A screenshot where two neighbouring *physical* pixels differ, so reading the
# wrong one — or an interpolated one — cannot pass by accident.  Logical 300,250
# is physical 600,500 at this scale.
striped = Image.new("RGB", tuple(shot.size), (0, 0, 0))
for x in range(striped.width):
    for y in range(striped.height):
        striped.putpixel((x, y), (255, 0, 0) if x % 2 else (0, 0, 255))
picker = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(striped)), metrics, screen,
    dim_percent=40, mode=MODE_RECTANGLE, confirm=True, magnifier=False,
)
picker.resize(screen.geometry().size())
check("it reads the physical pixel, not an average of two",
      picker.colour_at(QPoint(300, 250)).name() == "#0000ff",
      picker.colour_at(QPoint(300, 250)).name())
check("the physical pixel beside it really is a different one",
      striped.getpixel((600, 500)) != striped.getpixel((601, 500)),
      "the test image has to be able to tell an exact read from an averaged one")
check("off the edge of the screenshot is clamped, not an error",
      picker.colour_at(QPoint(-50, -50)).isValid()
      and picker.colour_at(QPoint(99999, 99999)).isValid())

check("the chip is there before anything is taken",
      button_named(picker, BAR_MODE_COLOUR).key == "K")
check("and is not lit", not button_named(picker, BAR_MODE_COLOUR).primary)
check("no loupe yet", not picker._loupe_showing())

target = button_named(picker, BAR_MODE_COLOUR)
click(picker, (target.rect.center().x(), target.rect.center().y()))
check("the chip turns the overlay into a picker", picker._picking_colour)
check("it lights up and the shapes go out",
      button_named(picker, BAR_MODE_COLOUR).primary
      and not button_named(picker, BAR_MODE_LASSO).primary
      and not button_named(picker, BAR_MODE_RECT).primary)
check("nothing else on the row applies now",
      [placed.action for placed in picker._layout_buttons()]
      == [BAR_MODE_LASSO, BAR_MODE_RECT, BAR_MODE_COLOUR],
      str([placed.action for placed in picker._layout_buttons()]))
check("the caption says what to do", picker._bar_caption() == i18n.tr("overlay.hint.colour"))
check("the loupe is the tool now, so it is up without a drag", picker._loupe_showing())

# The screen is not dimmed while picking: asking what colour something is over
# a wash would be answering about a different picture.
picker._current = QPoint(600, 500)
bright = QPixmap(picker.size())
picker.render(bright)
check("nothing is dimmed while picking",
      bright.toImage().pixelColor(50, 500) == QColor(0, 0, 255),
      bright.toImage().pixelColor(50, 500).name())

taken: list[str] = []
picker.colour_picked.connect(taken.append)
click(picker, (300, 250))
check("a click takes the colour", taken == ["#0000ff"], str(taken))
check("and closes the overlay", picker._finished)

# Shift asks for the CSS form instead.
css = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(striped)), metrics, screen,
    dim_percent=40, mode=MODE_RECTANGLE, confirm=True,
)
css.resize(screen.geometry().size())
in_css: list[str] = []
css.colour_picked.connect(in_css.append)
press_key(css, Qt.Key.Key_K)
check("K turns it on too", css._picking_colour)
css._current = QPoint(300, 250)
press_key(css, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
check("Shift copies rgb() instead", in_css == ["rgb(0, 0, 255)"], str(in_css))

# Esc leaves the mode; the second one closes the overlay, exactly like the text
# selection and the black rectangles.
leaving = make_overlay(MODE_RECTANGLE)
quit_calls: list[bool] = []
leaving.cancelled.connect(lambda: quit_calls.append(True))
press_key(leaving, Qt.Key.Key_K)
check("picking is on", leaving._picking_colour)
press_key(leaving, Qt.Key.Key_Escape)
check("Esc goes back to selecting", not leaving._picking_colour and not quit_calls)
press_key(leaving, Qt.Key.Key_Escape)
check("and the next one really cancels", quit_calls == [True])

# It cannot be turned on when there is no chip on screen to say so.
busy, _ = confirming()
busy._set_picking_colour(True)
check("not offered once a selection is waiting", not busy._picking_colour)

# --- leaving a piece of the screen on the screen -----------------------------
# Every other thing this program does with a selection takes it away — searching
# sends it, copying hides it in the clipboard, saving buries it in a folder — and
# the commonest reason to capture something is to look at it while typing
# somewhere else.
from PyQt6.QtGui import QWheelEvent  # noqa: E402

from circle_to_search import PINNED_WINDOW_TITLE  # noqa: E402
from circle_to_search.pinned import (  # noqa: E402
    MAX_SCALE,
    MAX_SHARE,
    MIN_SCALE,
    MIN_SIDE,
    PinnedCrop,
)

pinning, results = confirming()
check("the bar offers it", button_named(pinning, ACTION_PIN).key == "P")
check("and it sits with the other places a crop can go",
      [placed.action for placed in pinning._layout_buttons()][:4]
      == [ACTION_SEARCH, ACTION_COPY, ACTION_SAVE, ACTION_PIN],
      str([placed.action for placed in pinning._layout_buttons()]))
target = button_named(pinning, ACTION_PIN)
click(pinning, (target.rect.center().x(), target.rect.center().y()))
check("clicking it hands the crop over", ACTION_PIN in results, str(list(results)))

by_p, results = confirming()
press_key(by_p, Qt.Key.Key_P)
check("P does the same", ACTION_PIN in results, str(list(results)))
check("and it goes through the same commit as the rest, so a redaction and a "
      "lasso mask apply to it too",
      results[ACTION_PIN][0] == QRect(200, 200, 400, 300), str(results[ACTION_PIN][0]))


def wheel(widget, notches: int) -> None:
    widget.wheelEvent(
        QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, notches * 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
    )


crop = QPixmap(320, 120)
crop.fill(QColor("#fdfdfd"))
pin = PinnedCrop(crop)
check("a pin is the size of what was cropped", pin.size() == QSize(320, 120), str(pin.size()))
check("frameless, above everything, out of the switcher",
      bool(pin.windowFlags() & Qt.WindowType.FramelessWindowHint)
      and bool(pin.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
      and bool(pin.windowFlags() & Qt.WindowType.Tool))
check("and it does not steal the focus to appear",
      pin.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
check("the KWin script can find it by caption",
      pin.windowTitle() == PINNED_WINDOW_TITLE, pin.windowTitle())

wheel(pin, 5)
check("the wheel zooms", pin.scale() > 1.0 and pin.size().width() > 320, str(pin.size()))
wheel(pin, -60)
check("and stops at the floor", abs(pin.scale() - MIN_SCALE) < 1e-6, str(pin.scale()))
check("without becoming too small to click",
      pin.width() >= MIN_SIDE and pin.height() >= MIN_SIDE, str(pin.size()))
wheel(pin, 200)
check("and at the ceiling", abs(pin.scale() - MAX_SCALE) < 1e-6, str(pin.scale()))
press_key(pin, Qt.Key.Key_0)
check("0 puts it back to life size", pin.scale() == 1.0 and pin.size() == QSize(320, 120),
      str(pin.size()))

handed: list[QSize] = []
pin.copy_requested.connect(lambda pixmap: handed.append(pixmap.size()))
press_key(pin, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
check("Ctrl+C turns a pin into the copy action", handed == [QSize(320, 120)], str(handed))
check("and it is the crop, not what is on screen at this zoom",
      handed == [QSize(crop.width(), crop.height())])

shut: list[bool] = []
pin.closed.connect(lambda: shut.append(True))
press_key(pin, Qt.Key.Key_Escape)
check("Esc closes it", shut == [True] and not pin.isVisible())

middle = PinnedCrop(crop)
by_middle: list[bool] = []
middle.closed.connect(lambda: by_middle.append(True))
middle.mousePressEvent(
    QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton,
                Qt.KeyboardModifier.NoModifier)
)
check("a middle click closes it without needing the focus first", by_middle == [True])

# A selection can be the whole panel, and pinning that at its own size would
# cover the thing it was taken from.
whole = QPixmap(3840, 2160)
whole.fill(QColor("#336699"))
huge = PinnedCrop(whole)
huge.fit_to_screen(QRect(0, 0, 1920, 1080))
check("a screen-sized crop is scaled down to be a pin",
      huge.width() <= 1920 * MAX_SHARE + 1 and huge.height() <= 1080 * MAX_SHARE + 1,
      str(huge.size()))
check("and keeps its shape", abs(huge.width() / huge.height() - 3840 / 2160) < 0.01,
      str(huge.size()))
small = QPixmap(200, 100)
small.fill(QColor("#336699"))
untouched = PinnedCrop(small)
untouched.fit_to_screen(QRect(0, 0, 1920, 1080))
check("one that already fits is left at life size",
      untouched.scale() == 1.0 and untouched.size() == QSize(200, 100), str(untouched.size()))

# Drawn over an unknown window, so it needs an edge of its own — a white crop on
# a white background otherwise has no shape at all.
painted = QPixmap(untouched.size())
untouched.render(painted)
edge = painted.toImage()
check("it has a rim", edge.pixelColor(0, 0) != QColor("#336699"), edge.pixelColor(0, 0).name())
check("and the crop is inside it", edge.pixelColor(100, 50) == QColor("#336699"),
      edge.pixelColor(100, 50).name())

# --- the same area as last time --------------------------------------------
# Comparing a number that changes means taking the same rectangle twice, and a
# rectangle drawn by hand is never quite the same twice — which is exactly what
# makes the two results incomparable.
from circle_to_search.app import CircleToSearchApp  # noqa: E402
from circle_to_search.lastarea import (  # noqa: E402
    EVERY_SCREEN,
    LastArea,
    format_area,
    parse_area,
)
from circle_to_search.overlay import BAR_LAST_AREA  # noqa: E402

check("a round trip survives",
      parse_area(format_area("eDP-1", QRect(10, 20, 300, 200)))
      == LastArea("eDP-1", QRect(10, 20, 300, 200)),
      format_area("eDP-1", QRect(10, 20, 300, 200)))
check("it is readable by a person",
      format_area("eDP-1", QRect(10, 20, 300, 200)) == "eDP-1 10 20 300 200",
      format_area("eDP-1", QRect(10, 20, 300, 200)))
check("a nameless screen is not remembered", format_area("", QRect(10, 20, 300, 200)) == "")
check("nor is a slip of a selection", format_area("eDP-1", QRect(10, 20, 4, 300)) == "")
for junk in ("", "   ", "eDP-1", "eDP-1 1 2 3", "eDP-1 1 2 3 4 5", "eDP-1 a b c d",
             "eDP-1 1 2 3 x", "eDP-1 0 0 2 2"):
    check(f"junk is refused: {junk!r}", parse_area(junk) is None, str(parse_area(junk)))
check("a group's rectangle is filed under every screen",
      parse_area(format_area(EVERY_SCREEN, QRect(1900, 0, 400, 300))).screen == EVERY_SCREEN)
check("and only offered back to a group",
      not parse_area("* 0 0 40 30").matches("eDP-1")
      and parse_area("* 0 0 40 30").matches(EVERY_SCREEN))


def with_memory(area: QRect | None):
    view = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(shot)),
        metrics,
        screen,
        dim_percent=40,
        mode=MODE_LASSO,
        confirm=True,
        last_area=area,
    )
    view.resize(screen.geometry().size())
    return view


forgetful = with_memory(None)
check("no chip when there is nothing to offer",
      [placed.action for placed in forgetful._layout_buttons()]
      == [BAR_MODE_LASSO, BAR_MODE_RECT, BAR_MODE_COLOUR],
      str([placed.action for placed in forgetful._layout_buttons()]))
press_key(forgetful, Qt.Key.Key_R)
check("and R does nothing either", not forgetful._has_selection)

again = with_memory(QRect(120, 90, 300, 200))
check("one more chip when there is",
      [placed.action for placed in again._layout_buttons()]
      == [BAR_MODE_LASSO, BAR_MODE_RECT, BAR_MODE_COLOUR, BAR_LAST_AREA],
      str([placed.action for placed in again._layout_buttons()]))
check("with a key of its own", button_named(again, BAR_LAST_AREA).key == "R")
target = button_named(again, BAR_LAST_AREA)
click(again, (target.rect.center().x(), target.rect.center().y()))
check("it selects exactly where the last one was",
      again._selection_rect() == QRect(120, 90, 300, 200), str(again._selection_rect()))
check("and hands straight over to the action bar", again._confirming
      and [placed.action for placed in again._layout_buttons()]
      == [ACTION_SEARCH, ACTION_COPY, ACTION_SAVE, ACTION_PIN, BAR_REDACT, BAR_CANCEL])
check("as a rectangle, with no lasso outline to mask against",
      again.selection_polygon().count() == 0)

by_key = with_memory(QRect(120, 90, 300, 200))
press_key(by_key, Qt.Key.Key_R)
check("R does the same thing as the chip",
      by_key._selection_rect() == QRect(120, 90, 300, 200), str(by_key._selection_rect()))
press_key(by_key, Qt.Key.Key_R)
check("and does nothing once a selection is waiting",
      by_key._selection_rect() == QRect(120, 90, 300, 200), str(by_key._selection_rect()))

# The screen may have been rearranged since; the rectangle is clamped to what
# is there now, and dropped outright when nothing of it is left.
gone = with_memory(QRect(5000, 5000, 300, 200))
gone._use_last_area()
check("an area that is off the screen now is not offered",
      not gone._has_selection, str(gone._selection_rect()))
clipped = with_memory(QRect(screen.geometry().width() - 100, 100, 300, 200))
clipped._use_last_area()
check("one hanging over the edge is clamped to it",
      clipped._has_selection
      and screen.geometry().contains(clipped._selection_rect()),
      str(clipped._selection_rect()))


# What the application does either side of that: which screen a remembered
# rectangle is offered to, and in whose coordinates.
class _AreaStore:
    def __init__(self, value: str = "") -> None:
        self.last_area = value
        self.synced = 0

    def sync(self) -> None:
        self.synced += 1


class _AreaHost:
    """Just enough of the application to exercise the two ends of the memory."""

    _last_area_for = CircleToSearchApp._last_area_for
    _remember_area = CircleToSearchApp._remember_area

    def __init__(self, value: str = "") -> None:
        self._settings = _AreaStore(value)


host = _AreaHost("eDP-1 10 20 300 200")
check("offered back to the screen it came from",
      host._last_area_for("eDP-1") == QRect(10, 20, 300, 200),
      str(host._last_area_for("eDP-1")))
check("and to no other", host._last_area_for("HDMI-A-1").isNull(),
      str(host._last_area_for("HDMI-A-1")))
check("nothing remembered, nothing offered", _AreaHost()._last_area_for("eDP-1").isNull())
check("nor from a corrupt line", _AreaHost("what")._last_area_for("eDP-1").isNull())

# In a group the rectangle is global; each overlay wants it in its own space.
grouped = _AreaHost("* 1900 40 400 300")
check("a group's rectangle is shifted onto each screen",
      grouped._last_area_for(EVERY_SCREEN, origin=QPoint(1920, 0)) == QRect(-20, 40, 400, 300),
      str(grouped._last_area_for(EVERY_SCREEN, origin=QPoint(1920, 0))))
check("and the leftmost screen keeps it as it is",
      grouped._last_area_for(EVERY_SCREEN, origin=QPoint(0, 0)) == QRect(1900, 40, 400, 300))

writing = _AreaHost()
writing._remember_area("eDP-1", QRect(10, 20, 300, 200))
check("committing writes it out", writing._settings.last_area == "eDP-1 10 20 300 200"
      and writing._settings.synced == 1, writing._settings.last_area)
writing._remember_area("eDP-1", QRect(10, 20, 300, 200))
check("the same area again is not written twice", writing._settings.synced == 1,
      str(writing._settings.synced))
writing._remember_area("eDP-1", QRect(0, 0, 2, 2))
check("and a slip does not overwrite a real one",
      writing._settings.last_area == "eDP-1 10 20 300 200", writing._settings.last_area)

# --- the magnifier ---------------------------------------------------------
# Arrow-key nudging exists because precision was missing, but it only helps
# after the miss.  The loupe answers the same problem while the edge is still
# being placed — and it is a matter of taste, so it is a switch.
loupe = make_overlay(MODE_RECTANGLE)
check("no loupe before a drag", not loupe._loupe_showing())
_mouse(loupe, QEvent.Type.MouseButtonPress, (200, 200))
_mouse(loupe, QEvent.Type.MouseMove, (400, 340))
check("a drag brings it up", loupe._loupe_showing())
check("it is square, and the size it says it is",
      loupe._loupe_rect().width() == loupe._loupe_rect().height(), str(loupe._loupe_rect()))
check("beside the pointer, not under it", not loupe._loupe_rect().contains(QPoint(400, 340)),
      str(loupe._loupe_rect()))
check("and on the screen", loupe._visible_area().toRect().contains(loupe._loupe_rect()),
      str(loupe._loupe_rect()))
_mouse(loupe, QEvent.Type.MouseButtonRelease, (400, 340))
check("letting go takes it away", not loupe._loupe_showing())

# It comes back for a handle, which is the other half of aiming.
check("the drag is waiting to be confirmed", loupe._confirming)
corner = loupe._handle_rects()["se"].center()
_mouse(loupe, QEvent.Type.MouseButtonPress, (corner.x(), corner.y()))
check("moving a handle brings it back", loupe._loupe_showing())
_mouse(loupe, QEvent.Type.MouseButtonRelease, (corner.x(), corner.y()))
check("and letting the handle go takes it away", not loupe._loupe_showing())

# In the corner it has to flip rather than hang off the edge — and the readout
# has to give way to it, because both want the space below and right.
corner_loupe = make_overlay(MODE_RECTANGLE)
_mouse(corner_loupe, QEvent.Type.MouseButtonPress, (200, 200))
far = (corner_loupe.width() - 6, corner_loupe.height() - 6)
_mouse(corner_loupe, QEvent.Type.MouseMove, far)
placed = corner_loupe._loupe_rect()
check("in the corner it flips", placed.right() < far[0] and placed.bottom() < far[1],
      f"{placed} vs {far}")
check("and stays wholly on screen",
      corner_loupe._visible_area().toRect().contains(placed), str(placed))
_text, _font, readout = corner_loupe._size_label(corner_loupe._selection_rect())
check("the readout gives way to it", not readout.intersects(placed), f"{readout} vs {placed}")
check("and stays on screen itself",
      corner_loupe._visible_area().toRect().contains(readout), str(readout))

# Switched off it does not exist at all, however hard it is dragged.
plain = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(shot)),
    metrics,
    screen,
    dim_percent=40,
    mode=MODE_RECTANGLE,
    magnifier=False,
)
plain.resize(screen.geometry().size())
_mouse(plain, QEvent.Type.MouseButtonPress, (200, 200))
_mouse(plain, QEvent.Type.MouseMove, (400, 340))
check("switched off, there is no loupe", not plain._loupe_showing())
plain.render(QPixmap(plain.size()))
check("and the drag still paints", True)

# It shows the frozen screen magnified, not the dimming over it: a green
# screenshot has to come out green inside the circle even where the screen
# around it has been darkened.
zoomed = make_overlay(MODE_RECTANGLE)
_mouse(zoomed, QEvent.Type.MouseButtonPress, (200, 200))
_mouse(zoomed, QEvent.Type.MouseMove, (600, 600))
canvas = QPixmap(zoomed.size())
zoomed.render(canvas)
lens_image = canvas.toImage()
spot = zoomed._loupe_rect().center()
in_lens = lens_image.pixelColor(spot.x() - 20, spot.y() - 20)
outside = lens_image.pixelColor(spot.x(), zoomed._loupe_rect().top() - 20)
check("the loupe is not dimmed", in_lens.green() > outside.green() + 30,
      f"{in_lens.name()} vs {outside.name()}")

# --- the overlay paints its dimming instead of keeping a second copy -------
# Two full-resolution pixmaps are ~66 MB on a 4K screen, and with an overlay per
# monitor that multiplies.  What matters is that the result still looks right:
# the selection bright, everything else darkened.
check("only one screenshot is held", not hasattr(rect_overlay, "_dimmed"))

bright_shot = Image.new("RGB", (screen.geometry().width() * 2,
                               screen.geometry().height() * 2), (200, 200, 200))
dim_overlay = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(bright_shot)),
    hidpi.measure_screen(screen.name(), screen.geometry(), bright_shot.size, 2.0),
    screen,
    dim_percent=50,
    mode=MODE_RECTANGLE,
    confirm=True,
)
dim_overlay.resize(screen.geometry().size())

canvas = QPixmap(dim_overlay.size())
dim_overlay.render(canvas)
everything = canvas.toImage()
check("with no selection the whole screen is dimmed",
      everything.pixelColor(400, 400).red() < 150,
      str(everything.pixelColor(400, 400).red()))

drag(dim_overlay, (100, 100), (300, 250))
canvas = QPixmap(dim_overlay.size())
dim_overlay.render(canvas)
painted = canvas.toImage()
# Away from the middle: that is where the move grip is drawn now.
inside = painted.pixelColor(140, 130).red()
outside = painted.pixelColor(600, 600).red()
check("the selection is not dimmed", inside > 180, str(inside))
check("everything else is", outside < 150, str(outside))
check("and there is a real difference", inside - outside > 40, f"{inside} vs {outside}")

# dim_percent=0 means no dimming at all, not a black screen.
clear_overlay = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(bright_shot)),
    hidpi.measure_screen(screen.name(), screen.geometry(), bright_shot.size, 2.0),
    screen,
    dim_percent=0,
    mode=MODE_RECTANGLE,
)
clear_overlay.resize(screen.geometry().size())
canvas = QPixmap(clear_overlay.size())
clear_overlay.render(canvas)
check("zero dimming leaves the screenshot alone",
      canvas.toImage().pixelColor(400, 400).red() > 190,
      str(canvas.toImage().pixelColor(400, 400).red()))

# --- the text layer on the overlay -----------------------------------------
# Two lines of a paragraph and one line of another, at 2x, so the physical
# boxes tesseract reports have to be halved to land on screen.
sample_words = [
    Word("Hello", 200, 200, 100, 30, 95.0, (1, 1, 1, 1)),
    Word("there", 320, 200, 100, 30, 94.0, (1, 1, 1, 2)),
    Word("second", 200, 260, 140, 30, 92.0, (1, 1, 2, 1)),
    Word("elsewhere", 200, 400, 200, 30, 90.0, (2, 1, 1, 1)),
]

text_overlay = make_overlay(MODE_RECTANGLE)
picked: list[str] = []
text_overlay.text_selected.connect(picked.append)

check("no text layer to begin with", not text_overlay.has_words)
text_overlay.set_words(sample_words)
check("the words arrive", text_overlay.has_words)

# Physical 200,200 100x30 at scale 2 is 100,100 50x15 on screen.
first = text_overlay._words[0]
check("boxes are converted to screen pixels", first.rect == QRect(100, 100, 50, 15),
      str(first.rect))
check("the reading order is kept",
      [word.text for word in text_overlay._words] == ["Hello", "there", "second", "elsewhere"],
      str([w.text for w in text_overlay._words]))

# Dragging across words takes the text; dragging elsewhere still takes an area.
drag(text_overlay, (110, 105), (180, 135))
check("dragging over words selects text", text_overlay.has_text_selection())
check("and not an area", not text_overlay._has_selection)
check("the run reads in order", text_overlay.selected_text() == "Hello there\nsecond",
      repr(text_overlay.selected_text()))

press_key(text_overlay, Qt.Key.Key_C)
check("C copies the text", picked == ["Hello there\nsecond"], str(picked))

# Esc lets go of the text without throwing the capture away.
text_overlay2 = make_overlay(MODE_RECTANGLE)
cancels2: list[bool] = []
text_overlay2.cancelled.connect(lambda: cancels2.append(True))
text_overlay2.set_words(sample_words)
drag(text_overlay2, (110, 105), (140, 105))
check("a short drag still selects", text_overlay2.has_text_selection())
press_key(text_overlay2, Qt.Key.Key_Escape)
check("Esc drops the text selection", not text_overlay2.has_text_selection())
check("and does not cancel yet", not cancels2)
press_key(text_overlay2, Qt.Key.Key_Escape)
check("a second Esc cancels", cancels2 == [True])

# T takes everything that was recognised.
text_overlay3 = make_overlay(MODE_RECTANGLE)
text_overlay3.set_words(sample_words)
press_key(text_overlay3, Qt.Key.Key_T)
check("T selects all of it", len(text_overlay3.selected_words()) == 4,
      str(len(text_overlay3.selected_words())))
check("paragraphs are kept apart",
      text_overlay3.selected_text() == "Hello there\nsecond\n\nelsewhere",
      repr(text_overlay3.selected_text()))

# Reported from a real desktop: with an area already selected, pressing inside
# it grabbed the rectangle and dragged it, and the text underneath never had a
# chance.  Text outranks moving the box.
priority = make_overlay(MODE_RECTANGLE)
priority.set_words(sample_words)
drag(priority, (80, 80), (260, 130))          # an area covering all the text
check("the area is confirmed", priority._confirming and priority._has_selection)
box_before = QRect(priority._selection_rect())
drag(priority, (110, 105), (180, 108))        # now press on a word inside it
check("text wins over moving the box", priority.has_text_selection(),
      str(priority._selection_rect()))
check("and the box is gone rather than moved",
      not priority._has_selection or priority._selection_rect() == box_before,
      str(priority._selection_rect()))

# The corners still resize, or a box drawn over a paragraph could never be
# adjusted again.
handles = make_overlay(MODE_RECTANGLE)
handles.set_words(sample_words)
drag(handles, (80, 80), (260, 130))
corner = handles._handle_rects()["se"].center()
drag(handles, (corner.x(), corner.y()), (corner.x() + 20, corner.y() + 20))
check("a corner handle still resizes", not handles.has_text_selection()
      and handles._selection_rect().width() > box_before.width(),
      str(handles._selection_rect()))

# Aiming: the gap between two words on a line is still that line.
aim = make_overlay(MODE_RECTANGLE)
aim.set_words(sample_words)
gap = QPoint(155, 107)                        # between "Hello" and "there"
check("the gap between words is text", aim._word_at(gap) is not None, str(gap))
below = QPoint(120, 190)                      # well away from any line
check("empty screen is not text", aim._word_at(below) is None, str(below))

# The affordance: recognised text stays lit while the rest of the screen dims,
# which is the part that has to be visible before the pointer goes near it.
lit_shot = Image.new("RGB", (screen.geometry().width() * 2,
                            screen.geometry().height() * 2), (200, 200, 200))
lit = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(lit_shot)),
    hidpi.measure_screen(screen.name(), screen.geometry(), lit_shot.size, 2.0),
    screen,
    dim_percent=50,
    mode=MODE_RECTANGLE,
)
lit.resize(screen.geometry().size())
canvas = QPixmap(lit.size())
lit.render(canvas)
dark_everywhere = canvas.toImage().pixelColor(120, 105).red()
lit.set_words(sample_words)
canvas = QPixmap(lit.size())
lit.render(canvas)
shown = canvas.toImage()
on_text = shown.pixelColor(120, 105).red()          # inside "Hello"
off_text = shown.pixelColor(600, 600).red()         # nowhere near it
check("text is lit once it is recognised", on_text > dark_everywhere + 30,
      f"{dark_everywhere} -> {on_text}")
check("and the rest of the screen is not", on_text > off_text + 30,
      f"{on_text} vs {off_text}")

# Reported from a real desktop: with an area drawn, the marks over the text
# disappeared, so there was no way to tell it could still be taken.
kept_marks = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(lit_shot)),
    hidpi.measure_screen(screen.name(), screen.geometry(), lit_shot.size, 2.0),
    screen,
    dim_percent=50,
    mode=MODE_RECTANGLE,
)
kept_marks.resize(screen.geometry().size())
kept_marks.set_words(sample_words)
drag(kept_marks, (400, 400), (700, 700))          # an area away from the text
canvas = QPixmap(kept_marks.size())
kept_marks.render(canvas)
with_box = canvas.toImage()
check("the text marks survive an area selection",
      with_box.pixelColor(120, 105) != with_box.pixelColor(600, 300),
      f"{with_box.pixelColor(120, 105).name()} vs {with_box.pixelColor(600, 300).name()}")

# Selected text is drawn as one run per line, the way selected text looks
# everywhere else, rather than a separate box around each word.
runs = make_overlay(MODE_RECTANGLE)
runs.set_words(sample_words)
runs.select_all_text()
canvas = QPixmap(runs.size())
runs.render(canvas)
between = canvas.toImage().pixelColor(155, 107)   # the space between two words
inside = canvas.toImage().pixelColor(120, 107)    # inside the first word
check("the gap between selected words is filled too", between == inside,
      f"{between.name()} vs {inside.name()}")

# The bar changes with the state: selected text offers what you can do to text.
text_bar = make_overlay(MODE_RECTANGLE)
copied: list[str] = []
text_bar.text_selected.connect(copied.append)
text_bar.set_words(sample_words)
check("still the mode chips before anything is taken",
      [placed.action for placed in text_bar._layout_buttons()]
      == [BAR_MODE_LASSO, BAR_MODE_RECT, BAR_MODE_COLOUR],
      str([placed.action for placed in text_bar._layout_buttons()]))
drag(text_bar, (110, 105), (180, 135))
actions = [placed.action for placed in text_bar._layout_buttons()]
check("four buttons for a text selection",
      actions == [BAR_TEXT_SEARCH, BAR_TEXT_COPY, BAR_TEXT_ALL, BAR_TEXT_BACK], str(actions))
check("searching leads, the way it does for an area",
      button_named(text_bar, BAR_TEXT_SEARCH).primary
      and button_named(text_bar, BAR_TEXT_SEARCH).key == "Enter")
check("and copying keeps the same key as the area bar",
      button_named(text_bar, BAR_TEXT_COPY).key == "C")
check("the count is on the button itself",
      "3" in button_named(text_bar, BAR_TEXT_COPY).label,
      button_named(text_bar, BAR_TEXT_COPY).label)
check("the bar hangs off the words, not off an area",
      text_bar._bar_anchor().top() >= 100 and text_bar._bar_anchor().bottom() <= 200,
      str(text_bar._bar_anchor()))

target = button_named(text_bar, BAR_TEXT_ALL)
click(text_bar, (target.rect.center().x(), target.rect.center().y()))
check("All text takes everything", len(text_bar.selected_words()) == 4,
      str(len(text_bar.selected_words())))
check("and the label follows the selection",
      "4" in button_named(text_bar, BAR_TEXT_COPY).label,
      button_named(text_bar, BAR_TEXT_COPY).label)

target = button_named(text_bar, BAR_TEXT_COPY)
click(text_bar, (target.rect.center().x(), target.rect.center().y()))
check("Copy text hands over the whole run",
      copied == ["Hello there\nsecond\n\nelsewhere"], str(copied))

# Searching for the words themselves, which needs no upload at all.
searched: list[str] = []
text_search = make_overlay(MODE_RECTANGLE)
text_search.text_search_requested.connect(searched.append)
text_search.set_words(sample_words)
drag(text_search, (110, 105), (180, 135))
target = button_named(text_search, BAR_TEXT_SEARCH)
click(text_search, (target.rect.center().x(), target.rect.center().y()))
check("Search hands over the words, not a picture", searched == ["Hello there\nsecond"],
      str(searched))
check("and stays up to say the browser is coming", text_search.is_sending())

# Enter searches and C copies, the same two keys as the area bar below.
keys = make_overlay(MODE_RECTANGLE)
by_key: list[str] = []
keys.text_search_requested.connect(lambda text: by_key.append(f"search:{text}"))
keys.text_selected.connect(lambda text: by_key.append(f"copy:{text}"))
keys.set_words(sample_words)
drag(keys, (110, 105), (180, 135))
press_key(keys, Qt.Key.Key_Return)
check("Enter searches for the text", by_key == ["search:Hello there\nsecond"], str(by_key))

keys2 = make_overlay(MODE_RECTANGLE)
keys2.text_search_requested.connect(lambda text: by_key.append(f"search:{text}"))
keys2.text_selected.connect(lambda text: by_key.append(f"copy:{text}"))
keys2.set_words(sample_words)
drag(keys2, (110, 105), (180, 135))
by_key.clear()
press_key(keys2, Qt.Key.Key_C)
check("C still copies it", by_key == ["copy:Hello there\nsecond"], str(by_key))

# A single word that is a link is offered as a link instead of a search.
link_bar = make_overlay(MODE_RECTANGLE)
link_bar.set_words([Word("example.com", 200, 200, 260, 30, 95.0, (1, 1, 1, 1))])
drag(link_bar, (110, 105), (220, 112))
check("one word selected", len(link_bar.selected_words()) == 1)
check("the button offers to open it", button_named(link_bar, BAR_TEXT_SEARCH).label
      == i18n.tr("bar.open_link"), button_named(link_bar, BAR_TEXT_SEARCH).label)

sentence_bar = make_overlay(MODE_RECTANGLE)
sentence_bar.set_words(sample_words)
drag(sentence_bar, (110, 105), (180, 135))
check("prose is a search, not a link",
      button_named(sentence_bar, BAR_TEXT_SEARCH).label == i18n.tr("bar.search_text"),
      button_named(sentence_bar, BAR_TEXT_SEARCH).label)

# Back drops the text without throwing the capture away, exactly like Esc.
text_bar2 = make_overlay(MODE_RECTANGLE)
gone: list[bool] = []
text_bar2.cancelled.connect(lambda: gone.append(True))
text_bar2.set_words(sample_words)
drag(text_bar2, (110, 105), (180, 135))
target = button_named(text_bar2, BAR_TEXT_BACK)
click(text_bar2, (target.rect.center().x(), target.rect.center().y()))
check("Back lets go of the text", not text_bar2.has_text_selection())
check("and does not cancel the capture", not gone)

# A drag that starts on empty screen is an ordinary area selection, even with a
# text layer present — otherwise the feature would take the app over.
text_overlay4 = make_overlay(MODE_RECTANGLE)
text_overlay4.set_words(sample_words)
drag(text_overlay4, (500, 500), (600, 560))
check("empty space still selects an area", text_overlay4._has_selection
      and not text_overlay4.has_text_selection(), str(text_overlay4._selection_rect()))

# The badge only animates while something is actually being read.
scan_overlay = make_overlay(MODE_RECTANGLE)
check("no timer before scanning", scan_overlay._scan_timer is None)
scan_overlay.set_scanning(True)
check("scanning starts a timer", scan_overlay._scan_timer is not None)
scan_overlay.render(QPixmap(scan_overlay.size()))
check("the badge paints", True)
scan_overlay.set_words(sample_words)
check("words stop the timer",
      scan_overlay._scan_timer is None and scan_overlay._badge == BADGE_NONE,
      scan_overlay._badge)

scan_overlay2 = make_overlay(MODE_RECTANGLE)
scan_overlay2.set_scanning(True)
scan_overlay2._cancel()
check("finishing stops the timer too", scan_overlay2._scan_timer is None)

# --- saying that the sending is happening ----------------------------------
# The overlay used to disappear the instant Enter was pressed, and the browser
# arrives a second or more later: in between there was nothing at all on
# screen, which is the same complaint the loading badge was written for.
sending, results = confirming()
sending.show()
sending.render(QPixmap(sending.size()))     # a selection waiting, no badge
check("nothing is being sent yet", not sending.is_sending())
press_key(sending, Qt.Key.Key_Return)
check("Enter still sends the crop", ACTION_SEARCH in results, str(list(results)))
check("and the overlay stays up saying so", sending.is_sending() and sending.isVisible(),
      f"{sending.is_sending()} {sending.isVisible()}")
check("the badge animates", sending._scan_timer is not None)
check("with a dead-man's switch behind it",
      sending._dismiss_timer is not None and sending._dismiss_timer.isActive())
check("it does not outstay its welcome",
      sending._dismiss_timer.interval() == SENDING_LIMIT_MS,
      str(sending._dismiss_timer.interval()))
check("the selection is still shown under it", sending._has_selection)
sending.render(QPixmap(sending.size()))
check("the sending badge paints", True)
check("and nothing invites another click", sending._layout_buttons() == [],
      str(sending._layout_buttons()))

dismissals: list[bool] = []
sending.dismissed.connect(lambda: dismissals.append(True))
sending.dismiss()
check("dismissing takes it down", not sending.is_sending() and not sending.isVisible(),
      f"{sending.is_sending()} {sending.isVisible()}")
check("and says so once", dismissals == [True], str(dismissals))
check("the timers are stopped",
      sending._scan_timer is None and sending._dismiss_timer is None)
sending.dismiss()
check("dismissing twice is not two dismissals", dismissals == [True], str(dismissals))

# Nothing may be sent twice, whatever is pressed at it.
again, results = confirming()
press_key(again, Qt.Key.Key_Return)
box_while_sending = QRect(again._selection_rect())
drag(again, (400, 400), (500, 500))
press_key(again, Qt.Key.Key_Return)
check("a second Enter sends nothing more", list(results) == [ACTION_SEARCH], str(list(results)))
check("and a drag redraws nothing", again._selection_rect() == box_while_sending,
      str(again._selection_rect()))

# Copying and saving are done by the time the overlay would have closed, so a
# badge for them would be claiming to wait for something that already happened.
for key, expected in ((Qt.Key.Key_C, ACTION_COPY), (Qt.Key.Key_S, ACTION_SAVE)):
    instant, results = confirming()
    press_key(instant, key)
    check(f"{expected} closes the overlay outright",
          not instant.is_sending() and not instant.isVisible(), f"{expected}")
    check(f"and still {expected}s", list(results) == [expected], str(list(results)))

# Esc cancels without any of it.
quiet, results = confirming()
press_key(quiet, Qt.Key.Key_Escape)
check("cancelling never says sending", not quiet.is_sending())

# Any key or click takes the badge away early: it is a progress note, not a
# question, and the user may want the screen back at once.
for how in ("key", "click"):
    early, results = confirming()
    press_key(early, Qt.Key.Key_Return)
    if how == "key":
        press_key(early, Qt.Key.Key_Escape)
    else:
        click(early, (400, 400))
    check(f"a {how} dismisses the badge", not early.is_sending(), how)
    check(f"and a {how} cancels nothing", list(results) == [ACTION_SEARCH], str(list(results)))

# The browser stealing the focus is the sending having worked, not the user
# walking away — the badge goes, the capture does not come back as cancelled.
stolen, results = confirming()
press_key(stolen, Qt.Key.Key_Return)
stolen._accept_deactivation = True
stolen.changeEvent(QEvent(QEvent.Type.ActivationChange))
check("losing the focus ends the badge", not stolen.is_sending())
check("and does not report a cancellation", "cancelled" not in results, str(list(results)))

# Without the confirmation step the release sends straight away — and that path
# has to say so too, or the whole point is missed by the people who turned the
# confirmation off precisely because they want it quick.
quick = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(shot)),
    metrics,
    screen,
    dim_percent=40,
    mode=MODE_RECTANGLE,
    confirm=False,
)
quick.resize(screen.geometry().size())
quick_sent: list[tuple] = []
quick.selected.connect(lambda r, p: quick_sent.append((r, p)))
drag(quick, (100, 100), (300, 250))
check("send-on-release sends", len(quick_sent) == 1, str(quick_sent))
check("and says so as well", quick.is_sending())
quick.dismiss()

# Painting every state has to work, since a crash here takes the capture with it.
for state in ("hints", "selection"):
    painted = make_overlay(MODE_RECTANGLE)
    painted.set_words(sample_words)
    if state == "selection":
        painted.select_all_text()
    painted.render(QPixmap(painted.size()))
check("the text layer paints in every state", True)

# --- selecting across more than one screen ---------------------------------
from circle_to_search.multiscreen import (  # noqa: E402
    OverlayGroup,
    ScreenShot,
    VirtualDesktop,
)

# Two screens side by side at different scales — the case that makes stitching
# more than a paste: 1920x1080 logical at 200 %, and 1280x1024 at 100 %.
left_metrics = hidpi.measure_screen("eDP-1", QRect(0, 0, 1920, 1080), (3840, 2160))
right_metrics = hidpi.measure_screen("HDMI-A-1", QRect(1920, 0, 1280, 1024), (1280, 1024))
left_shot = ScreenShot(left_metrics, Image.new("RGB", (3840, 2160), (255, 0, 0)))
right_shot = ScreenShot(right_metrics, Image.new("RGB", (1280, 1024), (0, 0, 255)))
desktop = VirtualDesktop([left_shot, right_shot])

check("desktop bounds", desktop.bounds == QRect(0, 0, 3200, 1080), str(desktop.bounds))
check("the sharper screen sets the scale", desktop.scale_for(QRect(0, 0, 3200, 1080)) == 2.0,
      str(desktop.scale_for(QRect(0, 0, 3200, 1080))))
check("one screen keeps its own scale", desktop.scale_for(QRect(2000, 0, 100, 100)) == 1.0,
      str(desktop.scale_for(QRect(2000, 0, 100, 100))))
check("screen lookup", desktop.screen_at(QPoint(2000, 10)).name == "HDMI-A-1")
check("no screen out there", desktop.screen_at(QPoint(9000, 9000)) is None)

# A crop wholly inside the left screen must come out exactly as it would have
# without any of this: same size, same pixels.
inside = desktop.compose(QRect(100, 100, 200, 150))
check("a single-screen crop keeps its scale", inside.size == (400, 300), str(inside.size))
check("and its pixels", inside.getpixel((10, 10)) == (255, 0, 0), str(inside.getpixel((10, 10))))

# A crop straddling the seam: 100 logical px from each side, composed at the
# higher scale, so 400 physical px wide with the seam exactly in the middle.
across = desktop.compose(QRect(1820, 200, 200, 100))
check("a spanning crop is composed", across.size == (400, 200), str(across.size))
check("the left half comes from the left screen",
      across.getpixel((10, 10)) == (255, 0, 0), str(across.getpixel((10, 10))))
check("the right half comes from the right screen",
      across.getpixel((390, 10)) == (0, 0, 255), str(across.getpixel((390, 10))))

# The right screen is shorter: below its bottom edge there is nothing to show.
gapped = VirtualDesktop([
    left_shot,
    ScreenShot(
        hidpi.measure_screen("HDMI-A-1", QRect(1920, 0, 1280, 400), (1280, 400)),
        Image.new("RGB", (1280, 400), (0, 0, 255)),
    ),
])
gap = gapped.compose(QRect(1900, 300, 200, 200))
check("a gap in the layout is black", gap.getpixel((390, 390)) == (0, 0, 0),
      str(gap.getpixel((390, 390))))
check("and the screen part is not", gap.getpixel((10, 10)) == (255, 0, 0),
      str(gap.getpixel((10, 10))))

# Selections that miss every screen are refused rather than silently empty.
try:
    desktop.compose(QRect(9000, 9000, 100, 100))
except ValueError as exc:
    check("an off-desktop selection is refused", "does not overlap" in str(exc), str(exc))
else:
    check("an off-desktop selection is refused", False)
try:
    VirtualDesktop([])
except ValueError:
    check("an empty desktop is refused", True)
else:
    check("an empty desktop is refused", False)


def group_overlay(shot: ScreenShot, bounds: QRect) -> SelectionOverlay:
    overlay = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(Image.new("RGB", (40, 40), "green"))),
        shot.metrics,
        screen,
        dim_percent=40,
        mode=MODE_RECTANGLE,
        confirm=True,
        group_bounds=bounds,
    )
    overlay.resize(shot.geometry.size())
    return overlay


bounds = desktop.bounds
left_overlay = group_overlay(left_shot, bounds)
right_overlay = group_overlay(right_shot, bounds)
group = OverlayGroup([left_overlay, right_overlay])
committed: list[tuple] = []
group.committed.connect(lambda r, a: committed.append((r, a)))
group_cancelled: list[bool] = []
group.cancelled.connect(lambda: group_cancelled.append(True))

check("group members know it", left_overlay.in_group and right_overlay.in_group)

# A drag on the left screen that runs past its right edge: Wayland keeps
# sending the motion to the surface the button went down on, with coordinates
# beyond the edge, so the selection has to be allowed to grow past 1920.
drag(left_overlay, (1800, 200), (2020, 300))
check("a drag may cross the edge",
      left_overlay._selection_rect() == QRect(1800, 200, 220, 100),
      str(left_overlay._selection_rect()))

# ...and the neighbour draws its share of it, in its own coordinates.
check("the neighbour shows the shared part",
      right_overlay._selection_rect() == QRect(0, 200, 100, 100),
      str(right_overlay._selection_rect()))
right_overlay.render(QPixmap(right_overlay.size()))
check("the neighbour paints it", True)
check("but claims no selection of its own", not right_overlay._has_selection)

press_key(left_overlay, Qt.Key.Key_Return)
check("the group commits once", len(committed) == 1, str(committed))
check("in global coordinates", committed[0][0] == QRect(1800, 200, 220, 100),
      str(committed[0][0]))
check("with the action", committed[0][1] == ACTION_SEARCH, str(committed[0][1]))
check("and does not also cancel", not group_cancelled, str(group_cancelled))

# The one that owned the selection carries the badge; the rest go straight
# away, and release() must leave the one that is still saying something.
check("the owner says it is sending", left_overlay.is_sending())
check("the neighbour just goes", not right_overlay.is_sending())
group.release()
check("release leaves the badge alone", left_overlay.is_sending())
left_overlay.dismiss()
check("and it comes down when it is told", not left_overlay.is_sending())

# The selection still cannot leave the desktop altogether.
left_overlay2 = group_overlay(left_shot, bounds)
right_overlay2 = group_overlay(right_shot, bounds)
group2 = OverlayGroup([left_overlay2, right_overlay2])
drag(left_overlay2, (1800, 200), (5000, 300))
check("clamped to the virtual desktop",
      left_overlay2._selection_rect() == QRect(1800, 200, 1400, 100),
      str(left_overlay2._selection_rect()))

# Cancelling anywhere cancels the whole group, exactly once.
cancels: list[bool] = []
group2.cancelled.connect(lambda: cancels.append(True))
press_key(right_overlay2, Qt.Key.Key_Escape)
check("cancelling one cancels the group", len(cancels) == 1, str(cancels))
press_key(left_overlay2, Qt.Key.Key_Escape)
check("and only once", len(cancels) == 1, str(cancels))

# --- optional text recognition ---------------------------------------------
import tempfile  # noqa: E402

from circle_to_search import ocr  # noqa: E402

check("output is tidied",
      ocr.clean("\n\n  hello  \n\n\n\nworld   \n\n") == "hello\n\nworld",
      repr(ocr.clean("\n\n  hello  \n\n\n\nworld   \n\n")))
check("empty output stays empty", ocr.clean("   \n\n  ") == "")
check("a summary is one line", ocr.summarise("a\nb  c\n") == "a b c")

# A word the black rectangle so much as clips must not survive in the text kept
# beside the picture: half a password is still half a password, and the picture
# does not have it any more either.
covered = [
    ocr.Word("keep", 0, 0, 30, 10, 90.0, (1, 1, 1, 1)),
    ocr.Word("secret", 40, 0, 30, 10, 90.0, (1, 1, 1, 2)),
    ocr.Word("clipped", 65, 0, 30, 10, 90.0, (1, 1, 1, 3)),
    ocr.Word("safe", 200, 0, 30, 10, 90.0, (1, 1, 1, 4)),
]
survivors = [word.text for word in ocr.words_outside(covered, [(35, 0, 70, 10)])]
check("a word under a redaction is gone", survivors == ["keep", "safe"], str(survivors))
check("one merely clipped by it too", "clipped" not in survivors)
check("no boxes, no filtering", ocr.words_outside(covered, []) == covered)
check("a long summary is cut", ocr.summarise("x" * 400).endswith("…")
      and len(ocr.summarise("x" * 400)) == 160)


def fake_tesseract(directory: Path, body: str) -> Path:
    """A stand-in on PATH, so the subprocess plumbing is really exercised."""
    binary = directory / "tesseract"
    binary.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


original_path = os.environ.get("PATH", "")
with tempfile.TemporaryDirectory() as tmp:
    fake_dir = Path(tmp)
    os.environ["PATH"] = str(fake_dir)

    # Nothing installed at all: everything must degrade, not explode.
    check("missing binary is detected", not ocr.is_available())
    check("no languages without a binary", ocr.installed_languages() == [])
    check("languages still guessed", ocr.pick_languages("uk") == "ukr+eng",
          ocr.pick_languages("uk"))
    check("english falls back to eng", ocr.pick_languages("en") == "eng")
    try:
        ocr.recognise(Image.new("RGB", (10, 10), "white"))
    except ocr.OcrError as exc:
        check("recognising without tesseract raises", "not installed" in str(exc), str(exc))
    else:
        check("recognising without tesseract raises", False)

    fake_tesseract(
        fake_dir,
        'if [ "$1" = "--list-langs" ]; then\n'
        '  echo "List of available languages (3):"; echo eng; echo ukr; echo deu; exit 0\n'
        "fi\n"
        'echo "  Hello there  "; echo; echo; echo "second line"\n',
    )
    check("the binary is found", ocr.is_available())
    check("languages are listed", ocr.installed_languages() == ["eng", "ukr", "deu"],
          str(ocr.installed_languages()))
    check("the ui language leads", ocr.pick_languages("uk") == "ukr+eng",
          ocr.pick_languages("uk"))
    check("a configured language wins", ocr.pick_languages("uk", "deu") == "deu",
          ocr.pick_languages("uk", "deu"))
    check("an uninstalled language is ignored", ocr.pick_languages("uk", "fra") == "ukr+eng",
          ocr.pick_languages("uk", "fra"))

    text = ocr.recognise(Image.new("RGB", (20, 20), "white"), "ukr+eng")
    check("text comes back cleaned", text == "Hello there\n\nsecond line", repr(text))

    # The install hint follows the package manager: a dnf command shown to
    # somebody on Arch looks like an answer and is not one.
    import shutil as _shutil

    real_which = _shutil.which
    try:
        for manager, expected in (
            ("dnf", "dnf install tesseract"),
            ("apt-get", "apt install tesseract-ocr"),
            ("pacman", "pacman -S tesseract"),
            ("zypper", "zypper install tesseract-ocr"),
        ):
            _shutil.which = lambda binary, wanted=manager: (
                "/usr/bin/" + binary if binary == wanted else None
            )
            check(f"the {manager} hint", expected in ocr.install_hint(), ocr.install_hint())
        _shutil.which = lambda binary: None
        check("no package manager, no fake command",
              "sudo" not in ocr.install_hint(), ocr.install_hint())
    finally:
        _shutil.which = real_which

    # Dark desktops are light text on a dark background, which is the one
    # polarity tesseract is not built for.  Reported from a real one: it came
    # back with fragments of noise instead of words.
    dark = Image.new("L", (20, 20), 25)
    dark.putpixel((10, 10), 230)
    prepared = ocr.prepare(dark)
    check("a dark image is inverted", prepared.getpixel((0, 0)) > 200,
          str(prepared.getpixel((0, 0))))
    check("and its text becomes dark", prepared.getpixel((10, 10)) < 60,
          str(prepared.getpixel((10, 10))))

    light = Image.new("RGB", (20, 20), (240, 240, 240))
    check("a light image is left alone", ocr.prepare(light) is light)

    # The word boxes, which are what make the text selectable on screen.
    rows = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
        "\tleft\ttop\twidth\theight\tconf\ttext",
        "5\t1\t1\t1\t1\t1\t10\t20\t40\t12\t96\tHello",
        "5\t1\t1\t1\t1\t2\t55\t20\t40\t12\t95\tthere",
        "5\t1\t1\t1\t2\t1\t10\t40\t60\t12\t90\tsecond",
        "5\t1\t2\t1\t1\t1\t10\t80\t60\t12\t88\tnext",
        "5\t1\t2\t1\t1\t2\t80\t80\t20\t12\t9\tsmudge",
        "4\t1\t2\t1\t1\t0\t0\t0\t0\t0\t-1\t",
        "5\t1\t3\t1\t1\t1\t10\t99\t10\t10\t80\t   ",
        "rubbish",
    ]
    parsed = ocr.parse_tsv("\n".join(rows))
    check("words are parsed",
          [word.text for word in parsed] == ["Hello", "there", "second", "next"],
          str([word.text for word in parsed]))
    check("a box comes with them", (parsed[0].left, parsed[0].top, parsed[0].width) == (10, 20, 40),
          str(parsed[0]))
    check("an unsure word is dropped", all(word.text != "smudge" for word in parsed))
    check("lines and paragraphs survive",
          ocr.words_to_text(parsed) == "Hello there\nsecond\n\nnext",
          repr(ocr.words_to_text(parsed)))
    check("nothing at all is not a crash", ocr.parse_tsv("") == [])

    # A crop takes the words whose middle is inside it, so a word cut in half by
    # the rectangle goes to the side it mostly sits on.
    check("words inside a box",
          [word.text for word in ocr.words_in(parsed, 0, 0, 100, 50)]
          == ["Hello", "there", "second"],
          str([word.text for word in ocr.words_in(parsed, 0, 0, 100, 50)]))
    # "there" spans 55..95, so a box ending at 60 clips it — but its middle is
    # outside, and a word mostly out of the rectangle was not what was meant.
    check("a clipped word goes by its middle",
          [word.text for word in ocr.words_in(parsed, 0, 0, 60, 30)] == ["Hello"],
          str([word.text for word in ocr.words_in(parsed, 0, 0, 60, 30)]))

    fake_tesseract(
        fake_dir,
        'if [ "$1" = "--list-langs" ]; then echo eng; exit 0; fi\n'
        "printf '5\\t1\\t1\\t1\\t1\\t1\\t10\\t20\\t40\\t12\\t96\\tWord\\n'\n",
    )
    words = ocr.recognise_words(Image.new("RGB", (20, 20), "white"))
    check("recognise_words goes through tesseract", [w.text for w in words] == ["Word"], str(words))

    # Nothing on the image: an error the caller can show, not an empty success.
    fake_tesseract(fake_dir, 'if [ "$1" = "--list-langs" ]; then echo x; exit 0; fi\necho ""\n')
    try:
        ocr.recognise(Image.new("RGB", (20, 20), "white"))
    except ocr.OcrError as exc:
        check("empty recognition raises", "no text" in str(exc), str(exc))
    else:
        check("empty recognition raises", False)

    # And a crash is reported with what tesseract actually said.
    fake_tesseract(fake_dir, 'echo "Error opening data file" >&2\nexit 1\n')
    try:
        ocr.recognise(Image.new("RGB", (20, 20), "white"), "xxx")
    except ocr.OcrError as exc:
        check("a failure carries the reason", "Error opening data file" in str(exc), str(exc))
    else:
        check("a failure carries the reason", False)

os.environ["PATH"] = original_path

# --- the first-run window --------------------------------------------------
from PyQt6.QtWidgets import QLabel, QPushButton  # noqa: E402

from circle_to_search.welcome import WelcomeDialog  # noqa: E402


class _FakeSettings:
    """Just the properties the welcome window touches."""

    def __init__(self) -> None:
        self.language = "auto"
        self.selection_mode = "lasso"
        self.ocr_enabled = False
        self.ocr_asked = False
        self.synced = 0

    def sync(self) -> None:
        self.synced += 1


fake_settings = _FakeSettings()
welcome = WelcomeDialog(fake_settings)  # type: ignore[arg-type]

# The one thing a tray icon cannot tell anybody: what the gesture is.
told = " ".join(label.text() for label in welcome.findChildren(QLabel))
check("the shake is explained", "Shake" in told or "Потрясіть" in told, told[:60])
check("so is what to press afterwards", "Enter" in told, told[:60])
check("a language can be chosen", welcome.language_combo.count() >= 2,
      str(welcome.language_combo.count()))
check("a selection mode can be chosen", welcome.mode_combo.count() == 2)
check("the calibration is offered",
      any("alibr" in button.text() or "алібр" in button.text()
          for button in welcome.findChildren(QPushButton)),
      str([b.text() for b in welcome.findChildren(QPushButton)]))

welcome.language_combo.setCurrentIndex(welcome.language_combo.findData("uk"))
welcome.mode_combo.setCurrentIndex(welcome.mode_combo.findData("rectangle"))
welcome.apply()
check("the choices are saved", fake_settings.language == "uk"
      and fake_settings.selection_mode == "rectangle",
      f"{fake_settings.language} {fake_settings.selection_mode}")
check("answering here means no notification later", fake_settings.ocr_asked)
check("and written out", fake_settings.synced == 1, str(fake_settings.synced))

# Closing it a second way must not write everything again — and, more to the
# point, must not undo what the calibration did in between.
welcome.language_combo.setCurrentIndex(welcome.language_combo.findData("en"))
welcome.apply()
check("saving happens once", fake_settings.language == "uk", fake_settings.language)
i18n.set_language("auto")

# --- searching for the words rather than a picture of them -----------------
# Guessing wrong about a URL means opening something nobody asked for, so the
# guess is deliberately conservative and every edge of it is pinned here.
from circle_to_search import websearch  # noqa: E402

for text, expected in (
    ("https://example.com/a?b=c", "https://example.com/a?b=c"),
    ("http://localhost:8080/x", "http://localhost:8080/x"),
    ("www.example.com", "https://www.example.com"),
    ("example.com", "https://example.com"),
    ("docs.python.org/3/library/re.html", "https://docs.python.org/3/library/re.html"),
    ("  example.com  ", "https://example.com"),
    ("EXAMPLE.COM", "https://EXAMPLE.COM"),
):
    check(f"link: {text!r}", websearch.looks_like_url(text) == expected,
          str(websearch.looks_like_url(text)))

for text in (
    "",
    "   ",
    "hello world",
    "go to example.com",          # a sentence that merely contains one
    "See Fig. 3",
    "main.py",                    # a file, and a terminal is full of them
    "notes.txt",
    "README.md",
    "Dockerfile",
    "3.14",                       # a number, not a host
    "photo.jpeg",
    "archive.tar.gz",
    "libfoo.so",
    "-.com",
):
    check(f"not a link: {text!r}", websearch.looks_like_url(text) is None,
          str(websearch.looks_like_url(text)))

check("a file with a scheme is still a link",
      websearch.looks_like_url("https://example.com/main.py")
      == "https://example.com/main.py")

check("a query is escaped",
      websearch.search_url("a & b") == "https://www.google.com/search?q=a+%26+b&hl=",
      websearch.search_url("a & b"))
check("wrapped lines become one query",
      "q=hello+there+second" in websearch.search_url("hello there\nsecond\n\n"),
      websearch.search_url("hello there\nsecond\n\n"))
check("the language is passed on",
      websearch.search_url("x", "uk").endswith("&hl=uk"), websearch.search_url("x", "uk"))

# --- the lasso stroke ------------------------------------------------------
# Designed in docs/STROKE.md from photographs of the real thing, which
# corrected two drafts: the line carries no colour, the colour is a glow under
# its head, and it stretches because it is a fading trail rather than a shape
# computed from velocity.
from circle_to_search.overlay import _STROKE_CHUNK  # noqa: E402
from circle_to_search.stroke import (  # noqa: E402
    GLOW_RADIUS,
    LINE_WIDTH,
    Blob,
    Trail,
    glow_blob,
    glow_colour,
)

# The ramp hits its four colours at its four heights.
for fraction, name in ((0.00, "#4285f4"), (0.40, "#ea4335"),
                       (0.62, "#fbbc05"), (1.00, "#34a853")):
    got = glow_colour(fraction * 1000, 1000).name()
    check(f"the ramp is {name} at {int(fraction * 100)} %", got == name, got)

# Between them it interpolates, and it clamps rather than wrapping.
midway = glow_colour(200, 1000)          # between blue at 0 % and red at 40 %
check("halfway to red is neither", midway.name() not in ("#4285f4", "#ea4335"), midway.name())
check("and it is on the way there",
      QColor("#4285f4").red() < midway.red() < QColor("#ea4335").red(), midway.name())
check("above the top is still blue", glow_colour(-500, 1000).name() == "#4285f4")
check("below the bottom is still green", glow_colour(5000, 1000).name() == "#34a853")
check("a screen with no height does not divide by zero",
      glow_colour(10, 0).name() == "#4285f4", glow_colour(10, 0).name())

# The colour comes from the height on screen, not from a clock: the same place
# twice is the same colour, whenever it happened.
check("the same height is the same colour, always",
      glow_colour(300, 1000).name() == glow_colour(300, 1000).name())
check("and two heights are two colours",
      glow_colour(100, 1000).name() != glow_colour(900, 1000).name())

# The trail: what is kept, what is dropped, and in what order.
trail = Trail(lifetime_ms=500, min_gap_ms=0)
for index in range(5):
    trail.add(QPoint(index * 100, 200), glow_colour(200, 1000), index * 100)
check("everything recent is kept", len(trail) == 5, str(len(trail)))
check("in the order it arrived",
      [blob.point.x() for blob in trail.blobs] == [0, 100, 200, 300, 400],
      str([b.point.x() for b in trail.blobs]))

trail.prune(650)
check("and what is too old is dropped", [blob.point.x() for blob in trail.blobs] == [200, 300, 400],
      str([b.point.x() for b in trail.blobs]))
trail.prune(2000)
check("eventually there is nothing left", len(trail) == 0, str(len(trail)))

# The fade is a function of age, so it never has to be stored.
fading = Trail(lifetime_ms=500, min_gap_ms=0)
fading.add(QPoint(0, 0), glow_colour(0, 1000), 0)
check("a fresh blob is at full strength", fading.alive(0)[0][1] == 1.0, str(fading.alive(0)))
check("halfway through its life it is half gone",
      abs(fading.alive(250)[0][1] - 0.5) < 1e-9, str(fading.alive(250)))
check("and past the end it is not there at all", fading.alive(600) == [])

# The rate cap: a gaming mouse reports at 1000 Hz and every sample would
# otherwise become a blob to draw.
capped = Trail(lifetime_ms=500, min_gap_ms=11)
taken = sum(capped.add(QPoint(i, i), glow_colour(i, 1000), i) for i in range(100))
check("samples arriving too fast are dropped", taken < 100 and taken >= 8, str(taken))
check("but the first one is always taken", len(capped) >= 1)

# Moving fast, the blobs spread over the distance the pointer covered; standing
# still, they collapse onto one place.  This is the whole stretch mechanism —
# no velocity is ever calculated.
fast = Trail(lifetime_ms=500, min_gap_ms=0)
for index in range(6):
    fast.add(QPoint(index * 120, 300), glow_colour(300, 1000), index * 10)
spread = fast.bounds(0)
check("a fast pointer spreads the glow along its path",
      spread.width() == 600, str(spread))

still = Trail(lifetime_ms=500, min_gap_ms=0)
for index in range(6):
    still.add(QPoint(400, 300), glow_colour(300, 1000), index * 10)
check("a still pointer collapses it to one place", still.bounds(0).width() == 0,
      str(still.bounds(0)))
check("and they really are all stacked up", len(still) == 6, str(len(still)))

# A stroke that covers vertical distance carries the whole ramp along itself;
# the same stroke along one height carries one colour.
down = Trail(lifetime_ms=5000, min_gap_ms=0)
for index in range(11):
    y = index * 100
    down.add(QPoint(500, y), glow_colour(y, 1000), index)
check("top to bottom carries every colour",
      len({blob.colour.name() for blob in down.blobs}) == 11,
      str(len({b.colour.name() for b in down.blobs})))
across = Trail(lifetime_ms=5000, min_gap_ms=0)
for index in range(11):
    across.add(QPoint(index * 100, 400), glow_colour(400, 1000), index)
check("along one height carries one",
      len({blob.colour.name() for blob in across.blobs}) == 1,
      str({b.colour.name() for b in across.blobs}))

# The rectangle the fade asks to have repainted.  This is the one that matters
# most: the cursor glow that had to be taken back out repainted a large area at
# about ten frames a second.
lasso_overlay = make_overlay(MODE_LASSO)
_mouse(lasso_overlay, QEvent.Type.MouseButtonPress, (300, 300))
for step in range(1, 8):
    _mouse(lasso_overlay, QEvent.Type.MouseMove, (300 + step * 10, 300 + step * 8))
damage = lasso_overlay._stroke_damage()
check("the fade repaints a small rectangle", damage.isValid(), str(damage))
check("a few hundred pixels square, not the screen",
      damage.width() < 500 and damage.height() < 500, str(damage))
screen_area = lasso_overlay.width() * lasso_overlay.height()
check("a small fraction of the whole overlay",
      damage.width() * damage.height() * 4 < screen_area,
      f"{damage.width() * damage.height()} of {screen_area}")
check("the stroke animates while the drag is on",
      lasso_overlay._stroke_timer is not None and lasso_overlay._stroke_timer.isActive())

# Reported from a real desktop: a long scribble dropped the overlay to about
# one frame a second.  Re-stroking the whole path every frame is what did it —
# Qt takes 88 ms to stroke a 1500-point antialiased twelve-pixel ribbon, twice
# a frame, and clipping the painter does not help because the path is
# rasterised in full before anything is clipped away.  So the settled part is
# baked into a layer and only the tail is re-stroked.
long_stroke = make_overlay(MODE_LASSO)
_mouse(long_stroke, QEvent.Type.MouseButtonPress, (20, 20))
for step in range(1, 400):
    _mouse(long_stroke, QEvent.Type.MouseMove, (20 + (step * 7) % 700, 20 + (step * 13) % 700))
check("a long scribble keeps its points", len(long_stroke._points) > 300,
      str(len(long_stroke._points)))
check("nothing is frozen before it is painted", long_stroke._stroke_layer is None)
long_stroke.render(QPixmap(long_stroke.size()))
check("painting bakes the settled part into a layer",
      long_stroke._stroke_layer is not None and long_stroke._frozen_upto > 0,
      str(long_stroke._frozen_upto))
live = len(long_stroke._points) - max(0, long_stroke._frozen_upto - 1)
check("and leaves only a short tail to re-stroke", live <= _STROKE_CHUNK + 1, str(live))

# However long it gets, the tail stays the same length — that is the whole
# point, and it is what makes the cost per frame flat rather than linear.
for _ in range(600):
    step = len(long_stroke._points)
    _mouse(long_stroke, QEvent.Type.MouseMove, (20 + (step * 7) % 700, 20 + (step * 11) % 700))
long_stroke.render(QPixmap(long_stroke.size()))
longer = len(long_stroke._points) - max(0, long_stroke._frozen_upto - 1)
check("twice as long a line, the same amount of live path",
      longer <= _STROKE_CHUNK + 1, f"{longer} live of {len(long_stroke._points)}")

# The layer belongs to one drag: it is given back the moment the drag ends,
# because it is a full-screen pixmap and leaving it lying about would undo the
# work that halved this window's memory.
_mouse(long_stroke, QEvent.Type.MouseButtonRelease, (300, 300))
check("and the layer is given back when the drag ends",
      long_stroke._stroke_layer is None and long_stroke._frozen_upto == 0)

# The glow is blitted from a pre-rendered blob rather than rasterised forty
# times a frame.  Colours that round to the same bucket share one.
first_blob = glow_blob(QColor("#4285f4"))
check("a blob is rendered once and kept", glow_blob(QColor("#4285f4")) is first_blob)
check("and near-identical colours share it", glow_blob(QColor("#4386f5")) is first_blob)
check("while a different one gets its own", glow_blob(QColor("#34a853")) is not first_blob)
check("it is as wide as the glow reaches", first_blob.width() == GLOW_RADIUS * 2,
      str(first_blob.width()))

# Something is remembered, but the rate cap is doing its job: eight moves in
# the same millisecond are not eight blobs to draw.
check("the drag collects a trail", len(lasso_overlay._trail) > 0,
      str(len(lasso_overlay._trail)))
check("and the rate cap keeps it short", len(lasso_overlay._trail) < 8,
      str(len(lasso_overlay._trail)))

# Without the cap it follows every move, including the ones the polygon drops
# for being too close together to be worth keeping.
every_move = make_overlay(MODE_LASSO)
every_move._trail.min_gap_ms = 0
_mouse(every_move, QEvent.Type.MouseButtonPress, (300, 300))
for step in range(1, 10):
    _mouse(every_move, QEvent.Type.MouseMove, (300 + step, 300))   # 1 px steps
check("the glow follows every move, not every kept point",
      len(every_move._trail) == 10 and len(every_move._points) < 10,
      f"{len(every_move._trail)} blobs, {len(every_move._points)} points")

# The visible line is open; the one used for masking still closes, because a
# mask needs a closed shape and at twelve pixels a closing line is a bar across
# the middle of whatever is being circled.
_mouse(lasso_overlay, QEvent.Type.MouseButtonRelease, (400, 380))
check("the drawn stroke is open",
      lasso_overlay._stroke_path().elementCount() == len(lasso_overlay._points),
      f"{lasso_overlay._stroke_path().elementCount()} vs {len(lasso_overlay._points)}")
check("and the masking path still closes",
      lasso_overlay._selection_path().elementCount() > len(lasso_overlay._points),
      str(lasso_overlay._selection_path().elementCount()))
check("the ribbon goes when the gesture does", not lasso_overlay._stroke_showing())
check("and so does the glow", len(lasso_overlay._trail) == 0, str(len(lasso_overlay._trail)))
check("with no timer left running",
      lasso_overlay._stroke_timer is None or not lasso_overlay._stroke_timer.isActive())
# With the mask off the crop is the bounding box, which the un-dimmed area
# already shows: one more line tracing the loop is the same fact drawn twice.
check("and no thin outline in its place either", not lasso_overlay._outline_showing())

# Adjust the box by hand and the loop no longer describes it, so the outline
# comes back — as the rectangle it now really is.
press_key(lasso_overlay, Qt.Key.Key_Right)
check("an edited box is drawn as the rectangle it became",
      lasso_overlay._outline_showing() and not lasso_overlay._stroke_showing())

# A rectangle selection is never a ribbon.
rect_stroke = make_overlay(MODE_RECTANGLE)
drag(rect_stroke, (100, 100), (300, 250))
check("a rectangle is drawn as a rectangle", not rect_stroke._stroke_showing())
check("and it collects no trail", len(rect_stroke._trail) == 0, str(len(rect_stroke._trail)))

# The stroke is part of the gesture, not part of the answer: the whole of it —
# line and glow — ends when the button comes up.
settling = make_overlay(MODE_LASSO)
settling._trail.min_gap_ms = 0
_mouse(settling, QEvent.Type.MouseButtonPress, (300, 300))
for step in range(1, 6):
    _mouse(settling, QEvent.Type.MouseMove, (300 + step * 20, 300 + step * 10))
check("the drag is animating", settling._stroke_timer.isActive())
_mouse(settling, QEvent.Type.MouseButtonRelease, (400, 350))
check("letting go takes the tail with it", len(settling._trail) == 0,
      str(len(settling._trail)))
check("and stops the frames", not settling._stroke_timer.isActive())

# A pointer held still long enough for the whole trail to age out also stops
# the frames — and moving again starts them.  Aged explicitly rather than by
# shortening the lifetime, because a blob added in the same millisecond as the
# tick would survive that.
resting = make_overlay(MODE_LASSO)
_mouse(resting, QEvent.Type.MouseButtonPress, (300, 300))
resting._trail.blobs = [
    Blob(QPoint(340, 320), glow_colour(320, 800), resting._stroke_clock() - 10_000)
]
resting._tick_stroke()
check("a trail that ages out stops the frames",
      len(resting._trail) == 0 and not resting._stroke_timer.isActive(),
      str(len(resting._trail)))
_mouse(resting, QEvent.Type.MouseMove, (360, 340))
check("and moving again starts them",
      resting._stroke_timer.isActive() and len(resting._trail) == 1,
      str(len(resting._trail)))

# Cancelling stops the animation rather than leaving a timer running on a
# window that is on its way out.
lasso_overlay._cancel()
check("finishing stops the stroke timer",
      lasso_overlay._stroke_timer is None or not lasso_overlay._stroke_timer.isActive())

# --- what the stroke looks like, in pixels ---------------------------------
# White line, coloured glow, and the two must not bleed into each other.
white_shot = Image.new("RGB", (screen.geometry().width() * 2,
                              screen.geometry().height() * 2), (255, 255, 255))
painted_stroke = SelectionOverlay(
    QPixmap.fromImage(pil_to_qimage(white_shot)),
    hidpi.measure_screen(screen.name(), screen.geometry(), white_shot.size, 2.0),
    screen,
    dim_percent=0,          # no dimming, so nothing but the stroke is in the way
    mode=MODE_LASSO,
    magnifier=False,
)
painted_stroke.resize(screen.geometry().size())
painted_stroke._trail.min_gap_ms = 0
_mouse(painted_stroke, QEvent.Type.MouseButtonPress, (200, 400))
for step in range(1, 16):
    _mouse(painted_stroke, QEvent.Type.MouseMove, (200 + step * 20, 400))
canvas = QPixmap(painted_stroke.size())
painted_stroke.render(canvas)
drawn = canvas.toImage()

# Drawn along one axis, the bounding box has no area — which used to leave the
# screen blank while the user was still drawing the line.
check("a stroke with no area is still drawn",
      painted_stroke._selection_rect().height() < 1
      and drawn.pixelColor(300, 392) != drawn.pixelColor(300, 600),
      f"{painted_stroke._selection_rect()} {drawn.pixelColor(300, 392).name()}")

on_line = drawn.pixelColor(300, 400)
check("a pixel on the line is white",
      on_line.red() > 240 and on_line.green() > 240 and on_line.blue() > 240, on_line.name())
head = drawn.pixelColor(500, 400)
check("and it is still white right beside the brightest glow",
      head.red() > 240 and head.green() > 240 and head.blue() > 240, head.name())

in_glow = drawn.pixelColor(500, 400 + LINE_WIDTH + 20)
check("a pixel in the glow is coloured",
      max(in_glow.red(), in_glow.green(), in_glow.blue())
      - min(in_glow.red(), in_glow.green(), in_glow.blue()) > 20, in_glow.name())
away = drawn.pixelColor(500, 400 + GLOW_RADIUS + 60)
check("and one a glow-radius away is not",
      abs(away.red() - away.green()) < 12 and abs(away.green() - away.blue()) < 12, away.name())

# A white line on a white page is invisible without the shadow.  Android draws
# over photographs and does not need one; we draw over whatever was on screen.
edge = drawn.pixelColor(300, 400 - LINE_WIDTH // 2 - 2)
check("the shadow keeps a white line visible on white",
      edge.red() < 235, edge.name())

# --- the window under the pointer ------------------------------------------
# A Wayland client cannot see anybody else's geometry, so the compositor sends
# the layout with the trigger.  The screenshot of a lasso drawn laboriously
# around a rectangular panel is the case this removes.
from circle_to_search.windows import (  # noqa: E402
    MAX_RECTS,
    parse_rects,
    visible_on,
    window_at,
)

parsed = parse_rects("0,0,1920,1080;100,50,800,600;1900,-20,400,300")
check("the layout parses", len(parsed) == 3, str(parsed))
check("and keeps the order it arrived in", parsed[1] == QRect(100, 50, 800, 600), str(parsed[1]))
check("negative coordinates survive", parsed[2] == QRect(1900, -20, 400, 300), str(parsed[2]))

check("junk entries are skipped, not fatal",
      parse_rects("1,2,3;10,10,100,100;a,b,c,d;;20,20,200,200") ==
      [QRect(10, 10, 100, 100), QRect(20, 20, 200, 200)],
      str(parse_rects("1,2,3;10,10,100,100;a,b,c,d;;20,20,200,200")))
check("nothing at all is nothing", parse_rects("") == [])
check("slivers are not windows", parse_rects("0,0,4,900;0,0,900,4") == [])
crowd = ";".join(f"{i},{i},100,100" for i in range(MAX_RECTS + 30))
check("and the list has a ceiling", len(parse_rects(crowd)) == MAX_RECTS,
      str(len(parse_rects(crowd))))

# A window may hang off an edge or straddle two monitors, and a click must
# never ask for a crop that is not in the screenshot.
right_screen = QRect(1920, 0, 1280, 1024)
straddling = [QRect(1800, 100, 400, 300), QRect(2000, 200, 100, 100), QRect(0, 0, 500, 500)]
onto = visible_on(straddling, right_screen)
check("a straddling window is clipped to the screen", onto[0] == QRect(0, 100, 280, 300),
      str(onto[0]))
check("one wholly inside is only moved", onto[1] == QRect(80, 200, 100, 100), str(onto[1]))
check("and one on another screen is dropped", len(onto) == 2, str(onto))
# Barely overlapping the seam: five pixels of a window is not a target.
check("a window clipped to a sliver is dropped too",
      visible_on([QRect(1825, 0, 100, 100)], right_screen) == [],
      str(visible_on([QRect(1825, 0, 100, 100)], right_screen)))

# Overlapping windows: whichever KWin would have given the click.
stack = [QRect(100, 100, 200, 200), QRect(50, 50, 400, 400)]
check("the front-most one wins", window_at(stack, QPoint(150, 150)) == stack[0],
      str(window_at(stack, QPoint(150, 150))))
check("and the one behind is still reachable",
      window_at(stack, QPoint(60, 60)) == stack[1], str(window_at(stack, QPoint(60, 60))))
check("empty desktop is no window", window_at(stack, QPoint(900, 900)) is None)
check("no layout at all is no window", window_at([], QPoint(10, 10)) is None)

# On the overlay: the outline, and a click that takes exactly that window.
panes = [QRect(80, 80, 300, 200), QRect(420, 300, 200, 150)]
picker = make_overlay(MODE_RECTANGLE)
check("no outline before the layout arrives", picker._window_outline() is None)
picker.set_window_rects(panes)
picker._current = QPoint(200, 150)
check("the window under the pointer is outlined",
      picker._window_outline() == panes[0], str(picker._window_outline()))
picker._current = QPoint(700, 700)
check("and nothing is outlined over the desktop", picker._window_outline() is None)
picker._current = QPoint(200, 150)
picker.render(QPixmap(picker.size()))
check("the outline paints", True)

# Clicking one takes it, without a drag.
clicker, results = confirming()
clicker._reset_selection()
clicker.set_window_rects(panes)
click(clicker, (200, 150))
check("a click takes the whole window",
      clicker._confirming and clicker._selection_rect() == panes[0],
      str(clicker._selection_rect()))
check("and sends nothing by itself", results == {}, str(results))
check("it is a rectangle, whatever the mode was",
      clicker.selection_polygon().count() == 0, str(clicker.selection_polygon().count()))
press_key(clicker, Qt.Key.Key_Return)
check("and Enter then searches for it", ACTION_SEARCH in results, str(list(results)))

# A click on the desktop is still a click on the desktop.
misser, results = confirming()
misser._reset_selection()
misser.set_window_rects(panes)
click(misser, (700, 700))
check("a click on nothing selects nothing",
      not misser._confirming and not misser._has_selection, str(misser._selection_rect()))

# The outline is for *before* the drag: once the pointer is down the user is
# drawing, and an outline arguing with them would be noise.
busy = make_overlay(MODE_RECTANGLE)
busy.set_window_rects(panes)
busy._current = QPoint(200, 150)
_mouse(busy, QEvent.Type.MouseButtonPress, (200, 150))
_mouse(busy, QEvent.Type.MouseMove, (300, 250))
check("no outline while dragging", busy._window_outline() is None)
_mouse(busy, QEvent.Type.MouseButtonRelease, (300, 250))
check("nor once there is a selection to look at", busy._window_outline() is None)

# Text still wins where there is text: a press on a word takes the words, so
# offering the whole window there would promise something that will not happen.
worded = make_overlay(MODE_RECTANGLE)
worded.set_window_rects([QRect(0, 0, 700, 700)])
worded.set_words(sample_words)
worded._current = QPoint(120, 107)          # inside "Hello"
check("text outranks the window outline", worded._window_outline() is None,
      str(worded._window_outline()))
worded._current = QPoint(600, 600)          # the same window, away from the text
check("but the outline is there everywhere else",
      worded._window_outline() == QRect(0, 0, 700, 700), str(worded._window_outline()))

# The script has to send it, with the trigger and not from the poll tick.
script = (Path(__file__).resolve().parent.parent
          / "kwinscript/contents/code/main.js").read_text()
check("the script reports the window layout", '"WindowRects", encoded' in script)
check("with the trigger, before it",
      re.search(r"sendWindowRects\(\);[\s\S]{0,400}?method \|\| \"Trigger\"", script) is not None)
check("and it walks the real stacking order",
      "workspace.stackingOrder" in script.split("function stackedWindows")[1][:400])
check("and only offers what is on screen right now",
      "isShowingNow(window)" in
      script.split("function windowRects")[1].split("function sendWindowRects")[0])
showing = script.split("function isShowingNow")[1].split("function windowRects")[0]
for missing in ("minimized", "hidden", "isOverlay", "desktopWindow"):
    check(f"a {missing} window is left out", missing in showing, missing)
check("and so is one on another desktop", "isOnCurrentDesktop" in showing)

# --- the gesture, animated from the settings -------------------------------
# The welcome window explains a physical movement in three lines of prose, and
# the README has a placeholder for a screenshot of it that does not exist.
from circle_to_search.gesture import GesturePreview, build_gesture  # noqa: E402

shape = build_gesture(DetectionSettings.defaults())
check("two reversals means three strokes", len(shape.points) == 4, str(len(shape.points)))
check("and the swings agree", shape.swings == 3, str(shape.swings))
check("a stroke takes amplitude over speed",
      abs(shape.swing_ms - 150 / 700 * 1000) < 1, str(shape.swing_ms))
check("and the whole thing three of those",
      abs(shape.total_ms - 3 * shape.swing_ms) < 1, str(shape.total_ms))
check("the defaults are achievable", shape.fits_window)

# It is the settings, not a picture of them: change a number, change the path.
more = DetectionSettings.defaults()
more.reversals = 4
check("more reversals, more strokes", len(build_gesture(more).points) == 6,
      str(len(build_gesture(more).points)))
longer = DetectionSettings.defaults()
longer.minAmplitudePx = 300
check("a longer swing draws bigger",
      build_gesture(longer).width > shape.width * 1.9, str(build_gesture(longer).width))
slower = DetectionSettings.defaults()
slower.minSpeedPxPerSec = 350
check("a lower speed takes longer",
      abs(build_gesture(slower).swing_ms - shape.swing_ms * 2) < 1,
      str(build_gesture(slower).swing_ms))

# The two things the detector actually measures are the length of a stroke and
# its angle, so the drawing has to satisfy both — otherwise it is a picture of
# a gesture that would not be accepted.
strokes = list(itertools.pairwise(shape.points))
lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in strokes]
check("every stroke is as long as the threshold asks",
      all(abs(length - 150) < 1e-6 for length in lengths), str(lengths))
angles = [math.degrees(math.atan2(abs(b[1] - a[1]), abs(b[0] - a[0]))) for a, b in strokes]
check("and every one is within the angle tolerance of 45°",
      all(abs(angle - 45) <= DetectionSettings.defaults().angleTolerance for angle in angles),
      str(angles))
check("consecutive strokes do not retrace exactly",
      shape.points[0] != shape.points[2], str(shape.points[:3]))
check("nothing is drawn at a negative coordinate",
      all(x >= 0 and y >= 0 for x, y in shape.points), str(shape.points))

# A tolerance of nothing means strokes right on the diagonal, retraced.
tight = DetectionSettings.defaults()
tight.angleTolerance = 0
straight_shape = build_gesture(tight)
check("no tolerance, no lean",
      all(abs(abs(b[0] - a[0]) - abs(b[1] - a[1])) < 1e-6
          for a, b in itertools.pairwise(straight_shape.points)),
      str(straight_shape.points))

# The dot travels the path in order and arrives at the end.
def _close(a, b):
    return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


check("it starts at the beginning", _close(shape.at(0), shape.points[0]), str(shape.at(0)))
check("it finishes at the end", _close(shape.at(shape.total_ms), shape.points[-1]),
      str(shape.at(shape.total_ms)))
check("halfway through the first stroke is halfway along it",
      _close(shape.at(shape.swing_ms / 2),
             ((shape.points[0][0] + shape.points[1][0]) / 2,
              (shape.points[0][1] + shape.points[1][1]) / 2)),
      str(shape.at(shape.swing_ms / 2)))
check("time before the start is the start", _close(shape.at(-500), shape.points[0]))
check("time after the end is the end", _close(shape.at(shape.total_ms * 5), shape.points[-1]))

# Two numbers that look independent and are not: at the slowest speed the
# detector accepts, four reversals do not fit in a 600 ms window.
crowded = DetectionSettings.defaults()
crowded.reversals = 4
check("it says when the numbers fight each other", not build_gesture(crowded).fits_window)
crowded.windowMs = 3000
check("and stops saying it when they do not", build_gesture(crowded).fits_window)

# Absurd settings must animate, not divide by zero.
for name, field, value in (
    ("no speed at all", "minSpeedPxPerSec", 0),
    ("no amplitude", "minAmplitudePx", 0),
    ("one reversal", "reversals", 1),
    ("nonsense reversals", "reversals", 0),
):
    odd = DetectionSettings.defaults()
    setattr(odd, field, value)
    built = build_gesture(odd)
    check(f"{name} still builds a path", len(built.points) >= 2 and built.total_ms > 0,
          f"{name}: {built.points}")
    panel = GesturePreview(odd)
    panel.resize(400, 150)
    panel.render(QPixmap(panel.size()))
    check(f"{name} still paints", True)

# The panel does not repaint anything while it is not on screen.
panel = GesturePreview(DetectionSettings.defaults())
check("no timer before it is shown", not panel._timer.isActive())
panel.show()
check("it animates once it is", panel._timer.isActive())
panel.hide()
check("and stops when it goes away", not panel._timer.isActive())

# --- the settings window ---------------------------------------------------
# The calibration was written on the argument that tuning six thresholds by
# hand is the wrong job for a user, and then those thresholds were left as the
# most prominent thing in the window, which says the opposite.
from PyQt6.QtWidgets import QAbstractSpinBox  # noqa: E402

from circle_to_search.config import AppSettings  # noqa: E402
from circle_to_search.settings_dialog import SettingsDialog  # noqa: E402

dialog = SettingsDialog(AppSettings())
check("the thresholds are behind Advanced", dialog.advanced.isCheckable()
      and not dialog.advanced.isChecked())
numbers = dialog.advanced.findChildren(QAbstractSpinBox)
check("all ten of them are in there", len(numbers) == 10, str(len(numbers)))
check("and they start out of sight", all(box.isHidden() for box in numbers))
check("the calibration button is not", dialog.advanced.findChildren(QPushButton) == [],
      str([b.text() for b in dialog.advanced.findChildren(QPushButton)]))
check("nor is the switch that turns detection on",
      dialog.enabled_box.parent() is not dialog.advanced)

# Folded away, not taken away: the values still load and still save.
dialog.advanced.setChecked(True)
check("opening it shows them", not any(box.isHidden() for box in numbers))
before = dialog.speed_spin.value()
dialog.speed_spin.setValue(before + 100)
dialog.advanced.setChecked(False)
check("closing it does not reset anything", dialog.speed_spin.value() == before + 100,
      str(dialog.speed_spin.value()))
check("and the values are still readable while shut",
      dialog.reversals_spin.value() > 0, str(dialog.reversals_spin.value()))
dialog.deleteLater()

# --- recent captures -------------------------------------------------------
from circle_to_search.history import RecentCaptures  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    recent = RecentCaptures(Path(tmp), limit=3)
    check("nothing kept yet", recent.entries() == [])

    kept = []
    for index in range(5):
        image = Image.new("RGB", (10 + index, 20 + index), (index * 10, 0, 0))
        kept.append(recent.add(image))
    check("every add returns a path", all(path is not None for path in kept), str(kept))

    entries = recent.entries()
    check("only the limit is kept", len(entries) == 3, str(len(entries)))
    check("the newest is first", entries[0].width == 14, str([e.width for e in entries]))
    check("the oldest were dropped", not kept[0].exists() and not kept[1].exists())
    check("a label reads sensibly", "14 × 24" in entries[0].label, entries[0].label)

    loaded = recent.load(entries[0].path)
    check("a kept capture loads", loaded is not None and loaded.size == (14, 24),
          str(loaded.size if loaded else None))
    check("a missing one does not explode", recent.load(Path(tmp) / "gone.png") is None)

    # Two captures in the same second must not overwrite each other; the names
    # only have second resolution.
    before = len(recent.entries())
    recent.add(Image.new("RGB", (30, 30), "white"))
    check("a same-second capture gets its own name", len(recent.entries()) == before,
          str([e.path.name for e in recent.entries()]))
    check("names are unique",
          len({e.path.name for e in recent.entries()}) == len(recent.entries()))

    # The text recognised inside a crop is kept beside it, so asking for it
    # again from the tray never runs tesseract twice.
    with_text = recent.add(Image.new("RGB", (12, 12), "white"), text="a kept sentence")
    check("text is kept with the capture", recent.text_of(with_text) == "a kept sentence",
          repr(recent.text_of(with_text)))
    check("the entry says it has text",
          recent.entries()[0].has_text and "¶" in recent.entries()[0].label,
          recent.entries()[0].label)
    check("a capture without text says so", not recent.text_of(Path(tmp) / "nope.png"))
    recent.forget(with_text)
    check("forgetting takes the text too", not with_text.with_suffix(".txt").exists())


    current = recent.entries()
    recent.forget(current[-1].path)
    check("forgetting one works", not current[-1].path.exists())
    check("and it leaves the list", len(recent.entries()) == len(current) - 1,
          str(len(recent.entries())))

    remaining = len(recent.entries())
    check("clearing reports what it removed", recent.clear() == remaining, str(remaining))
    check("and leaves nothing", recent.entries() == [])

    # Files that are not ours, and unreadable ones, are ignored rather than
    # crashing the tray menu.
    (Path(tmp) / "notes.txt").write_text("not a capture")
    (Path(tmp) / "capture-20250101-000000.png").write_bytes(b"not a png")
    check("junk in the directory is ignored", recent.entries() == [],
          str(recent.entries()))

check("a missing directory is not an error", RecentCaptures(Path("/nonexistent/x")).entries() == [])

# --- a window for the captures, not a submenu -------------------------------
# Whatever was recognised inside each crop has been sitting on disk beside it
# the whole time, so there was already a searchable corpus of everything ever
# looked up — and a tray submenu, which is why the number of them had to stay
# at five.
import time  # noqa: E402

from circle_to_search.history import matches as history_matches  # noqa: E402
from circle_to_search.history_window import HistoryWindow  # noqa: E402

check("an empty query matches everything", history_matches("", "anything"))
check("and so does whitespace", history_matches("   ", "anything"))
check("a word anywhere counts", history_matches("conn", "error: connection refused"))
check("case does not", history_matches("REFUSED", "connection refused"))
check("every word has to be there, not just one",
      history_matches("error db", "error at db-01")
      and not history_matches("error zz", "error at db-01"))
check("and they may be far apart, or in another field",
      history_matches("14:02 refused", "14:02:33", "connection refused"))

with tempfile.TemporaryDirectory() as tmp:
    shelf = RecentCaptures(Path(tmp), limit=50)
    for text, colour in (
        ("error: connection refused at db-01", (200, 40, 40)),
        ("Deployment finished", (40, 160, 60)),
        ("", (60, 90, 200)),
    ):
        shelf.add(Image.new("RGB", (40, 20), colour), text=text)
        time.sleep(0.005)

    window = HistoryWindow(shelf)
    check("every kept capture gets a tile", window.list.count() == 3,
          str(window.list.count()))
    check("and the total is stated", "3" in window.status.text(), window.status.text())

    window.search.setText("refused")
    visible = [i for i in range(window.list.count()) if not window.list.item(i).isHidden()]
    check("the box searches the recognised text", len(visible) == 1, str(len(visible)))
    check("and says how much of the shelf that is", window.status.text() == i18n.tr(
        "history.found", shown=1, total=3), window.status.text())

    window.search.setText("error deployment")
    visible = [i for i in range(window.list.count()) if not window.list.item(i).isHidden()]
    check("two words that are not in the same one match nothing", visible == [], str(visible))
    check("and it says why that might be",
          "recognised" in window.status.text() or "розпізнан" in window.status.text(),
          window.status.text())

    window.search.setText("")
    check("clearing brings them all back",
          all(not window.list.item(i).isHidden() for i in range(3)))

    # The four things a kept capture can be used for, and the one that removes
    # it — all of them need something selected first.
    check("nothing selected, nothing to do",
          window.selected_path() is None
          and not any(button.isEnabled() for button, _ in window._buttons))
    window.list.item(0).setSelected(True)
    check("selecting one offers them", all(button.isEnabled() for button, _ in window._buttons))

    asked: list[tuple[str, str]] = []
    window.reuse_requested.connect(lambda path, action: asked.append((path.name, action)))
    window._act(ACTION_SEARCH)
    check("it hands the path and the action over, and does neither itself",
          len(asked) == 1 and asked[0][1] == ACTION_SEARCH, str(asked))

    # A hidden tile is not a selected tile, or the buttons would act on
    # something the search box says is not there.
    window.list.item(0).setSelected(True)
    window.search.setText("deployment")
    check("filtering something out deselects it", window.selected_path() is None
          or not window.selected_path().name.startswith(asked[0][0][:20]),
          str(window.selected_path()))
    window.search.setText("")

    gone = window.list.item(0)
    doomed = Path(str(gone.data(int(Qt.ItemDataRole.UserRole))))
    gone.setSelected(True)
    window._forget()
    check("forgetting removes the file", not doomed.exists())
    check("and the tile with it", window.list.count() == 2, str(window.list.count()))

    shelf.add(Image.new("RGB", (40, 20), (10, 10, 10)))
    window.reload()
    check("reload picks up what arrived while it was open", window.list.count() == 3,
          str(window.list.count()))

    # The ceiling is a setting now, and lowering it has to apply to what is
    # already kept rather than only to captures not taken yet.
    shelf.set_limit(2)
    window.reload()
    check("lowering the limit prunes the shelf", window.list.count() == 2,
          str(window.list.count()))
    window.deleteLater()

# --- learning from misfires ------------------------------------------------
import itertools  # noqa: E402
import json  # noqa: E402

from circle_to_search.misfires import (  # noqa: E402
    SURVEY_INTERVAL,
    SURVEY_LIMIT,
    SurveyState,
    after_opening,
    parse_trace,
    save_trace,
    should_ask,
    still_learning,
)

# The very first gesture is asked about; the next few are not.
state = SurveyState()
check("asks about the first opening", should_ask(state))
state = after_opening(state, asked=True)
check("does not ask again straight away", not should_ask(state))

asked_at = []
for opening in range(1, 60):
    if should_ask(state):
        asked_at.append(opening)
        state = after_opening(state, asked=True)
    else:
        state = after_opening(state, asked=False)

# Ten questions in the lifetime of the installation (one was already asked
# before the loop), with SURVEY_INTERVAL quiet openings between them, and then
# silence forever.
check("the budget is spent exactly once", len(asked_at) == SURVEY_LIMIT - 1, str(asked_at))
gaps = {b - a for a, b in itertools.pairwise(asked_at)}
check("questions are spaced out", gaps == {SURVEY_INTERVAL + 1}, str(gaps))
check("it stops asking for good", not should_ask(state))
check("and stops collecting traces", not still_learning(state))

# Switched off: no questions, and nothing recorded either.
off = SurveyState(enabled=False)
check("nothing when switched off", not should_ask(off) and not still_learning(off))

# An ignored question still costs budget — someone who does not answer is
# telling us something too, and counting only answers would ask forever.
ignored = SurveyState(asks=SURVEY_LIMIT - 1, since_ask=SURVEY_INTERVAL)
check("the last question is still allowed", should_ask(ignored))
check("after it, none", not should_ask(after_opening(ignored, asked=True)))

# The wire format from the KWin script.
samples = parse_trace("100,200,0;110,205,30;120,210,60")
check("trace parses", len(samples) == 3, str(samples))
check("trace values", samples[1] == {"x": 110, "y": 205, "t": 30}, str(samples[1]))
check("negative coordinates survive", parse_trace("-5,-9,0;1,2,10")[0]["x"] == -5)
check("junk is skipped, not fatal",
      len(parse_trace("1,2,3;bad;4,5;6,7,8;;9,10,11,12")) == 2,
      str(parse_trace("1,2,3;bad;4,5;6,7,8;;9,10,11,12")))
check("an empty trace is empty", parse_trace("") == [])

# A saved trace must be exactly what tests/replay-trace.js and the corpus
# runner expect, or the whole loop is decorative.
with tempfile.TemporaryDirectory() as tmp:
    path = save_trace(samples, expect="no-fire", description="unit test", directory=Path(tmp))
    check("a trace is written", path is not None and path.is_file(), str(path))
    saved = json.loads(path.read_text())
    check("expect is recorded", saved["expect"] == "no-fire")
    check("samples round-trip", saved["samples"] == samples)
    check("the name says what it is", path.name.startswith("misfire-"), path.name)

    confirmed = save_trace(samples, expect="fire", directory=Path(tmp))
    check("a confirmed shake is named differently",
          confirmed is not None and confirmed.name.startswith("shake-"), str(confirmed))

    check("nothing is written for an empty trace",
          save_trace([], expect="no-fire", directory=Path(tmp)) is None)

# The shipped corpus has to be loadable by the same rules the runner applies.
corpus = sorted((Path(__file__).resolve().parent / "traces").glob("*.json"))
check("the corpus is not empty", len(corpus) >= 5, str(len(corpus)))
for entry in corpus:
    data = json.loads(entry.read_text())
    check(f"corpus {entry.name}",
          data.get("expect") in {"fire", "no-fire"} and len(data.get("samples", [])) > 1,
          entry.name)

# --- what the tray icon is saying ------------------------------------------
# "The toggle does not work" came up twice while this was being built, and both
# times the cause was KWin still running the script it loaded at login rather
# than the one on disk.  Nothing on screen could have said so.
from circle_to_search import config as _config  # noqa: E402
from circle_to_search import traystate  # noqa: E402


class _World:
    """Stand in for the four things the state is read from."""

    def __init__(self, installed=True, enabled=True, version="1.8.0", detect=True):
        self.installed = installed
        self.enabled = enabled
        self.version = version
        self.detect = detect

    def __enter__(self):
        self._saved = (
            traystate.kwin_script_installed,
            traystate.kwin_script_enabled,
            traystate.kwin_script_version,
            traystate.read_detection,
        )
        detection = DetectionSettings.defaults()
        detection.enabled = self.detect
        traystate.kwin_script_installed = lambda: Path("/tmp/x") if self.installed else None
        traystate.kwin_script_enabled = lambda: self.enabled
        traystate.kwin_script_version = lambda: self.version
        traystate.read_detection = lambda: detection
        return self

    def __exit__(self, *_exc):
        (
            traystate.kwin_script_installed,
            traystate.kwin_script_enabled,
            traystate.kwin_script_version,
            traystate.read_detection,
        ) = self._saved
        return False


with _World() as _w:
    state = traystate.read_tray_state("1.8.0")
    check("everything working is working", state.state == traystate.STATE_OK, state.state)
    check("and the icon is not dimmed for it", state.working)

with _World(detect=False):
    state = traystate.read_tray_state("1.8.0")
    check("detection off is its own state", state.state == traystate.STATE_OFF, state.state)
    check("and the icon says so", not state.working)
    check("but the tooltip does not call it broken",
          "shortcut" in state.tooltip or "клавіш" in state.tooltip, state.tooltip)

with _World(installed=False):
    state = traystate.read_tray_state("1.8.0")
    check("no script at all", state.state == traystate.STATE_NO_SCRIPT, state.state)

with _World(enabled=False):
    state = traystate.read_tray_state("1.8.0")
    check("installed but switched off in kwinrc",
          state.state == traystate.STATE_DISABLED, state.state)

# The one this was written for.
with _World(version="1.8.0"):
    state = traystate.read_tray_state("1.6.0")
    check("a stale script is caught", state.state == traystate.STATE_STALE, state.state)
    check("and both versions are named",
          "1.6.0" in state.tooltip and "1.8.0" in state.tooltip, state.tooltip)
    check("it is not treated as working", not state.working)

# Silence is not evidence.  A daemon restarted mid-session has heard nothing,
# and must not therefore claim anything is wrong.
with _World():
    state = traystate.read_tray_state("")
    check("no news is not bad news", state.state == traystate.STATE_OK, state.state)
with _World(version=""):
    state = traystate.read_tray_state("1.6.0")
    check("nor is an unreadable script", state.state == traystate.STATE_OK, state.state)

# Being switched off outranks nothing: a missing script is the more useful
# thing to say, because turning detection back on would not fix it.
with _World(installed=False, detect=False):
    check("the more actionable reason wins",
          traystate.read_tray_state("").state == traystate.STATE_NO_SCRIPT)

# Every state has to have words in both languages, or the tooltip is a key.
for code in ("en", "uk"):
    i18n.set_language(code)
    for name in (
        traystate.STATE_OK,
        traystate.STATE_OFF,
        traystate.STATE_NO_SCRIPT,
        traystate.STATE_DISABLED,
    ):
        text = traystate.TrayState(name).tooltip
        check(f"{code}: {name} has words", text != f"tray.state.{name}", text)
    stale = traystate.TrayState(traystate.STATE_STALE, running="1", installed="2").tooltip
    check(f"{code}: stale has words", "1" in stale and "2" in stale, stale)
i18n.set_language("en")

# The version really is read out of the file the compositor loads, not out of
# metadata.json — they are kept in step by hand and the code is what runs.
shipped = Path(__file__).resolve().parent.parent / "kwinscript"
check("the shipped script has a version",
      re.search(r'SCRIPT_VERSION\s*=\s*"([^"]+)"',
                (shipped / "contents/code/main.js").read_text()) is not None)
with tempfile.TemporaryDirectory() as tmp:
    package = Path(tmp) / "pkg"
    (package / "contents/code").mkdir(parents=True)
    (package / "contents/code/main.js").write_text('var SCRIPT_VERSION = "9.9.9";\n')
    saved_installed = _config.kwin_script_installed
    _config.kwin_script_installed = lambda: package
    try:
        check("the version comes out of main.js",
              _config.kwin_script_version() == "9.9.9", _config.kwin_script_version())
        (package / "contents/code/main.js").write_text("nothing in here\n")
        check("a script without one is unknown, not a crash",
              _config.kwin_script_version() == "")
        _config.kwin_script_installed = lambda: None
        check("no script, no version", _config.kwin_script_version() == "")
    finally:
        _config.kwin_script_installed = saved_installed

# The script really does announce itself, and on both occasions that matter.
source = (shipped / "contents/code/main.js").read_text()
check("loadConfig is what announces",
      "announce();" in source.split("function announce")[0], "no announce() in loadConfig")
check("and it is the first thing init does",
      re.search(r"function init\(\)\s*\{\s*\n\s*loadConfig\(\);", source) is not None)
check("with the version it is really running",
      '"ScriptReady", SCRIPT_VERSION' in source)

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    sys.exit(1)
print("all good")
