"""Frame navigation bar — sits below the image view in each tab."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)


class FrameNavBar(QWidget):
    """Horizontal bar with Prev/Next buttons and frame counter.

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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        self.prev_btn = QPushButton("<")
        self.prev_btn.setFixedWidth(28)
        self.prev_btn.setToolTip("Previous frame (Up)")

        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(1, 1_000_000)
        self.frame_spin.setFixedWidth(80)
        self.frame_spin.setEnabled(False)

        self.next_btn = QPushButton(">")
        self.next_btn.setFixedWidth(28)
        self.next_btn.setToolTip("Next frame (Down)")

        self.total_label = QLabel("/ 0")

        # Scoped style: only affect frame-nav-bar buttons, override global blue button style
        self.setStyleSheet(
            "#frameNavBar QPushButton {"
            "  background: transparent; border: 1px solid palette(mid); border-radius: 4px;"
            "  padding: 2px 4px; font-weight: bold; color: palette(text);"
            "}"
            "#frameNavBar QPushButton:hover { background: rgba(128, 128, 128, 0.2); }"
            "#frameNavBar QPushButton:disabled { color: palette(mid); }"
            "#frameNavBar QSpinBox { padding: 2px 4px; }"
        )

        layout.addStretch()
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.frame_spin)
        layout.addWidget(self.next_btn)
        layout.addWidget(self.total_label)
        layout.addStretch()

        # Signals
        self.frame_spin.valueChanged.connect(self._on_spin_changed)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)

    # ── public API ───────────────────────────────────────────────────

    def set_frame_info(self, current: int, total: int) -> None:
        """Update frame display and enable/disable nav buttons.

        Parameters are 0-based internally; display is 1-based.
        """
        self.frame_spin.setRange(1, max(1, total))
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(current + 1)
        self.frame_spin.blockSignals(False)
        self.total_label.setText(f"/ {total}")
        has_multiple = total > 1
        self.frame_spin.setEnabled(has_multiple)
        self.prev_btn.setEnabled(has_multiple and current > 0)
        self.next_btn.setEnabled(has_multiple and current < total - 1)

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

    def _prev(self) -> None:
        if self.frame_spin.value() > 1:
            self.frame_spin.setValue(self.frame_spin.value() - 1)

    def _next(self) -> None:
        max_val = self.frame_spin.maximum()
        if self.frame_spin.value() < max_val:
            self.frame_spin.setValue(self.frame_spin.value() + 1)
