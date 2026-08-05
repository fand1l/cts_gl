"""Application object: tray icon, D-Bus wiring and the capture → Lens flow."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QStandardPaths,
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
    QPainter,
    QPixmap,
    QPolygon,
    QScreen,
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_ID, DBUS_SERVICE, __version__, ocr, websearch
from .calibration_dialog import CalibrationDialog
from .config import (
    AppSettings,
    invoke_global_shortcut,
    kwin_script_enabled,
    kwin_script_installed,
    read_detection,
    set_collect_traces,
    set_detection_enabled,
)
from .dbus_service import ServiceObject, register_service, unregister_service
from .hidpi import ScreenMetrics, measure_screen, physical_rect_to_logical
from .history import RecentCaptures
from .i18n import current_language, set_language, tr
from .imageops import (
    black_out,
    mask_outside_polygon,
    pil_to_qimage,
    polygon_to_crop_space,
    rects_to_crop_space,
)
from .lastarea import EVERY_SCREEN, format_area, parse_area
from .lens import (
    BACKEND_BROWSER,
    LensError,
    prepare_image,
    upload,
    write_browser_launcher,
)
from .logging_setup import get_logger
from .misfires import (
    SURVEY_LIMIT,
    SurveyState,
    after_opening,
    parse_trace,
    save_trace,
    should_ask,
    still_learning,
)
from .multiscreen import OverlayGroup, ScreenShot, VirtualDesktop
from .notify import notify, notify_error, supports_actions
from .overlay import (
    ACTION_COPY,
    ACTION_PIN,
    ACTION_SAVE,
    ACTION_SEARCH,
    ACTION_TEXT,
    SelectionOverlay,
)
from .pinned import PinnedCrop, open_pin
from .screenshot import CaptureError, capture_screen
from .settings_dialog import SettingsDialog
from .traystate import read_tray_state
from .welcome import WelcomeDialog
from .windows import parse_rects, visible_on

log = get_logger("app")

_ICON_NAMES = (APP_ID, "circle-to-search", "edit-select", "search")

#: How long the launcher page stays on disk.  It has to outlive a cold browser
#: start and leave room for one manual reload after a hiccup.
LAUNCHER_LIFETIME_MS = 10 * 60 * 1000

#: A trace older than this belongs to some earlier trigger, not to this one.
TRACE_MAX_AGE_S = 5.0

#: How long to give the compositor to answer "capture now" before falling back
#: to Qt's idea of where the pointer is.  One round trip through kglobalaccel,
#: KWin and back is a few milliseconds; this is generous.
SHORTCUT_GRACE_MS = 600

#: The misfire question stays up this long.  Long enough to notice after the
#: browser tab opened, short enough not to pile up.
SURVEY_TIMEOUT_MS = 25000


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


class _OcrSignals(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)


class _OcrTask(QRunnable):
    """Runs tesseract off the GUI thread; it takes long enough to be felt."""

    def __init__(self, image: Image.Image, languages: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = _OcrSignals()
        self._image = image
        self._languages = languages

    @pyqtSlot()
    def run(self) -> None:
        try:
            text = ocr.recognise(self._image, self._languages)
        except ocr.OcrError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            log.exception("unexpected error during text recognition")
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(text)


class _WordsSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class _WordsTask(QRunnable):
    """Reads the whole frozen screen, with a box around every word.

    Started as soon as the overlay is up rather than on a key press: by the time
    the user has decided what they want, the text is usually already selectable,
    and when it is not the overlay says so instead of looking stuck.
    """

    def __init__(self, image: Image.Image, languages: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = _WordsSignals()
        self._image = image
        self._languages = languages

    @pyqtSlot()
    def run(self) -> None:
        try:
            words = ocr.recognise_words(self._image, self._languages)
        except ocr.OcrError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            log.exception("unexpected error while reading the screen")
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(words)


def _as_box(rect: QRect) -> tuple[int, int, int, int]:
    """``QRect`` → the ``(left, top, right, bottom)`` the pure helpers take."""
    return (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())


class _EstimateSignals(QObject):
    finished = pyqtSignal(QRect, int)


class _EstimateTask(QRunnable):
    """Measure what a crop would weigh once it has been prepared for upload.

    By really preparing it.  A guess from pixels and quality would be wrong by a
    factor of three between a photograph and a page of text, and the point of the
    number is to make *Longest side* and *JPEG quality* mean something — a made-up
    one would do the opposite.

    Off the GUI thread because a full-screen 4K crop takes tens of milliseconds
    to resize and encode, and this runs again every time the box settles.
    """

    def __init__(
        self,
        image: Image.Image,
        crop: QRect,
        max_side: int,
        quality: int,
        redactions: list[QRect] | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = _EstimateSignals()
        self._image = image
        self._crop = QRect(crop)
        self._max_side = max_side
        self._quality = quality
        self._redactions = [QRect(rect) for rect in redactions or []]

    @pyqtSlot()
    def run(self) -> None:
        box = (
            self._crop.x(),
            self._crop.y(),
            self._crop.x() + self._crop.width(),
            self._crop.y() + self._crop.height(),
        )
        try:
            cropped = self._image.crop(box)
            # Solid black compresses to almost nothing, so a readout that
            # ignored the redactions would be measurably wrong about the very
            # image it claims to describe.
            cropped = black_out(cropped, rects_to_crop_space(self._redactions, self._crop))
            prepared = prepare_image(cropped, max_side=self._max_side, quality=self._quality)
        except Exception:
            # Nothing depends on this: without an answer the readout simply says
            # the dimensions and leaves the bytes out.  Still answered, with
            # nothing, so that the caller can let go of the task either way.
            log.debug("could not measure the upload size", exc_info=True)
            self.signals.finished.emit(self._crop, 0)
            return
        self.signals.finished.emit(self._crop, len(prepared.payload))


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
        self._service.calibration_sample.connect(self.on_calibration_sample)
        self._service.gesture_trace.connect(self.on_gesture_trace)
        self._service.window_rects.connect(self.on_window_rects)
        self._service.triggered_current.connect(self.on_trigger_current)
        self._service.settings_requested.connect(self.show_settings)

        self._overlay: SelectionOverlay | None = None
        self._group: OverlayGroup | None = None
        self._desktop: VirtualDesktop | None = None
        self._dialog: SettingsDialog | None = None
        self._calibration: CalibrationDialog | None = None
        self._welcome: WelcomeDialog | None = None
        self._tasks: set[QRunnable] = set()
        #: Overlays that have already handed their selection over and are only
        #: still on screen to say it is on its way.  Held here for the same
        #: reason as `_tasks`: nothing else references them, and letting Python
        #: collect one would take the badge off the screen it is drawn on.
        self._sending: set[SelectionOverlay] = set()
        #: Crops left on the screen.  Same reason again, and this one outlives
        #: everything else here: a pin is meant to still be there in ten
        #: minutes, long after the capture that made it was forgotten.
        self._pins: set[PinnedCrop] = set()
        self._busy = False

        # Learning from misfires: the movement the KWin script last sent, and
        # the movement that opened the overlay currently on screen.
        self._survey = self._load_survey()
        self._pending_trace: tuple[str, float] | None = None
        #: The window layout the KWin script sent with the last trigger,
        #: and when.  Buffered like the trace, and stale for the same reason.
        self._pending_windows: tuple[list[QRect], float] | None = None
        self._opening_trace = ""
        self._awaiting_shortcut = False
        #: What was read off the frozen screen, in physical pixels of it.
        self._ocr_words: list[ocr.Word] = []

        self._recent = RecentCaptures()

        self._icon = self._load_icon()
        #: The same icon greyed out, for every state in which shaking the
        #: pointer will not open anything.  Built once; it is a repaint of a
        #: 22-pixel picture, but it is asked for on every menu opening.
        self._dim_icon = self._dimmed(self._icon)
        self._tray = QSystemTrayIcon(self._icon, self)
        self._detection_action: QAction | None = None
        self._recent_menu: QMenu | None = None
        #: What the KWin script last said its version was.  Empty until it says
        #: so, which is normal for a daemon started after the session.
        self._script_version = ""
        self._service.script_ready.connect(self._on_script_ready)
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
        self._arm_trace_collection()
        if not self._settings.first_run_done:
            # After the tray icon, so the window has something to point at.
            QTimer.singleShot(1200, self.show_welcome)

    def stop(self) -> None:
        if self._calibration is not None:
            self._calibration.stop()
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

        self._recent_menu = QMenu(tr("tray.recent"), menu)
        # Rebuilt every time it is opened: it is a directory, not a cache, and
        # another instance or the user may have changed it in between.
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        menu.addMenu(self._recent_menu)

        menu.addSeparator()

        calibrate = QAction(tr("settings.calibrate"), menu)
        calibrate.triggered.connect(self.show_calibration)
        menu.addAction(calibrate)

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
        self._tray.activated.connect(self._on_tray_activated)
        # Read again every time the menu is opened, which is the moment the
        # user is asking "is this thing on?".  Anything cached here would be
        # capable of the exact disagreement this is meant to expose.
        menu.aboutToShow.connect(self.refresh_tray)
        self.refresh_tray()

    # ---------------------------------------------------------- what it says

    @staticmethod
    def _dimmed(icon: QIcon) -> QIcon:
        """The same icon at a third of its opacity."""
        source = icon.pixmap(64, 64)
        if source.isNull():
            return icon
        faded = QPixmap(source.size())
        faded.setDevicePixelRatio(source.devicePixelRatio())
        faded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(faded)
        painter.setOpacity(0.35)
        painter.drawPixmap(0, 0, source)
        painter.end()
        return QIcon(faded)

    @pyqtSlot(str)
    def _on_script_ready(self, version: str) -> None:
        """The KWin script said which version of itself is running."""
        if version != self._script_version:
            log.info("the KWin script running in the compositor is v%s", version)
        self._script_version = version
        self.refresh_tray()

    @pyqtSlot()
    def refresh_tray(self) -> None:
        """Make the icon and its tooltip say what is actually the case."""
        state = read_tray_state(self._script_version)
        self._tray.setIcon(self._icon if state.working else self._dim_icon)
        self._tray.setToolTip(state.tooltip)
        if self._detection_action is not None:
            self._detection_action.setChecked(read_detection().enabled)
        log.debug("tray state: %s", state.state)

    # ------------------------------------------------------ recent captures

    def _fill_recent_menu(self) -> None:
        """(Re)build the list of kept selections."""
        menu = self._recent_menu
        if menu is None:
            return
        menu.clear()

        entries = self._recent.entries() if self._settings.keep_recent else []
        if not entries:
            empty = menu.addAction(
                tr("tray.recent.empty") if self._settings.keep_recent else tr("tray.recent.off")
            )
            empty.setEnabled(False)
            return

        for entry in entries:
            submenu = menu.addMenu(entry.label)
            icon = QIcon(
                QPixmap(str(entry.path)).scaled(
                    48,
                    48,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            if not icon.isNull():
                submenu.setIcon(icon)
            for label, action in (
                (tr("tray.recent.search"), ACTION_SEARCH),
                (tr("tray.recent.copy"), ACTION_COPY),
                (tr("tray.recent.save"), ACTION_SAVE),
                (tr("tray.recent.text"), ACTION_TEXT),
            ):
                item = submenu.addAction(label)
                item.triggered.connect(
                    lambda _checked=False, path=entry.path, chosen=action: self._reuse(
                        path, chosen
                    )
                )
            submenu.addSeparator()
            forget = submenu.addAction(tr("tray.recent.forget"))
            forget.triggered.connect(
                lambda _checked=False, path=entry.path: self._recent.forget(path)
            )

        menu.addSeparator()
        clear = menu.addAction(tr("tray.recent.clear"))
        clear.triggered.connect(self._clear_recent)

    def _reuse(self, path: Path, action: str) -> None:
        """Do something with a kept selection instead of making a new one."""
        image = self._recent.load(path)
        if image is None:
            notify_error(tr("notify.recent_gone"), str(path))
            return
        log.info("reusing %s (%s)", path.name, action)
        self._deliver(image, action, remember=False, text=self._recent.text_of(path))

    def _clear_recent(self) -> None:
        removed = self._recent.clear()
        if removed:
            notify(tr("notify.recent_cleared", count=removed), transient=True, timeout_ms=4000)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.on_trigger_current()

    def _on_detection_toggled(self, checked: bool) -> None:
        set_detection_enabled(checked)
        log.info("cursor-shake detection %s", "enabled" if checked else "disabled")
        # Writing the key makes KWin reconfigure, which makes the running script
        # re-read its settings and announce itself — so this is also the moment
        # a stale one gives itself away, and it is the first thing anyone does
        # when the toggle appears to do nothing.
        self.refresh_tray()

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
        if not self._script_version:
            # Normal for a daemon restarted mid-session: the script announces
            # itself when it loads and whenever the settings change, and there
            # is no way to ask it directly.  Nothing is claimed about the
            # version until it does say, and the first settings change — which
            # is what someone chasing "the toggle does nothing" will do
            # immediately — brings the answer with it.
            log.debug("the KWin script has not announced its version yet")
        self.refresh_tray()

    # ---------------------------------------------------------------- events

    @pyqtSlot(int, int, str)
    def on_trigger(self, x: int, y: int, screen_name: str) -> None:
        """Handle ``Trigger`` from the KWin script."""
        self._open_for(x, y, screen_name, gesture=False)

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
        self._open_for(x, y, screen_name, gesture=True)

    def _open_for(self, x: int, y: int, screen_name: str, *, gesture: bool) -> None:
        screen = self._resolve_screen(screen_name, x, y)
        if screen is None:
            notify_error(tr("notify.no_screen"), tr("notify.no_screen_body", name=screen_name))
            return
        # Only a gesture can be a misfire; pressing the shortcut is deliberate,
        # so it is never questioned and its trace is not kept.
        trace = self._take_trace() if gesture else ""
        self._begin_selection(screen, screen_name or screen.name(), trace=trace)

    @pyqtSlot()
    def on_trigger_current(self) -> None:
        """Handle the tray action / ``TriggerCurrentScreen``.

        Wayland does not let a client ask for the global pointer position, and
        Qt only knows where it last saw the pointer inside one of our own
        windows.  So the first choice is to have *the compositor* do it: press
        the KWin script's shortcut through kglobalaccel and let its handler call
        back with ``workspace.cursorPos``, which is the real thing.

        kglobalaccel does not complain about an action it has never heard of, so
        success is not proof that anything happened — if no trigger arrives
        shortly, this falls back to Qt's guess.
        """
        if self._overlay is not None or self._group is not None:
            log.info("ignoring trigger, a selection is already in progress")
            return
        if not self._awaiting_shortcut and invoke_global_shortcut():
            self._awaiting_shortcut = True
            QTimer.singleShot(SHORTCUT_GRACE_MS, self._shortcut_timed_out)
            return
        self._capture_where_qt_thinks()

    def _shortcut_timed_out(self) -> None:
        if not self._awaiting_shortcut:
            return
        self._awaiting_shortcut = False
        log.info(
            "the compositor did not act on the shortcut; falling back to Qt's "
            "idea of the pointer position"
        )
        self._capture_where_qt_thinks()

    def _capture_where_qt_thinks(self) -> None:
        """Last resort: the position Qt last saw, then the primary screen.

        Accurate while the tray menu is open, because the pointer is then over a
        window of ours; a guess otherwise.
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

    def _begin_selection(self, screen: QScreen, screen_name: str, trace: str = "") -> None:
        # Whatever got us here, the shortcut we may have pressed has been
        # answered — by it or by something else, and either way the fallback
        # must not fire on top of it.
        self._awaiting_shortcut = False
        if self._overlay is not None or self._group is not None:
            log.info("ignoring trigger, a selection is already in progress")
            return
        if self._settings.all_screens and len(QGuiApplication.screens()) > 1:
            self._begin_selection_everywhere(trace)
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

        windows = self._take_window_rects()
        pixmap = QPixmap.fromImage(pil_to_qimage(capture.image))
        overlay = SelectionOverlay(
            pixmap=pixmap,
            metrics=metrics,
            screen=screen,
            dim_percent=self._settings.dim_percent,
            mode=self._settings.selection_mode,
            mask_outside=self._settings.lasso_mask,
            confirm=self._settings.confirm_selection,
            magnifier=self._settings.magnifier,
            max_side=self._settings.max_side,
            last_area=self._last_area_for(metrics.name),
        )
        # The compositor's answer to "what is under the pointer", clipped to
        # this screen and moved into its coordinates.  Empty when the KWin
        # script is old or absent, in which case there is simply no outline.
        overlay.set_window_rects(visible_on(windows, screen.geometry()))
        overlay.text_selected.connect(self._on_text_selected)
        overlay.text_search_requested.connect(self._on_text_search)
        overlay.colour_picked.connect(self._on_colour_picked)
        overlay.mode_changed.connect(self._on_mode_changed)
        overlay.estimate_requested.connect(
            lambda crop, ref=overlay: self._estimate_upload(ref, capture.image, crop)
        )
        for signal, action in (
            (overlay.selected, ACTION_SEARCH),
            (overlay.copy_requested, ACTION_COPY),
            (overlay.save_requested, ACTION_SAVE),
            (overlay.pin_requested, ACTION_PIN),
        ):
            signal.connect(
                lambda rect, polygon, chosen=action, ref=overlay: self._on_selected(
                    rect,
                    polygon,
                    capture.image,
                    metrics,
                    action=chosen,
                    # Read at delivery rather than carried through the signal:
                    # the overlay is the only thing that knows what was covered,
                    # and three signatures would have had to grow a fourth
                    # argument that nothing else uses.
                    redactions=ref.redactions(),
                )
            )
        overlay.cancelled.connect(self._on_cancelled)
        self._overlay = overlay
        # Set only here: this is the one point where an opening really happened,
        # so a capture that failed cannot leave a trace behind for the next one.
        self._opening_trace = trace
        self._start_reading(overlay, capture.image)
        QTimer.singleShot(500, lambda: self._check_overlay_geometry(screen))
        try:
            overlay.show_on_screen()
        except Exception as exc:
            log.exception("could not map the overlay")
            self._release_overlay()
            notify_error(tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc))

    def _begin_selection_everywhere(self, trace: str) -> None:
        """One overlay per screen, so a selection can cross the seam."""
        self._busy = True
        shots: list[ScreenShot] = []
        for screen in QGuiApplication.screens():
            name = screen.name()
            try:
                capture = capture_screen(screen, name)
            except Exception as exc:
                # One screen that cannot be captured takes the whole selection
                # down: half a desktop would be a lie about what is on screen.
                self._busy = False
                log.exception("could not capture %s", name)
                notify_error(
                    tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc)
                )
                return
            metrics = measure_screen(
                name=name,
                logical_geometry=screen.geometry(),
                physical_size=capture.size,
                fallback_dpr=screen.devicePixelRatio(),
            )
            log.info("%s (back end: %s)", metrics, capture.backend)
            shots.append(ScreenShot(metrics=metrics, image=capture.image))

        desktop = VirtualDesktop(shots)
        bounds = desktop.bounds
        log.info("selecting across %d screens, %s", len(shots), bounds)

        windows = self._take_window_rects()
        overlays = []
        for shot, screen in zip(shots, QGuiApplication.screens(), strict=True):
            overlays.append(
                SelectionOverlay(
                    pixmap=QPixmap.fromImage(pil_to_qimage(shot.image)),
                    metrics=shot.metrics,
                    screen=screen,
                    dim_percent=self._settings.dim_percent,
                    mode=self._settings.selection_mode,
                    mask_outside=self._settings.lasso_mask,
                    confirm=self._settings.confirm_selection,
                    magnifier=self._settings.magnifier,
                    last_area=self._last_area_for(EVERY_SCREEN, origin=shot.geometry.topLeft()),
                    group_bounds=bounds,
                )
            )

        group = OverlayGroup(overlays, self)
        group.committed.connect(self._on_group_committed)
        group.cancelled.connect(self._on_group_cancelled)
        for overlay, screen in zip(overlays, QGuiApplication.screens(), strict=True):
            overlay.set_window_rects(visible_on(windows, screen.geometry()))
            # Taking text closes the whole group, so it cannot go through the
            # group's own committed/cancelled pair.
            overlay.text_selected.connect(self._on_text_selected)
            overlay.text_search_requested.connect(self._on_text_search)
            overlay.colour_picked.connect(self._on_colour_picked)
            overlay.mode_changed.connect(self._on_mode_changed)
        self._group = group
        self._desktop = desktop
        self._opening_trace = trace
        for overlay, shot in zip(overlays, shots, strict=True):
            self._start_reading(overlay, shot.image)
        try:
            group.show()
        except Exception as exc:
            log.exception("could not map the overlays")
            self._release_group()
            notify_error(tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc))

    def _release_group(self) -> None:
        group = self._group
        self._group = None
        self._desktop = None
        self._busy = False
        if group is None:
            return
        # Whichever member owned the selection may still be showing the badge;
        # release() leaves that one alone and it is kept here instead.
        for overlay in group.overlays:
            self._hold_if_sending(overlay)
        group.release()

    @pyqtSlot(QRect, str)
    def _on_group_committed(self, rect: QRect, action: str) -> None:
        desktop = self._desktop
        # Whichever overlay owns the selection owns what was blacked out of it;
        # read before release(), which is what lets go of them.
        marks = [
            mark
            for overlay in (self._group.overlays if self._group is not None else [])
            for mark in overlay.redactions()
        ]
        self._release_group()
        if desktop is None:
            return
        # Already in global logical pixels, which is the only space a selection
        # spanning two monitors can be described in.
        self._remember_area(EVERY_SCREEN, rect)
        try:
            cropped = desktop.compose(rect)
        except ValueError as exc:
            log.error("could not compose the selection: %s", exc)
            self._stop_sending()
            notify_error(tr("notify.capture_failed"), tr("notify.capture_failed_body", error=exc))
            self._finish_opening()
            return
        # The composed image is at the highest scale any of the screens it
        # touches uses, and the rectangles are logical, so they are scaled by
        # the same factor compose() used.
        cropped = black_out(
            cropped, rects_to_crop_space(marks, rect, desktop.scale_for(rect))
        )
        log.info(
            "composed %dx%d from %d screen(s)",
            cropped.width,
            cropped.height,
            len(desktop.shots),
        )
        self._finish_opening()
        self._deliver(cropped, action)

    @pyqtSlot()
    def _on_group_cancelled(self) -> None:
        log.info("selection cancelled")
        self._release_group()
        self._finish_opening()

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
        if overlay is not None and not self._hold_if_sending(overlay):
            overlay.deleteLater()

    def _hold_if_sending(self, overlay: SelectionOverlay) -> bool:
        """Keep an overlay that is still saying "sending" alive and on screen.

        ``_busy`` is cleared either way: the badge is a courtesy and must never
        be able to lock the program out of the next capture, however long the
        upload takes.
        """
        if not overlay.is_sending():
            return False
        self._sending.add(overlay)
        overlay.dismissed.connect(lambda ref=overlay: self._drop_sending(ref))
        return True

    def _drop_sending(self, overlay: SelectionOverlay) -> None:
        self._sending.discard(overlay)
        overlay.deleteLater()

    def _stop_sending(self) -> None:
        """The launcher page is on disk (or it failed): the badge is done."""
        for overlay in list(self._sending):
            overlay.dismiss()

    def _on_cancelled(self) -> None:
        log.info("selection cancelled")
        self._release_overlay()
        self._finish_opening()

    def _on_selected(
        self,
        rect: QRect,
        polygon: QPolygon,
        image: Image.Image,
        metrics: ScreenMetrics,
        action: str = ACTION_SEARCH,
        redactions: list[QRect] | None = None,
    ) -> None:
        self._release_overlay()
        self._finish_opening()
        self._remember_area(metrics.name, physical_rect_to_logical(rect, metrics))
        box = (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
        log.info("cropping %s out of %dx%d", box, metrics.physical_width, metrics.physical_height)
        cropped = image.crop(box)

        # Before anything else is done with it, and long before anything is
        # written to disk: from here on there is no copy of this image with the
        # blacked-out parts still in it.
        marks = redactions or []
        cropped = black_out(cropped, rects_to_crop_space(marks, rect))

        # The lasso only marks out the edges: by default the upload is the plain
        # rectangular crop, the way Circle to Search behaves on a phone.  The
        # optional mask whitens everything outside the loop instead.
        if polygon.count() >= 3 and self._settings.lasso_mask:
            points = polygon_to_crop_space(polygon, rect.x(), rect.y(), metrics)
            cropped = mask_outside_polygon(cropped, points)
            log.debug("masked everything outside the %d-point lasso", len(points))

        # The screen has already been read, so the words inside the crop are
        # known: keeping them with it means "read the text" from the tray later
        # is instant instead of another run of tesseract.
        inside = ocr.words_in(
            self._ocr_words,
            rect.x(),
            rect.y(),
            rect.x() + rect.width(),
            rect.y() + rect.height(),
        )
        # A word that was painted over is not in the picture any more, and it
        # must not survive in the text file kept beside it either — that would
        # be the redacted thing on disk in plain UTF-8.
        inside = ocr.words_outside(inside, [_as_box(mark) for mark in marks])
        self._deliver(cropped, action, text=ocr.words_to_text(inside))

    def _deliver(
        self,
        cropped: Image.Image,
        action: str,
        *,
        remember: bool = True,
        text: str = "",
    ) -> None:
        """Do whatever the user asked with a finished crop.

        Shared by the single-screen path, the composed multi-screen one and the
        tray's recent list, so the four actions cannot drift apart between them.
        ``text`` is whatever was already recognised inside the crop, so it is
        never recognised twice.
        """
        if action != ACTION_SEARCH:
            # Copying, saving and reading are finished by the time this returns.
            # Only a search waits on anything, so any badge still up is stale.
            self._stop_sending()
        if remember and self._settings.keep_recent:
            self._recent.add(cropped, text=text)
        if action == ACTION_TEXT and text:
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
            log.info("reused %d character(s) of already-recognised text", len(text))
            notify(tr("notify.ocr_done"), ocr.summarise(text), timeout_ms=8000)
            return
        if action == ACTION_COPY:
            self._copy_to_clipboard(cropped)
            notify(tr("notify.copied"), transient=True, timeout_ms=3000)
            return
        if action == ACTION_SAVE:
            self._save_to_file(cropped)
            return
        if action == ACTION_PIN:
            self._pin(cropped)
            return
        if action == ACTION_TEXT:
            self._extract_text(cropped)
            return

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
        # Whatever happened, the overlay has nothing left to wait for: either
        # the page is on disk and the browser is about to be handed it, or there
        # is an error notification to read, and neither wants a frozen screen in
        # front of it.
        self._stop_sending()
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

    # ------------------------------------------------ optional text (OCR)

    def _start_reading(self, overlay: SelectionOverlay, image: Image.Image) -> None:
        """Begin reading the frozen screen, if the user has allowed it.

        Fire and forget: the overlay is already usable, and the words arrive
        whenever they arrive.  Nothing here may raise into the capture path.
        """
        self._ocr_words = []
        if not self._settings.ocr_enabled:
            return
        if not ocr.is_available():
            log.warning("text recognition is on but %s is not installed", ocr.BINARY)
            return

        languages = ocr.pick_languages(current_language(), self._settings.ocr_languages)
        log.info("reading the screen with %s", languages)
        overlay.set_scanning(True)

        task = _WordsTask(image, languages)
        task.signals.finished.connect(
            lambda words, ref=task, target=overlay: self._words_ready(ref, target, words)
        )
        task.signals.failed.connect(
            lambda error, ref=task, target=overlay: self._words_failed(ref, target, error)
        )
        self._tasks.add(task)
        QThreadPool.globalInstance().start(task)

    def _words_ready(
        self, task: QRunnable, overlay: SelectionOverlay, words: list[ocr.Word]
    ) -> None:
        self._tasks.discard(task)
        self._ocr_words = list(words)
        # The overlay may have been closed and replaced while tesseract worked;
        # handing words to a dead window would be harmless but pointless, and
        # handing them to the *next* one would put them in the wrong places.
        if overlay is not self._overlay and overlay not in self._group_overlays():
            log.debug("the overlay closed before the text was ready")
            return
        overlay.set_words(words)

    def _words_failed(self, task: QRunnable, overlay: SelectionOverlay, error: str) -> None:
        self._tasks.discard(task)
        log.warning("could not read the screen: %s", error)
        overlay.set_scanning(False)

    def _group_overlays(self) -> list[SelectionOverlay]:
        return self._group.overlays if self._group is not None else []

    @pyqtSlot(str)
    def _on_mode_changed(self, mode: str) -> None:
        """The chip on the overlay is the setting, reached where it is wanted.

        Kept rather than lasting for one capture: someone who switches to the
        rectangle mid-gesture almost certainly wants it next time too, and
        *Shift* is already there for changing it for exactly one drag.  In a
        group every overlay is told, so the other screens agree with this one.
        """
        if mode == self._settings.selection_mode:
            return
        self._settings.selection_mode = mode
        self._settings.sync()
        log.info("selection mode is now %s", mode)
        for overlay in self._group_overlays():
            overlay.set_mode(mode)

    @pyqtSlot(str)
    def _on_text_selected(self, text: str) -> None:
        """The user dragged across some words and asked for them."""
        self._release_overlay()
        self._release_group()
        self._finish_opening()
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        log.info("copied %d character(s) of recognised text", len(text))
        notify(tr("notify.ocr_done"), ocr.summarise(text), timeout_ms=8000)

    # ------------------------------------------ the same area as last time

    def _last_area_for(self, screen: str, origin: QPoint | None = None) -> QRect:
        """Where the previous capture was, in one overlay's own coordinates.

        Empty unless it was taken on the same screen: offering a rectangle from
        a different monitor would put the selection somewhere arbitrary, and a
        chip that did that would be worse than no chip.
        """
        remembered = parse_area(self._settings.last_area)
        if remembered is None or not remembered.matches(screen):
            return QRect()
        if origin is not None:
            # A group works in global logical pixels; each overlay's box is in
            # its own, which is the same thing shifted by where its screen is.
            return remembered.rect.translated(-origin)
        return QRect(remembered.rect)

    def _remember_area(self, screen: str, rect: QRect) -> None:
        """Keep where a capture was taken, so it can be taken there again."""
        value = format_area(screen, rect)
        if not value or value == self._settings.last_area:
            return
        self._settings.last_area = value
        self._settings.sync()
        log.debug("the last area is now %s", value)

    def _estimate_upload(self, overlay: SelectionOverlay, image: Image.Image, crop: QRect) -> None:
        """Answer the overlay's "how big would this be?", off the GUI thread."""
        task = _EstimateTask(
            image,
            crop,
            self._settings.max_side,
            self._settings.jpeg_quality,
            overlay.redactions(),
        )
        task.signals.finished.connect(
            lambda measured, nbytes, ref=task, view=overlay: self._finish_estimate(
                ref, view, measured, nbytes
            )
        )
        self._tasks.add(task)
        QThreadPool.globalInstance().start(task)

    def _finish_estimate(
        self, task: QRunnable, overlay: SelectionOverlay, crop: QRect, nbytes: int
    ) -> None:
        self._tasks.discard(task)
        if overlay is not self._overlay:
            # Released while this was being measured.  Touching it now would
            # reach a C++ object that deleteLater() has already taken away.
            return
        # The overlay drops the answer itself if the box has moved on since; it
        # is the one that knows what it is currently asking about.
        overlay.set_upload_size(crop, nbytes)

    @pyqtSlot(str)
    def _on_colour_picked(self, colour: str) -> None:
        """A pixel off the frozen screen, already written out."""
        self._release_overlay()
        self._release_group()
        self._finish_opening()
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(colour)
        log.info("copied %s to the clipboard", colour)
        notify(tr("notify.colour", colour=colour), transient=True, timeout_ms=4000)

    @pyqtSlot(str)
    def _on_text_search(self, text: str) -> None:
        """The user dragged across some words and asked to search for them.

        No image is made and nothing is uploaded: the words are already known,
        so this is a plain web search — or, if they picked out a link, the link.
        The overlay is left up with its badge and takes itself down when the
        browser window appears in front of it.
        """
        self._release_overlay()
        self._release_group()
        self._finish_opening()
        link = websearch.looks_like_url(text)
        if link:
            log.info("the selected text is a link, opening it")
            self._open_url(link)
            return
        self._open_url(websearch.search_url(text, current_language()))

    def _extract_text(self, image: Image.Image) -> None:
        """Read the text out of the selection instead of searching for it."""
        if not self._settings.ocr_enabled and not self._ask_about_ocr():
            return
        if not ocr.is_available():
            notify_error(
                tr("notify.ocr_missing"),
                tr("notify.ocr_missing_body", command=ocr.install_hint()),
            )
            return

        languages = ocr.pick_languages(current_language(), self._settings.ocr_languages)
        log.info("recognising text with %s", languages)
        notify(tr("notify.ocr_running"), transient=True, timeout_ms=3000)

        task = _OcrTask(image, languages)
        task.signals.finished.connect(lambda text, ref=task: self._finish_ocr(ref, text=text))
        task.signals.failed.connect(lambda error, ref=task: self._finish_ocr(ref, error=error))
        self._tasks.add(task)
        QThreadPool.globalInstance().start(task)

    def _maybe_offer_ocr(self) -> None:
        """Offer text recognition once, after an overlay has been used.

        A notification rather than a dialog, and afterwards rather than during:
        a modal window over the frozen screen would be in the way of the very
        thing it is asking about.
        """
        if self._settings.ocr_asked or self._settings.ocr_enabled:
            return
        if not supports_actions():
            return
        self._settings.ocr_asked = True
        self._settings.sync()
        body = (
            tr("ocr.offer.found")
            if ocr.is_available()
            else tr("ocr.offer.missing", command=ocr.install_hint())
        )
        notify(
            tr("ocr.offer.title"),
            body,
            timeout_ms=20000,
            actions=(("yes", tr("ocr.offer.yes")), ("no", tr("ocr.offer.no"))),
            on_action=self._on_ocr_offer_answered,
        )

    def _on_ocr_offer_answered(self, key: str) -> None:
        enable = key != "no"
        self._settings.ocr_enabled = enable
        self._settings.sync()
        log.info("text recognition %s from the offer", "enabled" if enable else "declined")
        if self._dialog is not None:
            self._dialog.reload()
        if enable:
            notify(tr("ocr.offer.on"), tr("ocr.offer.on_body"), timeout_ms=8000)

    def _ask_about_ocr(self) -> bool:
        """The one-time question.  True when recognition may go ahead now.

        Off by default and asked exactly once: it needs a package the user may
        not have, and turning something on behind their back is not a favour.
        """
        if self._settings.ocr_asked:
            notify(tr("notify.ocr_off"), tr("notify.ocr_off_body"), timeout_ms=8000)
            return False

        self._settings.ocr_asked = True
        found = ocr.is_available()
        box = QMessageBox()
        box.setWindowTitle(tr("ocr.ask.title"))
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(tr("ocr.ask.text"))
        box.setInformativeText(
            tr("ocr.ask.found") if found else tr("ocr.ask.missing", command=ocr.install_hint())
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        enable = box.exec() == QMessageBox.StandardButton.Yes
        self._settings.ocr_enabled = enable
        self._settings.sync()
        log.info("text recognition %s by the user", "enabled" if enable else "declined")
        if enable and self._dialog is not None:
            self._dialog.reload()
        return enable

    def _finish_ocr(
        self, task: QRunnable, text: str | None = None, error: str | None = None
    ) -> None:
        self._tasks.discard(task)
        if error is not None:
            notify_error(tr("notify.ocr_failed"), tr("notify.ocr_failed_body", error=error))
            return
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        log.info("recognised %d characters", len(text))
        notify(tr("notify.ocr_done"), ocr.summarise(text), timeout_ms=10000)

    def _save_to_file(self, image: Image.Image) -> None:
        """Write the selection next to the user's other screenshots."""
        directory = Path(
            self._settings.save_directory
            or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
            or str(Path.home())
        )
        path = directory / f"circle-to-search-{datetime.now():%Y%m%d-%H%M%S}.png"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            image.save(path, "PNG")
        except (OSError, ValueError) as exc:
            log.error("could not save the selection to %s: %s", path, exc)
            notify_error(tr("notify.save_failed"), tr("notify.save_failed_body", error=exc))
            return
        log.info("saved the selection to %s", path)
        notify(
            tr("notify.saved"),
            str(path),
            timeout_ms=10000,
            actions=(("open", tr("notify.open_folder")),),
            on_action=lambda _key: self._open_url(directory.as_uri()),
        )

    def _pin(self, image: Image.Image) -> None:
        """Leave the crop on the screen, above everything, until it is closed.

        Under the pointer's screen rather than the primary one: on two monitors
        the capture happened where you were looking, and so should the thing it
        left behind.
        """
        pin = open_pin(QPixmap.fromImage(pil_to_qimage(image)), near=QCursor.pos())
        self._pins.add(pin)
        pin.closed.connect(lambda ref=pin: self._drop_pin(ref))
        pin.copy_requested.connect(self._copy_pixmap)

    def _drop_pin(self, pin: PinnedCrop) -> None:
        self._pins.discard(pin)
        pin.deleteLater()

    @pyqtSlot(QPixmap)
    def _copy_pixmap(self, pixmap: QPixmap) -> None:
        """Ctrl+C on a pinned crop: it becomes the copy action, uncaptured."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setImage(pixmap.toImage())
        log.info("a pinned crop was copied to the clipboard")
        notify(tr("notify.copied"), transient=True, timeout_ms=3000)

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
        # Nothing is going to appear now, so an overlay still saying "opening"
        # would be sitting in front of the notification that explains why.
        self._stop_sending()
        log.error("could not open the result: %s", error)
        notify_error(tr("notify.open_failed"), tr("notify.open_failed_body", error=error))

    # ------------------------------------------- learning from misfires

    def _load_survey(self) -> SurveyState:
        return SurveyState(
            enabled=self._settings.learn_from_misfires,
            asks=self._settings.learn_asks,
            since_ask=self._settings.learn_since_ask,
        )

    def _store_survey(self) -> None:
        self._settings.learn_asks = self._survey.asks
        self._settings.learn_since_ask = self._survey.since_ask
        self._settings.sync()

    def _arm_trace_collection(self) -> None:
        """Tell the KWin script whether recent movement is worth keeping.

        Off is the resting state: once the last question has been asked, or the
        user switched the whole thing off, the script stops recording and the
        gesture costs exactly one D-Bus call again.
        """
        wanted = still_learning(self._survey)
        set_collect_traces(wanted)
        log.info(
            "misfire learning: %s (%d question(s) left)",
            "on" if wanted else "off",
            self._survey.remaining,
        )

    @pyqtSlot(str)
    def on_gesture_trace(self, points: str) -> None:
        """The movement that is about to trigger, from the KWin script."""
        self._pending_trace = (points, time.monotonic())

    @pyqtSlot(str)
    def on_window_rects(self, encoded: str) -> None:
        """Where the windows were when the trigger left the compositor.

        Buffered exactly like the movement trace, and for the same reason: it
        arrives immediately *before* the trigger, and a layout left over from an
        earlier one would outline windows that have since moved.
        """
        self._pending_windows = (parse_rects(encoded), time.monotonic())

    def _take_window_rects(self) -> list[QRect]:
        """Consume the pending layout, if it is recent enough to belong here."""
        pending = self._pending_windows
        self._pending_windows = None
        if pending is None:
            return []
        rects, at = pending
        if time.monotonic() - at > TRACE_MAX_AGE_S:
            log.debug("dropping a stale window layout")
            return []
        return rects

    def _take_trace(self) -> str:
        """Consume the pending trace, if it is recent enough to belong here."""
        pending = self._pending_trace
        self._pending_trace = None
        if pending is None:
            return ""
        points, at = pending
        if time.monotonic() - at > TRACE_MAX_AGE_S:
            log.debug("dropping a stale trace")
            return ""
        return points

    def _finish_opening(self) -> None:
        """One overlay opening is over: ask about it, or count it and move on."""
        self._maybe_offer_ocr()
        trace = self._opening_trace
        self._opening_trace = ""
        if not trace:
            return
        self._survey = SurveyState(
            enabled=self._settings.learn_from_misfires,
            asks=self._survey.asks,
            since_ask=self._survey.since_ask,
        )
        asked = should_ask(self._survey) and self._ask_about_trigger(trace)
        self._survey = after_opening(self._survey, asked=asked)
        self._store_survey()
        if asked and not still_learning(self._survey):
            # That was the last one; stop the script from recording movement.
            self._arm_trace_collection()

    def _ask_about_trigger(self, trace: str) -> bool:
        """Show the question.  False when it could not be shown at all."""
        if not supports_actions():
            log.debug("the notification server has no action buttons; not asking")
            return False
        left = self._survey.remaining
        notification = notify(
            tr("survey.title"),
            tr("survey.body", left=left, total=SURVEY_LIMIT),
            timeout_ms=SURVEY_TIMEOUT_MS,
            actions=(("yes", tr("survey.yes")), ("no", tr("survey.no"))),
            on_action=lambda key: self._on_survey_answer(key, trace),
        )
        return notification != 0

    def _on_survey_answer(self, key: str, trace: str) -> None:
        meant_it = key != "no"
        samples = parse_trace(trace)
        path = save_trace(
            samples,
            expect="fire" if meant_it else "no-fire",
            description="confirmed by the user" if meant_it else "reported as a misfire",
        )
        if meant_it:
            log.info("the user confirmed the trigger (%d samples kept)", len(samples))
            return
        log.info("the user reported a misfire (%d samples kept)", len(samples))
        if path is None:
            return
        notify(
            tr("survey.saved"),
            tr("survey.saved_body", path=str(path)),
            timeout_ms=12000,
        )

    # ------------------------------------------------------------------- GUI

    @pyqtSlot()
    def show_settings(self) -> None:
        if self._dialog is None:
            self._dialog = SettingsDialog(self._settings)
            self._dialog.applied.connect(self._on_settings_applied)
            self._dialog.calibrate_requested.connect(self.show_calibration)
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
        self.refresh_tray()
        self._survey = self._load_survey()
        self._arm_trace_collection()

    @pyqtSlot()
    def show_welcome(self) -> None:
        """The first-run window: what the gesture is, and a few choices."""
        if self._welcome is None:
            self._welcome = WelcomeDialog(self._settings)
            self._welcome.calibrate_requested.connect(self.show_calibration)
            self._welcome.finished.connect(self._on_welcome_closed)
        self._welcome.show()
        self._welcome.raise_()
        self._welcome.activateWindow()

    def _on_welcome_closed(self) -> None:
        dialog = self._welcome
        self._welcome = None
        # Whatever the user did with it — answered it, or closed it unread — it
        # has been seen and must not come back at every login.
        self._settings.first_run_done = True
        self._settings.sync()
        self._on_settings_applied()
        if dialog is not None:
            dialog.deleteLater()

    @pyqtSlot()
    def show_calibration(self) -> None:
        """Open the calibration window (from the settings or the tray)."""
        if self._calibration is None:
            self._calibration = CalibrationDialog()
            self._calibration.applied.connect(self._on_calibration_applied)
            self._calibration.finished.connect(self._on_calibration_closed)
        self._calibration.start()

    @pyqtSlot(int, int, int, int, int, int)
    def on_calibration_sample(
        self,
        length: int,
        speed: int,
        curvature_pct: int,
        diagonal_deg: int,
        turn_deg: int,
        duration_ms: int,
    ) -> None:
        if self._calibration is not None:
            self._calibration.on_sample(
                length, speed, curvature_pct, diagonal_deg, turn_deg, duration_ms
            )

    def _on_calibration_applied(self) -> None:
        if self._dialog is not None:
            self._dialog.reload()
        self.refresh_tray()

    def _on_calibration_closed(self) -> None:
        dialog = self._calibration
        self._calibration = None
        if dialog is not None:
            # Belt and braces: the dialog leaves measuring mode in done(), but
            # the script must never be left measuring if anything went wrong.
            dialog.stop()
            dialog.deleteLater()

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
