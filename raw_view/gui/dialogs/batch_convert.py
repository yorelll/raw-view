"""Batch file-to-RAW/YUV conversion dialog.

Each input may be an image (PNG/JPG/BMP) or an existing YUV file (lossless
byte rearrangement inside one chroma-sampling family).
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QBrush, QColor
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
    image_file_to_raw,
    image_file_to_yuv,
    resolve_output_path_collision,
)
from raw_view.formats import require_decode_size
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    THEME_PALETTES,
    format_output_template,
    normalize_ui_theme,
    resolve_output_dir,
)
from raw_view.gui.dialogs.convert import yuv_size_mismatch_hint
from raw_view.gui.panels import ControlPanel
from raw_view.gui.widgets import FileDropLineEdit
from raw_view.transcode import YUV_TRANSCODE_FORMATS


# 与本项目主面板 (panels.py) 的默认输出分辨率保持一致（转换默认产出高清，
# 与查看端默认对齐）。仅影响两对话框的默认值，不改动既有 CLI 默认。
DEFAULT_CONVERT_WIDTH = 2560
DEFAULT_CONVERT_HEIGHT = 1440


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
        # setEnabled(False) blocks selection but qt-material does not paint the
        # item grey, so a reserved entry looked selectable (user report).  Set
        # the theme's disabled text colour explicitly and refresh it whenever
        # the palette/style changes.
        self._refresh_reserved_input_item()
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
        self._same_dir_cb.setToolTip(
            "Checked: every output is written next to its own input file. "
            "Unchecked: use the output folder below."
        )

        # Visible destination when "Same directory as input" is unchecked.
        # Previously the option silently disappeared, so the user had no way to
        # tell where the files would land (user report, 0.5.11 review M-3).
        self.output_dir_edit = QLineEdit(settings.default_output_dirname)
        self.output_dir_edit.setAccessibleName("Output folder")
        self.output_dir_edit.setAccessibleDescription(
            "Folder for batch outputs when 'Same directory as input' is off. "
            "A relative name is created inside each input file's folder."
        )
        # Never leave this blank: an empty field would put the user back in
        # "I cannot tell where the files go" territory.
        if not self.output_dir_edit.text().strip():
            self.output_dir_edit.setText("convert_out")
        self.output_dir_edit.setPlaceholderText(
            "Output folder, e.g. convert_out or an absolute path"
        )
        self.output_dir_edit.setToolTip(
            "A relative name (default: the Settings 'Default output folder', "
            "e.g. convert_out) is created inside each input file's folder. "
            "An absolute path is used as-is."
        )
        self._output_dir_browse_btn = QPushButton("Browse...")
        self._output_dir_browse_btn.setObjectName("secondaryButton")
        self._output_dir_browse_btn.setFixedHeight(
            self.output_dir_edit.sizeHint().height()
        )
        self._output_dir_browse_btn.clicked.connect(self._browse_output_dir)
        output_dir_row = QHBoxLayout()
        output_dir_row.setContentsMargins(0, 0, 0, 0)
        output_dir_row.setSpacing(6)
        output_dir_row.addWidget(self.output_dir_edit, 1)
        output_dir_row.addWidget(self._output_dir_browse_btn, 0)
        self._output_dir_widget = QWidget()
        self._output_dir_widget.setObjectName("outputDirRow")
        self._output_dir_widget.setLayout(output_dir_row)
        # When the checkbox is on, keep an explicit (non-empty) statement that
        # outputs go next to each input instead of hiding the destination
        # completely; the user must always be able to tell where files land.
        self._output_dir_hint = QLabel()
        self._output_dir_hint.setObjectName("hintLabel")
        self._output_dir_hint.setWordWrap(True)

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
        output_folder_row = QHBoxLayout()
        output_folder_row.setContentsMargins(0, 0, 0, 0)
        self._output_folder_label = QLabel("Output folder")
        output_folder_row.addWidget(self._output_folder_label, 0)
        output_folder_row.addWidget(self._same_dir_cb, 0)
        output_folder_row.addStretch(1)
        params_right.addLayout(output_folder_row)
        params_right.addWidget(self._output_dir_widget)
        params_right.addWidget(self._output_dir_hint)
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
        # Sticky collision choice for the current batch only; reset at the
        # start and at the end of every run (0.5.11 review L-2).
        self._operation_collision_choice: str | None = None

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

        # Batch conversion is deliberately single-target per file.  Variant
        # fan-out lives on the Convert dialog's "Variant Convert" page; the
        # `multi_variant_enabled` setting must not add controls or extra
        # outputs here (user decision, 0.5.11.1 review L-7 removal).
        params_group.setObjectName("card")

        # The parameters block can grow tall, so wrap it in a scroll area that
        # scrolls internally instead of pushing the action buttons off-screen.
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
            # P3: same 0.85 / 720 px cap as Convert so the footer never falls
            # off a 1366x768 screen.  The form itself is deliberately unchanged
            # (its field sequence and row count are pinned by tests).
            self.setMaximumHeight(min(int(avail_h * 0.85), 720))

        # Signals
        self.input_edit.fileDropped.connect(self._on_files_dropped)
        self.target_type.currentTextChanged.connect(self._sync_controls)
        self.raw_source_mode.currentTextChanged.connect(self._sync_controls)
        self.input_type.currentTextChanged.connect(self._on_input_type_changed)
        self.input_yuv_type.currentTextChanged.connect(self._sync_controls)
        # 切换 YUV 格式（如 YUYV→YOnly）也要重跑条件显隐：YOnly 需显示位深/对齐/端序
        self.yuv_type.currentTextChanged.connect(self._sync_controls)
        self._same_dir_cb.toggled.connect(self._sync_output_dir_controls)
        self.output_dir_edit.textChanged.connect(self._sync_output_dir_controls)

        self._sync_controls()
        self._sync_output_dir_controls()

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

    # ── Output folder ──────────────────────────────────────────────────

    def _sync_output_dir_controls(self) -> None:
        """Keep the output destination visible whatever the checkbox says.

        Checked -> the editable folder is hidden but an explicit hint states
        that files go next to each input (so the destination never silently
        disappears).  Unchecked -> the line edit + Browse button are shown and
        carry the folder that will actually be used (default: the Settings
        ``Default output folder``).
        """
        same_dir = self._same_dir_cb.isChecked()
        self._output_dir_widget.setVisible(not same_dir)
        self._output_dir_browse_btn.setVisible(not same_dir)
        if same_dir:
            self._output_dir_hint.setText(
                "Output folder: next to each input file (the input's own folder)."
            )
        else:
            folder = (
                self.output_dir_edit.text().strip()
                or self._settings.default_output_dirname
            )
            self._output_dir_hint.setText(
                f"Output folder: {folder} "
                "(a relative name is created inside each input's folder)."
            )
        self._output_dir_hint.setVisible(True)

    def _browse_output_dir(self) -> None:
        start = self.output_dir_edit.text().strip()
        if not start or not Path(start).is_absolute():
            start = str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Output Folder", start)
        if path:
            self.output_dir_edit.setText(path)
            self._sync_output_dir_controls()

    def _output_dir_for(self, input_path: str) -> str:
        """Resolve the batch output folder for one input file."""
        if self._same_dir_cb.isChecked():
            return resolve_output_dir(
                True, input_path, self._settings.default_output_dirname
            )
        folder = (
            self.output_dir_edit.text().strip()
            or self._settings.default_output_dirname
        )
        return resolve_output_dir(False, input_path, folder)

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

    def _refresh_reserved_input_item(self) -> None:
        """Paint "RAW (coming soon)" with the current theme's disabled colour.

        The item stays disabled (flags unchanged), but qt-material's combo view
        renders an enabled-looking entry otherwise.  ``text_secondary`` is the
        same token the stylesheet uses for every disabled control.
        """
        combo = getattr(self, "input_type", None)
        settings = getattr(self, "_settings", None)
        if combo is None or settings is None:
            return
        item = combo.model().item(2)
        if item is None:
            return
        palette = THEME_PALETTES[normalize_ui_theme(settings.ui_theme)]
        item.setForeground(QBrush(QColor(palette["text_secondary"])))

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            refresh = getattr(self, "_refresh_reserved_input_item", None)
            if refresh is not None:
                refresh()

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
        """Reset Width/Height to the shared default when the input type changes.

        They change meaning (output size for an image, source frame size for a
        YUV file), so a carried-over value would be wrong for the new mode.
        Both modes now start at the documented 2560x1440 guess; for a YUV input
        this is only an initial value, and a mismatched file is reported when
        the user starts the conversion (0.5.10.x user request).
        """
        if self._is_yuv_input():
            # Defensive: the source list already only holds transcodable
            # formats, so a dead-end selection cannot normally exist.
            if self.input_yuv_type.currentText() not in YUV_TRANSCODE_FORMATS:
                self.input_yuv_type.setCurrentText(YUV_TRANSCODE_FORMATS[0])
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

        # Collision handling is the same checkbox interaction as Convert: the
        # first conflicting file asks Skip / Rename / Overwrite plus "Do this
        # for all remaining files".  The choice lives for this batch only and
        # is cleared in the finally block below (0.5.11 review L-2).
        self._operation_collision_choice = None
        template_yuv = (
            self.yuv_type.currentText() if yuv_input else self._resolve_yuv_fmt()
        )

        # 按需创建进度框，避免它在 Add Files 的 QFileDialog 等嵌套事件循环中
        # 闪现 "Batch conversion in progress..."（0.4.1 用户反馈）。
        # minimumDuration 300ms——很短的批量直接完成时不弹框。逐文件覆盖确认
        # 可能发生在进度框已经显示之后，因此 _resolve_collision() 会在弹框前
        # 临时隐藏进度框，返回后再恢复（0.5.11.1 review L-1）。
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

                output_path = format_output_template(
                    template, input_path, out_w, out_h, target_type,
                    output_dir=self._output_dir_for(input_path),
                    # Only a RAW target has a RAW type; for YUV targets the
                    # placeholder must render empty, exactly like Convert and
                    # the CLI (0.5.11.2 review L-3).
                    raw_type=(
                        self.raw_type.currentText()
                        if target_type == "RAW"
                        else ""
                    ),
                    yuv_type=template_yuv,
                    bayer_pattern=self.bayer_pattern.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                    source_yuv_type=(
                        self.input_yuv_type.currentText() if yuv_input else ""
                    ),
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
                    # An explicitly chosen (unchecked) output folder may not
                    # exist yet; the same-directory default always does.
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
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
                    detail = str(exc)
                    if yuv_input:
                        # The same actionable hint as Convert: a size mismatch
                        # almost always means Width/Height is not the source
                        # frame size (0.5.11 review M-1).
                        hint = yuv_size_mismatch_hint(
                            input_path,
                            self.input_yuv_type.currentText(),
                            out_w, out_h,
                        )
                        if hint:
                            detail = f"{detail}\n\n{hint}"
                    self._file_table.item(row, 2).setText(f"Failed: {detail}")
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
            # The sticky choice dies with this batch: the next Start Batch
            # Convert must ask again (0.5.11 review L-2).
            self._operation_collision_choice = None

        # Report
        total = len(files)
        summary = f"Batch complete: {success_count}/{total} succeeded"
        if fail_count > 0:
            summary += f", {fail_count} failed"
        QMessageBox.information(self, "Batch Convert", summary)

    def _on_cancel_batch(self) -> None:
        self._batch_cancelled = True

    # ── P1-6 覆盖确认 ───────────────────────────────────────────────────

    def _resolve_collision(self, output_path: str) -> str | None:
        """Resolve one target, asking the same checkbox prompt as Convert.

        The first conflicting file asks Skip / Rename (_1) / Overwrite plus
        "Do this for all remaining files".  Ticking that box applies the same
        choice to the rest of *this* batch without asking again; the choice is
        cleared when the batch ends, so the next run asks afresh.  The default
        is Rename (_1), i.e. never silently overwrite.
        """
        if not os.path.exists(output_path):
            return output_path
        choice = self._operation_collision_choice
        if choice is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Output file exists")
            box.setText(
                f"Output already exists:\n{output_path}\n\n"
                "How do you want to continue?"
            )
            skip_b = box.addButton("Skip", QMessageBox.RejectRole)
            rename_b = box.addButton("Rename (_1)", QMessageBox.ActionRole)
            ovw_b = box.addButton("Overwrite", QMessageBox.AcceptRole)
            box.setDefaultButton(rename_b)
            sticky = QCheckBox("Do this for all remaining files", box)
            box.setCheckBox(sticky)
            # A per-file prompt can appear after the progress dialog has already
            # shown (first file took >300 ms).  Hide it while the question is up
            # so the user does not read "Batch conversion in progress..." behind
            # a question that is actually waiting for them (0.5.11.1 review L-1).
            progress = self._progress
            progress_was_visible = progress is not None and progress.isVisible()
            if progress is not None:
                progress.hide()
            try:
                box.exec_()
            finally:
                if progress is not None and progress_was_visible:
                    progress.show()
            clicked = box.clickedButton()
            if clicked is skip_b:
                choice = "skip"
            elif clicked is ovw_b:
                choice = "overwrite"
            else:
                choice = "rename"
            if sticky.isChecked():
                self._operation_collision_choice = choice
        if choice == "skip":
            return None
        return resolve_output_path_collision(output_path, on_existing=choice)
