"""Entry point.

Environment has to be set up *before* ``QApplication`` exists, which is why the
layer-shell decision and the platform checks happen here and not in
:mod:`circle_to_search.app`.

Runnable both as ``python -m circle_to_search`` and as
``python .../circle_to_search/__main__.py`` — the second form is what the
``.desktop`` file uses, because KWin's ScreenShot2 permission check resolves the
caller through ``/proc/<pid>/exe`` and therefore needs an ``Exec=`` line that
starts with the real interpreter path.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # started as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from circle_to_search import APP_ID, APP_NAME, __version__
from circle_to_search.logging_setup import get_logger, setup_logging

log = get_logger("main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Select a screen region and search it with Google Lens (KDE Plasma 6/Wayland).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--settings",
        action="store_true",
        help="open the settings window of the running instance (or of a new one)",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="open the selection overlay immediately instead of waiting for a trigger",
    )
    return parser.parse_args(argv)


def find_layer_shell_plugin() -> Path | None:
    """Locate the ``layer-shell-qt`` Wayland shell-integration plugin.

    The plugin is only requested when the file really exists: Qt aborts with
    "Loading shell integration failed" when ``QT_WAYLAND_SHELL_INTEGRATION``
    names something it cannot load, and losing the whole application is a much
    worse outcome than an overlay that needs the KWin script to be raised.
    """
    from PyQt6.QtCore import QLibraryInfo

    directories = [
        Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "wayland-shell-integration",
        Path("/usr/lib64/qt6/plugins/wayland-shell-integration"),
        Path("/usr/lib/qt6/plugins/wayland-shell-integration"),
        Path("/usr/lib/x86_64-linux-gnu/qt6/plugins/wayland-shell-integration"),
    ]
    for directory in directories:
        if not directory.is_dir():
            continue
        for candidate in directory.glob("*layer-shell*.so"):
            return candidate
    return None


def configure_environment(use_layer_shell: bool) -> None:
    """Set the Qt environment variables the overlay depends on."""
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")
    # Qt 6 handles fractional scaling natively; rounding would put the overlay a
    # few pixels off on a 150 % screen.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    if not use_layer_shell:
        return
    if "QT_WAYLAND_SHELL_INTEGRATION" in os.environ:
        return
    plugin = find_layer_shell_plugin()
    if plugin is None:
        log.warning("layer-shell requested but the Qt plugin is not installed — using xdg-shell")
        return
    log.info("using the layer-shell integration from %s", plugin)
    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"


def warn_about_session() -> None:
    """Log (but never refuse) when the session is not Plasma 6 on Wayland."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if session_type != "wayland" and not os.environ.get("WAYLAND_DISPLAY"):
        log.warning(
            "this is not a Wayland session (XDG_SESSION_TYPE=%r) — cursor-shake "
            "detection needs KWin on Wayland",
            session_type,
        )
    if "KDE" not in desktop.upper():
        log.warning("XDG_CURRENT_DESKTOP=%r — this was written for KDE Plasma 6", desktop)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    warn_about_session()

    # AppSettings only needs QtCore, so the layer-shell decision can be made
    # before QApplication is constructed.
    from circle_to_search.config import AppSettings

    settings = AppSettings()
    configure_environment(settings.use_layer_shell)

    from PyQt6.QtWidgets import QApplication

    from circle_to_search.app import CircleToSearchApp
    from circle_to_search.dbus_service import DBusServiceError, call_running_instance
    from circle_to_search.i18n import tr

    application = QApplication(sys.argv[:1])
    application.setApplicationName(APP_NAME)
    application.setApplicationDisplayName("Circle to Search")
    application.setApplicationVersion(__version__)
    application.setOrganizationName(APP_NAME)
    # The Wayland app-id comes from the desktop file name; the KWin script uses
    # it (together with the window caption) to find the overlay window.
    application.setDesktopFileName(APP_ID)
    application.setQuitOnLastWindowClosed(False)

    controller = CircleToSearchApp(application)
    try:
        controller.start()
    except DBusServiceError as exc:
        log.info("%s (%s)", tr("error.dbus_name"), exc)
        # A second start acts as a remote control for the instance that is
        # already running instead of failing with a cryptic message.
        method = "TriggerCurrentScreen" if args.capture else "ShowSettings"
        if call_running_instance(method):
            log.info("forwarded %s to the running instance", method)
            return 0
        log.error("%s", tr("error.dbus_name"))
        return 1

    if args.settings:
        controller.show_settings()
    if args.capture:
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(200, controller.on_trigger_current)

    try:
        return application.exec()
    finally:
        controller.stop()


if __name__ == "__main__":
    raise SystemExit(main())
