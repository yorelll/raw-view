"""Left-side control panel for decode parameters."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from raw_view.models import ACTION_ICON_COLOR, BAYER_PATTERNS


def _qta_icon(name: str):
    """Build a qtawesome icon, tolerant of failures/headless envs."""
    try:
        import qtawesome as qta

        return qta.icon(name, color=ACTION_ICON_COLOR)
    except Exception:
        from PyQt5.QtGui import QIcon

        return QIcon()


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
    presetSelected = pyqtSignal(str)        # emitted when user picks a saved preset
    savePresetRequested = pyqtSignal()      # user clicked the "Save as..." button
    managePresetsRequested = pyqtSignal()   # user clicked the "Manage..." button
    valuesChanged = pyqtSignal()            # a decode parameter was edited

    # Sentinel item shown at index 0 of the preset combo.
    _PRESET_PLACEHOLDER = "(Select a preset)"

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
    # YOnly 系列：4:0:0 全分辨率灰度。YOnly8 为 1 字节/像素（"YOnly" 是其别名）；
    # YOnly10/12/14/16 为 16-bit 存储（2 字节/像素），由 Alignment(lsb/msb) +
    # Endianness 决定有效位位置与大小端，语义与 RAW10/12/16 一致。
    YUV_FORMATS = [
        "YOnly",
        "YOnly8",
        "YOnly10",
        "YOnly12",
        "YOnly14",
        "YOnly16",
        "I420",
        "YV12",
        "NV12",
        "NV21",
        "YUYV",
        "UYVY",
        "YVYU",
        "VYUY",
        "NV16",
        "NV61",
    ]
    # YOnly 多 bit（16-bit 存储）需要启用 Alignment/Endianness 控制。
    _YONLY_16BIT = {"YOnly10", "YOnly12", "YOnly14", "YOnly16"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(340)
        self.setObjectName("controlPanel")

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(0)  # no border
        content = QWidget()
        content.setObjectName("controlPanelContent")

        # ── Sensor preset row (shown at the very top) ──
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(self._PRESET_PLACEHOLDER)
        self.preset_combo.setToolTip(
            "Saved sensor presets. Selecting one fills the form below; "
            "press Apply to render."
        )
        # Let the combo shrink so the Save/Manage buttons are never pushed
        # off the edge of the narrow panel; the tooltip shows the full text.
        # Keep the box compact so both icon buttons always fit on the narrow
        # panel; the dropdown popup still expands to show full preset names.
        self.preset_combo.setMinimumWidth(96)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)

        # Compact icon buttons keep the preset row readable on the narrow
        # panel (text buttons got truncated to "MANAC" etc.).
        self.preset_save_btn = QPushButton()
        self.preset_save_btn.setObjectName("iconButton")
        self.preset_save_btn.setIcon(_qta_icon("fa5s.save"))
        self.preset_save_btn.setToolTip(
            "Save the current panel values as a named sensor preset for reuse."
        )
        self.preset_manage_btn = QPushButton()
        self.preset_manage_btn.setObjectName("iconButton")
        self.preset_manage_btn.setIcon(_qta_icon("fa5s.cog"))
        self.preset_manage_btn.setToolTip("Manage presets: rename, delete, import/export.")
        for btn in (self.preset_save_btn, self.preset_manage_btn):
            btn.setFixedSize(32, 30)

        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(4)
        # The combo gets the stretch; the buttons keep their natural width.
        preset_layout.addWidget(self.preset_combo, 1)
        preset_layout.addWidget(self.preset_save_btn, 0)
        preset_layout.addWidget(self.preset_manage_btn, 0)

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
        self.zoom_slider.setTickPosition(QSlider.NoTicks)
        # Editable spin box: slide for coarse, type for precise.
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(10, 1000)
        self.zoom_spin.setValue(100)
        self.zoom_spin.setSuffix("%")
        self.zoom_spin.setFixedWidth(96)
        self.zoom_spin.setToolTip("Zoom level (10%–1000%) — drag the slider or type a value")
        zoom_layout.addWidget(self.zoom_slider, 1)
        zoom_layout.addWidget(self.zoom_spin)

        # ── Apply button ──
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("accentButton")

        # ── Layout ──
        form = QFormLayout(content)
        form.setVerticalSpacing(10)
        form.addRow("Preset", preset_row)

        # Separator: the Preset row is "preset management"; everything below
        # is "parameter configuration" — divide them so the two functional
        # areas read as distinct groups.
        preset_divider = QFrame()
        preset_divider.setFrameShape(QFrame.HLine)
        preset_divider.setObjectName("groupDivider")
        preset_divider.setFixedHeight(1)
        form.addRow(preset_divider)

        form.addRow("Type", self.type_combo)
        form.addRow("Format", self.format_combo)

        # ── 折叠组：RAW 高级参数（位对齐/大小端/预览/Bayer）────────────
        # UI-2：高频用的是 Type/Format/Width/Height/Offset/Zoom；位对齐等仅在
        # RAW（及 YOnly 多 bit）时有意义，收敛进一个可折叠组，减少面板占用。
        # 用 QToolButton + 箭头指示展开/收起。
        self.adv_btn = QToolButton()
        self.adv_btn.setObjectName("advToggle")
        self.adv_btn.setCheckable(True)
        self.adv_btn.setChecked(True)
        self.adv_btn.setArrowType(Qt.DownArrow)
        self.adv_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_btn.setText("RAW 高级参数")
        self.adv_btn.setStyleSheet(
            "QToolButton { border: none; text-align: left; font-weight: 600; padding: 2px; }"
            "QToolButton:hover { color: #4A90D9; }"
        )
        adv_form = QFormLayout()
        adv_form.setContentsMargins(0, 0, 0, 0)
        adv_form.setVerticalSpacing(10)
        adv_form.addRow("Alignment", self.align_combo)
        adv_form.addRow("Endianness", self.endian_combo)
        adv_form.addRow("RAW preview", self.raw_preview_combo)
        adv_form.addRow("Bayer pattern", self.bayer_pattern_combo)
        self.adv_container = QWidget()
        self.adv_container.setLayout(adv_form)
        form.addRow(self.adv_btn)
        form.addRow(self.adv_container)
        self.adv_btn.toggled.connect(self._on_adv_toggled)

        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Offset", self.offset_spin)
        form.addRow("Zoom", zoom_row)

        # Make Apply visually prominent and tall enough that it never gets
        # half-clipped on small/maximized window heights.
        self.apply_btn.setMinimumHeight(36)
        self.apply_btn.setSizePolicy(
            self.apply_btn.sizePolicy().horizontalPolicy(),
            self.apply_btn.sizePolicy().Fixed,
        )

        scroll.setWidget(content)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 8)
        root_layout.setSpacing(6)
        root_layout.addWidget(scroll, 1)
        # Apply button lives outside the scroll area so it is always fully
        # visible regardless of the scroll-area's content height.
        root_layout.addWidget(self.apply_btn, 0)

        # ── Signals ──
        self.apply_btn.clicked.connect(self.applyClicked)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        self.raw_preview_combo.currentTextChanged.connect(self._on_raw_preview_changed)
        self.zoom_slider.valueChanged.connect(self._on_slider_zoom)
        self.zoom_spin.valueChanged.connect(self._on_spin_zoom)
        self.preset_combo.activated.connect(self._on_preset_activated)
        self.preset_save_btn.clicked.connect(self.savePresetRequested)
        self.preset_manage_btn.clicked.connect(self.managePresetsRequested)

        # Emit valuesChanged whenever a decode parameter is edited, so the
        # window can flag "unapplied changes" until Apply is pressed. Zoom is
        # a view-only control and is deliberately excluded.
        for combo in (
            self.type_combo, self.format_combo, self.align_combo,
            self.endian_combo, self.raw_preview_combo, self.bayer_pattern_combo,
        ):
            combo.currentTextChanged.connect(lambda _t: self.valuesChanged.emit())
        # 切换 YUV 格式时，若为 YOnly 多 bit（16-bit 存储）则启用 Alignment/Endianness
        self.format_combo.currentTextChanged.connect(lambda _f: self._sync_type_enabled())
        for spin in (self.width_spin, self.height_spin, self.offset_spin):
            spin.valueChanged.connect(lambda _v: self.valuesChanged.emit())

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
            self.preset_combo,
            self.preset_save_btn,
            self.preset_manage_btn,
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

    def set_preset_names(self, names: list[str], current: str | None = None) -> None:
        """Repopulate the preset combo. Pass current=None to leave selection unchanged.

        The first item is always the placeholder; selecting it has no effect.
        """
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem(self._PRESET_PLACEHOLDER)
        for name in names:
            if name:
                self.preset_combo.addItem(name)
        if current:
            idx = self.preset_combo.findText(current)
            if idx > 0:
                self.preset_combo.setCurrentIndex(idx)
            else:
                self.preset_combo.setCurrentIndex(0)
        else:
            self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    def reset_preset_selection(self) -> None:
        """Move the preset combo back to the placeholder without emitting signals."""
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    def set_zoom_percent(self, percent: int) -> None:
        """Update zoom slider and spin box without emitting zoomChanged."""
        percent = max(10, min(1000, percent))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(percent)
        self.zoom_slider.blockSignals(False)
        self.zoom_spin.blockSignals(True)
        self.zoom_spin.setValue(percent)
        self.zoom_spin.blockSignals(False)

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
            if hasattr(self, "adv_btn"):
                self.adv_btn.setChecked(True)
        elif image_type == "YUV":
            # YUV 默认禁用位对齐/大小端；YOnly 多 bit（16-bit 存储）除外——
            # 它们的有效位位置（lsb/msb）与大小端由这几个控件控制。
            is_yonly_16 = self.format_combo.currentText() in self._YONLY_16BIT
            self.align_combo.setEnabled(is_yonly_16)
            self.endian_combo.setEnabled(is_yonly_16)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
            if hasattr(self, "adv_btn"):
                # 仅当 YOnly 多 bit 需要位控时才展开，否则收起
                self.adv_btn.setChecked(is_yonly_16)
        else:
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
            if hasattr(self, "adv_btn"):
                self.adv_btn.setChecked(False)

    # ── internal slots ───────────────────────────────────────────────

    def _on_preset_activated(self, index: int) -> None:
        """Forward a preset selection. Index 0 is the placeholder — ignore it."""
        if index <= 0:
            return
        name = self.preset_combo.itemText(index)
        if name:
            self.presetSelected.emit(name)

    def _on_slider_zoom(self, value: int) -> None:
        self.zoom_spin.blockSignals(True)
        self.zoom_spin.setValue(value)
        self.zoom_spin.blockSignals(False)
        self.zoomChanged.emit(value)

    def _on_spin_zoom(self, value: int) -> None:
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(value)
        self.zoom_slider.blockSignals(False)
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
        # 类型联动折叠组：RAW 时自动展开（位对齐等有意义）；YUV/Standard 时收起
        if hasattr(self, "adv_btn"):
            self.adv_btn.setChecked(image_type == "RAW")
        self.typeChanged.emit(image_type)

    def _on_raw_preview_changed(self, value: str) -> None:
        is_raw = self.type_combo.currentText() == "RAW"
        self.bayer_pattern_combo.setEnabled(is_raw and value.startswith("Bayer"))
        self.rawPreviewChanged.emit(value)

    def _on_adv_toggled(self, checked: bool) -> None:
        """展开/收起"RAW 高级参数"折叠组（箭头方向随状态变化）。"""
        self.adv_container.setVisible(checked)
        self.adv_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
