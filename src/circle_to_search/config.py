"""Settings.

There are two stores, on purpose:

* **Detection settings** live in ``~/.config/kwinrc`` under
  ``[Script-circletosearch]`` and are read/written through
  ``kreadconfig6``/``kwriteconfig6``.  That is exactly where the KWin script's
  ``readConfig()`` looks, so the GUI and the script always agree.  After a write
  KWin is asked to ``reconfigure`` so the change takes effect immediately.

* **Application settings** (JPEG quality, clipboard, language …) are irrelevant
  to the KWin script and live in ``~/.config/circle-to-search/`` via QSettings.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtDBus import QDBusConnection, QDBusInterface

from . import APP_NAME, KWIN_SCRIPT_ID
from .logging_setup import get_logger

log = get_logger("config")

KWIN_CONFIG_FILE = "kwinrc"
KWIN_SCRIPT_GROUP = f"Script-{KWIN_SCRIPT_ID}"
KWIN_PLUGIN_KEY = f"{KWIN_SCRIPT_ID}Enabled"

DEFAULT_SHORTCUT = "Meta+Shift+L"


@dataclass
class DetectionSettings:
    """Mirror of the KWin script's configuration keys."""

    enabled: bool = True
    reversals: int = 2
    windowMs: int = 600
    minAmplitudePx: int = 150
    angleTolerance: int = 30
    pollMs: int = 50
    cooldownMs: int = 1500
    minStepPx: int = 6
    minSpeedPxPerSec: int = 700
    maxCurvaturePct: int = 140
    reversalTolerance: int = 40
    debug: bool = False
    disableInFullscreen: bool = True
    shortcut: str = DEFAULT_SHORTCUT

    @classmethod
    def defaults(cls) -> DetectionSettings:
        return cls()


_missing_tools_reported: set[str] = set()


def _which(tool: str) -> str | None:
    """``shutil.which`` that complains about a missing tool only once."""
    path = shutil.which(tool)
    if path is None and tool not in _missing_tools_reported:
        _missing_tools_reported.add(tool)
        log.warning("%s not found — detection settings fall back to the built-in defaults", tool)
    return path


def _kreadconfig() -> str | None:
    return _which("kreadconfig6")


def _kwriteconfig() -> str | None:
    return _which("kwriteconfig6")


