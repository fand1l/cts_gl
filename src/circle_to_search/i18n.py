"""Tiny two-language translation table (Ukrainian / English).

No gettext, no .po files, no build step: a plain dictionary is more than enough
for a few dozen strings and keeps the installer trivial.
"""

from __future__ import annotations

from PyQt6.QtCore import QLocale

_EN: dict[str, str] = {
    "app.name": "Circle to Search",
    "tray.detection": "Enable detection",
    "tray.capture": "Capture now",
    "tray.recent": "Recent captures",
    "tray.recent.empty": "(nothing yet)",
    "tray.recent.off": "(switched off in the settings)",
    "tray.recent.search": "Search it again",
    "tray.recent.copy": "Copy it",
    "tray.recent.save": "Save a copy…",
    "tray.recent.text": "Read the text",
    "tray.recent.forget": "Forget this one",
    "tray.recent.clear": "Forget all of them",
    "tray.state.ok": "Circle to Search — shake the cursor to select a region",
    "tray.state.off": (
        "Circle to Search — shake detection is off.\n"
        "The global shortcut and “Capture now” still work."
    ),
    "tray.state.no-script": (
        "Circle to Search — the KWin script is not installed, so the shake "
        "cannot be detected.\nRun install.sh again. The global shortcut and "
        "“Capture now” still work."
    ),
    "tray.state.disabled": (
        "Circle to Search — the KWin script is installed but switched off in "
        "System Settings → Window Management → KWin Scripts.\n"
        "The global shortcut and “Capture now” still work."
    ),
    "tray.state.stale": (
        "Circle to Search — KWin is running version {running} of the script "
        "while {installed} is installed.\nSettings will not take effect until "
        "you log out and back in."
    ),
    "tray.settings": "Settings…",
    "tray.about": "About",
    "tray.quit": "Quit",
    "notify.title": "Circle to Search",
    "notify.uploading": "Sending the selection to Google Lens…",
    "notify.capture_failed": "Screen capture failed",
    "notify.capture_failed_body": "None of the capture back ends worked: {error}",
    "notify.open_failed": "Could not open the browser",
    "notify.open_failed_body": "{error}",
    "notify.lens_failed": "Google Lens request failed",
    "notify.lens_failed_body": "{error}",
    "notify.no_screen": "Cannot find the screen",
    "notify.no_screen_body": "KWin reported the screen “{name}”, but Qt does not know it.",
    "notify.kwin_missing": "The KWin script is not installed",
    "notify.kwin_missing_body": (
        "Cursor-shake detection needs the KWin script. Run install.sh again, "
        "or enable “Circle to Search” in System Settings → Window Management → "
        "KWin Scripts. The tray menu and the global shortcut still work."
    ),
    "notify.copied": "The selection was copied to the clipboard",
    "notify.recent_gone": "That capture is no longer there",
    "notify.recent_cleared": "Forgot {count} kept capture(s)",
    "notify.saved": "The selection was saved",
    "notify.save_failed": "Could not save the selection",
    "notify.save_failed_body": "{error}",
    "notify.open_folder": "Open the folder",
    "notify.ocr_running": "Reading the text…",
    "notify.ocr_done": "The text was copied to the clipboard",
    "notify.ocr_failed": "Could not read the text",
    "notify.ocr_failed_body": "{error}",
    "notify.ocr_missing": "Text recognition is not installed",
    "notify.ocr_missing_body": "It needs tesseract:\n{command}",
    "notify.ocr_off": "Text recognition is switched off",
    "notify.ocr_off_body": "Turn it on in Settings → General if you want it.",
    "about.text": (
        "<h3>Circle to Search {version}</h3>"
        "<p>Select any region of the screen and search it with Google Lens.</p>"
        "<p>Shake the cursor diagonally (down-up-down-up) or press "
        "<b>{shortcut}</b>, then drag a rectangle.</p>"
        "<p>Built for KDE Plasma 6 on Wayland. No OCR, no API keys — the "
        "selection is uploaded to Google Lens and the result page is opened in "
        "your default browser.</p>"
    ),
    "settings.title": "Circle to Search — Settings",
    "settings.tab.detection": "Detection",
    "settings.tab.general": "General",
    "settings.group.shake": "Cursor shake",
    "settings.group.shortcut": "Global shortcut",
    "settings.group.advanced": "Advanced: the raw thresholds",
    "settings.group.advanced.hint": (
        "Calibrating sets all of these from a few real shakes, which is almost "
        "always the better way round. They are here for the cases it cannot "
        "help with — a pointer that behaves oddly, or a value you already know "
        "you want."
    ),
    "settings.group.image": "Image sent to Lens",
    "settings.group.behaviour": "Behaviour",
    "settings.enabled": "Detect cursor shake",
    "settings.fullscreen": "Do not detect the shake in full screen windows",
    "settings.fullscreen.hint": (
        "Games, video players and presentations: shaking the mouse there is normal "
        "and the overlay would be in the way. The global shortcut keeps working, "
        "because pressing it is deliberate."
    ),
    "settings.reversals": "Direction reversals required:",
    "settings.window": "Time window (ms):",
    "settings.amplitude": "Minimum swing length (px):",
    "settings.angle": "Angle tolerance around 45° (°):",
    "settings.poll": "Cursor polling interval (ms):",
    "settings.cooldown": "Cooldown after a trigger (ms):",
    "settings.step": "Movement noise floor (px):",
    "settings.speed": "Minimum swing speed (px/s):",
    "settings.speed.hint": (
        "The strongest guard against false positives: drawing and dragging are "
        "slow, a deliberate shake is a flick. Raise it if the overlay still "
        "appears while you work; lower it if your shake is not picked up."
    ),
    "settings.curvature": "Maximum swing curvature (%):",
    "settings.curvature.hint": "100 % is a perfectly straight swing; a drawn stroke curves.",
    "settings.reversal": "Turn-back tolerance (°):",
    "settings.reversal.hint": (
        "How far from a full 180° the pointer may turn and still count as coming "
        "back along the same line. Smaller is stricter."
    ),
    "settings.debug": "Log why each swing was accepted or rejected",
    "settings.debug.hint": (
        "journalctl --user -u plasma-kwin_wayland -f | grep circle — and "
        "tools/record-trace.sh turns a real misfire into a replayable file."
    ),
    "settings.calibrate": "Calibrate the gesture…",
    "settings.calibrate.hint": (
        "Rather than guessing at six thresholds: shake the way that feels "
        "natural a few times and let the numbers follow from that."
    ),
    "gesture.too_slow": (
        "At the minimum speed these strokes do not fit the time window — you "
        "will have to shake faster than the threshold"
    ),
    "gesture.caption": "This is the movement your current settings are looking for.",
    "calibrate.title": "Circle to Search — Calibration",
    "calibrate.instructions": (
        "Shake the pointer the way you would to open the overlay — diagonally, "
        "back and forth — a few times in a row. Nothing will open while this "
        "window is up; every swing is only being measured."
    ),
    "calibrate.progress": "%v of %m swings",
    "calibrate.waiting": "Waiting for the first swing…",
    "calibrate.keep_going": "{count} swings so far; keep going to about {total}.",
    "calibrate.ready": (
        "Measured {count} swings. These are the thresholds that fit them, with "
        "room to spare:"
    ),
    "calibrate.no_change": (
        "Measured {count} swings — the current settings already fit them, so "
        "there is nothing to change."
    ),
    "calibrate.no_script": (
        "The KWin script did not answer, so nothing can be measured. Check that "
        "it is installed and enabled."
    ),
    "calibrate.setting": "Setting",
    "calibrate.before": "Now",
    "calibrate.after": "Suggested",
    "calibrate.restart": "Start over",
    "welcome.title": "Circle to Search",
    "welcome.heading": (
        "<h3>Circle to Search is running</h3>"
        "<p>It lives in the system tray and waits for you to ask for it.</p>"
    ),
    "welcome.gesture": (
        "<p><b>Shake the pointer diagonally</b> — down-right, up-left, "
        "down-right, briskly, in one place — or press <b>{shortcut}</b>.</p>"
        "<p>The screen freezes and dims. Circle what you are interested in, "
        "then press <b>Enter</b> to search it with Google Lens, <b>C</b> to "
        "copy it, <b>S</b> to save it, or <b>Esc</b> to forget it.</p>"
    ),
    "welcome.choices": "Worth deciding now",
    "welcome.ocr": "Make the text on screen selectable",
    "welcome.ocr.hint": (
        "Runs tesseract locally while the overlay is up, so you can drag across "
        "a sentence and copy it. Nothing is uploaded. Needs the tesseract "
        "package; can be turned on later in Settings."
    ),
    "welcome.calibrate": "Calibrate the gesture…",
    "welcome.calibrate.hint": (
        "Recommended: shake a dozen times and the thresholds are fitted to how "
        "you actually move, instead of to an average nobody is. You can also do "
        "it later from the tray menu."
    ),
    "welcome.done": "Ready",
    "ocr.offer.title": "Read the text on screen too?",
    "ocr.offer.found": (
        "The overlay can make the text on the frozen screen selectable, so you "
        "can drag across a sentence and copy it. It runs tesseract on this "
        "machine — nothing is uploaded. Asked once."
    ),
    "ocr.offer.missing": (
        "The overlay can make the text on the frozen screen selectable. It "
        "needs tesseract, which is not installed yet:\n{command}\n"
        "You can say yes now and install it later. Asked once."
    ),
    "ocr.offer.yes": "Yes, read the text",
    "ocr.offer.no": "No thanks",
    "ocr.offer.on": "Text recognition is on",
    "ocr.offer.on_body": (
        "From the next capture the words on screen can be dragged across and "
        "copied. Settings → General turns it off again."
    ),
    "ocr.ask.title": "Circle to Search — Text recognition",
    "ocr.ask.text": (
        "Read the text out of this capture and copy it, instead of searching "
        "for the image?"
    ),
    "ocr.ask.found": (
        "It runs tesseract on this machine — nothing is uploaded and no key is "
        "needed. tesseract is installed. You will only be asked this once; the "
        "switch is in Settings → General."
    ),
    "ocr.ask.missing": (
        "It runs tesseract on this machine — nothing is uploaded and no key is "
        "needed, but tesseract is not installed yet:\n\n{command}\n\n"
        "You can turn this on now and install it afterwards. You will only be "
        "asked once; the switch is in Settings → General."
    ),
    "settings.ocr": "Make the text on screen selectable (needs tesseract)",
    "settings.ocr.hint": (
        "Reading starts as soon as the overlay opens. Drag across a word to "
        "take a sentence, T takes everything, Enter or C copies it. Runs "
        "tesseract locally — nothing is uploaded. Install it with: {command}"
    ),
    "settings.ocr.found": "tesseract found, languages: {languages}",
    "settings.ocr.missing": "tesseract is not installed",
    "settings.restore_focus": "Give the keyboard back to the window that had it",
    "settings.restore_focus.hint": (
        "After the overlay closes. Only when the keyboard ended up nowhere — a "
        "browser tab opened by a search keeps the focus it took."
    ),
    "settings.keep_recent": "Keep the last few selections for the tray menu",
    "settings.keep_recent.hint": (
        "The {limit} most recent crops stay in {directory} so they can be "
        "searched, copied or saved again without redoing the gesture. Older "
        "ones are deleted as new ones arrive; “Forget all of them” in the tray "
        "empties it now."
    ),
    "settings.all_screens": "Show the overlay on every screen",
    "settings.all_screens.hint": (
        "Off by default. With it on, all monitors are captured and dimmed at "
        "once, so you can select on whichever one you like — and a drag that "
        "runs past an edge carries on onto the next screen, if the compositor "
        "keeps sending the movement after the button went down."
    ),
    "settings.confirm": "Check the selection before sending it",
    "settings.confirm.hint": (
        "The drag only marks the area out. Enter searches, C copies, S saves to "
        "a file, Esc cancels — and the edges can be dragged, or nudged with the "
        "arrow keys, first. Turn this off to send the moment the button is "
        "released."
    ),
    "settings.learn": "Occasionally ask whether a trigger was wanted",
    "settings.learn.hint": (
        "At most {total} questions in total, with at least {interval} openings "
        "in between. Answering “no” saves the pointer movement to {directory} "
        "so the detection can be tested against it offline — nothing is sent "
        "anywhere."
    ),
    "survey.title": "Did you mean to open Circle to Search?",
    "survey.body": (
        "Answering keeps this pointer movement on your own machine so the shake "
        "detection can be tested against it. {left} of {total} questions left; "
        "this can be switched off in the settings."
    ),
    "survey.yes": "Yes",
    "survey.no": "No, that was accidental",
    "survey.saved": "The misfire was saved",
    "survey.saved_body": (
        "{path}\n\nReplay it with: node tests/replay-trace.js FILE — or run the "
        "gesture calibration to fit the thresholds to how you actually move."
    ),
    "settings.shortcut": "Shortcut:",
    "settings.shortcut.hint": (
        "The KWin script registers this sequence the first time it is loaded. "
        "If you have already changed it in System Settings, change it there — "
        "KDE remembers user-defined shortcuts."
    ),
    "settings.shortcut.open": "Open KDE shortcut settings",
    "settings.quality": "JPEG quality:",
    "settings.maxside": "Longest side (px):",
    "settings.maxside.hint": "Lens works with ~1000 px; smaller uploads are noticeably faster.",
    "settings.clipboard": "Also copy the selection to the clipboard",
    "settings.dim": "Overlay dimming (%):",
    "settings.autostart": "Start automatically with the session",
    "settings.layershell": "Use layer-shell for the overlay (experimental, needs a restart)",
    "settings.layershell.hint": (
        "Off by default: without the C++ layer-shell-qt API the surface cannot be "
        "anchored, so a plain full-screen window raised by the KWin script is more "
        "reliable."
    ),
    "settings.language": "Language:",
    "settings.language.auto": "System",
    "settings.restart_note": "Detection settings are applied to KWin immediately.",
    "settings.group.selection": "Selection",
    "settings.mode": "Selection shape:",
    "settings.magnifier": "Magnify while dragging",
    "settings.magnifier.hint": (
        "A 4x loupe with a crosshair beside the pointer while a selection is "
        "being drawn or an edge moved, so the pixel an edge lands on is visible "
        "while it is still being placed. The arrow keys do the same job, but "
        "only after the miss."
    ),
    "settings.mode.lasso": "Lasso (freehand)",
    "settings.mode.rect": "Rectangle",
    "settings.mode.hint": (
        "Hold Shift while starting a drag to use the other shape for one selection."
    ),
    "settings.lasso_mask": "Keep only the inside of the loop (whiten the rest)",
    "settings.lasso_mask.hint": (
        "Off by default: the loop only marks out the edges and the upload is the "
        "plain rectangle around it, the way Circle to Search works on a phone. "
        "Turn it on to isolate a single object from its surroundings."
    ),
    "settings.backend": "Google endpoint:",
    "settings.backend.browser": "Let the browser upload (recommended)",
    "settings.backend.auto": "Upload from the daemon: Lens, then search by image",
    "settings.backend.lens": "Lens only",
    "settings.backend.sbi": "Search by image only",
    "settings.backend.pin": "Only",
    "settings.backend.hint": (
        "Google ties a result page to the session that uploaded, so a page opened "
        "after the daemon uploaded shows the Lens interface with no image. The "
        "recommended option hands the picture to your browser and lets it do the "
        "upload, which is what makes the result appear. Compare the alternatives "
        "with: circle-to-search --probe-lens FILE"
    ),
    "launcher.stuck": (
        "The upload did not start. Press the button to try again, or open "
        "lens.google.com and drop the image there."
    ),
    "launcher.failed": "The browser could not start the upload.",
    "launcher.retry": "Try again",
    "overlay.hint.lasso": (
        "Circle what you want · drag across text to take the text · "
        "Esc or right-click to cancel"
    ),
    "overlay.hint.rect": (
        "Drag to select · drag across text to take the text · "
        "Esc or right-click to cancel"
    ),
    "overlay.scanning": "Reading the text on screen",
    "overlay.sending": "Sending it to Google Lens",
    "overlay.opening": "Opening it in the browser",
    "overlay.adjust": "drag the edges or use the arrow keys",
    "overlay.take_window": "click to take this window",
    "bar.search": "Search",
    "bar.copy": "Copy",
    "bar.save": "Save",
    "bar.cancel": "Cancel",
    "bar.search_text": "Search",
    "bar.open_link": "Open the link",
    "bar.copy_text": "Copy text ({count})",
    "bar.all_text": "All text",
    "bar.back": "Back",
    "bar.mode.lasso": "Lasso",
    "bar.mode.rect": "Rectangle",
    "error.dbus_name": "Another instance is already running (D-Bus name is taken).",
    "error.no_session_bus": "No D-Bus session bus — is this a desktop session?",
}

