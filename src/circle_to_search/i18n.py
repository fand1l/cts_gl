"""Tiny two-language translation table (Ukrainian / English).

No gettext, no .po files, no build step: a plain dictionary is more than enough
for a few dozen strings and keeps the installer trivial.
"""

from __future__ import annotations

from PyQt6.QtCore import QLocale

_EN: dict[str, str] = {
    "app.name": "Circle to Search",
    "app.tooltip": "Circle to Search — shake the cursor to select a region",
    "tray.detection": "Enable detection",
    "tray.capture": "Capture now",
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
    "settings.group.image": "Image sent to Lens",
    "settings.group.behaviour": "Behaviour",
    "settings.enabled": "Detect cursor shake",
    "settings.glow": "Light the cursor up while the gesture is recognised",
    "settings.glow.hint": (
        "A halo appears after the first swing and its ring closes as the rest are "
        "made, so you can see the gesture being picked up. The cursor position is "
        "only sent to the daemon while a shake is under way."
    ),
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
    "overlay.hint.lasso": "Circle what you want · Esc or right-click to cancel",
    "overlay.hint.rect": "Drag to select · Esc or right-click to cancel",
    "error.dbus_name": "Another instance is already running (D-Bus name is taken).",
    "error.no_session_bus": "No D-Bus session bus — is this a desktop session?",
}

_UK: dict[str, str] = {
    "app.name": "Обвести й знайти",
    "app.tooltip": "Circle to Search — потрясіть курсором, щоб виділити ділянку",
    "tray.detection": "Увімкнути детекцію",
    "tray.capture": "Зняти зараз",
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
    "settings.group.image": "Зображення для Lens",
    "settings.group.behaviour": "Поведінка",
    "settings.enabled": "Розпізнавати трясіння курсора",
    "settings.glow": "Підсвічувати курсор під час розпізнавання жесту",
    "settings.glow.hint": (
        "Після першого маху навколо курсора з’являється сяйво, а його кільце "
        "замикається з кожним наступним — видно, що жест зчитується. Позиція "
        "курсора надсилається демону лише поки триває трясіння."
    ),
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
    "overlay.hint.lasso": "Обведіть потрібне · Esc або права кнопка — скасувати",
    "overlay.hint.rect": "Потягніть, щоб виділити · Esc або права кнопка — скасувати",
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
