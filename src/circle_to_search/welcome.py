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

from . import material, ocr
from .config import (
    AppSettings,
    autostart_enabled,
    read_detection,
    set_autostart,
    write_detection,
)
from .gesture import GesturePreview
from .i18n import available_languages, set_language, tr
from .logging_setup import get_logger

log = get_logger("welcome")


def _quiet(text: str) -> QLabel:
    """A remark under the thing it is about.

    On the type scale's *body-small* and in the ``on-surface-variant`` role,
    rather than the disabled state it used to borrow.  Greying a label by
    switching it off says "you may not touch this", which was never true of a
    sentence — and it is the platform style, not this program, that decides how
    far a disabled widget fades, so on some themes the hints were nearly gone
    and on others barely quieter than the text above them.
    """
    return material.supporting(text)


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

        scheme = material.scheme_for_palette()

        # A headline and the line that supports it, which is MD3's own anatomy
        # for the top of a window — and two labels rather than one, because a
        # single one can only be given a single role.
        heading = QLabel(tr("welcome.heading"))
        heading.setWordWrap(True)
        material.restyle(heading, "headline-small", scheme.on_surface)

        intro = QLabel(tr("welcome.intro"))
        intro.setWordWrap(True)
        material.restyle(intro, "body-medium", scheme.on_surface_variant)

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
        material.restyle(gesture, "body-medium", scheme.on_surface)

        # Three lines of prose about a physical movement, and then the movement.
        # This is the one thing a first-run window has to get across, and it is
        # the one thing prose is worst at.
        self.preview = GesturePreview(self._detection, self)

        choices = QGroupBox(tr("welcome.choices"), self)
        form = QFormLayout(choices)
        form.setContentsMargins(*(material.space(1.5),) * 4)
        form.setHorizontalSpacing(material.space(2))
        form.setVerticalSpacing(material.space(1))

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

        self.ocr_box = QCheckBox(tr("welcome.ocr"), choices)
        self.ocr_box.setChecked(settings.ocr_enabled)
        self.ocr_box.setToolTip(ocr.install_hint())
        form.addRow(self.ocr_box)
        form.addRow(_quiet(tr("welcome.ocr.hint")))

        self.autostart_box = QCheckBox(tr("settings.autostart"), choices)
        self.autostart_box.setChecked(autostart_enabled())
        form.addRow(self.autostart_box)

        calibrate = QPushButton(tr("welcome.calibrate"), self)
        calibrate.clicked.connect(self._on_calibrate)

        hint = _quiet(tr("welcome.calibrate.hint"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, self)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(tr("welcome.done"))
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        # The 8 dp grid, rather than whatever the platform's default happened to
        # be: it is the one part of MD3's spacing that is not tied to a phone.
        layout.setContentsMargins(*(material.space(2),) * 4)
        layout.setSpacing(material.space(1.5))
        layout.addWidget(heading)
        layout.addWidget(intro)
        layout.addWidget(gesture)
        layout.addWidget(self.preview)
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
        settings.ocr_enabled = self.ocr_box.isChecked()
        # Answered here, so the offer never arrives as a notification later.
        settings.ocr_asked = True
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
