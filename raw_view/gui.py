"""Qt GUI for RAW/YUV viewer and converter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .converter import bayer8_to_rgb, image_file_to_raw, image_file_to_yuv
from .formats import (
    ImageSpec,
    decode_raw,
    decode_yuv,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    raw_to_display_gray,
)
from .help_content import HELP_HTML

from PyQt5.QtCore import QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

BAYER_PATTERNS = ["RGGB", "GRBG", "GBRG", "BGGR"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}


@dataclass
class DecodeOptions:
    file_path: str = ""
    image_type: str = "RAW"
    format_name: str = "RAW8"
    width: int = 640
    height: int = 480
    alignment: str = "lsb"
    endianness: str = "little"
    offset: int = 0


class AppSettings:
    def __init__(self) -> None:
        self._store = QSettings("yorelll", "raw-view")

    @property
    def default_output_dirname(self) -> str:
        return (self._store.value("convert/default_output_dirname", "out") or "out").strip() or "out"

    @default_output_dirname.setter
    def default_output_dirname(self, value: str) -> None:
        clean = (value or "out").strip() or "out"
        self._store.setValue("convert/default_output_dirname", clean)

    @property
    def save_dpi(self) -> int:
        value = self._store.value("save/dpi", 300)
        try:
            return max(72, min(2400, int(value)))
        except (TypeError, ValueError):
            return 300

    @save_dpi.setter
    def save_dpi(self, value: int) -> None:
        self._store.setValue("save/dpi", max(72, min(2400, int(value))))


def build_default_output_path(input_path: str, target_type: str, output_dir_name: str) -> str:
    if not input_path:
        return ""
    src = Path(input_path)
    suffix = ".raw" if target_type == "RAW" else ".yuv"
    out_dir = src.parent / (output_dir_name or "out")
    return str(out_dir / f"{src.stem}{suffix}")


def dpi_to_dots_per_meter(dpi: int) -> int:
    return int(round(max(1, dpi) / 0.0254))


class FileDropLineEdit(QLineEdit):
    fileDropped = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    # Qt override must keep camelCase method name.
    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    # Qt override must keep camelCase method name.
    def dropEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.setText(path)
                self.fileDropped.emit(path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class ImageView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._pixmap_item = QGraphicsPixmapItem()
        self.scene().addItem(self._pixmap_item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap_item.setPixmap(pixmap)
        self.setSceneRect(self._pixmap_item.boundingRect())

    # Qt override must keep camelCase method name to hook wheel events.
    def wheelEvent(self, event):  # noqa: N802
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            scale = 1.25 if event.angleDelta().y() > 0 else 0.8
            self.scale(scale, scale)
            return
        super().wheelEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")

        self.output_dir_edit = QLineEdit(settings.default_output_dirname)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 2400)
        self.dpi_spin.setValue(settings.save_dpi)

        form = QFormLayout()
        form.addRow("Default convert output folder", self.output_dir_edit)
        form.addRow("Saved image DPI", self.dpi_spin)

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(row)

    def _save(self) -> None:
        self._settings.default_output_dirname = self.output_dir_edit.text()
        self._settings.save_dpi = self.dpi_spin.value()
        self.accept()


class ConvertDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Convert Image")
        self.input_edit = FileDropLineEdit()
        self.output_edit = QLineEdit()
        self.target_type = QComboBox()
        self.target_type.addItems(["RAW", "YUV"])
        self.raw_type = QComboBox()
        self.raw_type.addItems(["RAW8", "RAW10", "RAW12", "RAW10 Packed", "RAW12 Packed", "RAW14 Packed", "RAW16"])
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
        layout.addLayout(row)

        self.input_edit.fileDropped.connect(self._sync_default_output)
        self.input_edit.textChanged.connect(self._sync_default_output)
        self.target_type.currentTextChanged.connect(self._sync_controls)
        self.raw_source_mode.currentTextChanged.connect(self._sync_controls)
        self.target_type.currentTextChanged.connect(self._sync_default_output)
        self.output_edit.textEdited.connect(self._on_output_edited)
        self._sync_controls()

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Input Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.input_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Output", self.output_edit.text(), "All Files (*.*)")
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

    def _sync_default_output(self) -> None:
        path = build_default_output_path(
            self.input_edit.text().strip(),
            self.target_type.currentText(),
            self._settings.default_output_dirname,
        )
        current = self.output_edit.text().strip()
        if path and (not current or current == self._auto_output_path):
            self._auto_output_path = path
            self.output_edit.setText(path)

    def _on_output_edited(self) -> None:
        self._auto_output_path = ""

    def _convert(self) -> None:
        try:
            input_path = self.input_edit.text().strip()
            output_path = self.output_edit.text().strip() or build_default_output_path(
                input_path,
                self.target_type.currentText(),
                self._settings.default_output_dirname,
            )
            if not input_path or not output_path:
                raise ValueError("input/output path is required")
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
        except Exception as exc:  # pragma: no cover - UI path
            QMessageBox.critical(self, "Convert Failed", str(exc))
            return
        QMessageBox.information(self, "Convert", "Conversion completed")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RAW/YUV Viewer")
        self.options = DecodeOptions()
        self.current_display: np.ndarray | None = None
        self.raw_formats = ["RAW8", "RAW10", "RAW10 Packed", "RAW12", "RAW12 Packed", "RAW14 Packed", "RAW16", "RAW32"]
        self.yuv_formats = ["I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "NV16"]
        self.settings = AppSettings()
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QMainWindow { background-color: #f4f6fa; } "
            "QWidget { font-size: 13px; } "
            "QPushButton { padding: 6px 10px; }"
        )
        self.image_view = ImageView()
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["RAW", "YUV", "Standard Image"])
        self.format_combo = QComboBox()
        self.format_combo.addItems(self.raw_formats)
        self.align_combo = QComboBox()
        self.align_combo.addItems(["lsb", "msb"])
        self.endian_combo = QComboBox()
        self.endian_combo.addItems(["little", "big"])
        self.raw_preview_combo = QComboBox()
        self.raw_preview_combo.addItems(["Bayer Color", "Grayscale"])
        self.bayer_pattern_combo = QComboBox()
        self.bayer_pattern_combo.addItems(BAYER_PATTERNS)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 65535)
        self.width_spin.setValue(640)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 65535)
        self.height_spin.setValue(480)
        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(0, 1_000_000_000)
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 1_000_000)
        self.frame_spin.setEnabled(False)
        self.yuv_desc = QLabel("YUV420: U/V 2x2 downsample; YUV422: horizontal 2:1 downsample")
        self.yuv_desc.setWordWrap(True)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.decode_current)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        self.raw_preview_combo.currentTextChanged.connect(self._on_raw_preview_changed)

        panel = QWidget()
        panel.setMinimumWidth(320)
        form = QFormLayout(panel)
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
        form.addRow("Frame Index", self.frame_spin)
        form.addRow("YUV Note", self.yuv_desc)
        form.addRow(self.apply_btn)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(panel)
        layout.addWidget(self.image_view, 1)
        self.setCentralWidget(root)

        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        view_menu = menu.addMenu("View")
        tools_menu = menu.addMenu("Tools")
        help_menu = menu.addMenu("Help")

        open_action = QAction("Open...", self)
        open_action.triggered.connect(self.open_file)
        save_action = QAction("Save As...", self)
        save_action.triggered.connect(self.save_display)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)

        file_menu.addActions([open_action, save_action, exit_action])

        zoom_in = QAction("Zoom In", self)
        zoom_in.triggered.connect(lambda: self.image_view.scale(1.25, 1.25))
        zoom_out = QAction("Zoom Out", self)
        zoom_out.triggered.connect(lambda: self.image_view.scale(0.8, 0.8))
        fit = QAction("Fit to Window", self)
        fit.triggered.connect(self._fit_image)
        view_menu.addActions([zoom_in, zoom_out, fit])

        convert = QAction("Convert Image...", self)
        convert.triggered.connect(self.open_convert_dialog)
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        tools_menu.addActions([convert, settings_action])

        fmt_help = QAction("Format Help", self)
        fmt_help.triggered.connect(self.show_help)
        help_menu.addAction(fmt_help)
        self._on_type_changed(self.type_combo.currentText())

    def _on_type_changed(self, image_type: str) -> None:
        self.format_combo.clear()
        if image_type == "RAW":
            self.format_combo.addItems(self.raw_formats)
            self.align_combo.setEnabled(True)
            self.endian_combo.setEnabled(True)
            self.raw_preview_combo.setEnabled(True)
            self.bayer_pattern_combo.setEnabled(self.raw_preview_combo.currentText().startswith("Bayer"))
            self.yuv_desc.setVisible(False)
        elif image_type == "YUV":
            self.format_combo.addItems(self.yuv_formats)
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
            self.yuv_desc.setVisible(True)
        else:
            self.format_combo.addItems(["N/A"])
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.raw_preview_combo.setEnabled(False)
            self.bayer_pattern_combo.setEnabled(False)
            self.yuv_desc.setVisible(False)

    def _on_raw_preview_changed(self, value: str) -> None:
        self.bayer_pattern_combo.setEnabled(self.type_combo.currentText() == "RAW" and value.startswith("Bayer"))

    def _qimage_from_gray(self, gray: np.ndarray) -> QImage:
        h, w = gray.shape
        return QImage(gray.data, w, h, gray.strides[0], QImage.Format_Grayscale8).copy()

    def _qimage_from_rgb(self, rgb: np.ndarray) -> QImage:
        h, w = rgb.shape[:2]
        return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()

    def _set_file_path(self, path: str) -> None:
        self.options.file_path = path
        ext = Path(path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            self.type_combo.setCurrentText("Standard Image")
        elif ext == ".yuv":
            self.type_combo.setCurrentText("YUV")
        self.status.showMessage(f"Opened: {path} ({os.path.getsize(path)} bytes)")

    def _fit_image(self) -> None:
        if not self.image_view.sceneRect().isNull():
            self.image_view.fitInView(self.image_view.sceneRect(), Qt.KeepAspectRatio)

    # Qt override must keep camelCase method name.
    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    # Qt override must keep camelCase method name.
    def dropEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path and os.path.isfile(path):
            self._set_file_path(path)
            self.decode_current()
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open",
            "",
            "RAW/YUV/Image (*.raw *.bin *.yuv *.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if not path:
            return
        self._set_file_path(path)
        self.decode_current()

    def _warn_size_mismatch(self, actual: int, expected: int) -> bool:
        if actual == expected:
            return True
        r = QMessageBox.warning(
            self,
            "File size mismatch",
            f"File size={actual}, expected frame size={expected}. Parse first frame anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return r == QMessageBox.Yes

    def decode_current(self) -> None:
        path = self.options.file_path
        if not path:
            return
        self.options.image_type = self.type_combo.currentText()
        self.options.format_name = self.format_combo.currentText()
        self.options.width = self.width_spin.value()
        self.options.height = self.height_spin.value()
        self.options.alignment = self.align_combo.currentText()
        self.options.endianness = self.endian_combo.currentText()
        self.options.offset = self.offset_spin.value()

        spec = ImageSpec(self.options.width, self.options.height, self.options.offset)
        with open(path, "rb") as f:
            data = f.read()

        try:
            if self.options.image_type == "RAW":
                expected = expected_frame_size_raw(self.options.format_name, spec.width, spec.height)
                if not self._warn_size_mismatch(len(data) - spec.offset, expected):
                    return
                raw = decode_raw(data, spec, self.options.format_name, self.options.alignment, self.options.endianness)
                raw8 = raw_to_display_gray(raw, self.options.format_name)
                if self.raw_preview_combo.currentText().startswith("Bayer"):
                    try:
                        rgb = bayer8_to_rgb(raw8, pattern=self.bayer_pattern_combo.currentText())
                    except ValueError as exc:
                        self.status.showMessage(
                            f"Bayer preview failed; switched to grayscale (check Bayer pattern/size): {exc}"
                        )
                        qimg = self._qimage_from_gray(raw8)
                        self.current_display = raw8
                    else:
                        qimg = self._qimage_from_rgb(rgb)
                        self.current_display = rgb
                else:
                    qimg = self._qimage_from_gray(raw8)
                    self.current_display = raw8
            elif self.options.image_type == "YUV":
                expected = expected_frame_size_yuv(self.options.format_name, spec.width, spec.height)
                if not self._warn_size_mismatch(len(data) - spec.offset, expected):
                    return
                rgb = decode_yuv(data, spec, self.options.format_name)
                qimg = self._qimage_from_rgb(rgb)
                self.current_display = rgb
            else:
                from .converter import load_bgr_image

                bgr = load_bgr_image(path)
                rgb = bgr[:, :, ::-1]
                qimg = self._qimage_from_rgb(rgb)
                self.current_display = rgb
            self.image_view.set_pixmap(QPixmap.fromImage(qimg))
            self._fit_image()
            self.status.showMessage(
                f"{os.path.basename(path)} | {self.options.width}x{self.options.height} | format={self.options.format_name}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Decode Failed", str(exc))

    def save_display(self) -> None:
        if self.current_display is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Image", "", "PNG (*.png);;JPEG (*.jpg *.jpeg)")
        if not path:
            return
        img = self.current_display
        if img.ndim == 2:
            qimg = self._qimage_from_gray(img)
        else:
            qimg = self._qimage_from_rgb(img)
        dpi = self.settings.save_dpi
        dpm = dpi_to_dots_per_meter(dpi)
        qimg.setDotsPerMeterX(dpm)
        qimg.setDotsPerMeterY(dpm)
        qimg.save(path)
        self.status.showMessage(f"Saved image: {path} @ {dpi} DPI")

    def show_help(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Format Help")
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser)
        dlg.resize(760, 560)
        dlg.exec_()

    def open_convert_dialog(self) -> None:
        dlg = ConvertDialog(self.settings, self)
        dlg.exec_()

    def open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        dlg.exec_()


def run() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1200, 700)
    w.show()
    app.exec_()
