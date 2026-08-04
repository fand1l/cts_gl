"""The calibration window.

Puts the KWin script into measuring mode, collects the swings it reports, and
shows what it would change before changing anything.  The arithmetic lives in
:mod:`circle_to_search.calibration`; this is only the part that talks to the
user.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .calibration import MINIMUM_SAMPLES, TARGET_SAMPLES, Sample, describe_changes, suggest
from .config import DetectionSettings, read_detection, set_calibrating, write_detection
from .i18n import tr
from .logging_setup import get_logger

log = get_logger("calibration-dialog")

#: Human names for the keys the calibration touches.
_LABELS = {
    "minAmplitudePx": "settings.amplitude",
    "minSpeedPxPerSec": "settings.speed",
    "maxCurvaturePct": "settings.curvature",
    "angleTolerance": "settings.angle",
    "reversalTolerance": "settings.reversal",
    "windowMs": "settings.window",
}


class CalibrationDialog(QDialog):
    """Shake a few times; the thresholds follow."""

    applied = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: list[Sample] = []
        self._current = read_detection()
        self._suggestion: DetectionSettings | None = None

        self.setWindowTitle(tr("calibrate.title"))
        self.setMinimumWidth(520)

        self._instructions = QLabel(tr("calibrate.instructions"))
        self._instructions.setWordWrap(True)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, TARGET_SAMPLES)
        self._progress.setFormat(tr("calibrate.progress"))

        self._status = QLabel(tr("calibrate.waiting"))
        self._status.setWordWrap(True)

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(
            [tr("calibrate.setting"), tr("calibrate.before"), tr("calibrate.after")]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setVisible(False)

        self._restart = QPushButton(tr("calibrate.restart"), self)
        self._restart.clicked.connect(self.restart)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if self._apply_button is not None:
            self._apply_button.setEnabled(False)
            self._apply_button.clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(self._restart, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._instructions)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._table, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ mode

    def start(self) -> None:
        """Show the dialog and put the script into measuring mode."""
        self.restart()
        if not set_calibrating(True):
            self._status.setText(tr("calibrate.no_script"))
        self.show()
        self.raise_()
        self.activateWindow()

    def restart(self) -> None:
        self._samples.clear()
        self._suggestion = None
        self._current = read_detection()
        self._progress.setValue(0)
        self._table.setVisible(False)
        self._table.setRowCount(0)
        self._status.setText(tr("calibrate.waiting"))
        if self._apply_button is not None:
            self._apply_button.setEnabled(False)

    def stop(self) -> None:
        """Leave measuring mode.  Safe to call more than once."""
        set_calibrating(False)

    # --------------------------------------------------------------- samples

    @pyqtSlot(int, int, int, int, int, int)
    def on_sample(
        self,
        length: int,
        speed: int,
        curvature_pct: int,
        diagonal_deg: int,
        turn_deg: int,
        duration_ms: int,
    ) -> None:
        """One swing measured by the KWin script."""
        if not self.isVisible():
            return
        self._samples.append(
            Sample(
                length=length,
                speed=speed,
                curvature_pct=curvature_pct,
                diagonal_deg=diagonal_deg,
                turn_deg=turn_deg,
                duration_ms=duration_ms,
            )
        )
        count = len(self._samples)
        self._progress.setValue(min(count, TARGET_SAMPLES))
        log.debug(
            "sample %d: %d px, %d px/s, curve %d%%, diag %d°, turn %d°, %d ms",
            count,
            length,
            speed,
            curvature_pct,
            diagonal_deg,
            turn_deg,
            duration_ms,
        )

        if count < MINIMUM_SAMPLES:
            self._status.setText(tr("calibrate.keep_going", count=count, total=TARGET_SAMPLES))
            return
        self._recompute()

    def _recompute(self) -> None:
        suggestion = suggest(self._samples, self._current)
        rows = describe_changes(self._current, suggestion)
        self._suggestion = suggestion

        self._table.setRowCount(len(rows))
        for index, (key, before, after) in enumerate(rows):
            label = tr(_LABELS.get(key, key)).rstrip(":")
            for column, text in enumerate((label, before, after)):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(index, column, item)
        self._table.resizeColumnsToContents()
        self._table.setVisible(bool(rows))

        if rows:
            self._status.setText(tr("calibrate.ready", count=len(self._samples)))
        else:
            self._status.setText(tr("calibrate.no_change", count=len(self._samples)))
        if self._apply_button is not None:
            self._apply_button.setEnabled(True)

    # ---------------------------------------------------------------- result

    def apply(self) -> None:
        if self._suggestion is None:
            return
        write_detection(self._suggestion)
        log.info("calibration applied")
        self._current = self._suggestion
        self.applied.emit()
        self.accept()

    def done(self, result: int) -> None:
        # Whatever closed the dialog — Apply, Cancel, the window button — the
        # script has to come out of measuring mode, or the gesture stays dead.
        self.stop()
        super().done(result)
