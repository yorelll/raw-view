"""Image-to-RAW/YUV conversion dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import image_file_to_raw, image_file_to_yuv, load_bgr_image
from raw_view.formats import expected_frame_size_raw, expected_frame_size_yuv
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    format_output_template,
)
from raw_view.gui.widgets import FileDropLineEdit


class ConvertDialog(QDialog):
    """Modal dialog for converting standard images to RAW or YUV format."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Convert Image")
        self.setMinimumWidth(520)

        self.input_edit = FileDropLineEdit()
        self.output_edit = QLineEdit()

        self.target_type = QComboBox()
        self.target_type.addItems(["RAW", "YUV"])

        self.raw_type = QComboBox()
        self.raw_type.addItems(
            ["RAW8", "RAW10", "RAW12", "RAW10 Packed", "RAW12 Packed", "RAW14 Packed", "RAW16"]
        )

        self.yuv_type = QComboBox()
        self.yuv_type.addItems(["I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "NV16"])

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
        preview_group.setFrameShape(QFrame.StyledPanel)
        preview_group.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 6px; }"
        )
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)

        preview_title = QLabel("Preview")
        preview_title.setStyleSheet("font-weight: bold; font-size: 12px; border: none;")
        preview_title.setAlignment(Qt.AlignLeft)

        preview_content = QHBoxLayout()
        self._preview_thumb = QLabel("(no image loaded)")
        self._preview_thumb.setFixedSize(160, 120)
        self._preview_thumb.setAlignment(Qt.AlignCenter)
        self._preview_thumb.setStyleSheet(
            "background: palette(window); border: 1px solid palette(mid); border-radius: 4px;"
        )

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

        # ── Layout construction ───────────────────────────────────────
        form = QFormLayout()
        form.addRow("Input image", self.input_edit)
        form.addRow("Output file", self.output_edit)
        form.addRow("Target", self.target_type)
        form.addRow("RAW type", self.raw_type)
        form.addRow("YUV format", self.yuv_type)
        form.addRow("Alignment", self.align)
        form.addRow("RAW source", self.raw_source_mode)
        form.addRow("Bayer pattern", self.bayer_pattern)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("", self._yuv_note)

        btn_in = QPushButton("Browse Input")
        btn_out = QPushButton("Browse Output")
        btn_run = QPushButton("Convert")
        btn_in.clicked.connect(self._browse_input)
        btn_out.clicked.connect(self._browse_output)
        btn_run.clicked.connect(self._convert)

        row = QHBoxLayout()
        row.addWidget(btn_in)
        row.addWidget(btn_out)
        row.addWidget(btn_run)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(preview_group)
        layout.addLayout(row)

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
            self._preview_thumb.setText("(no image loaded)")
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
            QMessageBox.information(self, "Convert", "Conversion completed")
        except Exception as exc:
            QMessageBox.critical(self, "Convert Failed", str(exc))
