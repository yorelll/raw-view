"""Left-side control panel for decode parameters."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
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
    zoomChanged(int)
        Emitted when the zoom slider is moved.
    """

    applyClicked = pyqtSignal()
    typeChanged = pyqtSignal(str)
    rawPreviewChanged = pyqtSignal(str)
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

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(0)  # no border
        content = QWidget()
        content.setObjectName("controlPanelContent")

        # ── Format parameters ──
        self.type_combo = QComboBox()
        self.type_combo.addItems(["RAW", "YUV", "Standard Image"])

        self.format_combo = QComboBox()
        self.format_combo.addItems(self.RAW_FORMATS)
        self.format_combo.setCurrentText("RAW12")

        self.align_combo = QComboBox()
        self.align_combo.addItems(["lsb", "msb"])
        self.align_combo.setCurrentText("msb")

        self.endian_combo = QComboBox()
        self.endian_combo.addItems(["little", "big"])

        self.raw_preview_combo = QComboBox()
        self.raw_preview_combo.addItems(["Bayer Color", "Grayscale"])

        self.bayer_pattern_combo = QComboBox()
        self.bayer_pattern_combo.addItems(BAYER_PATTERNS)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 65535)
        self.width_spin.setValue(2560)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 65535)
        self.height_spin.setValue(1440)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(0, 1_000_000_000)

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
        zoom_layout.addWidget(self.zoom_slider, 1)
        zoom_layout.addWidget(self.zoom_label)

        # ── Apply button ──
        self.apply_btn = QPushButton("Apply")

        # ── Layout ──
        form = QFormLayout(content)
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
        form.addRow("Zoom", zoom_row)
        form.addRow(self.apply_btn)

        scroll.setWidget(content)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(scroll)

        # ── Signals ──
        self.apply_btn.clicked.connect(self.applyClicked)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        self.raw_preview_combo.currentTextChanged.connect(self._on_raw_preview_changed)
        self.zoom_slider.valueChanged.connect(self._on_slider_zoom)

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
            self.apply_btn,
        ]:
            widget.setEnabled(enabled)

    def set_zoom_percent(self, percent: int) -> None:
        """Update zoom slider and label without emitting zoomChanged."""
        percent = max(10, min(1000, percent))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(percent)
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{percent}%")

    def _sync_type_enabled(self) -> None:
        """Re-apply type-specific enabled states without changing formats.

        Called after set_enabled(True) to restore type-appropriate controls.
        """
        image_type = self.type_combo.currentText()
        if image_type == "RAW":
            self.align_combo.setEnabled(True)
            self.endian_combo.setEnabled(True)
            self.raw_preview_combo.setEnabled(True)
            self.bayer_pattern_combo.setEnabled(
                self.raw_preview_combo.currentText().startswith("Bayer")
            )
        elif image_type == "YUV":
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
        else:
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)

    # ── internal slots ───────────────────────────────────────────────

    def _on_slider_zoom(self, value: int) -> None:
        self.zoom_label.setText(f"{value}%")
        self.zoomChanged.emit(value)

    def _on_type_changed(self, image_type: str) -> None:
        self.format_combo.clear()
        if image_type == "RAW":
            self.format_combo.addItems(self.RAW_FORMATS)
            self.format_combo.setCurrentText("RAW12")
            self.align_combo.setEnabled(True)
            self.endian_combo.setEnabled(True)
            self.raw_preview_combo.setEnabled(True)
            self.bayer_pattern_combo.setEnabled(
                self.raw_preview_combo.currentText().startswith("Bayer")
            )
        elif image_type == "YUV":
            self.format_combo.addItems(self.YUV_FORMATS)
            self.format_combo.setCurrentText("YUYV")
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
        else:
            self.format_combo.addItems(["N/A"])
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
        self.typeChanged.emit(image_type)

    def _on_raw_preview_changed(self, value: str) -> None:
        is_raw = self.type_combo.currentText() == "RAW"
        self.bayer_pattern_combo.setEnabled(is_raw and value.startswith("Bayer"))
        self.rawPreviewChanged.emit(value)
