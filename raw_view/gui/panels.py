"""Left-side control panel for decode parameters."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

from raw_view.models import BAYER_PATTERNS


class ControlPanel(QWidget):
    """Parameter controls for RAW/YUV decode options.

    Signals
    -------
    applyClicked()
        Emitted when the user clicks Apply.
    typeChanged(str)
        Emitted when the image type selection changes.
    rawPreviewChanged(str)
        Emitted when the RAW preview mode changes.
    frameChanged(int)
        Emitted when the user changes the frame index.
    zoomChanged(int)
        Emitted when the zoom slider is moved.
    """

    applyClicked = pyqtSignal()
    typeChanged = pyqtSignal(str)
    rawPreviewChanged = pyqtSignal(str)
    frameChanged = pyqtSignal(int)
    zoomChanged = pyqtSignal(int)

    RAW_FORMATS = [
        "RAW8",
        "RAW10",
        "RAW10 Packed",
        "RAW12",
        "RAW12 Packed",
        "RAW14 Packed",
        "RAW16",
        "RAW32",
    ]
    YUV_FORMATS = [
        "I420",
        "YV12",
        "NV12",
        "NV21",
        "YUYV",
        "UYVY",
        "NV16",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.setObjectName("controlPanel")

        # ── Format parameters ──
        self.type_combo = QComboBox()
        self.type_combo.addItems(["RAW", "YUV", "Standard Image"])

        self.format_combo = QComboBox()
        self.format_combo.addItems(self.RAW_FORMATS)

        self.align_combo = QComboBox()
        self.align_combo.addItems(["lsb", "msb"])

        self.endian_combo = QComboBox()
        self.endian_combo.addItems(["little", "big"])

        self.raw_preview_combo = QComboBox()
        self.raw_preview_combo.addItems(["Bayer Color", "Grayscale"])

        self.bayer_pattern_combo = QComboBox()
        self.bayer_pattern_combo.addItems(BAYER_PATTERNS)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 65535)
        self.width_spin.setValue(640)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 65535)
        self.height_spin.setValue(480)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(0, 1_000_000_000)

        # ── Frame navigation ──
        frame_row = QWidget()
        frame_layout = QHBoxLayout(frame_row)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_prev_btn = QPushButton("<")
        self.frame_prev_btn.setFixedWidth(32)
        self.frame_prev_btn.setToolTip("Previous frame")
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 1_000_000)
        self.frame_spin.setEnabled(False)
        self.frame_next_btn = QPushButton(">")
        self.frame_next_btn.setFixedWidth(32)
        self.frame_next_btn.setToolTip("Next frame")
        self.frame_total_label = QLabel("/ 0")
        frame_layout.addWidget(self.frame_prev_btn)
        frame_layout.addWidget(self.frame_spin)
        frame_layout.addWidget(self.frame_next_btn)
        frame_layout.addWidget(self.frame_total_label)
        frame_layout.addStretch()

        # ── YUV note ──
        self.yuv_desc = QLabel(
            "YUV420: U/V 2x2 downsample; YUV422: horizontal 2:1 downsample"
        )
        self.yuv_desc.setWordWrap(True)

        # ── Zoom controls ──
        zoom_row = QWidget()
        zoom_layout = QHBoxLayout(zoom_row)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        self.zoom_slider = QSlider()
        self.zoom_slider.setOrientation(1)  # Horizontal
        self.zoom_slider.setRange(10, 1000)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickPosition(QSlider.TicksBelow)
        self.zoom_slider.setTickInterval(100)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(48)
        self.one2one_btn = QPushButton("1:1")
        self.one2one_btn.setToolTip("Actual pixel size")
        zoom_layout.addWidget(self.zoom_slider, 1)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(self.one2one_btn)

        # ── Apply button ──
        self.apply_btn = QPushButton("Apply")

        # ── Layout ──
        form = QFormLayout(self)
        form.setVerticalSpacing(10)
        form.addRow("Type", self.type_combo)
        form.addRow("Format", self.format_combo)
        form.addRow("Alignment", self.align_combo)
        form.addRow("Endianness", self.endian_combo)
        form.addRow("RAW preview", self.raw_preview_combo)
        form.addRow("Bayer pattern", self.bayer_pattern_combo)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Offset", self.offset_spin)
        form.addRow("Frame", frame_row)
        form.addRow("YUV Note", self.yuv_desc)
        form.addRow("Zoom", zoom_row)
        form.addRow(self.apply_btn)

        # ── Signals ──
        self.apply_btn.clicked.connect(self.applyClicked)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        self.raw_preview_combo.currentTextChanged.connect(self._on_raw_preview_changed)
        self.frame_spin.valueChanged.connect(self.frameChanged.emit)
        self.frame_prev_btn.clicked.connect(self._prev_frame)
        self.frame_next_btn.clicked.connect(self._next_frame)
        self.zoom_slider.valueChanged.connect(self._on_slider_zoom)
        self.one2one_btn.clicked.connect(lambda: self.zoomChanged.emit(100))

        self._on_type_changed(self.type_combo.currentText())

    # ── public helpers ───────────────────────────────────────────────

    def set_type(self, image_type: str) -> None:
        self.type_combo.setCurrentText(image_type)

    def set_format(self, format_name: str) -> None:
        idx = self.format_combo.findText(format_name)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)

    def get_values(self) -> dict:
        """Return current control values as a flat dict."""
        return {
            "image_type": self.type_combo.currentText(),
            "format_name": self.format_combo.currentText(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "alignment": self.align_combo.currentText(),
            "endianness": self.endian_combo.currentText(),
            "offset": self.offset_spin.value(),
            "preview_mode": self.raw_preview_combo.currentText(),
            "bayer_pattern": self.bayer_pattern_combo.currentText(),
            "frame_index": self.frame_spin.value(),
        }

    def set_values(self, **kwargs) -> None:
        """Restore control values from a dict (keys match ``get_values()``)."""
        if "image_type" in kwargs:
            self.type_combo.setCurrentText(kwargs["image_type"])
        if "format_name" in kwargs:
            self.set_format(kwargs["format_name"])
        if "width" in kwargs:
            self.width_spin.setValue(kwargs["width"])
        if "height" in kwargs:
            self.height_spin.setValue(kwargs["height"])
        if "alignment" in kwargs:
            self.align_combo.setCurrentText(kwargs["alignment"])
        if "endianness" in kwargs:
            self.endian_combo.setCurrentText(kwargs["endianness"])
        if "offset" in kwargs:
            self.offset_spin.setValue(kwargs["offset"])
        if "preview_mode" in kwargs:
            self.raw_preview_combo.setCurrentText(kwargs["preview_mode"])
        if "bayer_pattern" in kwargs:
            self.bayer_pattern_combo.setCurrentText(kwargs["bayer_pattern"])
        if "frame_index" in kwargs:
            self.frame_spin.setValue(kwargs["frame_index"])

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all controls in the panel."""
        for widget in [
            self.type_combo,
            self.format_combo,
            self.align_combo,
            self.endian_combo,
            self.raw_preview_combo,
            self.bayer_pattern_combo,
            self.width_spin,
            self.height_spin,
            self.offset_spin,
            self.frame_spin,
            self.apply_btn,
        ]:
            widget.setEnabled(enabled)

    def set_frame_info(self, current: int, total: int) -> None:
        """Update frame display and enable/disable nav buttons."""
        self.frame_spin.setRange(0, max(0, total - 1))
        self.frame_spin.setValue(current)
        self.frame_total_label.setText(f"/ {total}")
        has_multiple = total > 1
        self.frame_spin.setEnabled(has_multiple)
        self.frame_prev_btn.setEnabled(has_multiple and current > 0)
        self.frame_next_btn.setEnabled(has_multiple and current < total - 1)

    def set_zoom_percent(self, percent: int) -> None:
        """Update zoom slider and label without emitting zoomChanged."""
        percent = max(10, min(1000, percent))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(percent)
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{percent}%")

    # ── internal slots ───────────────────────────────────────────────

    def _prev_frame(self) -> None:
        if self.frame_spin.value() > 0:
            self.frame_spin.setValue(self.frame_spin.value() - 1)

    def _next_frame(self) -> None:
        max_val = self.frame_spin.maximum()
        if self.frame_spin.value() < max_val:
            self.frame_spin.setValue(self.frame_spin.value() + 1)

    def _on_slider_zoom(self, value: int) -> None:
        self.zoom_label.setText(f"{value}%")
        self.zoomChanged.emit(value)

    def _on_type_changed(self, image_type: str) -> None:
        self.format_combo.clear()
        if image_type == "RAW":
            self.format_combo.addItems(self.RAW_FORMATS)
            self.align_combo.setEnabled(True)
            self.endian_combo.setEnabled(True)
            self.raw_preview_combo.setEnabled(True)
            self.bayer_pattern_combo.setEnabled(
                self.raw_preview_combo.currentText().startswith("Bayer")
            )
            self.yuv_desc.setVisible(False)
        elif image_type == "YUV":
            self.format_combo.addItems(self.YUV_FORMATS)
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
            self.yuv_desc.setVisible(True)
        else:
            self.format_combo.addItems(["N/A"])
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
            self.yuv_desc.setVisible(False)
        self.typeChanged.emit(image_type)

    def _on_raw_preview_changed(self, value: str) -> None:
        is_raw = self.type_combo.currentText() == "RAW"
        self.bayer_pattern_combo.setEnabled(is_raw and value.startswith("Bayer"))
        self.rawPreviewChanged.emit(value)
