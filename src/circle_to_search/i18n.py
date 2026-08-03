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
    "overlay.hint": "Drag to select · Esc or right-click to cancel",
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
    "overlay.hint": "Потягніть, щоб виділити · Esc або права кнопка — скасувати",
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
