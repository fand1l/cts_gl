"""The first time it runs.

A tray icon appearing after an install tells nobody what to do with it, and the
one thing this program needs the user to know — *shake the pointer* — is not
discoverable at all.  So the first start says it once, offers the three or four
decisions worth making up front, and points at the calibration.

Deliberately not a wizard: one window, sensible defaults already filled in, and
a button that finishes it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .config import (
    AppSettings,
    autostart_enabled,
    read_detection,
    set_autostart,
    write_detection,
)
from .i18n import available_languages, set_language, tr
from .logging_setup import get_logger

log = get_logger("welcome")


class WelcomeDialog(QDialog):
    """Shown once, on the first start."""

    calibrate_requested = pyqtSignal()

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._detection = read_detection()
        self._applied = False

        self.setWindowTitle(tr("welcome.title"))
        self.setMinimumWidth(560)

        heading = QLabel(tr("welcome.heading"))
        heading.setTextFormat(Qt.TextFormat.RichText)
        heading.setWordWrap(True)

        gesture = QLabel(
            tr(
                "welcome.gesture",
                shortcut=QKeySequence(self._detection.shortcut).toString(
                    QKeySequence.SequenceFormat.NativeText
                )
                or self._detection.shortcut,
            )
        )
        gesture.setTextFormat(Qt.TextFormat.RichText)
        gesture.setWordWrap(True)

        choices = QGroupBox(tr("welcome.choices"), self)
        form = QFormLayout(choices)

        self.language_combo = QComboBox(choices)
        self.language_combo.addItem(tr("settings.language.auto"), "auto")
        for code in available_languages():
            self.language_combo.addItem(code.upper(), code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(settings.language))
        )
        form.addRow(tr("settings.language"), self.language_combo)

        self.mode_combo = QComboBox(choices)
        self.mode_combo.addItem(tr("settings.mode.lasso"), "lasso")
        self.mode_combo.addItem(tr("settings.mode.rect"), "rectangle")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(settings.selection_mode)))
        form.addRow(tr("settings.mode"), self.mode_combo)

        self.detection_box = QCheckBox(tr("settings.enabled"), choices)
        self.detection_box.setChecked(self._detection.enabled)
        form.addRow(self.detection_box)

        self.autostart_box = QCheckBox(tr("settings.autostart"), choices)
        self.autostart_box.setChecked(autostart_enabled())
        form.addRow(self.autostart_box)

        calibrate = QPushButton(tr("welcome.calibrate"), self)
        calibrate.clicked.connect(self._on_calibrate)

        hint = QLabel(tr("welcome.calibrate.hint"))
        hint.setWordWrap(True)
        hint.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, self)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(tr("welcome.done"))
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(gesture)
        layout.addWidget(choices)
        layout.addWidget(calibrate, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(hint)
        layout.addStretch(1)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ data

    def apply(self) -> None:
        """Save the choices.  Called however the window is closed, once."""
        if self._applied:
            return
        self._applied = True
        settings = self._settings
        settings.language = str(self.language_combo.currentData())
        settings.selection_mode = str(self.mode_combo.currentData())
        settings.sync()
        set_language(settings.language)

        if self.detection_box.isChecked() != self._detection.enabled:
            self._detection.enabled = self.detection_box.isChecked()
            write_detection(self._detection)
        if self.autostart_box.isChecked() != autostart_enabled():
            set_autostart(self.autostart_box.isChecked())
        log.info("first-run choices saved")

    def _on_calibrate(self) -> None:
        # Save first: the calibration writes detection settings of its own, and
        # a later apply() from this window would put the old ones back.
        self.apply()
        self.calibrate_requested.emit()
        self.accept()

    def done(self, result: int) -> None:
        self.apply()
        super().done(result)
