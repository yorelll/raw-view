"""Frame navigation bar — sits below the image view in each tab."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)


class FrameNavBar(QWidget):
    """Horizontal bar with navigation buttons and frame counter.

    Layout::
        │◀  ◀  [1] / 5  ▶  ▶│

    Frame numbers displayed in the bar are 1-based for user-friendliness.
    Internal frame indices (passed via signal) remain 0-based.

    Signals
    -------
    frameChanged(int)
        Emitted when the user changes the frame index (0-based).
    """

    frameChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("frameNavBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)

        self._nav_min_h = 32
        self._nav_min_w = 36
        self._spin_min_w = 52

        # ── Buttons ────────────────────────────────────────────────────
        # Icon mapping: │◀  ◀  ▶  ▶│
        self.first_btn = QPushButton("\u2502\u25C0")   # │◀
        self.first_btn.setAccessibleName("First frame")
        self.first_btn.setAccessibleDescription("Go to the first frame (Home).")
        self.first_btn.setToolTip("First frame (Home)")
        self.prev_btn = QPushButton("\u25C0")           # ◀
        self.prev_btn.setAccessibleName("Previous frame")
        self.prev_btn.setAccessibleDescription("Go to the previous frame (Left or Up).")
        self.prev_btn.setToolTip("Previous frame (Left / Up)")

        # Current frame / total
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(1, 1_000_000)
        self.frame_spin.setAlignment(Qt.AlignCenter)
        self.frame_spin.setProperty("mono", True)
        self.frame_spin.setEnabled(False)
        self.frame_spin.setAccessibleName("Current frame")
        self.frame_spin.setAccessibleDescription("Current frame number.")

        self._total_label = QLabel("/ 0")
        self._total_label.setProperty("mono", True)
        self._total_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._total_label.setAccessibleName("Total frames")
        self._total_label.setAccessibleDescription("Total number of frames in the file.")

        self.next_btn = QPushButton("\u25B6")           # ▶
        self.next_btn.setAccessibleName("Next frame")
        self.next_btn.setAccessibleDescription("Go to the next frame (Right or Down).")
        self.next_btn.setToolTip("Next frame (Right / Down)")
        self.last_btn = QPushButton("\u25B6\u2502")     # ▶│
        self.last_btn.setAccessibleName("Last frame")
        self.last_btn.setAccessibleDescription("Go to the last frame (End).")
        self.last_btn.setToolTip("Last frame (End)")

        self._nav_buttons = (
            self.first_btn, self.prev_btn, self.next_btn, self.last_btn,
        )
        self._update_metrics()

        # Note: button/spin/label colours are themed globally via
        # ``build_ui_stylesheet`` (#frameNavBar …) in models.py, so they
        # follow the active light/dark theme instead of being hard-coded
        # here. Only the layout/metrics live in this widget.

        # ── Layout: │◀  ◀  [1] / 5  ▶  ▶│  ───────────────────────────
        layout.addStretch()
        for wdg in (self.first_btn, self.prev_btn):
            layout.addWidget(wdg, 0, Qt.AlignVCenter)
        layout.addSpacing(8)
        layout.addWidget(self.frame_spin, 0, Qt.AlignVCenter)
        layout.addWidget(self._total_label, 0, Qt.AlignVCenter)
        layout.addSpacing(8)
        layout.addWidget(self.next_btn, 0, Qt.AlignVCenter)
        layout.addWidget(self.last_btn, 0, Qt.AlignVCenter)
        layout.addStretch()

        # Signals
        self.frame_spin.valueChanged.connect(self._on_spin_changed)
        self.first_btn.clicked.connect(self._first)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.last_btn.clicked.connect(self._last)

    # ── public API ───────────────────────────────────────────────────

    def _update_metrics(self) -> None:
        """Size controls from the active font instead of fixed 36x32 boxes.

        The stylesheet can enlarge ``frameNavBar`` text at runtime (font-size
        setting), so constants alone clip ``│◀`` / ``▶│`` and large frame
        numbers.  Recompute on font/style changes; the old 36x32 remains the
        minimum for compact themes.
        """
        button_fm = QFontMetrics(self.first_btn.font())
        button_w = max(
            self._nav_min_w,
            max(button_fm.horizontalAdvance(btn.text()) for btn in self._nav_buttons) + 10,
        )
        button_h = max(self._nav_min_h, button_fm.height() + 8)
        for btn in self._nav_buttons:
            btn.setFixedSize(button_w, button_h)

        spin_fm = QFontMetrics(self.frame_spin.font())
        spin_w = max(
            self._spin_min_w,
            spin_fm.horizontalAdvance("1000000") + 40,
        )
        spin_h = max(self._nav_min_h, spin_fm.height() + 10)
        self.frame_spin.setFixedSize(spin_w, spin_h)

        label_fm = QFontMetrics(self._total_label.font())
        self._total_label.setFixedHeight(
            max(self._nav_min_h, label_fm.height() + 8)
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._update_metrics()

    def showEvent(self, event) -> None:
        # A stylesheet installed on QApplication before construction is applied
        # during polish without a StyleChange event, so size once after show.
        super().showEvent(event)
        self._update_metrics()

    def set_frame_info(self, current: int, total: int) -> None:
        """Update frame display and enable/disable nav buttons.

        Parameters are 0-based internally; display is 1-based.
        """
        self.frame_spin.setRange(1, max(1, total))
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(current + 1)
        self.frame_spin.blockSignals(False)
        self._total_label.setText(f"/ {total}")
        has_multiple = total > 1
        at_start = current <= 0
        at_end = current >= total - 1
        self.frame_spin.setEnabled(has_multiple)
        self.first_btn.setEnabled(has_multiple and not at_start)
        self.prev_btn.setEnabled(has_multiple and not at_start)
        self.next_btn.setEnabled(has_multiple and not at_end)
        self.last_btn.setEnabled(has_multiple and not at_end)

    def frame_index(self) -> int:
        """Return the current frame index (0-based)."""
        return self.frame_spin.value() - 1

    def set_frame_index(self, index: int) -> None:
        """Set the frame index (0-based) without emitting signal."""
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(index + 1)
        self.frame_spin.blockSignals(False)

    # ── internal slots ───────────────────────────────────────────────

    def _on_spin_changed(self, value: int) -> None:
        """Emit 0-based frame index when spin box changes."""
        self.frameChanged.emit(value - 1)

    def _first(self) -> None:
        self.frame_spin.setValue(1)

    def _prev(self) -> None:
        if self.frame_spin.value() > 1:
            self.frame_spin.setValue(self.frame_spin.value() - 1)

    def _next(self) -> None:
        max_val = self.frame_spin.maximum()
        if self.frame_spin.value() < max_val:
            self.frame_spin.setValue(self.frame_spin.value() + 1)

    def _last(self) -> None:
        self.frame_spin.setValue(self.frame_spin.maximum())
