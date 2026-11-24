"""Batch file-to-RAW/YUV conversion dialog.

Each input may be an image (PNG/JPG/BMP) or an existing YUV file (lossless
byte rearrangement inside one chroma-sampling family).
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import (
    generate_image_variants,
    image_file_to_raw,
    image_file_to_yuv,
    load_bgr_image,
    resolve_output_path_collision,
)
from raw_view.formats import (
    expected_frame_size_raw,
    expected_frame_size_yuv,
    require_decode_size,
)
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    format_output_template,
)
from raw_view.gui.panels import ControlPanel
from raw_view.gui.widgets import FileDropLineEdit, VariantSelector
from raw_view.transcode import YUV_TRANSCODE_FORMATS


# 与本项目主面板 (panels.py) 的默认输出分辨率保持一致（转换默认产出高清，
# 与查看端默认对齐）。仅影响两对话框的默认值，不改动既有 CLI 默认。
DEFAULT_CONVERT_WIDTH = 2560
DEFAULT_CONVERT_HEIGHT = 1440


def resolve_output_dir(same_dir: bool, input_path: str, settings_dir: str) -> str:
    """决定单文件转换的目标输出目录。

    语义与多变体分支统一：
    勾选 “Same directory as input” → 输出到输入文件同目录；
    未勾选 → 落到 Settings 里的默认输出目录（``settings_dir``）。
    返回目录字符串。
    """
    if same_dir:
        return str(Path(input_path).parent)
    return settings_dir


class BatchConvertDialog(QDialog):
    """Modal dialog for batch-converting multiple files to RAW or YUV."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Batch Convert")
        self.setMinimumSize(700, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # ── Source file list (drag-drop or browse) ──
        self.input_edit = FileDropLineEdit()
        self.input_edit.setAccessibleName("Files to convert")
        self.input_edit.setAccessibleDescription(
            "Drop image or YUV files here or use Add Files to build the batch list."
        )
        self.input_edit.setPlaceholderText(
            "Drop files here or use Add Files to add images or YUV files..."
        )
        self._add_btn = QPushButton("Add Files")
        self._add_btn.setObjectName("secondaryButton")
        self._clear_btn = QPushButton("Clear All")
        self._clear_btn.setObjectName("secondaryButton")
        # Align these inline buttons to the field height beside them.
        _field_h = self.input_edit.sizeHint().height()
        self._add_btn.setFixedHeight(_field_h)
        self._clear_btn.setFixedHeight(_field_h)
        self._add_btn.clicked.connect(self._browse_files)
        self._clear_btn.clicked.connect(self._clear_files)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self._add_btn)
        input_row.addWidget(self._clear_btn)

        # ── File table ──
        self._file_table = QTableWidget(0, 4)
        self._file_table.setHorizontalHeaderLabels(["File", "Size", "Status", "Output"])
        self._file_table.horizontalHeader().setStretchLastSection(True)
        self._file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._file_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._file_table.verticalHeader().setVisible(False)
        # 0.4.0-L-2：空表引导。QTableWidget 没有 setPlaceholderText；用一个只读
        # 覆盖标签提示“Add Files 或拖放文件”，随表格行数/尺寸变化显隐与定位，
        # 不改变 QTableWidget 的行列语义。
        self._empty_hint = QLabel("Add files with Add Files or drag & drop here", self._file_table.viewport())
        self._empty_hint.setObjectName("emptyTableHint")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        # Colour/font come from the theme-scoped rule in build_ui_stylesheet()
        # (was a hard-coded dark-theme grey that did not follow ui/font_size).
        self._empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._empty_hint.hide()
        # 事件过滤器必须装在 **viewport** 上：Qt 只把某个对象的自身事件交给装在该
        # 对象上的过滤器（装到 table 上收不到 viewport 的 Resize），否则窗口尺寸变化
        # 时空表提示不会重新居中（0.4.0-L-2 实测：先装 table 侧 → viewport Resize
        # 永远匹配不到）。
        self._file_table.viewport().installEventFilter(self)
        self._update_empty_hint()
        # 行数变化（Add/Clear）与窗口尺寸变化时都要重新定位/显隐。
        self._file_table.model().rowsInserted.connect(lambda *_: self._update_empty_hint())
        self._file_table.model().rowsRemoved.connect(lambda *_: self._update_empty_hint())

        # ── Output parameters ──
        params_group = QFrame()
        params_group.setFrameShape(QFrame.StyledPanel)
        params_layout = QHBoxLayout(params_group)
        params_layout.setContentsMargins(8, 8, 8, 8)

        # Input type mirrors ConvertDialog: Image keeps the historical
        # decode+resize path, YUV losslessly rearranges an existing YUV file
        # (width/height become SOURCE frame size, no resize).  "RAW" stays
        # visible but is genuinely disabled so it cannot be selected and then
        # silently fall through to the image branch.
        self.input_type = QComboBox()
        self.input_type.addItems(["Image", "YUV", "RAW (coming soon)"])
        self.input_type.setItemData(
            2,
            "RAW input conversion (packed RAW, bit alignment, bit depth) is "
            "reserved for a future release.",
            Qt.ToolTipRole,
        )
        raw_input_item = self.input_type.model().item(2)
        if raw_input_item is not None:
            raw_input_item.setEnabled(False)
        self.input_type.setAccessibleName("Input type")

        # Always-visible explanation of the selected input type (same as
        # ConvertDialog): make the Image vs YUV difference clear up front.
        self._input_hint = QLabel()
        self._input_hint.setObjectName("hintLabel")
        self._input_hint.setWordWrap(True)

        # Source YUV list: only formats the transcoder can read.  YOnly (4:0:0)
        # is excluded — it has no chroma planes, so every target would be
        # greyed out, leaving the row in a dead end.
        self.input_yuv_type = QComboBox()
        self.input_yuv_type.addItems(YUV_TRANSCODE_FORMATS)

        self.target_type = QComboBox()
        self.target_type.addItems(["RAW", "YUV"])

        self.raw_type = QComboBox()
        # RAW 类型列表与主面板 ControlPanel.RAW_FORMATS 保持一致（含 RAW16/RAW32）。
        self.raw_type.addItems(ControlPanel.RAW_FORMATS)

        self.yuv_type = QComboBox()
        # YUV 类型列表与主面板 ControlPanel.YUV_FORMATS 保持一致
        # （YOnly 是一个独立格式 + Bit depth 下拉，见 bit_depth_combo）。
        self.yuv_type.addItems(ControlPanel.YUV_FORMATS)

        # YOnly 位深（仅 Format=YOnly 时显示/可用），内部映射 YOnly<bit>。
        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.addItems(ControlPanel.YONLY_BIT_DEPTHS)
        self.bit_depth_combo.setCurrentText("12")

        self.align = QComboBox()
        self.align.addItems(["msb", "lsb"])

        # Endianness：RAW 与 YOnly 多 bit（16-bit 存储）可选用大小端；
        # 普通 YUV 无意义（_sync_controls 禁用）。
        self.endian = QComboBox()
        self.endian.addItems(["little", "big"])

        self.raw_source_mode = QComboBox()
        self.raw_source_mode.addItems(["bayer", "gray"])

        self.bayer_pattern = QComboBox()
        self.bayer_pattern.addItems(BAYER_PATTERNS)

        self.width = QSpinBox()
        self.width.setRange(1, 65535)
        self.width.setValue(DEFAULT_CONVERT_WIDTH)

        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(DEFAULT_CONVERT_HEIGHT)

        self._same_dir_cb = QCheckBox("Same directory as input")
        self._same_dir_cb.setChecked(True)

        params_form = QFormLayout()
        # Kept for _apply_cond_rows(): hidden conditional rows are taken out of
        # the layout (setVisible alone still reserves a 6px row gap).
        self._form = params_form
        params_form.addRow("Input type", self.input_type)
        params_form.addRow("", self._input_hint)
        params_form.addRow("Input YUV format", self.input_yuv_type)
        params_form.addRow("Target", self.target_type)
        params_form.addRow("RAW type", self.raw_type)
        params_form.addRow("YUV format", self.yuv_type)
        params_form.addRow("Bit depth", self.bit_depth_combo)
        params_form.addRow("Alignment", self.align)
        params_form.addRow("Endianness", self.endian)
        params_form.addRow("RAW source", self.raw_source_mode)
        params_form.addRow("Bayer pattern", self.bayer_pattern)
        params_form.addRow("Width", self.width)
        params_form.addRow("Height", self.height)

        # QFormLayout hides only the field, never the row label.  Cache every
        # conditional row's label so _apply_cond_rows() can move both together;
        # otherwise a hidden field leaves its label dangling (0.5.10 H-1).
        # Collected after all addRow() calls: labelForField() only answers for
        # a field already in the form.
        self._cond_row_labels = {
            field: params_form.labelForField(field)
            for field in (
                self.input_yuv_type,
                self.raw_type,
                self.bit_depth_combo,
                self.align,
                self.endian,
                self.raw_source_mode,
                self.bayer_pattern,
            )
        }
        self._cond_row_labels = {
            field: label
            for field, label in self._cond_row_labels.items()
            if label is not None
        }

        # Conditional rows grouped by the fixed row that follows them, so a
        # visible row is inserted back at its original position (0.5.10.1
        # review L-1).
        self._cond_row_groups = (
            (
                self.target_type, ((self.input_yuv_type, "Input YUV format"),),
            ),
            (
                self.yuv_type, ((self.raw_type, "RAW type"),),
            ),
            (
                self.width, (
                    (self.bit_depth_combo, "Bit depth"),
                    (self.align, "Alignment"),
                    (self.endian, "Endianness"),
                    (self.raw_source_mode, "RAW source"),
                    (self.bayer_pattern, "Bayer pattern"),
                ),
            ),
        )

        params_right = QVBoxLayout()
        params_right.addLayout(params_form)
        params_right.addWidget(self._same_dir_cb)
        params_right.addStretch(1)

        params_layout.addLayout(params_right, 1)

        # ── Progress ──────────────────────────────────────────────────
        # 刻意不在这里构造 QProgressDialog：QProgressDialog(minimumDuration=0,
        # modal=True) 在构造后即使 hide()，也会在下一个嵌套事件循环（例如
        # Add Files 的 QFileDialog / Clear / Start 的窗体重绘）中闪现 "Batch
        # conversion in progress"（0.4.1 用户反馈）。改为在 _run_batch 真正开始
        # 时按需创建，且 minimumDuration 稍大，只有任务真正耗时才显示。
        self._progress: QProgressDialog | None = None
        self._batch_cancelled = False
        # P1-6：会话内"全部覆盖"开关（选中后本次对话框不再问覆盖策略）。
        self._overwrite_all = False

        # ── Buttons ──
        self._run_btn = QPushButton("Start Batch Convert")
        self._run_btn.setObjectName("accentButton")
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip("Add at least one file before starting the batch conversion.")
        self._run_btn.clicked.connect(self._run_batch)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(close_btn)

        # ── Multi-variant generator (opt-in via Settings) ────────────────
        self._variant_selector: VariantSelector | None = None
        if self._settings.multi_variant_enabled:
            self._variant_selector = VariantSelector()
            params_right.addWidget(self._variant_selector)

        params_group.setObjectName("card")

        # The parameters block can grow tall (especially with the variant
        # selector), so wrap it in a scroll area that scrolls internally
        # instead of pushing the action buttons off-screen.
        params_scroll = QScrollArea()
        params_scroll.setWidgetResizable(True)
        params_scroll.setFrameShape(QFrame.NoFrame)
        params_scroll.setWidget(params_group)

        # ── Main layout ──
        layout = QVBoxLayout(self)
        layout.addLayout(input_row)
        layout.addWidget(self._file_table, 1)
        layout.addWidget(params_scroll)
        layout.addLayout(btn_row)

        # Bound the params scroll and the dialog so buttons stay visible.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail_h = screen.availableGeometry().height()
            params_scroll.setMaximumHeight(int(avail_h * 0.45))
            self.setMaximumHeight(int(avail_h * 0.9))

        # Signals
        self.input_edit.fileDropped.connect(self._on_files_dropped)
        self.target_type.currentTextChanged.connect(self._sync_controls)
        self.raw_source_mode.currentTextChanged.connect(self._sync_controls)
        self.input_type.currentTextChanged.connect(self._on_input_type_changed)
        self.input_yuv_type.currentTextChanged.connect(self._sync_controls)
        # 切换 YUV 格式（如 YUYV→YOnly）也要重跑条件显隐：YOnly 需显示位深/对齐/端序
        self.yuv_type.currentTextChanged.connect(self._sync_controls)

        self._sync_controls()

    # ── File management ────────────────────────────────────────────────

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Files",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if paths:
            self._add_files(paths)

    def _clear_files(self) -> None:
        self._file_table.setRowCount(0)
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip("Add at least one file before starting the batch conversion.")

    # ── 0.4.0-L-2：空表引导覆盖标签 ─────────────────────────────────────

    def _update_empty_hint(self) -> None:
        """按表格行数显隐空表提示，并把它对齐到 viewport 可视区中央。

        有行时隐藏；无行时显示并随 viewport resize 重新居中。覆盖 label 在
        viewport 上（不占行列），鼠标事件透明（WA_TransparentForMouseEvents），
        不干扰拖放/点击。
        """
        if self.__dict__.get("_file_table") is None:
            return
        empty = self._file_table.rowCount() == 0
        hint = self.__dict__.get("_empty_hint")
        if hint is None:
            return
        hint.setVisible(empty)
        if empty:
            vp = self._file_table.viewport()
            hint.setGeometry(vp.rect())
            hint.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt override
        """viewport resize 时重排空表提示；其余事件交给默认处理。"""
        if obj is self._file_table.viewport() and event.type() == QEvent.Resize:
            if self._file_table.rowCount() == 0:
                self._update_empty_hint()
        return super().eventFilter(obj, event)

    def _on_files_dropped(self, path: str) -> None:
        """Handle dropped file (single path from FileDropLineEdit)."""
        if path:
            self._add_files([path])

    def _add_files(self, paths: list[str]) -> None:
        existing = set()
        for row in range(self._file_table.rowCount()):
            item = self._file_table.item(row, 0)
            if item:
                existing.add(item.text())

        for path in paths:
            if path in existing:
                continue
            row = self._file_table.rowCount()
            self._file_table.insertRow(row)
            self._file_table.setItem(row, 0, QTableWidgetItem(path))
            try:
                size = Path(path).stat().st_size
                size_str = f"{size:,} bytes"
            except OSError:
                size_str = "-"
            self._file_table.setItem(row, 1, QTableWidgetItem(size_str))
            self._file_table.setItem(row, 2, QTableWidgetItem("Pending"))
            self._file_table.setItem(row, 3, QTableWidgetItem(""))

        count = self._file_table.rowCount()
        self.input_edit.setText(f"{count} file(s) loaded")
        self._run_btn.setEnabled(count > 0)
        self._run_btn.setToolTip(
            "Start converting the loaded files."
            if count > 0 else
            "Add at least one file before starting the batch conversion."
        )

    # ── Control sync ───────────────────────────────────────────────────

    def _resolve_yuv_fmt(self) -> str:
        """把 UI 的 YOnly + Bit depth 映射为内部有效格式名（YOnly8/10/12/14/16）。"""
        return ControlPanel._yonly_internal_name(
            self.yuv_type.currentText(), self.bit_depth_combo.currentText()
        )

    def _sync_controls(self) -> None:
        # QComboBox.setItemData() re-emits currentTextChanged here; without this
        # guard the target-list refresh below would recurse into itself forever.
        if getattr(self, "_syncing_controls", False):
            return
        self._syncing_controls = True
        try:
            self._sync_controls_locked()
        finally:
            self._syncing_controls = False

    def _sync_controls_locked(self) -> None:
        is_yuv_input = self._is_yuv_input()
        # Pin the target BEFORE reading is_raw: setCurrentText() re-enters
        # _sync_controls, which the guard swallows, so the pre-switch value
        # would otherwise leave Alignment/Endianness visible and enabled for a
        # YUV input even though transcoding ignores them (0.5.10 review M-1).
        self.target_type.setEnabled(not is_yuv_input)
        if is_yuv_input and self.target_type.currentText() != "YUV":
            self.target_type.setCurrentText("YUV")
        is_raw = self.target_type.currentText() == "RAW"
        is_bayer = self.raw_source_mode.currentText() == "bayer"
        # YOnly 是一个独立格式 + Bit depth 下拉：选它时显示位深/对齐/大小端
        is_yonly = self.yuv_type.currentText() == "YOnly"
        show_raw = is_raw and not is_yuv_input
        show_bit = (not is_raw) and is_yonly and not is_yuv_input
        show_align_endian = is_raw or show_bit
        # One call drives visibility, layout membership and enabled state for
        # every conditional row.  Hidden rows are taken out of the QFormLayout
        # instead of only being hidden: setVisible(False) still reserves a 6px
        # row gap (0.5.10.1 review L-1).  Enabled state is derived here rather
        # than from isVisible(), which is always False before show().
        self._apply_cond_rows({
            self.input_yuv_type: is_yuv_input,
            self.raw_type: show_raw,
            self.bit_depth_combo: show_bit,
            self.align: show_align_endian,
            self.endian: show_align_endian,
            self.raw_source_mode: show_raw,
            self.bayer_pattern: show_raw and is_bayer,
        })
        self.yuv_type.setEnabled(True if is_yuv_input else not is_raw)
        self._refresh_target_formats()
        # Variants fan one IMAGE out into many formats; grey the panel out for a
        # YUV source instead of letting the user pick options that cannot run.
        variant_selector = getattr(self, "_variant_selector", None)
        if variant_selector is not None:
            variant_selector.setEnabled(not is_yuv_input)
            # Explain *why* it is greyed out; Convert's button carries the same
            # tooltip, and an unexplained disabled panel reads like a bug to the
            # user (0.5.10 review L-7).
            variant_selector.setToolTip(
                "Variants are generated from an image; not available for a YUV input."
                if is_yuv_input else ""
            )
        self._sync_input_hint()

    def _apply_cond_rows(self, desired: dict[QWidget, bool]) -> None:
        """Move conditional rows in/out of the QFormLayout to match *desired*.

        ``QFormLayout`` keeps the row label visible when only the field is
        hidden (0.5.10 review H-1), and still reserves a 6px gap for a row
        whose label and field are both hidden (0.5.10.1 review L-1).  Hidden
        rows are therefore ``takeRow``-ed out of the form and visible rows are
        ``insertRow``-ed back at their canonical position.

        Rows that stay visible are deliberately **not** touched: hiding them
        even for an instant closes an open QComboBox popup and moves the
        keyboard focus to the next widget in the chain; re-showing afterwards
        does not undo either (0.5.10.2 review M-1).  Only rows that must
        disappear are detached, and only rows that must appear *and are not in
        the form yet* are inserted.

        ``_cond_row_groups`` is the only place the row order is declared and
        ``_cond_row_labels`` the only place label widgets are cached, so a
        label can never drift from its field.
        """
        form = self._form
        # 1) Detach only the rows that must be hidden.  Recompute the row index
        # each time: takeRow() shifts the rows below it.
        for _anchor, rows in self._cond_row_groups:
            for field, _label_text in rows:
                if desired.get(field, False):
                    continue
                row = form.getWidgetPosition(field)[0]
                if row >= 0:
                    taken = form.takeRow(row)
                    label = (
                        taken.labelItem.widget()
                        if taken.labelItem is not None else None
                    )
                    if label is not None:
                        self._cond_row_labels[field] = label
                        label.setVisible(False)
                else:
                    # Already outside the form (previous sync) but still a
                    # child widget; keep it hidden until re-inserted.
                    label = self._cond_row_labels.get(field)
                    if label is not None:
                        label.setVisible(False)
                field.setVisible(False)
                field.setEnabled(False)

        # 2) Insert the rows that must show but are not in the form yet, group
        # by group, in declared order.  Start at the *earliest* row this group
        # still occupies: a row that becomes visible again has to be inserted
        # before the first surviving sibling declared after it, not merely
        # before the anchor (otherwise ``Bit depth`` would land after the
        # already-present ``Alignment``/``Endianness``).
        for anchor, rows in self._cond_row_groups:
            insert_at = form.getWidgetPosition(anchor)[0]
            if insert_at < 0:
                insert_at = form.rowCount()
            for field, _label_text in rows:
                position = form.getWidgetPosition(field)[0]
                if position >= 0 and desired.get(field, False):
                    insert_at = min(insert_at, position)
            for field, label_text in rows:
                if not desired.get(field, False):
                    continue
                position = form.getWidgetPosition(field)[0]
                if position < 0:
                    label = self._cond_row_labels.get(field)
                    if label is not None:
                        form.insertRow(insert_at, label, field)
                    else:
                        form.insertRow(insert_at, label_text, field)
                        label = form.labelForField(field)
                        if label is not None:
                            self._cond_row_labels[field] = label
                    if label is not None:
                        label.setVisible(True)
                    field.setVisible(True)
                    position = insert_at
                # Enabled state is derived here rather than from isVisible(),
                # which is always False before show() (0.2.2).
                field.setEnabled(True)
                insert_at = position + 1

    def _is_yuv_input(self) -> bool:
        return self.input_type.currentText() == "YUV"

    def _sync_input_hint(self) -> None:
        """Explain the selected input type before the user commits to it."""
        if self._is_yuv_input():
            self._input_hint.setText(
                "YUV input: lossless byte rearrangement inside the same "
                "4:2:2 / 4:2:0 family. Nothing is resized — Width/Height are "
                "the SOURCE frame size (same for every file in the batch)."
            )
        else:
            self._input_hint.setText(
                "Image input: decode PNG/JPG/BMP and encode to RAW/YUV. "
                "Width/Height are the OUTPUT size (each image is resized)."
            )

    def _on_input_type_changed(self) -> None:
        """Clear Width/Height when the input type changes (they change meaning)."""
        if self._is_yuv_input():
            # Defensive: the source list already only holds transcodable
            # formats, so a dead-end selection cannot normally exist.
            if self.input_yuv_type.currentText() not in YUV_TRANSCODE_FORMATS:
                self.input_yuv_type.setCurrentText(YUV_TRANSCODE_FORMATS[0])
            self.width.setValue(1)
            self.height.setValue(1)
        else:
            self.width.setValue(DEFAULT_CONVERT_WIDTH)
            self.height.setValue(DEFAULT_CONVERT_HEIGHT)
        self._sync_controls()

    def _refresh_target_formats(self) -> None:
        """Grey out targets the source YUV layout cannot reach (same as Convert)."""
        from raw_view.transcode import reject_reason

        model = self.yuv_type.model()
        blocked = self.yuv_type.blockSignals(True)
        try:
            if not self._is_yuv_input():
                for index in range(self.yuv_type.count()):
                    item = model.item(index)
                    if item is not None:
                        item.setEnabled(True)
                    self.yuv_type.setItemData(index, None, Qt.ToolTipRole)
                return
            source = self.input_yuv_type.currentText()
            for index in range(self.yuv_type.count()):
                reason = reject_reason(source, self.yuv_type.itemText(index))
                item = model.item(index)
                if item is not None:
                    item.setEnabled(reason is None)
                self.yuv_type.setItemData(index, reason, Qt.ToolTipRole)
        finally:
            self.yuv_type.blockSignals(blocked)
        source = self.input_yuv_type.currentText()
        if reject_reason(source, self.yuv_type.currentText()) is not None:
            for index in range(self.yuv_type.count()):
                if reject_reason(source, self.yuv_type.itemText(index)) is None:
                    self.yuv_type.setCurrentIndex(index)
                    break

    # ── Batch conversion ───────────────────────────────────────────────

    def _run_batch(self) -> None:
        rows = self._file_table.rowCount()
        if rows == 0:
            QMessageBox.information(self, "Batch Convert", "No files to convert.")
            return

        target_type = self.target_type.currentText()
        yuv_input = self._is_yuv_input()
        if (
            yuv_input
            and self._variant_selector is not None
            and self._variant_selector.isEnabled()
        ):
            QMessageBox.information(
                self, "Batch Convert",
                "Multi-variant generation works on images only; it is disabled "
                "for a YUV input batch.",
            )
            return
        fmt = self.raw_type.currentText() if target_type == "RAW" else self._resolve_yuv_fmt()
        out_w = self.width.value()
        out_h = self.height.value()
        template = self._settings.output_template

        # Collect files
        files = []
        for row in range(rows):
            path_item = self._file_table.item(row, 0)
            if path_item:
                p = path_item.text().strip()
                if p:
                    files.append((row, p))

        if not files:
            return

        # Reset statuses
        for row, _ in files:
            self._file_table.item(row, 2).setText("Pending")
            self._file_table.item(row, 3).setText("")

        self._batch_cancelled = False

        # P1-6 覆盖策略：默认 "rename"（不覆盖不丢文件）；用户选过"全部覆盖"
        # 则覆盖。先一次性询问所有冲突文件（会话内记住选择）。
        existing_paths = []
        template_yuv = (
            self.yuv_type.currentText() if yuv_input else self._resolve_yuv_fmt()
        )
        for _row, input_path in files:
            out = format_output_template(
                template, input_path, out_w, out_h, target_type,
                output_dir=resolve_output_dir(
                    self._same_dir_cb.isChecked(), input_path,
                    self._settings.default_output_dirname,
                ),
                raw_type=self.raw_type.currentText(),
                yuv_type=template_yuv,
                bayer_pattern=self.bayer_pattern.currentText(),
                source_mode=self.raw_source_mode.currentText(),
                alignment=self.align.currentText(),
                endianness=self.endian.currentText(),
            )
            if os.path.exists(out):
                existing_paths.append(out)
        self._collision_policy = self._ask_overwrite_strategy(existing_paths)

        # 按需创建进度框：等冲突策略询问（QMessageBox 模态 event loop）结束后
        # 才构造，避免它在 Add Files 的 QFileDialog 或冲突确认框等嵌套事件循环
        # 中闪现 "Batch conversion in progress..."（0.4.1 用户反馈）。
        # minimumDuration 300ms——很短的批量直接完成时不弹框。
        # 注意：这里**不能**显式 show()——QProgressDialog 的 deferred-show 由
        # 后续的 setValue() 触发（内部 setMinimumDuration 只在 setValue 后真正
        # 生效）；显式 show() 会立刻绘制并让短批量重新闪现（0.4.1 复核实测，
        # PyQt5 5.15.11）。
        self._progress = QProgressDialog(
            "Batch conversion in progress...", "Cancel", 0, len(files), self
        )
        self._progress.setWindowTitle("Batch Convert")
        self._progress.setMinimumDuration(300)
        self._progress.setAutoClose(True)
        self._progress.setAutoReset(True)
        self._progress.setModal(True)
        self._progress.canceled.connect(self._on_cancel_batch)
        self._progress.setValue(0)
        self._progress.setLabelText("Batch conversion in progress...")

        success_count = 0
        fail_count = 0

        try:
            for i, (row, input_path) in enumerate(files):
                if self._batch_cancelled:
                    self._file_table.item(row, 2).setText("Cancelled")
                    continue

                self._progress.setValue(i)
                self._progress.setLabelText(f"Converting {i + 1}/{len(files)}: {Path(input_path).name}")

                # Multi-variant mode: fan each file into all selected combos.
                if self._variant_selector is not None and self._variant_selector.isEnabled():
                    formats = self._variant_selector.selected_formats()
                    sizes = self._variant_selector.selected_sizes()
                    bayer = self._variant_selector.selected_bayer()
                    if not formats or not sizes:
                        self._file_table.item(row, 2).setText("Skipped: no variants selected")
                        continue
                    out_dir = resolve_output_dir(
                        self._same_dir_cb.isChecked(), input_path,
                        self._settings.default_output_dirname,
                    )
                    # P1-6：多变体按会话策略处理已存在目标（skip → None 跳过本文件）
                    policy = "overwrite" if self._overwrite_all else getattr(
                        self, "_collision_policy", "rename"
                    )
                    if policy == "skip":
                        self._file_table.item(row, 2).setText("Skipped (exists)")
                        continue

                    def _map_output(out: str) -> str:
                        return resolve_output_path_collision(out, on_existing=policy)

                    try:
                        written = generate_image_variants(
                            input_path, formats, sizes, bayer,
                            source_mode=self.raw_source_mode.currentText(),
                            alignment=self.align.currentText(),
                            endianness=self.endian.currentText(),
                            output_dir=out_dir,
                            template=template,
                            output_paths=_map_output,
                        )
                        self._file_table.item(row, 2).setText(f"OK ({len(written)} files)")
                        self._file_table.item(row, 3).setText(str(Path(written[0]).parent) if written else "")
                        success_count += 1
                    except Exception as exc:
                        self._file_table.item(row, 2).setText(f"Failed: {exc}")
                        fail_count += 1
                    QApplication.processEvents()
                    if self._batch_cancelled:
                        break
                    continue

                output_path = format_output_template(
                    template, input_path, out_w, out_h, target_type,
                    output_dir=resolve_output_dir(
                        self._same_dir_cb.isChecked(), input_path,
                        self._settings.default_output_dirname,
                    ),
                    raw_type=self.raw_type.currentText(),
                    yuv_type=template_yuv,
                    bayer_pattern=self.bayer_pattern.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                )

                self._file_table.item(row, 3).setText(output_path)

                # P1-6：按会话策略处理已存在的目标（skip → None 跳过本文件）
                resolved = self._resolve_collision(output_path)
                if resolved is None:
                    self._file_table.item(row, 2).setText("Skipped (exists)")
                    continue
                output_path = resolved
                self._file_table.item(row, 3).setText(output_path)

                try:
                    if yuv_input:
                        from raw_view.transcode import (
                            TranscodeSpec,
                            frame_size,
                            transcode_file,
                        )

                        spec = TranscodeSpec(
                            source_format=self.input_yuv_type.currentText(),
                            target_format=self.yuv_type.currentText(),
                            width=out_w,
                            height=out_h,
                            alignment=self.align.currentText(),
                            endianness=self.endian.currentText(),
                        )
                        # 512MB single-frame limit, checked before writing. A
                        # batch runs unattended, so an over-limit entry must
                        # fail visibly instead of filling the disk.
                        require_decode_size(spec.width, spec.height, frame_size(spec))
                        transcode_file(input_path, output_path, spec)
                    elif target_type == "RAW":
                        image_file_to_raw(
                            input_path,
                            output_path,
                            self.raw_type.currentText(),
                            out_w,
                            out_h,
                            alignment=self.align.currentText(),
                            endianness=self.endian.currentText(),
                            source_mode=self.raw_source_mode.currentText(),
                            bayer_pattern=self.bayer_pattern.currentText(),
                        )
                    else:
                        image_file_to_yuv(
                            input_path, output_path, self._resolve_yuv_fmt(),
                            out_w, out_h,
                            alignment=self.align.currentText(),
                            endianness=self.endian.currentText(),
                        )

                    self._file_table.item(row, 2).setText("OK")
                    success_count += 1
                except Exception as exc:
                    self._file_table.item(row, 2).setText(f"Failed: {exc}")
                    fail_count += 1

                QApplication.processEvents()

                if self._batch_cancelled:
                    break
        finally:
            if self._progress is not None:
                self._progress.close()
                self._progress.setParent(None)
                self._progress.deleteLater()
                self._progress = None

        # Report
        total = len(files)
        summary = f"Batch complete: {success_count}/{total} succeeded"
        if fail_count > 0:
            summary += f", {fail_count} failed"
        QMessageBox.information(self, "Batch Convert", summary)

    def _on_cancel_batch(self) -> None:
        self._batch_cancelled = True

    # ── P1-6 覆盖确认 ───────────────────────────────────────────────────

    def _ask_overwrite_strategy(self, existing_paths: list[str]) -> str:
        """批量转换目标已存在时，一次性询问处理策略。

        返回 "overwrite"（覆盖，含"全部覆盖"）/ "rename"（自动改名，默认）/
        "skip"（遇到已存在文件标题跳过该文件）。无冲突时直接返回默认
        "rename"（保持不覆盖不丢文件的语义）。
        """
        if not existing_paths:
            # 无冲突：默认 rename（不覆盖不丢文件），避免意外覆盖历史产物。
            return "rename"
        if self._overwrite_all:
            return "overwrite"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Output file exists")
        box.setText(
            f"{len(existing_paths)} output file(s) already exist."
            "How do you want to handle them?"
        )
        rename_b = box.addButton("Rename (_1)", QMessageBox.ActionRole)
        ovw_b = box.addButton("Overwrite All", QMessageBox.AcceptRole)
        skip_b = box.addButton("Skip existing", QMessageBox.RejectRole)
        box.setDefaultButton(rename_b)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is ovw_b:
            self._overwrite_all = True
            return "overwrite"
        if clicked is skip_b:
            return "skip"
        return "rename"

    def _resolve_collision(self, output_path: str) -> str | None:
        """按当前策略解析单个输出路径；返回 None 表示应跳过（skip 策略）。"""
        policy = getattr(self, "_collision_policy", "rename")
        if self._overwrite_all:
            policy = "overwrite"
        if policy == "skip":
            return None if os.path.exists(output_path) else output_path
        return resolve_output_path_collision(output_path, on_existing=policy)
