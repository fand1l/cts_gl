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

from PIL import Image  # noqa: E402

from circle_to_search import hidpi, i18n, lens  # noqa: E402
from circle_to_search.overlay import SelectionOverlay  # noqa: E402
from circle_to_search.screenshot import _decode_raw, pil_to_qimage  # noqa: E402

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
url, headers, files = lens.build_request(prepared)
check("url", url.startswith("https://lens.google.com/v3/upload?ep=ccm&s=&st="), url)
check("ua", "Chrome/" in headers["User-Agent"])
check("fields", set(files) == {"encoded_image", "processed_image_dimensions"})
check("dims field", files["processed_image_dimensions"][1] == b"1000,500")

small = lens.prepare_image(Image.new("RGB", (50, 50)), max_side=1000)
check("no upscale", (small.width, small.height) == (50, 50))

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

# --- overlay ---------------------------------------------------------------
screen = app.primaryScreen()
shot = Image.new("RGB", (screen.geometry().width() * 2, screen.geometry().height() * 2), "green")
metrics = hidpi.measure_screen(screen.name(), screen.geometry(), shot.size, 2.0)
pixmap = QPixmap.fromImage(pil_to_qimage(shot))
overlay = SelectionOverlay(pixmap, metrics, screen, dim_percent=40)
got: list[QRect] = []
overlay.selected.connect(got.append)
cancelled: list[bool] = []
overlay.cancelled.connect(lambda: cancelled.append(True))
overlay.resize(screen.geometry().size())
overlay._origin = QPoint(100, 100)
overlay._current = QPoint(300, 250)
check("selection rect", overlay._selection_rect() == QRect(100, 100, 200, 150))
overlay.render(QPixmap(overlay.size()))  # exercises paintEvent
check("paint ok", True)
overlay._finish(lambda: overlay.selected.emit(
    hidpi.logical_rect_to_physical(overlay._selection_rect(), metrics)))
check("emitted once", len(got) == 1 and got[0] == QRect(200, 200, 400, 300), str(got))
check("no double cancel", not cancelled)

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    sys.exit(1)
print("all good")
