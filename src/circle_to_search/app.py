"""Application object: tray icon, D-Bus wiring and the capture → Lens flow."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import (
    QObject,
    QRect,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QAction,
    QCursor,
    QGuiApplication,
    QIcon,
    QPixmap,
    QPolygon,
    QScreen,
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_ID, DBUS_SERVICE, __version__
from .config import (
    AppSettings,
    kwin_script_enabled,
    kwin_script_installed,
    read_detection,
    set_detection_enabled,
)
from .dbus_service import ServiceObject, register_service, unregister_service
from .hidpi import ScreenMetrics, measure_screen
from .i18n import current_language, set_language, tr
from .imageops import mask_outside_polygon, pil_to_qimage, polygon_to_crop_space
from .lens import (
    BACKEND_BROWSER,
    LensError,
    prepare_image,
    upload,
    write_browser_launcher,
)
from .logging_setup import get_logger
from .notify import notify, notify_error
from .overlay import SelectionOverlay
from .screenshot import CaptureError, capture_screen
from .settings_dialog import SettingsDialog

log = get_logger("app")

_ICON_NAMES = (APP_ID, "circle-to-search", "edit-select", "search")

#: How long the launcher page stays on disk.  It has to outlive a cold browser
#: start and leave room for one manual reload after a hiccup.
LAUNCHER_LIFETIME_MS = 10 * 60 * 1000


class _UploadSignals(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)


class _UploadTask(QRunnable):
    """Runs the Lens upload off the GUI thread."""

    def __init__(
        self,
        image: Image.Image,
        max_side: int,
        quality: int,
        backend: str,
        language: str,
        launcher_strings: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        # QThreadPool deletes an auto-delete runnable as soon as run() returns,
        # which would take `signals` down with it while the queued emission is
        # still on its way to the GUI thread.  The application keeps a reference
        # instead and drops it once the result has been handled.
        self.setAutoDelete(False)
        self.signals = _UploadSignals()
        self._image = image
        self._max_side = max_side
        self._quality = quality
        self._backend = backend
        self._language = language
        self._launcher_strings = launcher_strings or {}

    @pyqtSlot()
    def run(self) -> None:
        try:
            prepared = prepare_image(self._image, max_side=self._max_side, quality=self._quality)
            if self._backend == BACKEND_BROWSER:
                # Nothing is uploaded from here: the browser posts the image
                # itself, so the session that uploads is the session that shows
                # the result.  See the long comment in lens.py.
                path = write_browser_launcher(
                    prepared,
                    language=self._language,
                    strings=self._launcher_strings,
                )
                target = path.as_uri()
            else:
                target = upload(prepared, backend=self._backend, language=self._language)
        except LensError as exc:
            self.signals.failed.emit(str(exc))
        except OSError as exc:
            self.signals.failed.emit(f"cannot write the launcher page: {exc}")
        except Exception as exc:
            log.exception("unexpected error while sending the selection")
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(target)


class _OpenSignals(QObject):
    failed = pyqtSignal(str)


class _OpenTask(QRunnable):
    """Run ``xdg-open`` and notice when it fails.

    ``Popen`` returns as soon as the child is spawned, so a missing handler for
    text/html, a browser that refuses to start or a sandbox that cannot read the
    file all used to end in complete silence: the log said "opening …" and
    nothing ever appeared on screen.
    """

    def __init__(self, target: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = _OpenSignals()
        self._target = target

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = subprocess.run(
                ["xdg-open", self._target],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            self.signals.failed.emit("xdg-open is not installed")
            return
        except subprocess.TimeoutExpired:
            # The handler is still running (some browsers do not detach); that
            # is not a failure, the tab is on screen.
            log.debug("xdg-open is still running after 30 s, assuming it worked")
            return
        except OSError as exc:
            self.signals.failed.emit(str(exc))
            return
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
            self.signals.failed.emit(f"xdg-open: {message}")


class CircleToSearchApp(QObject):
    """Ties the tray icon, the D-Bus service and the overlay together."""

    def __init__(self, application: QApplication) -> None:
        super().__init__()
        self._application = application
        self._settings = AppSettings()
        set_language(self._settings.language)

        self._service = ServiceObject(self)
        self._service.triggered.connect(self.on_trigger)
        self._service.shake_triggered.connect(self.on_shake_trigger)
        self._service.overlay_geometry.connect(self.on_overlay_geometry)
        self._service.triggered_current.connect(self.on_trigger_current)
        self._service.settings_requested.connect(self.show_settings)

        self._overlay: SelectionOverlay | None = None
        self._dialog: SettingsDialog | None = None
        self._tasks: set[QRunnable] = set()
        self._busy = False

        self._icon = self._load_icon()
        self._tray = QSystemTrayIcon(self._icon, self)
        self._detection_action: QAction | None = None
        self._build_tray()

    # -------------------------------------------------------------- start-up

    def start(self) -> None:
        """Claim the D-Bus name and show the tray icon."""
        register_service(self._service)
        self._application.setWindowIcon(self._icon)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        else:
            log.warning("no system tray available — running headless")
        QTimer.singleShot(4000, self._check_kwin_script)

    def stop(self) -> None:
        unregister_service()

    def _load_icon(self) -> QIcon:
        for name in _ICON_NAMES:
            icon = QIcon.fromTheme(name)
            if not icon.isNull():
                return icon
        # Not in the icon theme (yet) — fall back to the file we ship, both in
        # its installed location and in a checkout.
        here = Path(__file__).resolve().parent
        candidates = [
            Path.home() / f".local/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
            Path(f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg"),
            here.parent.parent / "data/icons/hicolor/scalable/apps" / f"{APP_ID}.svg",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return QIcon(str(candidate))
        return QIcon.fromTheme("edit-select")

    def _build_tray(self) -> None:
        menu = QMenu()

        detection = QAction(tr("tray.detection"), menu)
        detection.setCheckable(True)
        detection.setChecked(read_detection().enabled)
        detection.toggled.connect(self._on_detection_toggled)
        menu.addAction(detection)
        self._detection_action = detection

        capture = QAction(tr("tray.capture"), menu)
        capture.triggered.connect(self.on_trigger_current)
        menu.addAction(capture)

        menu.addSeparator()

        settings = QAction(tr("tray.settings"), menu)
        settings.triggered.connect(self.show_settings)
        menu.addAction(settings)

        about = QAction(tr("tray.about"), menu)
        about.triggered.connect(self.show_about)
        menu.addAction(about)

        menu.addSeparator()

        quit_action = QAction(tr("tray.quit"), menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.setToolTip(tr("app.tooltip"))
        self._tray.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.on_trigger_current()

    def _on_detection_toggled(self, checked: bool) -> None:
        set_detection_enabled(checked)
        log.info("cursor-shake detection %s", "enabled" if checked else "disabled")

    def _check_kwin_script(self) -> None:
        """Tell the user when the KWin half of the application is missing."""
        path = kwin_script_installed()
        if path is None:
            notify(tr("notify.kwin_missing"), tr("notify.kwin_missing_body"))
            return
        if not kwin_script_enabled():
            log.warning("the KWin script is installed in %s but not enabled in kwinrc", path)
            notify(tr("notify.kwin_missing"), tr("notify.kwin_missing_body"))
            return
        log.info("KWin script found in %s", path)

    # ---------------------------------------------------------------- events

    @pyqtSlot(int, int, str)
    def on_trigger(self, x: int, y: int, screen_name: str) -> None:
        """Handle ``Trigger`` from the KWin script."""
        screen = self._resolve_screen(screen_name, x, y)
        if screen is None:
            notify_error(tr("notify.no_screen"), tr("notify.no_screen_body", name=screen_name))
            return
        self._begin_selection(screen, screen_name or screen.name())

    @pyqtSlot(int, int, str)
    def on_shake_trigger(self, x: int, y: int, screen_name: str) -> None:
        """``TriggerShake`` — the gesture, which the user can switch off.

        The KWin script checks the same setting, but it reads it from its own
        copy of the configuration; checking here as well means the checkbox
        cannot be defeated by a stale script.
        """
        if not read_detection().enabled:
            log.info("ignoring a shake: detection is switched off")
            return
        self.on_trigger(x, y, screen_name)

    @pyqtSlot()
    def on_trigger_current(self) -> None:
        """Handle the tray action / ``TriggerCurrentScreen``.

        Wayland does not let a client ask for the global pointer position, so
        this path uses the last position Qt saw (accurate while the tray menu is
        open) and falls back to the primary screen.  The KWin script's
        ``Trigger`` always carries an exact position.
        """
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            log.error("Qt reports no screens")
            return
        self._begin_selection(screen, screen.name())

    def _resolve_screen(self, screen_name: str, x: int, y: int) -> QScreen | None:
        """KWin's output name → QScreen, with a geometry-based fallback."""
        screens = QGuiApplication.screens()
        for screen in screens:
            if screen_name and screen.name() == screen_name:
                return screen
        for screen in screens:
            if screen.geometry().contains(x, y):
                log.debug(
                    "screen %r is unknown to Qt, matched %s by geometry",
                    screen_name,
                    screen.name(),
                )
                return screen
        return QGuiApplication.primaryScreen()

    # -------------------------------------------------------------- overlay

    def _begin_selection(self, screen: QScreen, screen_name: str) -> None:
        if self._overlay is not None:
            log.info("ignoring trigger, a selection is already in progress")
            return
        if self._busy:
            # The flag is cleared when the overlay finishes, so finding it set
            # with no overlay around means an earlier attempt died somewhere in
            # between.  Recovering here beats ignoring every trigger until the
            # service is restarted.
            log.warning("clearing a stale busy flag from an earlier trigger")
            self._busy = False
        self._busy = True
        try:
            capture = capture_screen(screen, screen_name)
        except CaptureError as exc:
            self._busy = False
            notify_error(tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc))
            return
        except Exception as exc:
            self._busy = False
            log.exception("unexpected capture failure")
            notify_error(tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc))
            return

        metrics = measure_screen(
            name=screen_name or screen.name(),
            logical_geometry=screen.geometry(),
            physical_size=capture.size,
            fallback_dpr=screen.devicePixelRatio(),
        )
        log.info("%s (back end: %s)", metrics, capture.backend)

        pixmap = QPixmap.fromImage(pil_to_qimage(capture.image))
        overlay = SelectionOverlay(
            pixmap=pixmap,
            metrics=metrics,
            screen=screen,
            dim_percent=self._settings.dim_percent,
            mode=self._settings.selection_mode,
            mask_outside=self._settings.lasso_mask,
        )
        overlay.selected.connect(
            lambda rect, polygon: self._on_selected(rect, polygon, capture.image, metrics)
        )
        overlay.cancelled.connect(self._on_cancelled)
        self._overlay = overlay
        QTimer.singleShot(500, lambda: self._check_overlay_geometry(screen))
        try:
            overlay.show_on_screen()
        except Exception as exc:
            log.exception("could not map the overlay")
            self._release_overlay()
            notify_error(tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc))

    @pyqtSlot(int, int, int, int)
    def on_overlay_geometry(self, x: int, y: int, width: int, height: int) -> None:
        """The KWin script telling us where the overlay window really landed."""
        overlay = self._overlay
        if overlay is None:
            return
        overlay.set_window_offset(x, y)
        log.debug("overlay window geometry from KWin: %dx%d+%d+%d", width, height, x, y)

    def _check_overlay_geometry(self, screen: QScreen) -> None:
        """Say so when the overlay was not given the whole output.

        A window left in the work area shows the panel above it and paints the
        screenshot shifted down by the panel's height, which looks like two
        panels stacked on top of each other.  Promoting it is the KWin script's
        job; this is how the journal shows whether that worked.
        """
        overlay = self._overlay
        if overlay is None or not overlay.isVisible():
            return
        expected = screen.geometry().size()
        actual = overlay.size()
        if actual != expected:
            log.warning(
                "the overlay is %dx%d but %s is %dx%d — the KWin script did not "
                "make it full screen, so the screenshot will look shifted",
                actual.width(),
                actual.height(),
                screen.name(),
                expected.width(),
                expected.height(),
            )

    def _release_overlay(self) -> None:
        overlay = self._overlay
        self._overlay = None
        self._busy = False
        if overlay is not None:
            overlay.deleteLater()

    def _on_cancelled(self) -> None:
        log.info("selection cancelled")
        self._release_overlay()

    def _on_selected(
        self,
        rect: QRect,
        polygon: QPolygon,
        image: Image.Image,
        metrics: ScreenMetrics,
    ) -> None:
        self._release_overlay()
        box = (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
        log.info("cropping %s out of %dx%d", box, metrics.physical_width, metrics.physical_height)
        cropped = image.crop(box)

        # The lasso only marks out the edges: by default the upload is the plain
        # rectangular crop, the way Circle to Search behaves on a phone.  The
        # optional mask whitens everything outside the loop instead.
        if polygon.count() >= 3 and self._settings.lasso_mask:
            points = polygon_to_crop_space(polygon, rect.x(), rect.y(), metrics)
            cropped = mask_outside_polygon(cropped, points)
            log.debug("masked everything outside the %d-point lasso", len(points))

        if self._settings.copy_to_clipboard:
            self._copy_to_clipboard(cropped)

        notify(tr("notify.uploading"), transient=True, timeout_ms=3000)

        task = _UploadTask(
            cropped,
            self._settings.max_side,
            self._settings.jpeg_quality,
            self._settings.lens_backend,
            current_language(),
            launcher_strings={
                "title": tr("app.name"),
                "status": tr("notify.uploading"),
                "stuck": tr("launcher.stuck"),
                "failed": tr("launcher.failed"),
                "retry": tr("launcher.retry"),
            },
        )
        task.signals.finished.connect(lambda url, ref=task: self._finish_upload(ref, url=url))
        task.signals.failed.connect(lambda error, ref=task: self._finish_upload(ref, error=error))
        self._tasks.add(task)
        QThreadPool.globalInstance().start(task)

    def _finish_upload(
        self, task: _UploadTask, url: str | None = None, error: str | None = None
    ) -> None:
        self._tasks.discard(task)
        if error is not None:
            notify_error(tr("notify.lens_failed"), tr("notify.lens_failed_body", error=error))
            return
        if not url:
            return
        self._open_url(url)
        if url.startswith("file://") and not self._settings.keep_launcher:
            # Long enough to survive a cold browser start and to let the page be
            # reloaded by hand after a failed attempt, short enough that the
            # screenshot does not sit on disk.
            path = Path(QUrl(url).toLocalFile())
            QTimer.singleShot(LAUNCHER_LIFETIME_MS, lambda: self._remove_launcher(path))

    @staticmethod
    def _remove_launcher(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
            log.debug("removed %s", path)
        except OSError as exc:
            log.debug("could not remove %s: %s", path, exc)

    def _copy_to_clipboard(self, image: Image.Image) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setImage(pil_to_qimage(image))
        log.info("selection copied to the clipboard")

    def _open_url(self, url: str) -> None:
        log.info("opening %s", url)
        task = _OpenTask(url)
        task.signals.failed.connect(lambda error, ref=task: self._open_failed(ref, error))
        self._tasks.add(task)
        QThreadPool.globalInstance().start(task)

    def _open_failed(self, task: QRunnable, error: str) -> None:
        self._tasks.discard(task)
        log.error("could not open the result: %s", error)
        notify_error(tr("notify.open_failed"), tr("notify.open_failed_body", error=error))

    # ------------------------------------------------------------------- GUI

    @pyqtSlot()
    def show_settings(self) -> None:
        if self._dialog is None:
            self._dialog = SettingsDialog(self._settings)
            self._dialog.applied.connect(self._on_settings_applied)
            self._dialog.finished.connect(self._on_dialog_closed)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def _on_dialog_closed(self) -> None:
        dialog = self._dialog
        self._dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def _on_settings_applied(self) -> None:
        set_language(self._settings.language)
        detection = read_detection()
        if self._detection_action is not None:
            self._detection_action.setChecked(detection.enabled)

    @pyqtSlot()
    def show_about(self) -> None:
        shortcut = read_detection().shortcut
        box = QMessageBox()
        box.setWindowTitle(tr("tray.about"))
        box.setIconPixmap(self._icon.pixmap(64, 64))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(tr("about.text", version=__version__, shortcut=shortcut))
        box.setInformativeText(f"D-Bus: {DBUS_SERVICE}")
        box.exec()

    def _quit(self) -> None:
        log.info("quitting")
        self.stop()
        self._application.quit()
