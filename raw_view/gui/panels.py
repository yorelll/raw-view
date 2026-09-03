"""Left-side control panel for decode parameters."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
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
    QVBoxLayout,
    QWidget,
)

from raw_view.formats import (
    FormatError,
    MAX_DECODE_BYTES,
    expected_frame_size_raw,
    expected_frame_size_yuv,
)
from raw_view.models import ACTION_ICON_COLOR, BAYER_PATTERNS


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a compact human-readable string (UI-9)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


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
    # YOnly 是一个独立格式（YUV 4:0:0 全分辨率灰度）；位深由 Bit depth 下拉
    # 单独配置（8/10/12/14/16），内部映射为 YOnly8/10/12/14/16。普通 YUV 无需位深。
    YUV_FORMATS = [
        "YOnly",
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
    # YOnly 可选位深（内部格式名 YOnly<bit>）：8 = 1 字节/像素；10/12/14/16 =
    # 16-bit 存储（2 字节/像素），由 Alignment(lsb/msb) + Endianness 决定有效位。
    YONLY_BIT_DEPTHS = ["8", "10", "12", "14", "16"]
    # YOnly 多 bit（16-bit 存储）需要启用 Alignment/Endianness 控制。
    _YONLY_16BIT = {"YOnly10", "YOnly12", "YOnly14", "YOnly16"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(340)
        self.setObjectName("controlPanel")
        # 面板整体启用态（供帧大小门禁判断是否干预 Apply）；初始启用。
        self._panel_enabled = True

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
        self.preset_save_btn.setAccessibleName("Save sensor preset")
        self.preset_save_btn.setAccessibleDescription(
            "Save the current panel values as a named sensor preset."
        )
        self.preset_save_btn.setIcon(_qta_icon("fa5s.save"))
        self.preset_save_btn.setToolTip(
            "Save the current panel values as a named sensor preset for reuse."
        )
        self.preset_manage_btn = QPushButton()
        self.preset_manage_btn.setObjectName("iconButton")
        self.preset_manage_btn.setAccessibleName("Manage sensor presets")
        self.preset_manage_btn.setAccessibleDescription(
            "Open sensor preset management to rename, delete, import, or export presets."
        )
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

        # YOnly 位深（仅 Format=YOnly 时显示）。内部映射为 YOnly<bit> 有效格式名。
        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.addItems(self.YONLY_BIT_DEPTHS)
        self.bit_depth_combo.setCurrentText("12")

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
        self.zoom_slider.setAccessibleName("Zoom level")
        self.zoom_slider.setAccessibleDescription(
            "Adjust the image zoom from 10% to 1000%."
        )
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
        # 供条件显隐（_set_row_visibility）隐藏控件时同步隐藏其行标签——
        # 主 QFormLayout 直接行：只隐藏 field 时 QFormLayout 的行 label 仍会
        # 留在表单里占一行（实测验证），必须 field+label 一起隐藏行才真正收起。
        self._main_form = form
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

        # ── 条件显隐区：RAW 高级参数 + YOnly 位深（主 QFormLayout 直接行）──
        # 不做折叠组；这些行是主表单的直接行，每个 label+field 一行。行本身
        # 不放进容器，显隐由「状态槽同步主表单行可见性」控制：
        #   - Type=RAW            → 显示 Alignment/Endianness/RAW preview/Bayer
        #   - Type=YUV+Format=YOnly → 显示 Bit depth/Alignment/Endianness
        #   - 其它（YUV 非 YOnly / Standard Image）→ 全部隐藏
        form.addRow("Bit depth", self.bit_depth_combo)
        form.addRow("Alignment", self.align_combo)
        form.addRow("Endianness", self.endian_combo)
        form.addRow("RAW preview", self.raw_preview_combo)
        form.addRow("Bayer pattern", self.bayer_pattern_combo)

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
            self.bit_depth_combo,
        ):
            combo.currentTextChanged.connect(lambda _t: self.valuesChanged.emit())
        # 切换格式时联动条件显隐（YOnly → 位深+对齐+端序；RAW → 高级参数）
        self.format_combo.currentTextChanged.connect(lambda _f: self._sync_type_enabled())
        for spin in (self.width_spin, self.height_spin, self.offset_spin):
            spin.valueChanged.connect(lambda _v: self.valuesChanged.emit())
        # UI-9：宽高/格式/位深/对齐变化时刷新 Apply 门禁（Estimated frame 提示
        # 已按需求 3 移除，门禁逻辑保留）
        self.width_spin.valueChanged.connect(lambda _v: self._refresh_frame_size_hint())
        self.height_spin.valueChanged.connect(lambda _v: self._refresh_frame_size_hint())
        self.format_combo.currentTextChanged.connect(lambda _f: self._refresh_frame_size_hint())
        self.bit_depth_combo.currentTextChanged.connect(lambda _b: self._refresh_frame_size_hint())
        self.align_combo.currentTextChanged.connect(lambda _a: self._refresh_frame_size_hint())
        self._refresh_frame_size_hint()

        self._on_type_changed(self.type_combo.currentText())

    # ── public helpers ───────────────────────────────────────────────

    def set_type(self, image_type: str) -> None:
        self.type_combo.setCurrentText(image_type)

    def set_format(self, format_name: str) -> None:
        idx = self.format_combo.findText(format_name)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)

    @staticmethod
    def _yonly_internal_name(display_name: str, bit: str) -> str:
        """把 UI 的 YOnly（+位深）映射为内部有效格式名 YOnly<bit>。

        底层 decode/encode（worker/converter/formats）按 YOnly8/10/12/14/16 工作；
        UI 层只暴露一个 "YOnly" 条目 + Bit depth 下拉，这里做归一。
        """
        if display_name == "YOnly":
            return f"YOnly{bit}"
        return display_name

    def get_values(self) -> dict:
        """Return current control values as a flat dict.

        ``format_name`` 为**有效内部格式名**（YOnly + bit → ``YOnly12`` 等），
        以便 DecodeOptions / worker / converter 直接按原语义工作。
        """
        fmt = self.format_combo.currentText()
        return {
            "image_type": self.type_combo.currentText(),
            "format_name": self._yonly_internal_name(
                fmt, self.bit_depth_combo.currentText()
            ),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "alignment": self.align_combo.currentText(),
            "endianness": self.endian_combo.currentText(),
            "offset": self.offset_spin.value(),
            "preview_mode": self.raw_preview_combo.currentText(),
            "bayer_pattern": self.bayer_pattern_combo.currentText(),
            "bit_depth": self.bit_depth_combo.currentText(),
        }

    def set_values(self, **kwargs) -> None:
        """Restore control values from a dict (keys match ``get_values()``).

        兼容：传入的 ``format_name`` 可能是内部有效名（``YOnly12``）——若以
        ``YOnly`` 开头且 / 或带 ``bit_depth``，则拆成 UI 的 ``YOnly`` + Bit depth。
        """
        if "image_type" in kwargs:
            self.type_combo.setCurrentText(kwargs["image_type"])
        if "format_name" in kwargs:
            fname = kwargs["format_name"]
            if fname.startswith("YOnly"):
                bit = kwargs.get("bit_depth") or fname[len("YOnly"):] or "8"
                if bit not in self.YONLY_BIT_DEPTHS:
                    bit = "12"
                self.bit_depth_combo.setCurrentText(bit)
                self.set_format("YOnly")
            else:
                self.set_format(fname)
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
        if "bit_depth" in kwargs:
            bit = str(kwargs["bit_depth"])
            if bit in self.YONLY_BIT_DEPTHS:
                self.bit_depth_combo.setCurrentText(bit)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all controls in the panel."""
        # 记录面板整体启用态，供 _refresh_frame_size_hint 的门禁判断：
        # 面板禁用（无 item）时不改动 Apply，面板启用时门禁才能干预 Apply。
        self._panel_enabled = bool(enabled)
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
            self.bit_depth_combo,
            self.width_spin,
            self.height_spin,
            self.offset_spin,
            self.apply_btn,
        ]:
            widget.setEnabled(enabled)

    def _refresh_frame_size_hint(self) -> None:
        """按当前宽高/格式估算单帧字节数并刷新 Apply 门禁（UI-9 内部逻辑）。

        Estimated frame 提示标签已按需求 3 从 UI 移除（状态栏已有
        ``Image: WxH (frame_size)``），这里只保留**必要**的 512MB 门禁：
        - 计算失败（如 YUV 偶数宽高校验、非法参数）→ Apply 恢复可用；
        - 超过 MAX_DECODE_BYTES（512MB）→ 禁用 Apply，阻止大帧 OOM
          （与 decode_current / CLI 的 require_decode_size 保护语义一致，只是提前
          到参数编辑阶段）；
        - 其它 → 只更新门禁，不干扰 Apply（对齐/位深变化不触发禁用）。
        """
        fmt = self.format_combo.currentText()
        image_type = self.type_combo.currentText()
        width = self.width_spin.value()
        height = self.height_spin.value()
        # 面板整体被禁用（无 item）时，不干预 Apply 的状态（保持灰）。
        # 注意不能拿 apply_btn.isEnabled() 当“面板启用态”：一旦门禁因超大帧
        # 临时禁用过 Apply，参数改回合法时也要能重新点亮——所以这里用一个
        # 由 set_enabled() 维护的独立面板启用标志。
        panel_enabled = getattr(self, "_panel_enabled", True)

        def _set_apply(allowed: bool) -> None:
            # 只在面板启用态下调整 Apply 门禁；面板被禁用时维持原状
            # （否则 _refresh_frame_size_hint 会在无 item 时把 Apply 点亮）。
            if panel_enabled:
                self.apply_btn.setEnabled(allowed)

        dynamic = image_type != "Standard Image"
        try:
            if image_type == "YUV":
                if fmt == "YOnly":
                    fmt = ControlPanel._yonly_internal_name(
                        "YOnly", self.bit_depth_combo.currentText()
                    )
                frame_size = expected_frame_size_yuv(
                    fmt, width, height,
                    alignment=self.align_combo.currentText(),
                    endianness=self.endian_combo.currentText(),
                )
            else:  # RAW
                frame_size = expected_frame_size_raw(fmt, width, height)
        except (FormatError, ValueError):
            # 非法参数（偶数宽高要求不满足等）不可能解码成功，这里不做门禁——
            # 解码时会由 worker 报错。仅恢复 Apply。
            _set_apply(True)
            return
        if not dynamic:
            _set_apply(True)
            return
        if frame_size > MAX_DECODE_BYTES:
            _set_apply(False)
        else:
            _set_apply(True)

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
        """Re-apply type/format-appropriate visibility + enabled states.

        Called after set_enabled(True) / type change / format change / tab switch.
        规则（用户需求 1/2）：
        - Type=RAW：显示 RAW 高级参数（Alignment/Endianness/RAW preview/Bayer）；
          Bit depth 隐藏。
        - Type=YUV 且 Format=YOnly：显示 Bit depth + Alignment + Endianness
          （YOnly 多 bit 的 16-bit 存储需要）；隐藏 RAW preview/Bayer。
        - 其它（YUV 非 YOnly / Standard Image）：全部隐藏。
        """
        fmt = self.format_combo.currentText()
        image_type = self.type_combo.currentText()
        if image_type == "RAW":
            self._set_advanced_visible(
                bit=False, align=True, endian=True, preview=True, bayer=(
                    self.raw_preview_combo.currentText().startswith("Bayer")
                ),
            )
        elif image_type == "YUV" and fmt == "YOnly":
            self._set_advanced_visible(
                bit=True, align=True, endian=True, preview=False, bayer=False,
            )
        else:
            self._set_advanced_visible(False, False, False, False, False)

    def _set_advanced_visible(self, bit, align, endian, preview, bayer) -> None:
        """统一控制条件显隐区（主 QFormLayout 直接行）各控件的显隐/可用性。

        隐藏的控件同时禁用，避免 tab 焦点/键盘可达。因这些行是主 QFormLayout
        的直接行，隐藏 field 必须**连带隐藏其行 label** 行才会真正从表单中收起
        （实测：只隐藏 field，QFormLayout 仍会把 label 留在原位占一行）。
        """
        self._set_row_visibility((self.bit_depth_combo, bit))
        self._set_row_visibility((self.align_combo, align))
        self._set_row_visibility((self.endian_combo, endian))
        self._set_row_visibility((self.raw_preview_combo, preview))
        self._set_row_visibility((self.bayer_pattern_combo, bayer))

    def _set_row_visibility(self, item: tuple) -> None:
        """``(field, visible)`` 的行的 field 与 label 同步显隐/启用。

        不依赖 Qt 自动同步（实测只藏 field 不会收起 label 行），手动把主表单里
        该 field 的 label 一起 setVisible / setEnabled，保证行整体收起、不残留
        孤零零的 label 占位。
        """
        field, visible = item
        visible = bool(visible)
        field.setVisible(visible)
        field.setEnabled(visible)
        label = self._main_form.labelForField(field) if self._main_form else None
        if label is not None:
            label.setVisible(visible)
            label.setEnabled(visible)

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
        else:
            self.format_combo.addItems(["N/A"])
        # 类型/格式联动条件显隐（RAW 显示高级参数 / YOnly 显示位深+对齐+端序）
        self._sync_type_enabled()
        self._refresh_frame_size_hint()
        self.typeChanged.emit(image_type)

    def _on_raw_preview_changed(self, value: str) -> None:
        # 只在 RAW 且 Bayer 时启用/显示 Bayer 控件（连同行 label 一起显隐，
        # 否则隐藏 field 后 label 仍占一行，见 _set_row_visibility 注释）
        is_bayer = self.type_combo.currentText() == "RAW" and value.startswith("Bayer")
        self._set_row_visibility((self.bayer_pattern_combo, is_bayer))
        self.rawPreviewChanged.emit(value)