_UK: dict[str, str] = {
    "app.name": "Обвести й знайти",
    "tray.detection": "Увімкнути детекцію",
    "tray.capture": "Зняти зараз",
    "tray.recent": "Останні знімки",
    "tray.recent.empty": "(поки порожньо)",
    "tray.recent.off": "(вимкнено в налаштуваннях)",
    "tray.recent.search": "Шукати знову",
    "tray.recent.copy": "Скопіювати",
    "tray.recent.save": "Зберегти копію…",
    "tray.recent.text": "Розпізнати текст",
    "tray.recent.forget": "Забути цей",
    "tray.recent.clear": "Забути всі",
    "tray.state.ok": "Circle to Search — потрясіть курсором, щоб виділити ділянку",
    "tray.state.off": (
        "Circle to Search — детекцію струшування вимкнено.\n"
        "Глобальне сполучення клавіш і «Зняти зараз» працюють."
    ),
    "tray.state.no-script": (
        "Circle to Search — скрипт KWin не встановлено, тому струшування не "
        "розпізнається.\nЗапустіть install.sh ще раз. Глобальне сполучення "
        "клавіш і «Зняти зараз» працюють."
    ),
    "tray.state.disabled": (
        "Circle to Search — скрипт KWin установлено, але вимкнено в "
        "Системних параметрах → Керування вікнами → Скрипти KWin.\n"
        "Глобальне сполучення клавіш і «Зняти зараз» працюють."
    ),
    "tray.state.stale": (
        "Circle to Search — KWin виконує версію скрипта {running}, а "
        "встановлено {installed}.\nНалаштування не подіють, доки ви не вийдете "
        "з сеансу і не зайдете знову."
    ),
    "tray.settings": "Налаштування…",
    "tray.about": "Про програму",
    "tray.quit": "Вихід",
    "notify.title": "Circle to Search",
    "notify.uploading": "Надсилаю виділення в Google Lens…",
    "notify.capture_failed": "Не вдалося зняти екран",
    "notify.capture_failed_body": "Жоден зі способів захоплення не спрацював: {error}",
    "notify.open_failed": "Не вдалося відкрити браузер",
    "notify.open_failed_body": "{error}",
    "notify.lens_failed": "Помилка запиту до Google Lens",
    "notify.lens_failed_body": "{error}",
    "notify.no_screen": "Не знайдено екран",
    "notify.no_screen_body": "KWin повідомив про екран «{name}», але Qt його не бачить.",
    "notify.kwin_missing": "KWin-скрипт не встановлено",
    "notify.kwin_missing_body": (
        "Для детекції трясіння курсора потрібен KWin-скрипт. Запустіть install.sh "
        "ще раз або увімкніть «Circle to Search» у Системних параметрах → "
        "Керування вікнами → Скрипти KWin. Меню в треї та глобальний хоткей "
        "працюють і без нього."
    ),
    "notify.copied": "Виділення скопійовано в буфер обміну",
    "notify.recent_gone": "Цього знімка вже немає",
    "notify.recent_cleared": "Забуто знімків: {count}",
    "notify.saved": "Виділення збережено",
    "notify.save_failed": "Не вдалося зберегти виділення",
    "notify.save_failed_body": "{error}",
    "notify.open_folder": "Відкрити теку",
    "notify.ocr_running": "Розпізнаю текст…",
    "notify.ocr_done": "Текст скопійовано в буфер обміну",
    "notify.ocr_failed": "Не вдалося розпізнати текст",
    "notify.ocr_failed_body": "{error}",
    "notify.ocr_missing": "Розпізнавання тексту не встановлено",
    "notify.ocr_missing_body": "Потрібен tesseract:\n{command}",
    "notify.ocr_off": "Розпізнавання тексту вимкнене",
    "notify.ocr_off_body": "Увімкніть його в Налаштуваннях → Загальні, якщо потрібно.",
    "about.text": (
        "<h3>Circle to Search {version}</h3>"
        "<p>Виділіть будь-яку ділянку екрана й знайдіть її через Google Lens.</p>"
        "<p>Потрясіть курсором по діагоналі (вниз-вгору-вниз-вгору) або натисніть "
        "<b>{shortcut}</b>, потім потягніть прямокутник.</p>"
        "<p>Зроблено для KDE Plasma 6 на Wayland. Ніякого OCR і жодних ключів API — "
        "виділення вивантажується в Google Lens, а сторінка результатів "
        "відкривається у браузері за замовчуванням.</p>"
    ),
    "settings.title": "Circle to Search — Налаштування",
    "settings.tab.detection": "Детекція",
    "settings.tab.general": "Загальне",
    "settings.group.shake": "Трясіння курсора",
    "settings.group.shortcut": "Глобальний хоткей",
    "settings.group.advanced": "Додатково: самі порогові значення",
    "settings.group.advanced.hint": (
        "Калібрування встановлює їх усі з кількох справжніх махів — майже "
        "завжди це кращий шлях. Вони тут для випадків, де воно не допоможе: "
        "дивна поведінка вказівника або значення, яке ви вже знаєте."
    ),
    "settings.group.image": "Зображення для Lens",
    "settings.group.behaviour": "Поведінка",
    "settings.enabled": "Розпізнавати трясіння курсора",
    "settings.fullscreen": "Не розпізнавати трясіння в повноекранних вікнах",
    "settings.fullscreen.hint": (
        "Ігри, відеоплеєри, презентації: там трясти мишею — звична річ, і оверлей "
        "тільки заважав би. Глобальний хоткей продовжує працювати, бо це свідома "
        "дія."
    ),
    "settings.reversals": "Потрібно розворотів:",
    "settings.window": "Часове вікно (мс):",
    "settings.amplitude": "Мінімальна довжина маху (px):",
    "settings.angle": "Допуск кута навколо 45° (°):",
    "settings.poll": "Інтервал опитування курсора (мс):",
    "settings.cooldown": "Пауза після спрацювання (мс):",
    "settings.step": "Поріг шуму руху (px):",
    "settings.speed": "Мінімальна швидкість маху (px/с):",
    "settings.speed.hint": (
        "Найсильніший захист від хибних спрацювань: малювання й перетягування "
        "повільні, а свідоме трясіння — це різкий рух. Підвищуйте, якщо оверлей "
        "усе одно вилазить під час роботи; знижуйте, якщо ваш жест не ловиться."
    ),
    "settings.curvature": "Максимальна кривина маху (%):",
    "settings.curvature.hint": "100 % — ідеально прямий мах; намальована лінія завжди вигинається.",
    "settings.reversal": "Допуск на розворот (°):",
    "settings.reversal.hint": (
        "Наскільки рух може відхилятися від повних 180°, щоб вважатися поверненням "
        "тією ж лінією. Менше — суворіше."
    ),
    "settings.debug": "Записувати в журнал, чому мах зараховано або відхилено",
    "settings.debug.hint": (
        "journalctl --user -u plasma-kwin_wayland -f | grep circle — а "
        "tools/record-trace.sh перетворює реальне хибне спрацювання на файл для "
        "повторного відтворення."
    ),
    "settings.calibrate": "Відкалібрувати жест…",
    "settings.calibrate.hint": (
        "Замість того щоб вгадувати шість порогів: потрясіть кілька разів так, "
        "як вам зручно, і числа виведуться з цього."
    ),
    "gesture.too_slow": (
        "На мінімальній швидкості ці махи не влазять у часове вікно — доведеться "
        "трясти швидше за поріг"
    ),
    "gesture.caption": "Саме такий рух шукають ваші поточні налаштування.",
    "calibrate.title": "Circle to Search — Калібрування",
    "calibrate.instructions": (
        "Потрясіть курсором так, як робили б це для відкриття оверлея — по "
        "діагоналі, туди-сюди — кілька разів поспіль. Поки це вікно відкрите, "
        "нічого не з’явиться: кожен мах лише вимірюється."
    ),
    "calibrate.progress": "%v з %m махів",
    "calibrate.waiting": "Чекаю на перший мах…",
    "calibrate.keep_going": "Уже {count} махів; продовжуйте приблизно до {total}.",
    "calibrate.ready": (
        "Виміряно {count} махів. Ось пороги, які їм відповідають, із запасом:"
    ),
    "calibrate.no_change": (
        "Виміряно {count} махів — поточні налаштування вже їм відповідають, "
        "змінювати нічого."
    ),
    "calibrate.no_script": (
        "KWin-скрипт не відповів, тож вимірювати нічим. Перевірте, чи він "
        "встановлений і увімкнений."
    ),
    "calibrate.setting": "Параметр",
    "calibrate.before": "Зараз",
    "calibrate.after": "Пропоную",
    "calibrate.restart": "Почати спочатку",
    "welcome.title": "Circle to Search",
    "welcome.heading": (
        "<h3>Circle to Search працює</h3>"
        "<p>Живе в системному лотку й чекає, доки ви його покличете.</p>"
    ),
    "welcome.gesture": (
        "<p><b>Потрясіть курсором по діагоналі</b> — вниз-праворуч, "
        "вгору-ліворуч, вниз-праворуч, різко, на одному місці — або натисніть "
        "<b>{shortcut}</b>.</p>"
        "<p>Екран застигне й потемніє. Обведіть те, що цікавить, і натисніть "
        "<b>Enter</b>, щоб пошукати через Google Lens, <b>C</b> — скопіювати, "
        "<b>S</b> — зберегти, <b>Esc</b> — забути.</p>"
    ),
    "welcome.choices": "Варто вирішити одразу",
    "welcome.ocr": "Робити текст на екрані виділюваним",
    "welcome.ocr.hint": (
        "Запускає tesseract локально, поки відкритий оверлей, — щоб провести "
        "по реченню й скопіювати його. Нічого не надсилається. Потрібен пакет "
        "tesseract; можна увімкнути й пізніше в Налаштуваннях."
    ),
    "welcome.calibrate": "Відкалібрувати жест…",
    "welcome.calibrate.hint": (
        "Рекомендую: потрясіть десяток разів — і пороги підлаштуються під те, "
        "як ви справді рухаєтесь, а не під середнього користувача. Це можна "
        "зробити й пізніше з меню в треї."
    ),
    "welcome.done": "Готово",
    "ocr.offer.title": "Розпізнавати ще й текст на екрані?",
    "ocr.offer.found": (
        "Оверлей може зробити текст на застиглому екрані виділюваним — щоб "
        "провести по реченню й скопіювати його. Працює через tesseract на "
        "вашій машині, нічого не надсилається. Запитую один раз."
    ),
    "ocr.offer.missing": (
        "Оверлей може зробити текст на застиглому екрані виділюваним. Для "
        "цього потрібен tesseract, якого ще немає:\n{command}\n"
        "Можна погодитись зараз, а встановити пізніше. Запитую один раз."
    ),
    "ocr.offer.yes": "Так, розпізнавати",
    "ocr.offer.no": "Ні, дякую",
    "ocr.offer.on": "Розпізнавання тексту увімкнено",
    "ocr.offer.on_body": (
        "Починаючи з наступного знімка по словах на екрані можна буде провести "
        "й скопіювати їх. Вимкнути — Налаштування → Загальні."
    ),
    "ocr.ask.title": "Circle to Search — Розпізнавання тексту",
    "ocr.ask.text": (
        "Зчитати текст із цього знімка та скопіювати його замість пошуку "
        "зображення?"
    ),
    "ocr.ask.found": (
        "Працює через tesseract на вашій машині — нічого не надсилається і "
        "ключі не потрібні. tesseract встановлено. Запитаю лише раз; перемикач "
        "є в Налаштуваннях → Загальні."
    ),
    "ocr.ask.missing": (
        "Працює через tesseract на вашій машині — нічого не надсилається і "
        "ключі не потрібні, але tesseract ще не встановлений:\n\n{command}\n\n"
        "Можна увімкнути зараз, а встановити потім. Запитаю лише раз; перемикач "
        "є в Налаштуваннях → Загальні."
    ),
    "settings.ocr": "Робити текст на екрані виділюваним (потрібен tesseract)",
    "settings.ocr.hint": (
        "Розпізнавання починається одразу з відкриттям оверлея. Проведіть по "
        "слову, щоб узяти речення, T бере все, Enter або C копіює. tesseract "
        "працює локально — нічого не надсилається. Встановити: {command}"
    ),
    "settings.ocr.found": "tesseract знайдено, мови: {languages}",
    "settings.ocr.missing": "tesseract не встановлено",
    "settings.restore_focus": "Повертати клавіатуру вікну, яке її мало",
    "settings.restore_focus.hint": (
        "Після закриття оверлея. Лише якщо фокус нікуди не потрапив — вкладка "
        "браузера, відкрита пошуком, свій фокус залишає собі."
    ),
    "settings.keep_recent": "Зберігати кілька останніх виділень для меню в треї",
    "settings.keep_recent.hint": (
        "Останні {limit} знімків лежать у {directory}, щоб їх можна було "
        "повторно знайти, скопіювати чи зберегти без повторення жесту. Старіші "
        "видаляються, коли з’являються нові; «Забути всі» в треї очищає теку "
        "одразу."
    ),
    "settings.all_screens": "Показувати оверлей на всіх екранах",
    "settings.all_screens.hint": (
        "Вимкнено за замовчуванням. Якщо увімкнути, знімок робиться з усіх "
        "моніторів одразу, тож виділяти можна на будь-якому — а виділення, "
        "що виходить за край, продовжиться на сусідньому екрані, якщо "
        "композитор і далі надсилає рух після натискання кнопки."
    ),
    "settings.confirm": "Перевіряти виділення перед надсиланням",
    "settings.confirm.hint": (
        "Перетягування лише окреслює область. Enter — пошук, C — копіювати, "
        "S — зберегти у файл, Esc — скасувати, а краї перед цим можна тягнути "
        "або підправити стрілками. Вимкніть, щоб надсилати одразу після "
        "відпускання кнопки."
    ),
    "settings.learn": "Іноді запитувати, чи спрацювання було потрібним",
    "settings.learn.hint": (
        "Щонайбільше {total} запитань загалом, і між ними щонайменше "
        "{interval} відкриттів оверлея. Відповідь «ні» зберігає рух курсора "
        "в {directory}, щоб розпізнавання можна було на ньому перевірити — "
        "нікуди нічого не надсилається."
    ),
    "survey.title": "Ви справді хотіли відкрити Circle to Search?",
    "survey.body": (
        "Відповідь збереже цей рух курсора на вашій машині, щоб на ньому можна "
        "було перевірити розпізнавання трясіння. Залишилось запитань: {left} з "
        "{total}; це можна вимкнути в налаштуваннях."
    ),
    "survey.yes": "Так",
    "survey.no": "Ні, випадково",
    "survey.saved": "Хибне спрацювання збережено",
    "survey.saved_body": (
        "{path}\n\nВідтворити: node tests/replay-trace.js ФАЙЛ — або запустіть "
        "калібрування жесту, щоб підігнати пороги під те, як ви рухаєтесь."
    ),
    "settings.shortcut": "Хоткей:",
    "settings.shortcut.hint": (
        "KWin-скрипт реєструє цю комбінацію при першому завантаженні. Якщо ви вже "
        "змінювали її в Системних параметрах — міняйте там: KDE запам’ятовує "
        "користувацькі хоткеї."
    ),
    "settings.shortcut.open": "Відкрити налаштування хоткеїв KDE",
    "settings.quality": "Якість JPEG:",
    "settings.maxside": "Найбільша сторона (px):",
    "settings.maxside.hint": (
        "Lens працює приблизно з 1000 px; менший файл вантажиться помітно швидше."
    ),
    "settings.clipboard": "Також копіювати виділення в буфер обміну",
    "settings.dim": "Затемнення оверлея (%):",
    "settings.autostart": "Запускати автоматично разом із сесією",
    "settings.layershell": (
        "Використовувати layer-shell для оверлея (експеримент, потрібен перезапуск)"
    ),
    "settings.layershell.hint": (
        "Вимкнено за замовчуванням: без C++-API layer-shell-qt поверхню неможливо "
        "прив’язати до краю екрана, тож звичайне повноекранне вікно, підняте "
        "KWin-скриптом, надійніше."
    ),
    "settings.language": "Мова:",
    "settings.language.auto": "Системна",
    "settings.restart_note": "Налаштування детекції застосовуються до KWin одразу.",
    "settings.group.selection": "Виділення",
    "settings.mode": "Форма виділення:",
    "settings.magnifier": "Збільшувати під час виділення",
    "settings.magnifier.hint": (
        "Лупа з чотириразовим збільшенням і перехрестям біля курсора, поки ви "
        "малюєте виділення або рухаєте край, — щоб бачити піксель, на який "
        "потрапить край, ще коли ви його ставите. Стрілки роблять те саме, але "
        "вже після промаху."
    ),
    "settings.mode.lasso": "Ласо (від руки)",
    "settings.mode.rect": "Прямокутник",
    "settings.mode.hint": (
        "Затисніть Shift на початку руху, щоб один раз скористатися іншою формою."
    ),
    "settings.lasso_mask": "Залишати тільки обведене (решту забілювати)",
    "settings.lasso_mask.hint": (
        "Вимкнено за замовчуванням: ласо лише вказує межі, а надсилається "
        "звичайний прямокутник навколо нього — так само, як Circle to Search на "
        "телефоні. Вмикайте, щоб відділити один об’єкт від фону."
    ),
    "settings.backend": "Ендпоінт Google:",
    "settings.backend.browser": "Вивантажує браузер (рекомендовано)",
    "settings.backend.auto": "Вивантажує демон: Lens, потім пошук за зображенням",
    "settings.backend.lens": "Лише Lens",
    "settings.backend.sbi": "Лише пошук за зображенням",
    "settings.backend.pin": "Лише",
    "settings.backend.hint": (
        "Google прив’язує сторінку результату до сесії, яка вивантажила картинку, "
        "тож після вивантаження демоном сторінка відкривається з інтерфейсом Lens, "
        "але без зображення. Рекомендований варіант віддає картинку браузеру, і "
        "той вивантажує її сам — саме тому результат з’являється. Порівняти "
        "альтернативи: circle-to-search --probe-lens ФАЙЛ"
    ),
    "launcher.stuck": (
        "Вивантаження не почалося. Натисніть кнопку, щоб спробувати ще раз, або "
        "відкрийте lens.google.com і перетягніть картинку туди."
    ),
    "launcher.failed": "Браузер не зміг почати вивантаження.",
    "launcher.retry": "Спробувати ще раз",
    "overlay.hint.lasso": (
        "Обведіть потрібне · проведіть по тексту, щоб узяти текст · "
        "Esc або права кнопка — скасувати"
    ),
    "overlay.hint.rect": (
        "Потягніть, щоб виділити · проведіть по тексту, щоб узяти текст · "
        "Esc або права кнопка — скасувати"
    ),
    "overlay.scanning": "Читаю текст на екрані",
    "overlay.sending": "Надсилаю в Google Lens",
    "overlay.opening": "Відкриваю в браузері",
    "overlay.adjust": "тягніть за краї або стрілками",
    "overlay.take_window": "клацніть, щоб узяти це вікно",
    "bar.search": "Шукати",
    "bar.copy": "Копіювати",
    "bar.save": "Зберегти",
    "bar.cancel": "Скасувати",
    "bar.search_text": "Шукати",
    "bar.open_link": "Відкрити посилання",
    "bar.copy_text": "Копіювати текст ({count})",
    "bar.all_text": "Увесь текст",
    "bar.back": "Назад",
    "bar.mode.lasso": "Ласо",
    "bar.mode.rect": "Прямокутник",
    "error.dbus_name": "Уже запущено інший екземпляр (ім’я D-Bus зайняте).",
    "error.no_session_bus": "Немає сесійної шини D-Bus — це точно сесія робочого столу?",
}

_TABLES: dict[str, dict[str, str]] = {"en": _EN, "uk": _UK}

_language = "en"


def available_languages() -> tuple[str, ...]:
    """Language codes with a translation table."""
    return tuple(_TABLES)


def detect_language() -> str:
    """Pick ``uk`` for Ukrainian locales, ``en`` otherwise."""
    name = QLocale.system().name().lower()
    return "uk" if name.startswith("uk") else "en"


def set_language(language: str) -> None:
    """Select the active language.  ``auto`` follows the system locale."""
    global _language
    chosen = detect_language() if language == "auto" else language
    _language = chosen if chosen in _TABLES else "en"


def current_language() -> str:
    """Currently active language code."""
    return _language


def tr(key: str, **kwargs: object) -> str:
    """Translate ``key``; unknown keys are returned as-is to stay debuggable."""
    table = _TABLES.get(_language, _EN)
    text = table.get(key) or _EN.get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
