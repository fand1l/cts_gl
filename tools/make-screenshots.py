"""Render the screenshots the README uses.

    python3 tools/make-screenshots.py docs/images

Every picture is the **real** widget, painted offscreen by the same code that
paints it on your screen, over a mock desktop drawn here.  So the interface in
the documentation cannot drift away from the interface in the program, and
nothing in the pictures is anybody's actual screen — the article in the mock
window is this project's own description of itself, and the terminal behind it
shows lines the KWin script really prints.

Both languages, from one run.  Needs no display: `QT_QPA_PLATFORM=offscreen` is
set below.
"""

from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication

from circle_to_search import hidpi
from circle_to_search.config import AppSettings
from circle_to_search.i18n import set_language
from circle_to_search.imageops import black_out, rects_to_crop_space
from circle_to_search.lens import prepare_image
from circle_to_search.ocr import Word
from circle_to_search.overlay import MODE_LASSO, MODE_RECTANGLE, SelectionOverlay
from circle_to_search.pinned import PinnedCrop
from circle_to_search.settings_dialog import SettingsDialog
from circle_to_search.welcome import WelcomeDialog

#: Read out of the KWin script rather than typed here, so the banner the mock
#: terminal shows is the banner the script really prints.
SCRIPT_VERSION = re.search(
    r'var SCRIPT_VERSION = "([^"]+)"',
    (Path(__file__).resolve().parent.parent / "kwinscript/contents/code/main.js").read_text(),
).group(1)

W, H, SCALE = 1440, 900, 2
#: The default *Longest side*, so the readout in the pictures is the readout a
#: fresh installation shows.
MAX_SIDE = 1000
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

app = QApplication(sys.argv)
screen = app.primaryScreen()

# The article the mock window shows: this project's own words, so nothing in
# the pictures is invented and nothing belongs to anybody else.
ARTICLE = {
    "en": (
        "Circle to Search",
        [
            "Shake the pointer, circle anything on screen,",
            "and get a Google Lens result in your browser.",
            "",
            "The gesture is recognised inside the compositor,",
            "because a Wayland client cannot ask where the",
            "pointer is. A KWin script watches the cursor and",
            "hands the position to the daemon over D-Bus.",
            "",
            "Nothing is uploaded by the daemon itself: it writes",
            "a small page with the image inlined and lets the",
            "browser post it, so the session that uploads is the",
            "session that shows the result.",
        ],
    ),
    "uk": (
        "Обвести й знайти",
        [
            "Потрясіть курсором, обведіть будь-що на екрані —",
            "і отримайте результат Google Lens у браузері.",
            "",
            "Жест розпізнається всередині композитора, бо",
            "клієнт Wayland не може спитати, де вказівник.",
            "Скрипт KWin стежить за курсором і передає",
            "координати демонові через D-Bus.",
            "",
            "Демон нічого не вивантажує сам: він пише",
            "маленьку сторінку з вбудованою картинкою, а",
            "браузер надсилає її — тому сеанс, який вивантажив,",
            "і є сеансом, який показує результат.",
        ],
    ),
}

LINE_TOP = 250
LINE_STEP = 44
TEXT_LEFT = 150


