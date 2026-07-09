"""Image-to-RAW/YUV conversion dialog."""

from __future__ import annotations

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
)
from raw_view.formats import expected_frame_size_raw, expected_frame_size_yuv
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    format_output_template,
)
from raw_view.gui.widgets import FileDropLineEdit, VariantSelector


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
        self.raw_type.addItems(
            ["RAW8", "RAW10", "RAW12", "RAW10 Packed", "RAW12 Packed", "RAW14 Packed", "RAW16"]
        )

        self.yuv_type = QComboBox()
        self.yuv_type.addItems(
            ["I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "YVYU", "VYUY", "NV16", "NV61"]
        )

        self.align = QComboBox()
        self.align.addItems(["lsb", "msb"])

        self.raw_source_mode = QComboBox()
        self.raw_source_mode.addItems(["bayer", "gray"])

        self.bayer_pattern = QComboBox()
        self.bayer_pattern.addItems(BAYER_PATTERNS)

        self.width = QSpinBox()
        self.width.setRange(1, 65535)
        self.width.setValue(640)

        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(480)

        self._auto_output_path = ""

        # Help text for YUV formats
        self._yuv_note = QLabel(
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
        form.addRow("Alignment", self.align)
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
        self.target_type.currentTextChanged.connect(self._sync_default_output)
        self.target_type.currentTextChanged.connect(self._update_preview)
        self.width.valueChanged.connect(self._sync_default_output)
        self.width.valueChanged.connect(self._update_preview)
        self.height.valueChanged.connect(self._sync_default_output)
        self.height.valueChanged.connect(self._update_preview)
        self.raw_type.currentTextChanged.connect(self._update_preview)
        self.yuv_type.currentTextChanged.connect(self._update_preview)
        self.align.currentTextChanged.connect(self._update_preview)
        self.raw_source_mode.currentTextChanged.connect(self._update_preview)
        self.bayer_pattern.currentTextChanged.connect(self._update_preview)
        # Format-aware placeholders ({format}/{bayer}/{bits}/{packed}/...)
        # depend on these fields, so refresh the auto output path whenever
        # any of them changes.
        self.raw_type.currentTextChanged.connect(self._sync_default_output)
        self.yuv_type.currentTextChanged.connect(self._sync_default_output)
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
        self.raw_type.setEnabled(is_raw)
        self.align.setEnabled(is_raw)
        self.raw_source_mode.setEnabled(is_raw)
        self.bayer_pattern.setEnabled(is_raw and is_bayer)
        self.yuv_type.setEnabled(not is_raw)
        self._yuv_note.setVisible(not is_raw)

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
            yuv_type=self.yuv_type.currentText(),
            bayer_pattern=self.bayer_pattern.currentText(),
            source_mode=self.raw_source_mode.currentText(),
            alignment=self.align.currentText(),
        )
        current = self.output_edit.text().strip()
        if path and (not current or current == self._auto_output_path):
            self._auto_output_path = path
            self.output_edit.setText(path)

    def _on_output_edited(self) -> None:
        self._auto_output_path = ""

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
                fmt = self.yuv_type.currentText()
                try:
                    fsize = expected_frame_size_yuv(fmt, out_w, out_h)
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
        self._btn_run.setEnabled(False)
        self._btn_run.setText("Converting…")
        QApplication.processEvents()
        try:
            self._do_convert()
        finally:
            self._btn_run.setText("Convert")
            self._btn_run.setEnabled(bool(self.input_edit.text().strip()))

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
                    yuv_type=self.yuv_type.currentText(),
                    bayer_pattern=self.bayer_pattern.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    alignment=self.align.currentText(),
                )
            if not input_path:
                raise ValueError("input path is required")
            if not output_path:
                raise ValueError("output path is required")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            if self.target_type.currentText() == "RAW":
                image_file_to_raw(
                    input_path,
                    output_path,
                    self.raw_type.currentText(),
                    self.width.value(),
                    self.height.value(),
                    alignment=self.align.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    bayer_pattern=self.bayer_pattern.currentText(),
                )
            else:
                image_file_to_yuv(
                    input_path,
                    output_path,
                    self.yuv_type.currentText(),
                    self.width.value(),
                    self.height.value(),
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
        plans = plan_image_variants(
            input_path, formats, sizes, bayer,
            source_mode=source_mode, alignment=alignment,
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

        try:
            written = generate_image_variants(
                input_path, formats, sizes, bayer,
                source_mode=source_mode, alignment=alignment,
                output_dir=self._settings.default_output_dirname,
                template=self._settings.output_template,
                on_output=_progress,
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
