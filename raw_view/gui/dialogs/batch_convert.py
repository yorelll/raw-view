"""Batch image-to-RAW/YUV conversion dialog."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import Qt
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
from raw_view.formats import expected_frame_size_raw, expected_frame_size_yuv
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    format_output_template,
)
from raw_view.gui.panels import ControlPanel
from raw_view.gui.widgets import FileDropLineEdit, VariantSelector


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
    """Modal dialog for batch-converting multiple images to RAW or YUV format."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Batch Convert")
        self.setMinimumSize(700, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # ── Source file list (drag-drop or browse) ──
        self.input_edit = FileDropLineEdit()
        self.input_edit.setAccessibleName("Images to convert")
        self.input_edit.setAccessibleDescription(
            "Drop image files here or use Add Files to build the batch list."
        )
        self.input_edit.setPlaceholderText(
            "Drop files here or use Browse to add multiple images..."
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

        # ── Output parameters ──
        params_group = QFrame()
        params_group.setFrameShape(QFrame.StyledPanel)
        params_layout = QHBoxLayout(params_group)
        params_layout.setContentsMargins(8, 8, 8, 8)

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

        params_right = QVBoxLayout()
        params_right.addLayout(params_form)
        params_right.addWidget(self._same_dir_cb)
        params_right.addStretch(1)

        params_layout.addLayout(params_right, 1)

        # ── Progress ──
        self._progress = QProgressDialog("Batch conversion in progress...", "Cancel", 0, 100, self)
        self._progress.setWindowTitle("Batch Convert")
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(True)
        self._progress.setAutoReset(True)
        self._progress.setModal(True)
        self._progress.canceled.connect(self._on_cancel_batch)
        self._progress.hide()
        self._batch_cancelled = False
        # P1-6：会话内"全部覆盖"开关（选中后本次对话框不再问覆盖策略）。
        self._overwrite_all = False

        # ── Buttons ──
        self._run_btn = QPushButton("Start Batch Convert")
        self._run_btn.setObjectName("accentButton")
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip("Add at least one image before starting the batch conversion.")
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
        # 切换 YUV 格式（如 YUYV→YOnly）也要重跑条件显隐：YOnly 需显示位深/对齐/端序
        self.yuv_type.currentTextChanged.connect(self._sync_controls)

        self._sync_controls()

    # ── File management ────────────────────────────────────────────────

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if paths:
            self._add_files(paths)

    def _clear_files(self) -> None:
        self._file_table.setRowCount(0)
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip("Add at least one image before starting the batch conversion.")

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
            "Start converting the loaded images."
            if count > 0 else
            "Add at least one image before starting the batch conversion."
        )

    # ── Control sync ───────────────────────────────────────────────────

    def _resolve_yuv_fmt(self) -> str:
        """把 UI 的 YOnly + Bit depth 映射为内部有效格式名（YOnly8/10/12/14/16）。"""
        return ControlPanel._yonly_internal_name(
            self.yuv_type.currentText(), self.bit_depth_combo.currentText()
        )

    def _sync_controls(self) -> None:
        is_raw = self.target_type.currentText() == "RAW"
        is_bayer = self.raw_source_mode.currentText() == "bayer"
        # YOnly 是一个独立格式 + Bit depth 下拉：选它时显示位深/对齐/大小端
        is_yonly = self.yuv_type.currentText() == "YOnly"
        show_raw = is_raw
        show_bit = (not is_raw) and is_yonly
        show_align_endian = is_raw or show_bit
        self.raw_type.setVisible(show_raw)
        self.bit_depth_combo.setVisible(show_bit)
        self.align.setVisible(show_align_endian)
        self.endian.setVisible(show_align_endian)
        self.raw_source_mode.setVisible(is_raw)
        self.bayer_pattern.setVisible(is_raw and is_bayer)
        # 隐藏的控件同时禁用，避免焦点/键盘可达。注意不能从 isVisible() 反推：
        # 对话框未 show 时 isVisible() 恒为 False，会导致可见控件也被禁用。
        for w, shown in (
            (self.raw_type, show_raw),
            (self.bit_depth_combo, show_bit),
            (self.align, show_align_endian),
            (self.endian, show_align_endian),
            (self.raw_source_mode, is_raw),
            (self.bayer_pattern, is_raw and is_bayer),
        ):
            w.setEnabled(shown)
        self.yuv_type.setEnabled(not is_raw)

    # ── Batch conversion ───────────────────────────────────────────────

    def _run_batch(self) -> None:
        rows = self._file_table.rowCount()
        if rows == 0:
            QMessageBox.information(self, "Batch Convert", "No files to convert.")
            return

        target_type = self.target_type.currentText()
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
        self._progress.setMaximum(len(files))
        self._progress.setValue(0)
        self._progress.setLabelText("Batch conversion in progress...")
        self._progress.show()

        # P1-6 覆盖策略：默认 "rename"（不覆盖不丢文件）；用户选过"全部覆盖"
        # 则覆盖。先一次性询问所有冲突文件（会话内记住选择）。
        existing_paths = []
        for _row, input_path in files:
            out = format_output_template(
                template, input_path, out_w, out_h, target_type,
                output_dir=resolve_output_dir(
                    self._same_dir_cb.isChecked(), input_path,
                    self._settings.default_output_dirname,
                ),
                raw_type=self.raw_type.currentText(),
                yuv_type=self._resolve_yuv_fmt(),
                bayer_pattern=self.bayer_pattern.currentText(),
                source_mode=self.raw_source_mode.currentText(),
                alignment=self.align.currentText(),
                endianness=self.endian.currentText(),
            )
            if os.path.exists(out):
                existing_paths.append(out)
        self._collision_policy = self._ask_overwrite_strategy(existing_paths)

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
                if self._variant_selector is not None:
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
                    yuv_type=self._resolve_yuv_fmt(),
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
                    if target_type == "RAW":
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
            self._progress.close()

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
