"""Qt GUI for RAW/YUV viewer and converter."""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from .converter import image_file_to_raw, image_file_to_yuv
from .formats import (
    ImageSpec,
    RAW_BITS,
    decode_raw,
    decode_yuv,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    raw_to_display_gray,
)
from .help_content import HELP_HTML

from PyQt5.QtCore import Qt
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


class ConvertDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert Image")
        self.input_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.target_type = QComboBox()
        self.target_type.addItems(["RAW", "YUV"])
        self.raw_type = QComboBox()
        self.raw_type.addItems(["RAW8", "RAW10", "RAW12", "RAW10 Packed", "RAW12 Packed", "RAW14 Packed", "RAW16"])
        self.yuv_type = QComboBox()
        self.yuv_type.addItems(["I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "NV16"])
        self.align = QComboBox()
        self.align.addItems(["lsb", "msb"])
        self.width = QSpinBox()
        self.width.setRange(1, 65535)
        self.width.setValue(640)
        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(480)

        form = QFormLayout()
        form.addRow("Input image", self.input_edit)
        form.addRow("Output file", self.output_edit)
        form.addRow("Target", self.target_type)
        form.addRow("RAW type", self.raw_type)
        form.addRow("YUV format", self.yuv_type)
        form.addRow("Alignment", self.align)
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

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Input Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.input_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Output", "", "All Files (*.*)")
        if path:
            self.output_edit.setText(path)

    def _convert(self) -> None:
        try:
            if self.target_type.currentText() == "RAW":
                image_file_to_raw(
                    self.input_edit.text(),
                    self.output_edit.text(),
                    self.raw_type.currentText(),
                    self.width.value(),
                    self.height.value(),
                    alignment=self.align.currentText(),
                )
            else:
                image_file_to_yuv(
                    self.input_edit.text(),
                    self.output_edit.text(),
                    self.yuv_type.currentText(),
                    self.width.value(),
                    self.height.value(),
                )
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
        self._build_ui()

    def _build_ui(self) -> None:
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

        panel = QWidget()
        form = QFormLayout(panel)
        form.addRow("Type", self.type_combo)
        form.addRow("Format", self.format_combo)
        form.addRow("Alignment", self.align_combo)
        form.addRow("Endianness", self.endian_combo)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Offset", self.offset_spin)
        form.addRow("Frame Index", self.frame_spin)
        form.addRow("YUV Note", self.yuv_desc)
        form.addRow(self.apply_btn)

        root = QWidget()
        layout = QHBoxLayout(root)
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
        fit.triggered.connect(lambda: self.image_view.fitInView(self.image_view.sceneRect(), Qt.KeepAspectRatio))
        actual = QAction("Actual Size", self)
        actual.triggered.connect(lambda: self.image_view.resetTransform())
        view_menu.addActions([zoom_in, zoom_out, fit, actual])

        convert = QAction("Convert Image...", self)
        convert.triggered.connect(self.open_convert_dialog)
        tools_menu.addAction(convert)

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
            self.yuv_desc.setVisible(False)
        elif image_type == "YUV":
            self.format_combo.addItems(self.yuv_formats)
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.yuv_desc.setVisible(True)
        else:
            self.format_combo.addItems(["N/A"])
            self.align_combo.setEnabled(False)
            self.endian_combo.setEnabled(False)
            self.yuv_desc.setVisible(False)

    def _qimage_from_gray(self, gray: np.ndarray) -> QImage:
        h, w = gray.shape
        return QImage(gray.data, w, h, gray.strides[0], QImage.Format_Grayscale8).copy()

    def _qimage_from_rgb(self, rgb: np.ndarray) -> QImage:
        h, w = rgb.shape[:2]
        return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open",
            "",
            "RAW/YUV/Image (*.raw *.bin *.yuv *.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if not path:
            return
        self.options.file_path = path
        self.status.showMessage(f"Opened: {path} ({os.path.getsize(path)} bytes)")
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
                gray = raw_to_display_gray(raw, self.options.format_name)
                qimg = self._qimage_from_gray(gray)
                self.current_display = gray
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
        qimg.save(path)

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
        dlg = ConvertDialog(self)
        dlg.exec_()


def run() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1200, 700)
    w.show()
    app.exec_()
