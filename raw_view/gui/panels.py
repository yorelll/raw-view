"""Left-side control panel for decode parameters."""

from __future__ import annotations

from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from raw_view.gui.image_utils import _format_size
from raw_view.models import (
    ACTION_ICON_COLOR,
    BAYER_PATTERNS,
    PRESET_CATEGORY_ALL,
    PRESET_CATEGORY_CHOICES,
    SensorPreset,
    filter_sensor_presets_by_category,
    is_supported_preset_format_for_type,
    normalize_preset_format_name,
    normalize_preset_image_type,
    search_sensor_presets,
)


def _qta_icon(name: str):
    """Build a qtawesome icon, tolerant of failures/headless envs."""
    try:
        import qtawesome as qta

        return qta.icon(name, color=ACTION_ICON_COLOR)
    except Exception:
        from PyQt5.QtGui import QIcon

        return QIcon()


class PresetComboBox(QComboBox):
    """Editable preset selector that announces native-arrow popup requests."""

    popupRequested = pyqtSignal()

    def showPopup(self) -> None:  # noqa: N802 — Qt virtual override
        self.popupRequested.emit()
        super().showPopup()


class _PresetQueryLineEdit(QLineEdit):
    """Line edit that asks the panel to detach a settled preset before editing."""

    editingStarted = pyqtSignal()

    @staticmethod
    def _is_editing_key(event) -> bool:
        key = event.key()
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            return True
        if event.matches(QKeySequence.Paste) or event.matches(QKeySequence.Cut):
            return True
        # Keep Ctrl+A as native Select All (not an edit transition); Ctrl+V is
        # handled above.  AltGr printable input still carries text and is valid.
        return bool(event.text()) and not (event.modifiers() & Qt.ControlModifier)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt virtual override
        if self._is_editing_key(event):
            self.editingStarted.emit()
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 — Qt virtual override
        self.editingStarted.emit()
        super().insertFromMimeData(source)


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

        # ── Sensor preset controls (shown at the very top) ──────────────
        # One editable combo unifies selection and name search.  Typed text is
        # strictly query state: only an item carrying a preset-name userData can
        # emit presetSelected, so arbitrary text can never be loaded/saved as a
        # preset.  Clicking the native arrow intentionally clears a query and
        # switches to category-filtered dropdown mode.
        self._preset_records: list[SensorPreset] = []
        self._preset_names_only = False  # compatibility path for set_preset_names()
        # Identity, query, and what the line edit happens to display are separate
        # states.  In particular, Qt must not turn a selected item's display text
        # into the next typed search query.
        self._preset_selected_name: str | None = None
        self._preset_query = ""
        self._preset_search_mode = False
        self._preset_edit_transitioning = False
        self.preset_combo = PresetComboBox()
        self.preset_combo.setEditable(True)
        self.preset_combo.setInsertPolicy(QComboBox.NoInsert)
        self.preset_combo.setPlaceholderText(self._PRESET_PLACEHOLDER)
        self.preset_combo.setToolTip(
            "Type to search all saved presets, or use the arrow to browse the selected preset type. "
            "Structured search: <type:raw + format:raw10 packed>name."
        )
        self.preset_combo.setAccessibleName("Search or select sensor preset")
        self.preset_combo.setAccessibleDescription(
            "Type a preset name or official <type:... + format:...> search across all saved presets; "
            "use the arrow to browse by preset type."
        )
        self.preset_combo.setMinimumWidth(96)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._preset_line_edit = _PresetQueryLineEdit(self.preset_combo)
        self.preset_combo.setLineEdit(self._preset_line_edit)
        # QComboBox otherwise installs an inline QCompleter that appends a
        # matching item suffix to the visible editor.  Candidates are already
        # provided by our filtered popup; autocomplete must stay off so visible
        # editor text is always the exact user query.
        self.preset_combo.setCompleter(None)
        self._preset_line_edit.setCompleter(None)
        self._preset_line_edit.setPlaceholderText("Search or select preset…")
        self._preset_line_edit.setClearButtonEnabled(True)

        self.preset_category_filter_combo = QComboBox()
        self.preset_category_filter_combo.setObjectName("presetCategoryFilter")
        self.preset_category_filter_combo.addItems(PRESET_CATEGORY_CHOICES)
        self.preset_category_filter_combo.setToolTip(
            "Browse All, RAW (including Packed), YUV (including YOnly), or Standard images. "
            "Typed search always covers every type."
        )
        self.preset_category_filter_combo.setAccessibleName("Preset type")
        self.preset_category_filter_combo.setAccessibleDescription(
            "Choose All, RAW, YUV, or Standard images for dropdown browsing."
        )

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

        # Preset type and Preset are individual main-form rows so their labels
        # and fields use precisely the same alignment and sizing rules as Type,
        # Format, and Bit depth.  The action buttons remain attached only to the
        # Preset field.
        preset_select_row = QWidget()
        preset_select_layout = QHBoxLayout(preset_select_row)
        preset_select_layout.setContentsMargins(0, 0, 0, 0)
        preset_select_layout.setSpacing(4)
        preset_select_layout.addWidget(self.preset_combo, 1)
        preset_select_layout.addWidget(self.preset_save_btn, 0)
        preset_select_layout.addWidget(self.preset_manage_btn, 0)

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
        self.apply_btn.setToolTip("Apply current decode parameters (Ctrl+A)")

        # ── Layout ──
        form = QFormLayout(content)
        form.setVerticalSpacing(10)
        # 条件行显隐由 _apply_cond_rows 统一管理：用 takeRow/insertRow 把隐藏行
        # 彻底移出表单（仅 setVisible 时本机 PyQt5 的 QFormLayout 仍保留行几何，
        # 会在 Format 与 Width 之间留出大段空白，0.4.1 修复）。
        self._main_form = form
        form.addRow("Preset type", self.preset_category_filter_combo)
        form.addRow("Preset", preset_select_row)

        # Separator: the Preset rows are "preset management"; everything below
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

        # 条件行 label 缓存：_apply_cond_rows 用 takeRow/insertRow 搬移这些行时
        # 总是优先复用首次 addRow 创建的 QLabel（0.4.1-L-2），保证 label 的样式
        # /对齐/属性不被按文本重建丢失。必须在 addRow 之后采集（labelForField 只
        # 对标已在表单中的 field 生效）。
        self._cond_row_labels: dict[str, QLabel] = {
            name: form.labelForField(getattr(self, name))
            for name, _lbl in self._COND_ROW_SPECS
        }
        self._cond_row_labels = {
            k: v for k, v in self._cond_row_labels.items() if v is not None
        }

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
        self.preset_combo.popupRequested.connect(self._show_preset_popup)
        # The custom line edit clears settled display text before an edit is
        # applied.  textEdited then owns all query updates, including the native
        # clear button's empty string.
        self._preset_line_edit.editingStarted.connect(self._begin_preset_edit)
        self._preset_line_edit.textEdited.connect(self._on_preset_query_edited)
        self.preset_category_filter_combo.currentTextChanged.connect(self._on_preset_category_changed)
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
        # 切换格式时联动条件显隐（YOnly → 位深+对齐+端序；RAW →高级参数）
        self.format_combo.currentTextChanged.connect(lambda _f: self._sync_type_enabled())
        self.format_combo.currentTextChanged.connect(lambda _f: self._refresh_preset_combo())
        self.bit_depth_combo.currentTextChanged.connect(lambda _b: self._refresh_preset_combo())
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

    _DYNAMIC_FORMAT_ROLE = Qt.UserRole + 41
    _DYNAMIC_TYPE_ROLE = Qt.UserRole + 42

    def _current_dynamic_format(self) -> str:
        """Return the active temporary format, or empty when a registry item is selected."""
        index = self.format_combo.currentIndex()
        if index >= 0 and self.format_combo.itemData(index, self._DYNAMIC_FORMAT_ROLE):
            return self.format_combo.itemText(index)
        return ""

    def _remove_stale_dynamic_types(self, keep: str = "") -> None:
        """Drop unsupported temporary image types once the user selects another one."""
        for index in range(self.type_combo.count() - 1, -1, -1):
            if self.type_combo.itemData(index, self._DYNAMIC_TYPE_ROLE):
                if self.type_combo.itemText(index) != keep:
                    self.type_combo.removeItem(index)

    def set_type(self, image_type: str) -> None:
        """Atomically select a type while preserving a compatible stored format.

        Removing a selected dynamic type makes Qt choose a temporary neighbour
        (often Standard Image), whose signal handler can rebuild the format box
        to N/A before the requested RAW/YUV type is installed.  Capture the
        identifier first and block both combos for the whole cleanup/select/
        rebuild sequence; then invoke exactly one final type handler.
        """
        kind = normalize_preset_image_type(image_type)
        previous_format = self.format_combo.currentText()
        with QSignalBlocker(self.type_combo), QSignalBlocker(self.format_combo):
            index = self.type_combo.findText(kind)
            if index < 0:
                self._remove_stale_dynamic_types(keep=kind)
                self.type_combo.addItem(kind)
                index = self.type_combo.count() - 1
                self.type_combo.setItemData(index, True, self._DYNAMIC_TYPE_ROLE)
            else:
                keep = kind if self.type_combo.itemData(index, self._DYNAMIC_TYPE_ROLE) else ""
                self._remove_stale_dynamic_types(keep=keep)
                index = self.type_combo.findText(kind)
            if index >= 0:
                self.type_combo.setCurrentIndex(index)
            self._rebuild_format_for_type(kind, previous_format)
        # QSignalBlocker intentionally suppresses intermediate Type/Format
        # events.  Reapply dependent visibility/gating once after the atomic
        # transition, without emitting valuesChanged/dirty from false states.
        self._finish_type_change(kind)

    def _rebuild_format_for_type(self, image_type: str, previous_format: str) -> None:
        """Populate format options for *image_type*, retaining compatible prior value.

        Called with type/format combo signals blocked by :meth:`set_type`.
        """
        self.format_combo.clear()
        if image_type == "RAW":
            self.format_combo.addItems(self.RAW_FORMATS)
            target = previous_format if previous_format in self.RAW_FORMATS else "RAW12"
        elif image_type == "YUV":
            self.format_combo.addItems(self.YUV_FORMATS)
            target = previous_format if previous_format in self.YUV_FORMATS else "YUYV"
        elif image_type == "Standard Image":
            self.format_combo.addItems(["N/A"])
            target = "N/A"
        else:
            self.format_combo.addItems([previous_format] if previous_format else ["N/A"])
            target = previous_format or "N/A"
        self.format_combo.setCurrentText(target)

    def _finish_type_change(self, image_type: str) -> None:
        """Apply post-type-change UI state once, after atomic combo transition."""
        if image_type == "RAW":
            self.align_combo.setEnabled(True)
            self.endian_combo.setEnabled(True)
            self.raw_preview_combo.setEnabled(True)
            self.bayer_pattern_combo.setEnabled(
                self.raw_preview_combo.currentText().startswith("Bayer")
            )
        self._sync_type_enabled()
        self._refresh_frame_size_hint()
        if hasattr(self, "preset_category_filter_combo"):
            self._refresh_preset_combo()
        self.typeChanged.emit(image_type)

    def _remove_stale_dynamic_formats(
        self, keep: str = "", *, preserve_current: bool = True,
    ) -> None:
        """Remove stale temporary formats without silently replacing active legacy state.

        A current marked entry represents the actual options of the active item.
        Refreshing preset records after deletion must not remove it: QComboBox
        would auto-select a neighbouring registry item and later overwrite the
        item on Apply.  Explicit user selection passes ``preserve_current=False``
        and may remove the prior active temporary value.
        """
        current_dynamic = self._current_dynamic_format() if preserve_current else ""
        for index in range(self.format_combo.count() - 1, -1, -1):
            if self.format_combo.itemData(index, self._DYNAMIC_FORMAT_ROLE):
                text = self.format_combo.itemText(index)
                if text not in {keep, current_dynamic}:
                    self.format_combo.removeItem(index)

    def _add_dynamic_format(self, fmt: str) -> int:
        """Append one raw stored legacy/mismatched identifier and mark it temporary."""
        self.format_combo.addItem(fmt)
        index = self.format_combo.count() - 1
        self.format_combo.setItemData(index, True, self._DYNAMIC_FORMAT_ROLE)
        return index

    def set_format(self, format_name: str) -> None:
        """Select *format_name*, preserving unsupported legacy values verbatim.

        A saved unknown/mismatched format must never silently fall back to the
        current RAW12/YUV entry.  Temporary entries are cleaned when a later
        legitimate format is loaded, while the current legacy identifier stays
        visible long enough to round-trip safely.
        """
        fmt = normalize_preset_format_name(format_name)
        idx = self.format_combo.findText(fmt)
        if idx < 0 and fmt:
            # Caller explicitly chose/loaded a different format; old temporary
            # entries may now be removed.  (set_presets refresh uses the default
            # preservation path instead.)
            self._remove_stale_dynamic_formats(keep=fmt, preserve_current=False)
            idx = self._add_dynamic_format(fmt)
        elif idx >= 0:
            # A legal/current item supersedes old temporary entries.  If *fmt*
            # itself is a marked legacy entry, retain just that one.
            keep = fmt if self.format_combo.itemData(idx, self._DYNAMIC_FORMAT_ROLE) else ""
            self._remove_stale_dynamic_formats(keep=keep, preserve_current=False)
            idx = self.format_combo.findText(fmt)
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
            "image_type": normalize_preset_image_type(self.type_combo.currentText()),
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
            self.set_type(kwargs["image_type"])
        if "format_name" in kwargs:
            fname = normalize_preset_format_name(kwargs["format_name"])
            if fname in {"YOnly", "YOnly8", "YOnly10", "YOnly12", "YOnly14", "YOnly16"}:
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
            self.preset_category_filter_combo,
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
        # The list above deliberately treats every field uniformly, but the
        # conditional rows have a second invariant: hidden fields must stay
        # disabled and visible rows must follow the current type/format.  This
        # matters when set_enabled(True) is called after set_values() or when a
        # tab is opened (the caller may invoke set_enabled more than once).
        self._sync_type_enabled()

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
        effective_format = self._yonly_internal_name(
            fmt, self.bit_depth_combo.currentText(),
        )
        if not is_supported_preset_format_for_type(image_type, effective_format):
            # Legacy/imported values remain editable/selectable so they
            # round-trip intact, but unknown OR type-mismatched formats must
            # never decode through an accidental RAW/YUV fallback.
            _set_apply(False)
            self.apply_btn.setToolTip(
                f"Unsupported format '{effective_format}' for {image_type}. "
                "Select a supported format to Apply."
            )
            return
        self.apply_btn.setToolTip("Apply current decode parameters (Ctrl+A)")
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

    def _preset_candidates(self) -> list[SensorPreset]:
        """Return candidates for the separate query/selection state machine.

        Any nonempty query uses the shared official bracket-search parser and
        therefore ignores the browse category.  Empty query means browse mode.
        Unknown legacy records remain in All and explicit/name search results,
        but never create a misleading type category.
        """
        if self._preset_query:
            return search_sensor_presets(self._preset_records, self._preset_query)
        return filter_sensor_presets_by_category(
            self._preset_records,
            self.preset_category_filter_combo.currentText() or PRESET_CATEGORY_ALL,
        )

    def set_presets(
        self, presets: list[SensorPreset], current: str | None = None,
    ) -> None:
        """Set complete records without overwriting an active typed query."""
        self._preset_records = list(presets)
        self._preset_names_only = False
        # Selector refresh is not a user format choice: retain an active legacy
        # decoder value while pruning inactive temporary format entries.
        self._remove_stale_dynamic_formats()
        names = {preset.name for preset in self._preset_records}
        if self._preset_selected_name not in names:
            self._preset_selected_name = None
        # ``current`` intentionally selects a newly saved record only if there
        # is no active query.  A save/manager refresh must not replace user text.
        if current and not self._preset_query and current in names:
            self._preset_selected_name = current
        self._refresh_preset_combo()

    def set_preset_names(self, names: list[str], current: str | None = None) -> None:
        """Backward-compatible names-only population for older callers/tests."""
        self._preset_records = [SensorPreset(name=name, format_name="") for name in names if name]
        self._preset_names_only = True
        self._remove_stale_dynamic_formats()
        available = {preset.name for preset in self._preset_records}
        if self._preset_selected_name not in available:
            self._preset_selected_name = None
        if current and not self._preset_query and current in available:
            self._preset_selected_name = current
        self._refresh_preset_combo()

    def _set_preset_combo_text(self, text: str) -> None:
        """Set line-edit text without allowing Qt to reinterpret selection/query."""
        with QSignalBlocker(self._preset_line_edit):
            self._preset_line_edit.setText(text)

    def _begin_preset_edit(self) -> None:
        """Detach settled selected text before the first real edit is applied."""
        if self._preset_edit_transitioning or self._preset_search_mode:
            return
        self._preset_edit_transitioning = True
        try:
            self._preset_search_mode = True
            self._preset_query = ""
            # The identity may remain internally for bookkeeping, but its text
            # must never be prepended to the user input or restored on clear.
            self._set_preset_combo_text("")
            with QSignalBlocker(self.preset_combo):
                self.preset_combo.setCurrentIndex(-1)
        finally:
            self._preset_edit_transitioning = False

    def _refresh_preset_combo(self, *_signal_args) -> None:
        """Refresh items while preserving query verbatim and selection identity."""
        query = self._preset_query
        visible = self._preset_candidates()
        visible_names = {preset.name for preset in visible}
        selected_name = self._preset_selected_name
        if selected_name not in {preset.name for preset in self._preset_records}:
            selected_name = None
            self._preset_selected_name = None
        with QSignalBlocker(self.preset_combo), QSignalBlocker(self._preset_line_edit):
            self.preset_combo.clear()
            for preset in visible:
                self.preset_combo.addItem(preset.name, preset.name)
            if query and not visible:
                self.preset_combo.addItem("(No matching presets)", None)
                item = getattr(self.preset_combo.model(), "item", lambda _i: None)(
                    self.preset_combo.count() - 1
                )
                if item is not None:
                    item.setEnabled(False)
            if (
                not query
                and not self._preset_search_mode
                and selected_name in visible_names
            ):
                self.preset_combo.setCurrentIndex(self.preset_combo.findData(selected_name))
            else:
                self.preset_combo.setCurrentIndex(-1)
            # Query always wins over selected display.  Once the query is empty,
            # selected text is shown only when it represents an explicit settled
            # selection rather than a cleared/deleted search state.
            if query:
                self._preset_line_edit.setText(query)
            elif not self._preset_search_mode and selected_name:
                self._preset_line_edit.setText(selected_name)
            else:
                self._preset_line_edit.clear()

    def _on_preset_query_edited(self, text: str) -> None:
        """Store user text as a query; it never implicitly selects/loads data."""
        if not self._preset_search_mode:
            self._begin_preset_edit()
        self._preset_query = text
        self._preset_search_mode = True
        self._refresh_preset_combo()

    def _on_preset_category_changed(self, _category: str) -> None:
        # While a query is active category changes deliberately do not disturb
        # all-type search results.  Empty browse mode refreshes immediately.
        if not self._preset_query:
            self._refresh_preset_combo()

    def _show_preset_popup(self) -> None:
        """Arrow browsing clears the query and never loads a preset by itself."""
        self._preset_query = ""
        self._preset_search_mode = True
        self._refresh_preset_combo()

    def reset_preset_selection(self) -> None:
        """Clear query and identity without emitting preset or Apply signals."""
        self._preset_selected_name = None
        self._preset_query = ""
        self._preset_search_mode = True
        with QSignalBlocker(self.preset_combo), QSignalBlocker(self._preset_line_edit):
            self.preset_combo.setCurrentIndex(-1)
            self._preset_line_edit.clear()

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

        隐藏的行通过 ``takeRow`` 从主表单移出（field 与 label 都不再占用表单
        高度——实测 QFormLayout 对 ``setVisible(False)`` 的隐藏行仍会保留其
        行几何，导致 YUV 普通格式下 Format 与 Width 之间出现一大段空白，
        0.4.1 用户反馈）；显示的行再按顺序 ``insertRow`` 回表单。因此隐藏的
        条件行在布局上“彻底消失”，可见行仍通过 ``labelForField`` 定位。
        """
        names = {
            "bit_depth_combo": bit,
            "align_combo": align,
            "endian_combo": endian,
            "raw_preview_combo": preview,
            "bayer_pattern_combo": bayer,
        }
        self._apply_cond_rows(names)

    # 条件行规范顺序：(属性名, label 文本)。_apply_cond_rows 按此顺序把需要显示
    # 的行插回 Width 行之前，保证 RAW / YOnly / 其它状态下的行序一致。
    _COND_ROW_SPECS = [
        ("bit_depth_combo", "Bit depth"),
        ("align_combo", "Alignment"),
        ("endian_combo", "Endianness"),
        ("raw_preview_combo", "RAW preview"),
        ("bayer_pattern_combo", "Bayer pattern"),
    ]

    def _apply_cond_rows(self, desired: dict) -> None:
        """按 ``{属性名: visible}`` 重建主表单的条件行段。

        每次先把当前已在表单中的条件行全部 ``takeRow`` 移出，再按
        ``_COND_ROW_SPECS`` 顺序把需要显示的行 ``insertRow`` 回 ``Width`` 行
        之前。移出/移入都不删除 field 与 label 对象；隐藏的 field 同时禁用，
        避免 tab 焦点可达。

        背景：实测本机 PyQt5 的 QFormLayout 对 ``setVisible(False)`` 的隐藏行
        仍保留行几何（YUV 普通格式下 Format 与 Width 之间因此有大段空白）。
        ``takeRow`` 把它们从布局里摘下后，隐藏行彻底不占高度，可见行仍可通过
        ``labelForField`` 定位（0.4.1 修复）。
        """
        form = self._main_form
        if form is None:
            return

        def _row_of(widget) -> int | None:
            pos = form.getWidgetPosition(widget)
            return pos[0] if pos[0] >= 0 else None

        # 1) 先把当前在表单里的条件行全部移出（takeRow 摘下但不删除）。
        labels: dict[str, object] = {}
        fields: dict[str, object] = {}
        for name, _lbl in self._COND_ROW_SPECS:
            field = getattr(self, name)
            fields[name] = field
            r = _row_of(field)
            if r is not None:
                took = form.takeRow(r)
                lw = took.labelItem.widget() if took.labelItem is not None else None
                if lw is not None:
                    labels[name] = lw
                    # takeRow() detaches the label from the form but does not
                    # change its visibility.  Hide the label explicitly while
                    # the row is out of the layout; otherwise its text remains
                    # painted in the old position (the field itself is hidden
                    # but the QLabel is still a child of the content widget).
                    lw.setVisible(False)
            else:
                # A row may already have been taken out by an earlier sync.
                # The cached QLabel is still a live child and must remain
                # hidden until this call inserts its row again.
                cached_label = self._cond_row_labels.get(name)
                if cached_label is not None:
                    cached_label.setVisible(False)
            field.setVisible(False)

        # 2) 找到 Width 行（固定行）当前的插入位置，条件行段统一插在它前面。
        anchor = _row_of(self.width_spin)
        insert_at = anchor if anchor is not None else form.rowCount()

        # 3) 按规范顺序插入需要显示的条件行，隐藏的只禁用。
        # ``set_enabled(True)`` is allowed to run independently of the
        # type/format sync (for example while a tab is being loaded), so the
        # panel-wide state must participate in the conditional-row decision.
        # Otherwise a hidden field can become enabled and a disabled panel can
        # leak keyboard focus to a visible conditional field.
        panel_enabled = getattr(self, "_panel_enabled", True)
        for name, label_text in self._COND_ROW_SPECS:
            field = fields[name]
            visible = bool(desired.get(name, False))
            field.setEnabled(visible and panel_enabled)
            if not visible:
                field.setVisible(False)
                continue
            # 0.4.1-L-2：优先复用 __init__ 缓存的首建 label（样式/对齐得以保持），
            # 其次复用本次 takeRow 摘下的既有 label，最后才按文本重建。
            label_widget = self._cond_row_labels.get(name) or labels.get(name)
            if label_widget is not None:
                form.insertRow(insert_at, label_widget, field)
            else:
                form.insertRow(insert_at, label_text, field)
            new_label = form.labelForField(field)
            if new_label is not None:
                new_label.setVisible(True)
                new_label.setEnabled(True)
            field.setVisible(True)
            insert_at += 1

    # ── internal slots ───────────────────────────────────────────────

    def _on_preset_activated(self, index: int) -> None:
        """Load only a real item; Enter on arbitrary query text is inert."""
        if index < 0:
            return
        name = self.preset_combo.itemData(index)
        if not name:
            return
        selected = str(name)
        # The popup/item itself is the sole selection authority.  Query text
        # cannot become a preset identity, so keyboard Enter remains safe.
        self._preset_selected_name = selected
        self._preset_query = ""
        self._preset_search_mode = False
        self._refresh_preset_combo()
        self.presetSelected.emit(selected)

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
        """Handle direct user type-combo changes (signals are already final)."""
        image_type = normalize_preset_image_type(image_type)
        previous_format = self.format_combo.currentText()
        # A user-driven QComboBox selection is one deliberate change.  Remove
        # its now-inactive legacy type under a blocker, and rebuild Format under
        # a blocker, so no transient Standard Image/N/A/default signal reaches
        # valuesChanged or gets saved to the current ViewerItem.
        with QSignalBlocker(self.type_combo), QSignalBlocker(self.format_combo):
            type_index = self.type_combo.currentIndex()
            keep = (
                image_type
                if type_index >= 0 and self.type_combo.itemData(type_index, self._DYNAMIC_TYPE_ROLE)
                else ""
            )
            self._remove_stale_dynamic_types(keep=keep)
            self._rebuild_format_for_type(image_type, previous_format)
        self._finish_type_change(image_type)

    def _on_raw_preview_changed(self, value: str) -> None:
        # 只在 RAW 且 Bayer 时启用/显示 Bayer 控件。复用 _sync_type_enabled 的
        # 条件行重建（它会按当前 RAW preview 值重新计算 Bayer 行的显隐）。
        self._sync_type_enabled()
        self.rawPreviewChanged.emit(value)
