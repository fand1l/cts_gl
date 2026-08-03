"""Offscreen tests for the parts that do not need a Plasma session.

Run with::

    python3 tests/test_logic.py

Covers the HiDPI crop arithmetic, the Lens request/redirect handling, the
raw ScreenShot2 buffer decoder, the translation table and the overlay's
selection geometry.  Qt runs on the "offscreen" platform, so no display is
needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QPoint, QRect
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


def make_overlay(mode: str) -> SelectionOverlay:
    overlay = SelectionOverlay(
        QPixmap.fromImage(pil_to_qimage(shot)), metrics, screen, dim_percent=40, mode=mode
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
rect_overlay._origin = QPoint(100, 100)
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

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    sys.exit(1)
print("all good")