def _read_raw(key: str) -> str | None:
    """Read one key from the KWin script group, or ``None`` when unset."""
    binary = _kreadconfig()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [
                binary,
                "--file",
                KWIN_CONFIG_FILE,
                "--group",
                KWIN_SCRIPT_GROUP,
                "--key",
                key,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("kreadconfig6 failed for %s: %s", key, exc)
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _write_raw(key: str, value: str, config_type: str | None = None) -> bool:
    """Write one key into ``kwinrc``.  Returns ``False`` when the tool is absent."""
    binary = _kwriteconfig()
    if binary is None:
        return False
    command = [
        binary,
        "--file",
        KWIN_CONFIG_FILE,
        "--group",
        KWIN_SCRIPT_GROUP,
        "--key",
        key,
    ]
    if config_type is not None:
        command[1:1] = ["--type", config_type]
    command.append(value)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("kwriteconfig6 failed for %s: %s", key, exc)
        return False
    if result.returncode != 0:
        log.error("kwriteconfig6 failed for %s: %s", key, result.stderr.strip())
        return False
    return True


def _as_bool(text: str) -> bool:
    return text.strip().lower() in {"true", "1", "yes", "on"}


def read_detection() -> DetectionSettings:
    """Load the detection settings the KWin script is currently using."""
    settings = DetectionSettings.defaults()
    for field in fields(settings):
        raw = _read_raw(field.name)
        if raw is None:
            continue
        current = getattr(settings, field.name)
        try:
            if isinstance(current, bool):
                setattr(settings, field.name, _as_bool(raw))
            elif isinstance(current, int):
                setattr(settings, field.name, int(float(raw)))
            else:
                setattr(settings, field.name, raw)
        except (TypeError, ValueError):
            log.warning("ignoring malformed value for %s: %r", field.name, raw)
    return settings


def write_detection(settings: DetectionSettings, *, reconfigure: bool = True) -> bool:
    """Persist the detection settings and make KWin pick them up."""
    ok = True
    for name, value in asdict(settings).items():
        if isinstance(value, bool):
            ok &= _write_raw(name, "true" if value else "false", config_type="bool")
        else:
            ok &= _write_raw(name, str(value))
    if reconfigure:
        kwin_reconfigure()
    return ok


def set_detection_enabled(enabled: bool) -> bool:
    """Toggle just the ``enabled`` key (used by the tray checkbox)."""
    ok = _write_raw("enabled", "true" if enabled else "false", config_type="bool")
    kwin_reconfigure()
    return ok


def kwin_reconfigure() -> bool:
    """Ask KWin to re-read its configuration (reloads script settings).

    Uses D-Bus directly — that is what ``qdbus6 org.kde.KWin /KWin reconfigure``
    does, minus the dependency on the ``qdbus6`` binary being installed.
    """
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        log.error("no session bus, cannot reconfigure KWin")
        return False
    interface = QDBusInterface("org.kde.KWin", "/KWin", "org.kde.KWin", bus)
    interface.setTimeout(5000)
    reply = interface.call("reconfigure")
    if reply.errorName():
        log.warning("KWin reconfigure failed: %s %s", reply.errorName(), reply.errorMessage())
        return False
    log.debug("KWin reconfigured")
    return True


def kwin_script_installed() -> Path | None:
    """Return the path of the installed KWin script package, if any."""
    candidates = [
        Path.home() / ".local/share/kwin/scripts" / KWIN_SCRIPT_ID / "metadata.json",
        Path("/usr/share/kwin/scripts") / KWIN_SCRIPT_ID / "metadata.json",
        Path("/usr/local/share/kwin/scripts") / KWIN_SCRIPT_ID / "metadata.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.parent
    return None


def kwin_script_enabled() -> bool:
    """True when ``[Plugins] circletosearchEnabled`` is set in ``kwinrc``."""
    binary = _kreadconfig()
    if binary is None:
        return False
    try:
        result = subprocess.run(
            [
                binary,
                "--file",
                KWIN_CONFIG_FILE,
                "--group",
                "Plugins",
                "--key",
                KWIN_PLUGIN_KEY,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return _as_bool(result.stdout)


class AppSettings:
    """Application-only settings, stored as a plain INI file."""

    def __init__(self) -> None:
        self._settings = QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            APP_NAME,
            APP_NAME,
        )

    @property
    def path(self) -> str:
        return self._settings.fileName()

    def _get_int(self, key: str, default: int) -> int:
        try:
            return int(self._settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return _as_bool(str(value))

    @property
    def jpeg_quality(self) -> int:
        return max(30, min(100, self._get_int("jpeg_quality", 85)))

    @jpeg_quality.setter
    def jpeg_quality(self, value: int) -> None:
        self._settings.setValue("jpeg_quality", int(value))

    @property
    def max_side(self) -> int:
        return max(200, min(4000, self._get_int("max_side", 1000)))

    @max_side.setter
    def max_side(self, value: int) -> None:
        self._settings.setValue("max_side", int(value))

    @property
    def copy_to_clipboard(self) -> bool:
        return self._get_bool("copy_to_clipboard", False)

    @copy_to_clipboard.setter
    def copy_to_clipboard(self, value: bool) -> None:
        self._settings.setValue("copy_to_clipboard", bool(value))

    @property
    def dim_percent(self) -> int:
        return max(0, min(90, self._get_int("dim_percent", 40)))

    @dim_percent.setter
    def dim_percent(self, value: int) -> None:
        self._settings.setValue("dim_percent", int(value))

    @property
    def language(self) -> str:
        return str(self._settings.value("language", "auto"))

    @language.setter
    def language(self, value: str) -> None:
        self._settings.setValue("language", value)

    @property
    def selection_mode(self) -> str:
        """``lasso`` (freehand, the default) or ``rectangle``."""
        value = str(self._settings.value("selection_mode", "lasso"))
        return value if value in ("lasso", "rectangle") else "lasso"

    @selection_mode.setter
    def selection_mode(self, value: str) -> None:
        self._settings.setValue("selection_mode", value)

    @property
    def lasso_mask(self) -> bool:
        """Paint everything outside the lasso white before uploading.

        Off by default: the loop is there to mark out the edges, and Google
        should get the screenshot as it looks, not a shape cut out of it.
        """
        return self._get_bool("lasso_mask", False)

    @lasso_mask.setter
    def lasso_mask(self, value: bool) -> None:
        self._settings.setValue("lasso_mask", bool(value))

    @property
    def lens_backend(self) -> str:
        """``browser`` (default), ``auto``, ``lens``, ``searchbyimage`` or a variant.

        ``browser`` does not upload anything from this process — see lens.py.
        """
        return str(self._settings.value("lens_backend", "browser")) or "browser"

    @lens_backend.setter
    def lens_backend(self, value: str) -> None:
        self._settings.setValue("lens_backend", value)

    @property
    def keep_launcher(self) -> bool:
        """Do not delete the browser launcher page (for debugging a failed run)."""
        return self._get_bool("keep_launcher", False)

    @keep_launcher.setter
    def keep_launcher(self, value: bool) -> None:
        self._settings.setValue("keep_launcher", bool(value))

    @property
    def use_layer_shell(self) -> bool:
        return self._get_bool("use_layer_shell", False)

    @use_layer_shell.setter
    def use_layer_shell(self, value: bool) -> None:
        self._settings.setValue("use_layer_shell", bool(value))

    def sync(self) -> None:
        self._settings.sync()


# --------------------------------------------------------------------------- #
# Autostart is implemented with the systemd --user unit the installer drops in,
# so "autostart" and "the service is enabled" cannot drift apart.
# --------------------------------------------------------------------------- #

SERVICE_NAME = "circle-to-search.service"


def _systemctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    binary = shutil.which("systemctl")
    if binary is None:
        return None
    try:
        return subprocess.run(
            [binary, "--user", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("systemctl %s failed: %s", " ".join(args), exc)
        return None


def autostart_enabled() -> bool:
    result = _systemctl("is-enabled", SERVICE_NAME)
    if result is None:
        return False
    return result.stdout.strip() == "enabled"


def set_autostart(enabled: bool) -> bool:
    action = "enable" if enabled else "disable"
    result = _systemctl(action, SERVICE_NAME)
    if result is None:
        return False
    if result.returncode != 0:
        log.warning("systemctl --user %s failed: %s", action, result.stderr.strip())
        return False
    return True
