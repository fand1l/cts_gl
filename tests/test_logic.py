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
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])

import requests  # noqa: E402
from PIL import Image  # noqa: E402

from circle_to_search import hidpi, i18n, lens  # noqa: E402
from circle_to_search.imageops import (  # noqa: E402
    mask_outside_polygon,
    pil_to_qimage,
    polygon_to_crop_space,
)
from circle_to_search.overlay import (  # noqa: E402
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
from PyQt6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402

from circle_to_search.ocr import Word  # noqa: E402
from circle_to_search.overlay import (  # noqa: E402
    ACTION_COPY,
    ACTION_SAVE,
    ACTION_SEARCH,
    ACTION_TEXT,
    BADGE_NONE,
    BAR_CANCEL,
    BAR_MODE_LASSO,
    BAR_MODE_RECT,
    BAR_TEXT_ALL,
    BAR_TEXT_BACK,
    BAR_TEXT_COPY,
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

# Pressing outside the selection starts again rather than adjusting it.
overlay, results = confirming()
drag(overlay, (400, 300), (500, 400))
check("a second drag replaces the first",
      overlay._selection_rect() == QRect(400, 300, 100, 100), str(overlay._selection_rect()))
check("and still sends nothing by itself", results == {}, str(results))

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
check("the idle bar offers the two modes", actions == [BAR_MODE_LASSO, BAR_MODE_RECT],
      str(actions))
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
check("four buttons once a selection is waiting",
      actions == [ACTION_SEARCH, ACTION_COPY, ACTION_SAVE, BAR_CANCEL], str(actions))
check("search is the primary one", button_named(overlay, ACTION_SEARCH).primary)
check("the keys are still shown",
      [placed.key for placed in overlay._layout_buttons()] == ["Enter", "C", "S", "Esc"],
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
inside = painted.pixelColor(200, 175).red()
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
      [placed.action for placed in text_bar._layout_buttons()] == [BAR_MODE_LASSO, BAR_MODE_RECT],
      str([placed.action for placed in text_bar._layout_buttons()]))
drag(text_bar, (110, 105), (180, 135))
actions = [placed.action for placed in text_bar._layout_buttons()]
check("three buttons for a text selection",
      actions == [BAR_TEXT_COPY, BAR_TEXT_ALL, BAR_TEXT_BACK], str(actions))
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

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    sys.exit(1)
print("all good")
