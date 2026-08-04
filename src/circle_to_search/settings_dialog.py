"""Settings dialog.

Detection values are written straight into ``kwinrc`` so the KWin script reads
exactly what is configured here; everything else goes into the application's own
INI file.
"""

from __future__ import annotations

import shutil
import subprocess

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import (
    AppSettings,
    DetectionSettings,
    autostart_enabled,
    read_detection,
    set_autostart,
    write_detection,
)
from .i18n import available_languages, tr
from .lens import VARIANTS
from .logging_setup import get_logger
from .misfires import SURVEY_INTERVAL, SURVEY_LIMIT, TRACE_DIR

log = get_logger("settings")


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)
    return label


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
        tabs.addTab(self._build_detection_tab(), tr("settings.tab.detection"))
        tabs.addTab(self._build_general_tab(), tr("settings.tab.general"))

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
        layout.addWidget(tabs)
        layout.addWidget(_hint(tr("settings.restart_note")))
        layout.addWidget(buttons)

        self._load()

    # ------------------------------------------------------------------ tabs

    def _build_detection_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        shake = QGroupBox(tr("settings.group.shake"), page)
        form = QFormLayout(shake)

        self.enabled_box = QCheckBox(tr("settings.enabled"), shake)
        form.addRow(self.enabled_box)

        calibrate = QPushButton(tr("settings.calibrate"), shake)
        calibrate.clicked.connect(self.calibrate_requested)
        form.addRow(calibrate)
        form.addRow(_hint(tr("settings.calibrate.hint")))

        self.fullscreen_box = QCheckBox(tr("settings.fullscreen"), shake)
        form.addRow(self.fullscreen_box)
        form.addRow(_hint(tr("settings.fullscreen.hint")))

        self.reversals_spin = self._spin(shake, 1, 6, 1)
        form.addRow(tr("settings.reversals"), self.reversals_spin)

        self.window_spin = self._spin(shake, 150, 3000, 50, suffix=" ms")
        form.addRow(tr("settings.window"), self.window_spin)

        self.amplitude_spin = self._spin(shake, 20, 1000, 10, suffix=" px")
        form.addRow(tr("settings.amplitude"), self.amplitude_spin)

        self.angle_spin = self._spin(shake, 5, 44, 1, suffix=" °")
        form.addRow(tr("settings.angle"), self.angle_spin)

        self.poll_spin = self._spin(shake, 20, 200, 5, suffix=" ms")
        form.addRow(tr("settings.poll"), self.poll_spin)

        self.cooldown_spin = self._spin(shake, 200, 10000, 100, suffix=" ms")
        form.addRow(tr("settings.cooldown"), self.cooldown_spin)

        self.step_spin = self._spin(shake, 1, 40, 1, suffix=" px")
        form.addRow(tr("settings.step"), self.step_spin)

        self.speed_spin = self._spin(shake, 0, 5000, 50)
        form.addRow(tr("settings.speed"), self.speed_spin)
        form.addRow(_hint(tr("settings.speed.hint")))

        self.curvature_spin = self._spin(shake, 100, 400, 10, suffix=" %")
        form.addRow(tr("settings.curvature"), self.curvature_spin)
        form.addRow(_hint(tr("settings.curvature.hint")))

        self.reversal_spin = self._spin(shake, 5, 90, 5, suffix=" °")
        form.addRow(tr("settings.reversal"), self.reversal_spin)
        form.addRow(_hint(tr("settings.reversal.hint")))

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

        self.debug_box = QCheckBox(tr("settings.debug"), shake)
        form.addRow(self.debug_box)
        form.addRow(_hint(tr("settings.debug.hint")))

        layout.addWidget(shake)

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
        return page

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
        self.confirm_box = QCheckBox(tr("settings.confirm"), selection)
        selection_layout.addWidget(self.confirm_box)
        selection_layout.addWidget(_hint(tr("settings.confirm.hint")))
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
        self.shortcut_edit.setKeySequence(QKeySequence(detection.shortcut))

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
        self.confirm_box.setChecked(settings.confirm_selection)
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
        settings.confirm_selection = self.confirm_box.isChecked()
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