def desktop(language: str) -> QImage:
    """A plausible desktop: a wallpaper and one window with an article in it."""
    image = QImage(W * SCALE, H * SCALE, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(SCALE, SCALE)

    sky = QLinearGradient(0, 0, W * 0.4, H)
    sky.setColorAt(0.0, QColor("#1b2733"))
    sky.setColorAt(1.0, QColor("#31465c"))
    painter.fillRect(0, 0, W, H, sky)

    # A window behind, to give the stack some depth.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2a2e32"))
    painter.drawRoundedRect(QRect(760, 470, 600, 380), 8, 8)
    painter.setBrush(QColor("#31363b"))
    painter.drawRoundedRect(QRect(760, 470, 600, 44), 8, 8)
    painter.setPen(QColor("#c8ccd0"))
    painter.setFont(QFont("Noto Sans", 11))
    painter.drawText(QRect(780, 470, 560, 44), int(Qt.AlignmentFlag.AlignVCenter), "Terminal")
    painter.setFont(QFont("Noto Sans Mono", 10))
    painter.setPen(QColor("#8fbc8f"))
    # Real output: every one of these lines is printed by the KWin script.
    for index, line in enumerate(
        ["$ journalctl --user -u plasma-kwin_wayland -f \\", "      | grep circle",
         "KWin script started (v" + SCRIPT_VERSION + ")", "watching options.configChanged",
         "swing 412 px at 1840 px/s, turn 173",
         "TriggerShake at 812,540 on 'eDP-1'"]):
        painter.drawText(786, 550 + index * 30, line)

    # The window in front, with the article.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#fcfcfc"))
    painter.drawRoundedRect(QRect(90, 120, 820, 640), 8, 8)
    painter.setBrush(QColor("#e8eaec"))
    painter.drawRoundedRect(QRect(90, 120, 820, 46), 8, 8)
    painter.setBrush(QColor("#e8eaec"))
    painter.drawRect(QRect(90, 150, 820, 16))

    title, lines = ARTICLE[language]
    painter.setPen(QColor("#3c4043"))
    painter.setFont(QFont("Noto Sans", 11))
    painter.drawText(QRect(112, 120, 780, 46), int(Qt.AlignmentFlag.AlignVCenter), title)

    painter.setPen(QColor("#202124"))
    heading = QFont("Noto Sans", 21)
    heading.setBold(True)
    painter.setFont(heading)
    painter.drawText(TEXT_LEFT, 205, title)

    painter.setFont(QFont("Noto Sans", 13))
    painter.setPen(QColor("#3c4043"))
    for index, line in enumerate(lines):
        painter.drawText(TEXT_LEFT, LINE_TOP + index * LINE_STEP, line)
    painter.end()
    return image


def words_for(language: str) -> list[Word]:
    """Word boxes over the article, in physical pixels, as tesseract would give."""
    metrics_font = QFont("Noto Sans", 13)
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    probe = QPainter(image)
    probe.setFont(metrics_font)
    metrics = probe.fontMetrics()
    words: list[Word] = []
    _title, lines = ARTICLE[language]
    for row, line in enumerate(lines):
        if not line.strip():
            continue
        x = TEXT_LEFT
        for column, word in enumerate(line.split(" ")):
            width = metrics.horizontalAdvance(word)
            if word:
                words.append(
                    Word(
                        text=word,
                        left=int(x * SCALE),
                        top=int((LINE_TOP + row * LINE_STEP - metrics.ascent()) * SCALE),
                        width=int(width * SCALE),
                        height=int(metrics.height() * SCALE),
                        confidence=95.0,
                        order=(1, 1, row + 1, column + 1),
                    )
                )
            x += width + metrics.horizontalAdvance(" ")
    probe.end()
    return words


def overlay_for(
    language: str,
    mode: str = MODE_LASSO,
    magnifier: bool = False,
    last_area: QRect | None = None,
):
    shot = desktop(language)
    metrics = hidpi.measure_screen(screen.name(), QRect(0, 0, W, H), (W * SCALE, H * SCALE), SCALE)
    overlay = SelectionOverlay(
        QPixmap.fromImage(shot), metrics, screen,
        dim_percent=45, mode=mode, confirm=True, magnifier=magnifier,
        max_side=MAX_SIDE, last_area=last_area,
    )
    overlay.resize(W, H)
    overlay.setProperty("shot", shot)
    return overlay


def measure_upload(overlay) -> None:
    """Fill in the "and this is what it weighs" half of the size readout.

    By really preparing the really-selected part of the really-drawn desktop:
    the daemon does this in a worker thread, and a screenshot cannot wait for
    one, but the number in the picture is still the number the program would
    show for that picture.
    """
    # Through the real question, so the overlay accepts the real answer: it
    # drops any that arrives for a crop it is not currently asking about.
    overlay._ask_for_upload_size()
    crop = hidpi.logical_rect_to_physical(overlay._selection_rect(), overlay._metrics)
    shot = overlay.property("shot")
    box = (crop.x(), crop.y(), crop.x() + crop.width(), crop.y() + crop.height())
    payload = black_out(
        qimage_to_pil(shot).crop(box),
        rects_to_crop_space(overlay.redactions(), crop),
    )
    prepared = prepare_image(payload, max_side=MAX_SIDE, quality=85)
    overlay.set_upload_size(crop, len(prepared.payload))


def qimage_to_pil(image: QImage) -> Image.Image:
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    bits = rgb.constBits()
    bits.setsize(rgb.sizeInBytes())
    return Image.frombytes(
        "RGB", (rgb.width(), rgb.height()), bytes(bits), "raw", "RGB", rgb.bytesPerLine()
    )


def send(overlay, kind, point):
    position = QPointF(float(point[0]), float(point[1]))
    button = (
        Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    )
    event = QMouseEvent(kind, position, position, button, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: overlay.mousePressEvent,
     QEvent.Type.MouseMove: overlay.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: overlay.mouseReleaseEvent}[kind](event)


def stroke(overlay, path, release=False):
    send(overlay, QEvent.Type.MouseButtonPress, path[0])
    for point in path[1:]:
        send(overlay, QEvent.Type.MouseMove, point)
    if release:
        send(overlay, QEvent.Type.MouseButtonRelease, path[-1])


def save(overlay, name: str) -> None:
    canvas = QPixmap(overlay.size())
    overlay.render(canvas)
    canvas.save(str(OUT / name))
    print("   ", name)


def loop(cx, cy, rx, ry, steps=54, turn=1.04):
    return [(cx + rx * math.cos(2 * math.pi * turn * i / steps - 1.9),
             cy + ry * math.sin(2 * math.pi * turn * i / steps - 1.9)) for i in range(steps + 1)]


for language in ("en", "uk"):
    set_language(language)
    suffix = "" if language == "en" else "-uk"
    print(f"{language}:")

    # 1. Nothing drawn yet: the mode chips, the hint, and — because there was a
    #    capture before this one — the offer to take the same rectangle again.
    idle = overlay_for(language, last_area=QRect(150, 180, 620, 300))
    idle._current = QPoint(700, 500)
    save(idle, f"overlay-start{suffix}.png")

    # 1b. The same moment, with the third chip pressed: no dimming, and the
    #     loupe following the pointer with the pixel's colour under it.
    picking = overlay_for(language)
    picking._set_picking_colour(True)
    #     Aimed at a glyph in the terminal, so the swatch is a real colour off
    #     something rather than a flat patch of wallpaper.
    picking._current = QPoint(981, 636)
    save(picking, f"overlay-colour{suffix}.png")

    # 2. Mid-gesture: the ribbon and its glow.
    drawing = overlay_for(language)
    drawing._trail.min_gap_ms = 0
    path = loop(430, 330, 300, 120)
    stroke(drawing, path[: int(len(path) * 0.82)])
    # The trail fades with the wall clock, and how much of it survives to the
    # moment of painting depends on how busy the machine is.  Stop the clock
    # first, then age the blobs against it, and the picture is the same picture
    # on every run — which is the whole point of generating them.
    now = drawing._stroke_clock()
    drawing._stroke_clock = lambda frozen=now: frozen
    for index, blob in enumerate(drawing._trail.blobs):
        object.__setattr__(blob, "at_ms", now - (len(drawing._trail.blobs) - 1 - index) * 11)
    save(drawing, f"overlay-drawing{suffix}.png")

    # 3. Let go: the selection waits with the action bar.
    waiting = overlay_for(language)
    stroke(waiting, loop(430, 330, 300, 120), release=True)
    measure_upload(waiting)
    save(waiting, f"overlay-actions{suffix}.png")

    # 3b. The same selection with two lines painted over.  What is under the
    #     black is the article's own words — the point of the picture is that
    #     you cannot tell, which is also the point of the feature.
    hiding = overlay_for(language, MODE_RECTANGLE)
    stroke(hiding, [(100, 175), (900, 560)], release=True)
    hiding._set_redacting(True)
    for row in (5, 6):
        top = LINE_TOP + row * LINE_STEP
        stroke(hiding, [(TEXT_LEFT - 6, top - 22), (TEXT_LEFT + 430, top + 8)], release=True)
    measure_upload(hiding)
    save(hiding, f"overlay-redact{suffix}.png")

    # 3c. A pinned crop, over the desktop it was taken from.  Composed here
    #     rather than rendered from a live window, because a pin is placed by
    #     the compositor and there is no compositor in an offscreen run — but
    #     the pin itself is the real widget, painted at its real size.
    plate = QPixmap.fromImage(desktop(language))
    canvas = QPixmap(W, H)
    painter = QPainter(canvas)
    painter.drawPixmap(QRect(0, 0, W, H), plate)
    crop = plate.copy(QRect(140 * SCALE, 230 * SCALE, 480 * SCALE, 110 * SCALE))
    pin = PinnedCrop(crop.scaled(480, 110))
    shown = QPixmap(pin.size())
    pin.render(shown)
    painter.drawPixmap(QRect(830, 600, pin.width(), pin.height()), shown)
    painter.end()
    canvas.save(str(OUT / f"pinned{suffix}.png"))
    print("   ", f"pinned{suffix}.png")

    # 4. The text layer: words lit, a run selected, the text bar.
    reading = overlay_for(language, MODE_RECTANGLE)
    reading.set_words(words_for(language))
    send(reading, QEvent.Type.MouseButtonPress, (TEXT_LEFT + 6, LINE_TOP - 8))
    send(reading, QEvent.Type.MouseMove, (TEXT_LEFT + 380, LINE_TOP + LINE_STEP - 8))
    send(reading, QEvent.Type.MouseButtonRelease, (TEXT_LEFT + 380, LINE_TOP + LINE_STEP - 8))
    save(reading, f"overlay-text{suffix}.png")

    # 5. The window under the pointer.
    picking = overlay_for(language)
    picking.set_window_rects([QRect(90, 120, 820, 640), QRect(760, 470, 600, 380)])
    picking._current = QPoint(500, 400)
    save(picking, f"overlay-window{suffix}.png")

    # 6. The magnifier, mid-drag.
    aiming = overlay_for(language, MODE_RECTANGLE, magnifier=True)
    send(aiming, QEvent.Type.MouseButtonPress, (150, 190))
    send(aiming, QEvent.Type.MouseMove, (600, 430))
    save(aiming, f"overlay-magnifier{suffix}.png")

    # 7. The settings, both tabs.
    dialog = SettingsDialog(AppSettings())
    dialog.show()
    for _ in range(6):
        app.processEvents()
    # Stopped before it is placed: otherwise the next processEvents() advances
    # the dot by however long the run happened to take to get here.
    dialog.preview._timer.stop()
    dialog.preview._elapsed = dialog.preview._gesture.total_ms * 0.72
    dialog.preview.update()
    app.processEvents()
    dialog.grab().save(str(OUT / f"settings{suffix}.png"))
    print("   ", f"settings{suffix}.png")
    dialog.deleteLater()

    # 8. The first-run window.
    welcome = WelcomeDialog(AppSettings())
    welcome.show()
    for _ in range(6):
        app.processEvents()
    welcome.preview._timer.stop()
    welcome.preview._elapsed = welcome.preview._gesture.total_ms * 0.72
    welcome.preview.update()
    app.processEvents()
    welcome.grab().save(str(OUT / f"welcome{suffix}.png"))
    print("   ", f"welcome{suffix}.png")
    welcome.deleteLater()

print("done")
