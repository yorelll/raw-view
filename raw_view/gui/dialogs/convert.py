"""Image-to-RAW/YUV conversion dialog."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import (
    generate_image_variants,
    image_file_to_raw,
    image_file_to_yuv,
    load_bgr_image,
    plan_image_variants,
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
# 与查看端默认对齐）。仅影响对话框默认值，不改动既有 CLI 默认。
DEFAULT_CONVERT_WIDTH = 2560
DEFAULT_CONVERT_HEIGHT = 1440


def _human_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string (e.g. 14.5 MB)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class ConvertDialog(QDialog):
    """Modal dialog for converting standard images to RAW or YUV format."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Convert Image")
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.input_edit = FileDropLineEdit()
        self.input_edit.setPlaceholderText("Select an input image (PNG/JPG/BMP), or drag one here...")
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output path — auto-filled from the template if left blank")

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

        # P1-6：会话内"全部覆盖"开关（选中后本对话框不再逐个询问覆盖）。
        self._overwrite_all = False

        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(DEFAULT_CONVERT_HEIGHT)

        self._auto_output_path = ""

        # Help text for YUV formats
        self._yuv_note = QLabel(
            "YOnly: full-resolution grayscale (4:0:0); "
            "YUV420: U/V 2x2 downsample; YUV422: horizontal 2:1 downsample"
        )
        self._yuv_note.setWordWrap(True)
        self._yuv_note.setVisible(False)

        # ── Preview area ─────────────────────────────────────────────
        preview_group = QFrame()
        preview_group.setObjectName("card")
        preview_group.setFrameShape(QFrame.StyledPanel)
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)

        preview_title = QLabel("Preview")
        preview_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        preview_title.setAlignment(Qt.AlignLeft)

        preview_content = QHBoxLayout()
        self._preview_thumb = QLabel("Drop an image\nor click Browse")
        self._preview_thumb.setObjectName("previewThumb")
        self._preview_thumb.setFixedSize(160, 120)
        self._preview_thumb.setAlignment(Qt.AlignCenter)

        self._preview_info = QLabel(
            "Source: -\n"
            "Output size: -\n"
            "Frame size: -"
        )
        self._preview_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        preview_content.addWidget(self._preview_thumb)
        preview_content.addWidget(self._preview_info, 1)

        preview_layout.addWidget(preview_title)
        preview_layout.addLayout(preview_content)

        # ── Browse buttons live inline next to their fields ───────────
        # Match the field height so the inline buttons align cleanly.
        field_h = self.input_edit.sizeHint().height()
        btn_in = QPushButton("Browse...")
        btn_in.setObjectName("secondaryButton")
        btn_in.setFixedHeight(field_h)
        btn_out = QPushButton("Browse...")
        btn_out.setObjectName("secondaryButton")
        btn_out.setFixedHeight(field_h)
        btn_in.clicked.connect(self._browse_input)
        btn_out.clicked.connect(self._browse_output)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(btn_in, 0)
        input_row_w = QWidget()
        input_row_w.setLayout(input_row)

        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(6)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(btn_out, 0)
        output_row_w = QWidget()
        output_row_w.setLayout(output_row)

        # ── Layout construction ───────────────────────────────────────
        form = QFormLayout()
        form.addRow("Input image", input_row_w)
        form.addRow("Output file", output_row_w)
        form.addRow("Target", self.target_type)
        form.addRow("RAW type", self.raw_type)
        form.addRow("YUV format", self.yuv_type)
        # YOnly 位深（YOnly 时显示）；其后为位对齐/大小端（YOnly 多 bit 需要）
        form.addRow("Bit depth", self.bit_depth_combo)
        form.addRow("Alignment", self.align)
        form.addRow("Endianness", self.endian)
        form.addRow("RAW source", self.raw_source_mode)
        form.addRow("Bayer pattern", self.bayer_pattern)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("", self._yuv_note)

        # CONVERT is the primary action; disabled until an input is chosen.
        btn_run = QPushButton("Convert")
        btn_run.setObjectName("accentButton")
        btn_run.clicked.connect(self._convert)
        btn_run.setEnabled(bool(self.input_edit.text().strip()))
        self._btn_run = btn_run
        self.input_edit.textChanged.connect(
            lambda t: btn_run.setEnabled(bool(t.strip()))
        )

        # ── Scrollable content ────────────────────────────────────────
        # Wrap the form + preview + (optional) variant selector in a scroll
        # area so the dialog never grows past the screen. The action buttons
        # live outside the scroll area and stay pinned at the bottom.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addLayout(form)
        content_layout.addWidget(preview_group)

        # ── Multi-variant generator (opt-in via Settings) ────────────────
        self._variant_selector: VariantSelector | None = None
        btn_variants: QPushButton | None = None
        if self._settings.multi_variant_enabled:
            variant_group = QFrame()
            variant_group.setObjectName("card")
            variant_group.setFrameShape(QFrame.StyledPanel)
            variant_layout = QVBoxLayout(variant_group)
            variant_layout.setContentsMargins(8, 8, 8, 8)
            self._variant_selector = VariantSelector()
            variant_layout.addWidget(self._variant_selector)
            self.setMinimumWidth(620)
            content_layout.addWidget(variant_group)
            btn_variants = QPushButton("Generate Variants")
            btn_variants.setObjectName("secondaryButton")
            btn_variants.clicked.connect(self._generate_variants)
            self._btn_variants = btn_variants

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)

        # ── Pinned action buttons ─────────────────────────────────────
        row = QHBoxLayout()
        row.addStretch(1)
        if btn_variants is not None:
            row.addWidget(btn_variants)
        row.addWidget(btn_run)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addLayout(row)

        # Cap the dialog height to the available screen so the buttons never
        # get pushed off-screen, then size to the content within that cap.
        # NB: self.width / self.height are QSpinBox attributes on this dialog,
        # so QWidget.width()/height() are shadowed — use sizeHint() instead.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            max_h = int(screen.availableGeometry().height() * 0.9)
            self.setMaximumHeight(max_h)
            hint = self.sizeHint()
            self.resize(hint.width(), min(hint.height(), max_h))

        # Signals
        self.input_edit.fileDropped.connect(self._sync_default_output)
        self.input_edit.fileDropped.connect(self._update_preview)
        self.input_edit.textChanged.connect(self._sync_default_output)
        self.input_edit.textChanged.connect(self._update_preview)
        self.target_type.currentTextChanged.connect(self._sync_controls)
        self.raw_source_mode.currentTextChanged.connect(self._sync_controls)
        # 切换 YUV 格式（如 YUYV→YOnly）也要重跑条件显隐：YOnly 需显示位深/对齐/端序
        self.yuv_type.currentTextChanged.connect(self._sync_controls)
        self.target_type.currentTextChanged.connect(self._sync_default_output)
        self.target_type.currentTextChanged.connect(self._update_preview)
        self.width.valueChanged.connect(self._sync_default_output)
        self.width.valueChanged.connect(self._update_preview)
        self.height.valueChanged.connect(self._sync_default_output)
        self.height.valueChanged.connect(self._update_preview)
        self.raw_type.currentTextChanged.connect(self._update_preview)
        self.yuv_type.currentTextChanged.connect(self._update_preview)
        # YOnly 位深变化直接影响 YOnly<bit> 帧大小 → 预览帧大小需刷新
        self.bit_depth_combo.currentTextChanged.connect(self._update_preview)
        self.align.currentTextChanged.connect(self._update_preview)
        self.raw_source_mode.currentTextChanged.connect(self._update_preview)
        self.bayer_pattern.currentTextChanged.connect(self._update_preview)
        # Format-aware placeholders ({format}/{bayer}/{bits}/{packed}/...)
        # depend on these fields, so refresh the auto output path whenever
        # any of them changes.
        self.raw_type.currentTextChanged.connect(self._sync_default_output)
        self.yuv_type.currentTextChanged.connect(self._sync_default_output)
        # YOnly 位深决定 {format} 中的 YOnly<bit> 与 {bits} 占位 → 输出路径需刷新
        self.bit_depth_combo.currentTextChanged.connect(self._sync_default_output)
        self.align.currentTextChanged.connect(self._sync_default_output)
        self.raw_source_mode.currentTextChanged.connect(self._sync_default_output)
        self.bayer_pattern.currentTextChanged.connect(self._sync_default_output)
        self.output_edit.textEdited.connect(self._on_output_edited)

        self._sync_controls()

    # ── internal slots ───────────────────────────────────────────────

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Input Image", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            self.input_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Output", self.output_edit.text(), "All Files (*.*)"
        )
        if path:
            self.output_edit.setText(path)

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
        self._yuv_note.setVisible(not is_raw)
        # 隐藏的控件同时禁用，避免焦点/键盘可达。注意不能从 isVisible() 反推：
        # 对话框未 show 时 isVisible() 恒为 False，会导致可见控件也被禁用
        # （0.2.2 review 发现：初启 Convert 对话框所有高级控件全灰）。
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

    def _resolve_yuv_fmt(self) -> str:
        """把 UI 的 YOnly + Bit depth 映射为内部有效格式名（YOnly8/10/12/14/16）。

        worker/converter 按 YOnly<bit> 工作；普通 YUV 原样返回。
        """
        return ControlPanel._yonly_internal_name(
            self.yuv_type.currentText(), self.bit_depth_combo.currentText()
        )

    def _sync_default_output(self) -> None:
        input_path = self.input_edit.text().strip()
        if not input_path:
            return
        target_type = self.target_type.currentText()
        template = self._settings.output_template
        path = format_output_template(
            template,
            input_path,
            self.width.value(),
            self.height.value(),
            target_type,
            output_dir=self._settings.default_output_dirname,
            raw_type=self.raw_type.currentText(),
            yuv_type=self._resolve_yuv_fmt(),
            bayer_pattern=self.bayer_pattern.currentText(),
            source_mode=self.raw_source_mode.currentText(),
            alignment=self.align.currentText(),
            endianness=self.endian.currentText(),
        )
        current = self.output_edit.text().strip()
        if path and (not current or current == self._auto_output_path):
            self._auto_output_path = path
            self.output_edit.setText(path)

    def _on_output_edited(self) -> None:
        self._auto_output_path = ""

    # ── P1-6 覆盖确认 ───────────────────────────────────────────────────

    def _confirm_output_collision(self, output_path: str) -> str | None:
        """目标已存在时询问用户：跳过 / 覆盖 / 全部覆盖 / 自动重命名。

        返回最终写入路径；None 表示用户选择跳过，调用方应中止本次写出。
        """
        if not os.path.exists(output_path):
            return output_path
        if getattr(self, "_overwrite_all", False):
            return output_path
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Output file exists")
        box.setText(
            f"Output already exists:\n{output_path}\n\n"
            "How do you want to continue?"
        )
        skip_b = box.addButton("Skip", QMessageBox.RejectRole)
        keep_b = box.addButton("Rename (_1)", QMessageBox.ActionRole)
        ovw_b = box.addButton("Overwrite", QMessageBox.AcceptRole)
        ovw_all_b = box.addButton("Overwrite All", QMessageBox.AcceptRole)
        box.setDefaultButton(ovw_b)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is skip_b:
            return None
        if clicked is ovw_all_b:
            self._overwrite_all = True
            return output_path
        if clicked is keep_b:
            return resolve_output_path_collision(output_path, on_existing="rename")
        return output_path

    def _set_convert_busy(self, busy: bool) -> None:
        """统一转换按钮的 busy 禁用/文案状态。"""
        self._btn_run.setEnabled(not busy and bool(self.input_edit.text().strip()))
        wanted = "Convert" if not busy else "Converting…"
        if self._btn_run.text() != wanted:
            self._btn_run.setText(wanted)

    def _update_preview(self) -> None:
        """Update the thumbnail preview and info labels."""
        import cv2

        input_path = self.input_edit.text().strip()
        if not input_path or not Path(input_path).is_file():
            self._preview_thumb.setText("Drop an image\nor click Browse")
            self._preview_info.setText("Source: -\nOutput size: -\nFrame size: -")
            return

        try:
            bgr = load_bgr_image(input_path)
            src_h, src_w = bgr.shape[:2]

            # Build thumbnail
            max_thumb_w, max_thumb_h = 158, 118
            scale = min(max_thumb_w / src_w, max_thumb_h / src_h, 1.0)
            thumb_w = max(1, int(src_w * scale))
            thumb_h = max(1, int(src_h * scale))
            if scale < 1.0:
                thumb = cv2.resize(bgr, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
            else:
                thumb = bgr
            rgb_thumb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_thumb.shape
            bytes_per_line = w * ch
            qimg = QImage(rgb_thumb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            self._preview_thumb.setPixmap(QPixmap.fromImage(qimg.copy()))

            # Frame size info
            target_type = self.target_type.currentText()
            out_w = self.width.value()
            out_h = self.height.value()
            if target_type == "RAW":
                fmt = self.raw_type.currentText()
                try:
                    fsize = expected_frame_size_raw(fmt, out_w, out_h)
                except Exception:
                    fsize = 0
            else:
                fmt = self._resolve_yuv_fmt()
                try:
                    # alignment/endianness 对 YOnly 多 bit 决定 16-bit 存储帧大小
                    fsize = expected_frame_size_yuv(
                        fmt, out_w, out_h,
                        alignment=self.align.currentText(),
                        endianness="little",  # 预览仅估算大小，端序不影响字节数
                    )
                except Exception:
                    fsize = 0

            source_info = f"Source: {Path(input_path).name} ({src_w}x{src_h})"
            output_info = f"Output size: {out_w}x{out_h}"
            frame_info = f"Frame size: {fsize}" if fsize > 0 else "Frame size: -"
            self._preview_info.setText(f"{source_info}\n{output_info}\n{frame_info}")
        except Exception:
            self._preview_thumb.setText("(preview unavailable)")
            self._preview_info.setText("Source: -\nOutput size: -\nFrame size: -")

    def _convert(self) -> None:
        # Busy feedback: disable the button and show progress text so the user
        # knows work is happening (conversion is synchronous).
        self._set_convert_busy(True)
        QApplication.processEvents()
        try:
            self._do_convert()
        finally:
            self._set_convert_busy(False)

    def _do_convert(self) -> None:
        try:
            input_path = self.input_edit.text().strip()
            target_type = self.target_type.currentText()
            output_path = self.output_edit.text().strip()
            if not output_path:
                template = self._settings.output_template
                output_path = format_output_template(
                    template,
                    input_path,
                    self.width.value(),
                    self.height.value(),
                    target_type,
                    output_dir=self._settings.default_output_dirname,
                    raw_type=self.raw_type.currentText(),
                    yuv_type=self._resolve_yuv_fmt(),
                    bayer_pattern=self.bayer_pattern.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                )
            if not input_path:
                raise ValueError("input path is required")
            if not output_path:
                raise ValueError("output path is required")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            # P1-6 覆盖确认：目标已存在时弹"跳过/覆盖/全部覆盖/重命名"。
            # 返回 None 表示用户选择跳过 → 直接结束本次转换。
            resolved = self._confirm_output_collision(output_path)
            if resolved is None:
                self._set_convert_busy(False)
                return
            output_path = resolved

            if self.target_type.currentText() == "RAW":
                image_file_to_raw(
                    input_path,
                    output_path,
                    self.raw_type.currentText(),
                    self.width.value(),
                    self.height.value(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    bayer_pattern=self.bayer_pattern.currentText(),
                )
            else:
                image_file_to_yuv(
                    input_path,
                    output_path,
                    self._resolve_yuv_fmt(),
                    self.width.value(),
                    self.height.value(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                )
            self.output_edit.setText(output_path)
            try:
                size = Path(output_path).stat().st_size
                size_str = f" ({_human_size(size)})"
            except OSError:
                size_str = ""
            QMessageBox.information(
                self, "Convert",
                f"\u2705 Converted:\n{output_path}{size_str}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Convert Failed", f"\u274c {exc}")

    def _generate_variants(self) -> None:
        """Generate every selected format/bayer/size combination from the input."""
        if self._variant_selector is None:
            return
        input_path = self.input_edit.text().strip()
        if not input_path or not Path(input_path).is_file():
            QMessageBox.warning(self, "Generate Variants", "Please choose a valid input image.")
            return
        formats = self._variant_selector.selected_formats()
        sizes = self._variant_selector.selected_sizes()
        bayer = self._variant_selector.selected_bayer()
        if not formats:
            QMessageBox.warning(self, "Generate Variants", "Select at least one format.")
            return
        if not sizes:
            QMessageBox.warning(self, "Generate Variants", "Select at least one size.")
            return
        source_mode = self.raw_source_mode.currentText()
        alignment = self.align.currentText()
        endianness = self.endian.currentText()
        plans = plan_image_variants(
            input_path, formats, sizes, bayer,
            source_mode=source_mode, alignment=alignment, endianness=endianness,
            output_dir=self._settings.default_output_dirname,
            template=self._settings.output_template,
        )
        self._btn_variants.setEnabled(False)
        self._btn_variants.setText(f"Generating 0/{len(plans)}…")
        QApplication.processEvents()

        written: list[str] = []
        done = {"n": 0}

        def _progress(_path: str) -> None:
            done["n"] += 1
            self._btn_variants.setText(f"Generating {done['n']}/{len(plans)}…")
            QApplication.processEvents()

        # P1-6\uff1a\u591a\u53d8\u4f53\u76ee\u6807\u5df2\u5b58\u5728\u65f6\u6309\u5df2\u9009\u7b56\u7565\u5904\u7406\u3002\u9ed8\u8ba4 "rename"\uff08\u4e0d\u8986\u76d6\u4e0d\u4e22
        # \u6587\u4ef6\uff09\uff1b\u7528\u6237\u5728\u5f53\u524d\u4f1a\u8bdd\u91cc\u9009\u4e86"\u5168\u90e8\u8986\u76d6"\u5219\u8986\u76d6\u3002
        collision_policy = "overwrite" if self._overwrite_all else "rename"
        rewritten: dict[str, str] = {}

        def _map_output(out: str) -> str:
            if out in rewritten:
                return rewritten[out]
            resolved = resolve_output_path_collision(out, on_existing=collision_policy)
            if resolved != out:
                # \u6a21\u677f\u7b97\u51fa\u7684\u65e7\u6587\u4ef6\u6240\u5728\u5e8f\u53f7\u53ef\u80fd\u53cd\u590d\u51fa\u73b0\u540c\u4e00\u540d\u5b57 \u2192 \u8bb0\u5fc6\u4e00\u6b21\u6620\u5c04
                rewritten[out] = resolved
            return resolved

        try:
            written = generate_image_variants(
                input_path, formats, sizes, bayer,
                source_mode=source_mode, alignment=alignment, endianness=endianness,
                output_dir=self._settings.default_output_dirname,
                template=self._settings.output_template,
                on_output=_progress,
                output_paths=_map_output,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Generate Variants Failed", f"\u274c {exc}")
            return
        finally:
            self._btn_variants.setText("Generate Variants")
            self._btn_variants.setEnabled(True)
        out_dir = str(Path(written[0]).parent) if written else "-"
        QMessageBox.information(
            self,
            "Generate Variants",
            f"\u2705 Generated {len(written)} file(s) from {len(plans)} planned variant(s).\n"
            f"Output folder:\n{out_dir}",
        )
