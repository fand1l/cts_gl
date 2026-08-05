"""Settings dialog.

Detection values are written straight into ``kwinrc`` so the KWin script reads
exactly what is configured here; everything else goes into the application's own
INI file.
"""

from __future__ import annotations

import shutil
import subprocess

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import material, ocr, qr
from .config import (
    AppSettings,
    DetectionSettings,
    autostart_enabled,
    read_detection,
    set_autostart,
    write_detection,
)
from .gesture import GesturePreview
from .history import RECENT_DIR
from .i18n import available_languages, tr
from .lens import VARIANTS
from .logging_setup import get_logger
from .misfires import SURVEY_INTERVAL, SURVEY_LIMIT, TRACE_DIR

log = get_logger("settings")


def _hint(text: str) -> QLabel:
    """The sentence under a setting that says what it is for.

    MD3's *body-small* in the ``on-surface-variant`` role, rather than the
    disabled state these used to borrow: switching a label off to grey it says
    "you may not touch this", which was never true of a sentence, and how far a
    disabled widget fades is the platform style's decision rather than this
    program's — so the hints came out nearly invisible on some themes and barely
    quieter than the text above them on others.
    """
    return material.supporting(text)


class SettingsDialog(QDialog):
    """Modeless settings window."""

    #: Emitted after settings were written, so the tray can refresh itself.
    applied = pyqtSignal()
    #: The user asked for the calibration window.
    calibrate_requested = pyqtSignal()

    def __init__(self, app_settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_settings = app_settings
        self._detection = read_detection()

        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(520)

        tabs = QTabWidget(self)
        tabs.addTab(self._scrollable(self._build_detection_tab()), tr("settings.tab.detection"))
        tabs.addTab(self._scrollable(self._build_general_tab()), tr("settings.tab.general"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        # The 8 dp grid.
        layout.setContentsMargins(*(material.space(2),) * 4)
        layout.setSpacing(material.space(1.5))
        layout.addWidget(tabs)
        layout.addWidget(_hint(tr("settings.restart_note")))
        layout.addWidget(buttons)

        self._load()
        self._fit_to_screen()

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """Let a tab be taller than the window, instead of the other way round.

        The hints in here are word-wrapped prose, and a wrapped label only knows
        how tall it is once it knows how wide it is — which is why this window
        used to open *shorter than its own contents* and lay the sentences over
        the controls below them.  Now that the labels report an honest minimum
        the window can no longer shrink into that state, and the same honesty
        makes the General tab taller than a laptop screen.  Both are the same
        fix: the content is as tall as it needs to be, and the part that gives
        is the view onto it.
        """
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        # Never sideways: everything in here wraps or is a control, so a
        # horizontal bar would only ever mean something had been mis-measured.
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area

    def _fit_to_screen(self) -> None:
        """Open at the size the contents want, or the screen's, whichever is less."""
        wanted = self.sizeHint()
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.resize(
            min(wanted.width(), available.width()),
            min(wanted.height(), round(available.height() * 0.9)),
        )

    # ------------------------------------------------------------------ tabs

    def _build_detection_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        shake = QGroupBox(tr("settings.group.shake"), page)
        form = QFormLayout(shake)

        self.enabled_box = QCheckBox(tr("settings.enabled"), shake)
        form.addRow(self.enabled_box)

        # The movement, animated from the settings themselves — so it is not a
        # drawing of "the" gesture that can drift out of step with the code, it
        # is what these numbers are currently asking for.  Above the button,
        # because it is also the answer to "did the calibration do anything?".
        self.preview = GesturePreview(self._detection, shake)
        form.addRow(self.preview)
        form.addRow(_hint(tr("gesture.caption")))

        calibrate = QPushButton(tr("settings.calibrate"), shake)
        calibrate.clicked.connect(self.calibrate_requested)
        form.addRow(calibrate)
        form.addRow(_hint(tr("settings.calibrate.hint")))

        self.fullscreen_box = QCheckBox(tr("settings.fullscreen"), shake)
        form.addRow(self.fullscreen_box)
        form.addRow(_hint(tr("settings.fullscreen.hint")))

        self.learn_box = QCheckBox(tr("settings.learn"), shake)
        form.addRow(self.learn_box)
        form.addRow(
            _hint(
                tr(
                    "settings.learn.hint",
                    total=SURVEY_LIMIT,
                    interval=SURVEY_INTERVAL,
                    directory=str(TRACE_DIR),
                )
            )
        )

        layout.addWidget(shake)

        # The ten thresholds, folded away.  The calibration exists on the
        # argument that tuning six numbers by hand is the wrong job for a
        # person — and then those numbers were left as the most prominent thing
        # in the window, which says the opposite.  They are still all here,
        # unchanged, one click away, for the case the calibration cannot help
        # with; but the first thing the window offers is the button that does
        # the tuning for you.
        self.advanced = QGroupBox(tr("settings.group.advanced"), page)
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        advanced_form = QFormLayout(self.advanced)
        advanced_form.addRow(_hint(tr("settings.group.advanced.hint")))

        self.reversals_spin = self._spin(self.advanced, 1, 6, 1)
        advanced_form.addRow(tr("settings.reversals"), self.reversals_spin)

        self.window_spin = self._spin(self.advanced, 150, 3000, 50, suffix=" ms")
        advanced_form.addRow(tr("settings.window"), self.window_spin)

        self.amplitude_spin = self._spin(self.advanced, 20, 1000, 10, suffix=" px")
        advanced_form.addRow(tr("settings.amplitude"), self.amplitude_spin)

        self.angle_spin = self._spin(self.advanced, 5, 44, 1, suffix=" °")
        advanced_form.addRow(tr("settings.angle"), self.angle_spin)

        self.poll_spin = self._spin(self.advanced, 20, 200, 5, suffix=" ms")
        advanced_form.addRow(tr("settings.poll"), self.poll_spin)

        self.cooldown_spin = self._spin(self.advanced, 200, 10000, 100, suffix=" ms")
        advanced_form.addRow(tr("settings.cooldown"), self.cooldown_spin)

        self.step_spin = self._spin(self.advanced, 1, 40, 1, suffix=" px")
        advanced_form.addRow(tr("settings.step"), self.step_spin)

        self.speed_spin = self._spin(self.advanced, 0, 5000, 50)
        advanced_form.addRow(tr("settings.speed"), self.speed_spin)
        advanced_form.addRow(_hint(tr("settings.speed.hint")))

        self.curvature_spin = self._spin(self.advanced, 100, 400, 10, suffix=" %")
        advanced_form.addRow(tr("settings.curvature"), self.curvature_spin)
        advanced_form.addRow(_hint(tr("settings.curvature.hint")))

        self.reversal_spin = self._spin(self.advanced, 5, 90, 5, suffix=" °")
        advanced_form.addRow(tr("settings.reversal"), self.reversal_spin)
        advanced_form.addRow(_hint(tr("settings.reversal.hint")))

        self.debug_box = QCheckBox(tr("settings.debug"), self.advanced)
        advanced_form.addRow(self.debug_box)
        advanced_form.addRow(_hint(tr("settings.debug.hint")))

        # The four numbers the animation is made of, wired straight into it: a
        # threshold set to something absurd shows up as absurd movement before
        # the dialog is even closed, which is a great deal faster than shaking
        # the mouse and guessing.
        for spin in (
            self.reversals_spin,
            self.window_spin,
            self.amplitude_spin,
            self.speed_spin,
        ):
            spin.valueChanged.connect(self._preview_current)

        # A checkable QGroupBox disables its contents rather than hiding them,
        # which would make an unopened section look like ten broken spin boxes.
        self._advanced_margins = advanced_form.contentsMargins()
        self.advanced.toggled.connect(self._show_advanced)
        layout.addWidget(self.advanced)

        shortcut = QGroupBox(tr("settings.group.shortcut"), page)
        shortcut_layout = QVBoxLayout(shortcut)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("settings.shortcut")))
        self.shortcut_edit = QKeySequenceEdit(shortcut)
        row.addWidget(self.shortcut_edit, 1)
        shortcut_layout.addLayout(row)
        shortcut_layout.addWidget(_hint(tr("settings.shortcut.hint")))
        open_button = QPushButton(tr("settings.shortcut.open"), shortcut)
        open_button.clicked.connect(self._open_shortcut_settings)
        shortcut_layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(shortcut)

        layout.addStretch(1)
        self._show_advanced(False)
        return page

    def _preview_current(self) -> None:
        """Animate what is in the boxes now, not what was last saved."""
        self.preview.set_detection(self.collect())

    def _show_advanced(self, shown: bool) -> None:
        """Fold the thresholds away, without pretending they are unavailable.

        Everything inside the box is hidden rather than greyed out: a disabled
        spin box says "you may not change this", which is not true — the box is
        simply shut.  The window is re-laid out afterwards so it shrinks back
        instead of leaving a hole where the numbers were.
        """
        layout = self.advanced.layout()
        if layout is None:
            return
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setVisible(shown)
        # Otherwise a shut section is a title with an empty box under it.
        if shown:
            layout.setContentsMargins(self._advanced_margins)
        else:
            layout.setContentsMargins(0, 0, 0, 0)
        layout.activate()
        if shown:
            # Grow if the numbers need more room than the window currently has.
            # Only ever grow: half of what is in here is word-wrapped prose, and
            # a window resized down towards minimumSizeHint() lays those labels
            # out on top of the spin boxes, because heightForWidth is not part
            # of that number.  An oversized window is merely roomy.
            QTimer.singleShot(0, self._grow_to_fit)

    def _grow_to_fit(self) -> None:
        self.resize(self.width(), max(self.sizeHint().height(), self.height()))

    def _build_general_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        image = QGroupBox(tr("settings.group.image"), page)
        form = QFormLayout(image)

        self.quality_spin = self._spin(image, 30, 100, 5)
        form.addRow(tr("settings.quality"), self.quality_spin)

        self.max_side_spin = self._spin(image, 200, 4000, 100, suffix=" px")
        form.addRow(tr("settings.maxside"), self.max_side_spin)
        form.addRow(_hint(tr("settings.maxside.hint")))
        layout.addWidget(image)

        selection = QGroupBox(tr("settings.group.selection"), page)
        selection_layout = QVBoxLayout(selection)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(tr("settings.mode")))
        self.mode_combo = QComboBox(selection)
        self.mode_combo.addItem(tr("settings.mode.lasso"), "lasso")
        self.mode_combo.addItem(tr("settings.mode.rect"), "rectangle")
        mode_row.addWidget(self.mode_combo, 1)
        selection_layout.addLayout(mode_row)
        selection_layout.addWidget(_hint(tr("settings.mode.hint")))
        self.lasso_mask_box = QCheckBox(tr("settings.lasso_mask"), selection)
        selection_layout.addWidget(self.lasso_mask_box)
        selection_layout.addWidget(_hint(tr("settings.lasso_mask.hint")))
        self.restore_focus_box = QCheckBox(tr("settings.restore_focus"), selection)
        selection_layout.addWidget(self.restore_focus_box)
        selection_layout.addWidget(_hint(tr("settings.restore_focus.hint")))

        self.keep_recent_box = QCheckBox(tr("settings.keep_recent"), selection)
        selection_layout.addWidget(self.keep_recent_box)
        recent_row = QHBoxLayout()
        recent_row.addWidget(QLabel(tr("settings.recent_limit"), selection))
        self.recent_limit_spin = self._spin(selection, 1, 500, 5)
        recent_row.addWidget(self.recent_limit_spin)
        recent_row.addStretch(1)
        selection_layout.addLayout(recent_row)
        selection_layout.addWidget(_hint(tr("settings.recent_limit.hint")))
        selection_layout.addWidget(
            _hint(tr("settings.keep_recent.hint", directory=str(RECENT_DIR)))
        )
        self.keep_recent_box.toggled.connect(self.recent_limit_spin.setEnabled)

        self.all_screens_box = QCheckBox(tr("settings.all_screens"), selection)
        selection_layout.addWidget(self.all_screens_box)
        selection_layout.addWidget(_hint(tr("settings.all_screens.hint")))

        self.confirm_box = QCheckBox(tr("settings.confirm"), selection)
        selection_layout.addWidget(self.confirm_box)
        selection_layout.addWidget(_hint(tr("settings.confirm.hint")))

        self.magnifier_box = QCheckBox(tr("settings.magnifier"), selection)
        selection_layout.addWidget(self.magnifier_box)
        selection_layout.addWidget(_hint(tr("settings.magnifier.hint")))

        self.ocr_box = QCheckBox(tr("settings.ocr"), selection)
        selection_layout.addWidget(self.ocr_box)
        selection_layout.addWidget(_hint(tr("settings.ocr.hint", command=ocr.install_hint())))
        self.ocr_state = _hint(self._ocr_state())
        selection_layout.addWidget(self.ocr_state)

        self.qr_box = QCheckBox(tr("settings.qr"), selection)
        selection_layout.addWidget(self.qr_box)
        selection_layout.addWidget(
            _hint(
                tr("settings.qr.hint")
                if qr.is_available()
                else tr("settings.qr.missing", command=qr.install_hint())
            )
        )
        layout.addWidget(selection)

        backend_row = QHBoxLayout()
        backend_row.addWidget(QLabel(tr("settings.backend")))
        self.backend_combo = QComboBox(image)
        self.backend_combo.addItem(tr("settings.backend.browser"), "browser")
        self.backend_combo.addItem(tr("settings.backend.auto"), "auto")
        self.backend_combo.addItem(tr("settings.backend.lens"), "lens")
        self.backend_combo.addItem(tr("settings.backend.sbi"), "searchbyimage")
        for variant in VARIANTS:
            self.backend_combo.addItem(f"{tr('settings.backend.pin')} {variant.name}", variant.name)
        backend_row.addWidget(self.backend_combo, 1)
        form.addRow(backend_row)
        form.addRow(_hint(tr("settings.backend.hint")))

        behaviour = QGroupBox(tr("settings.group.behaviour"), page)
        behaviour_layout = QVBoxLayout(behaviour)

        self.clipboard_box = QCheckBox(tr("settings.clipboard"), behaviour)
        behaviour_layout.addWidget(self.clipboard_box)

        dim_row = QHBoxLayout()
        dim_row.addWidget(QLabel(tr("settings.dim")))
        self.dim_slider = QSlider(Qt.Orientation.Horizontal, behaviour)
        self.dim_slider.setRange(0, 80)
        self.dim_slider.setTickInterval(10)
        self.dim_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.dim_value = QLabel("40 %")
        self.dim_slider.valueChanged.connect(lambda value: self.dim_value.setText(f"{value} %"))
        dim_row.addWidget(self.dim_slider, 1)
        dim_row.addWidget(self.dim_value)
        behaviour_layout.addLayout(dim_row)

        self.autostart_box = QCheckBox(tr("settings.autostart"), behaviour)
        behaviour_layout.addWidget(self.autostart_box)

        self.layer_shell_box = QCheckBox(tr("settings.layershell"), behaviour)
        behaviour_layout.addWidget(self.layer_shell_box)
        behaviour_layout.addWidget(_hint(tr("settings.layershell.hint")))

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel(tr("settings.language")))
        self.language_combo = QComboBox(behaviour)
        self.language_combo.addItem(tr("settings.language.auto"), "auto")
        for code in available_languages():
            self.language_combo.addItem(code.upper(), code)
        language_row.addWidget(self.language_combo, 1)
        behaviour_layout.addLayout(language_row)

        layout.addWidget(behaviour)
        layout.addStretch(1)
        return page

    @staticmethod
    def _ocr_state() -> str:
        """Say plainly whether the optional dependency is actually there."""
        if not ocr.is_available():
            return tr("settings.ocr.missing")
        languages = ocr.installed_languages()
        return tr("settings.ocr.found", languages=", ".join(languages) or "?")

    @staticmethod
    def _spin(
        parent: QWidget, minimum: int, maximum: int, step: int, suffix: str = ""
    ) -> QSpinBox:
        spin = QSpinBox(parent)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    # ------------------------------------------------------------------ data

    def reload(self) -> None:
        """Pick up settings that were changed elsewhere (by the calibration)."""
        self._detection = read_detection()
        self._load()

    def _load(self) -> None:
        detection = self._detection
        self.enabled_box.setChecked(detection.enabled)
        self.fullscreen_box.setChecked(detection.disableInFullscreen)
        self.reversals_spin.setValue(detection.reversals)
        self.window_spin.setValue(detection.windowMs)
        self.amplitude_spin.setValue(detection.minAmplitudePx)
        self.angle_spin.setValue(detection.angleTolerance)
        self.poll_spin.setValue(detection.pollMs)
        self.cooldown_spin.setValue(detection.cooldownMs)
        self.step_spin.setValue(detection.minStepPx)
        self.speed_spin.setValue(detection.minSpeedPxPerSec)
        self.curvature_spin.setValue(detection.maxCurvaturePct)
        self.reversal_spin.setValue(detection.reversalTolerance)
        self.debug_box.setChecked(detection.debug)
        self.restore_focus_box.setChecked(detection.restoreFocus)
        self.shortcut_edit.setKeySequence(QKeySequence(detection.shortcut))
        self.preview.set_detection(detection)

        settings = self._app_settings
        self.quality_spin.setValue(settings.jpeg_quality)
        self.max_side_spin.setValue(settings.max_side)
        self.clipboard_box.setChecked(settings.copy_to_clipboard)
        self.learn_box.setChecked(settings.learn_from_misfires)
        self.dim_slider.setValue(settings.dim_percent)
        self.dim_value.setText(f"{settings.dim_percent} %")
        self.layer_shell_box.setChecked(settings.use_layer_shell)
        self.autostart_box.setChecked(autostart_enabled())
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(settings.selection_mode)))
        self.lasso_mask_box.setChecked(settings.lasso_mask)
        self.keep_recent_box.setChecked(settings.keep_recent)
        self.recent_limit_spin.setValue(settings.recent_limit)
        self.recent_limit_spin.setEnabled(settings.keep_recent)
        self.all_screens_box.setChecked(settings.all_screens)
        self.confirm_box.setChecked(settings.confirm_selection)
        self.magnifier_box.setChecked(settings.magnifier)
        self.ocr_box.setChecked(settings.ocr_enabled)
        self.qr_box.setChecked(settings.qr_enabled)
        self.ocr_state.setText(self._ocr_state())
        self.backend_combo.setCurrentIndex(
            max(0, self.backend_combo.findData(settings.lens_backend))
        )

        index = self.language_combo.findData(settings.language)
        self.language_combo.setCurrentIndex(max(0, index))

    def collect(self) -> DetectionSettings:
        """Read the detection widgets back into a settings object."""
        sequence = self.shortcut_edit.keySequence().toString(
            QKeySequence.SequenceFormat.PortableText
        )
        return DetectionSettings(
            enabled=self.enabled_box.isChecked(),
            disableInFullscreen=self.fullscreen_box.isChecked(),
            reversals=self.reversals_spin.value(),
            windowMs=self.window_spin.value(),
            minAmplitudePx=self.amplitude_spin.value(),
            angleTolerance=self.angle_spin.value(),
            pollMs=self.poll_spin.value(),
            cooldownMs=self.cooldown_spin.value(),
            minStepPx=self.step_spin.value(),
            minSpeedPxPerSec=self.speed_spin.value(),
            maxCurvaturePct=self.curvature_spin.value(),
            reversalTolerance=self.reversal_spin.value(),
            debug=self.debug_box.isChecked(),
            restoreFocus=self.restore_focus_box.isChecked(),
            shortcut=sequence or self._detection.shortcut,
        )

    def apply(self) -> None:
        """Persist everything."""
        detection = self.collect()
        write_detection(detection)
        self._detection = detection

        settings = self._app_settings
        settings.jpeg_quality = self.quality_spin.value()
        settings.max_side = self.max_side_spin.value()
        settings.copy_to_clipboard = self.clipboard_box.isChecked()
        settings.learn_from_misfires = self.learn_box.isChecked()
        settings.dim_percent = self.dim_slider.value()
        settings.use_layer_shell = self.layer_shell_box.isChecked()
        settings.selection_mode = str(self.mode_combo.currentData())
        settings.lasso_mask = self.lasso_mask_box.isChecked()
        settings.keep_recent = self.keep_recent_box.isChecked()
        settings.recent_limit = self.recent_limit_spin.value()
        settings.all_screens = self.all_screens_box.isChecked()
        settings.confirm_selection = self.confirm_box.isChecked()
        settings.magnifier = self.magnifier_box.isChecked()
        settings.ocr_enabled = self.ocr_box.isChecked()
        settings.qr_enabled = self.qr_box.isChecked()
        if self.ocr_box.isChecked():
            # Ticking it here counts as the answer, so the overlay does not ask
            # the same question again the first time T is pressed.
            settings.ocr_asked = True
        settings.lens_backend = str(self.backend_combo.currentData())
        settings.language = str(self.language_combo.currentData())
        settings.sync()

        if self.autostart_box.isChecked() != autostart_enabled():
            set_autostart(self.autostart_box.isChecked())

        log.info("settings saved (%s)", settings.path)
        self.applied.emit()

    def _on_accept(self) -> None:
        self.apply()
        self.accept()

    @staticmethod
    def _open_shortcut_settings() -> None:
        binary = shutil.which("systemsettings") or shutil.which("systemsettings6")
        if binary is None:
            log.warning("systemsettings is not installed")
            return
        try:
            subprocess.Popen(
                [binary, "kcm_keys"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.warning("cannot start systemsettings: %s", exc)
