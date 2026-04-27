"""Qt GUI for RAW/YUV viewer and converter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QImage, QKeySequence, QPixmap
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
    QMenu,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QStyle,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

BAYER_PATTERNS = ["RGGB", "GRBG", "GBRG", "BGGR"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}
MAX_RECENT_FILES = 10
UI_THEMES = {"light", "dark"}
THEME_PALETTES = {
    "light": {
        "main_bg": "#F8FAFC",
        "text_color": "#1E293B",
        "panel_bg": "#FFFFFF",
        "border_color": "#E2E8F0",
        "input_bg": "#FFFFFF",
    },
    "dark": {
        "main_bg": "#0F172A",
        "text_color": "#E2E8F0",
        "panel_bg": "#111827",
        "border_color": "#334155",
        "input_bg": "#1F2937",
    },
}


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


@dataclass
class ViewerItem:
    """State container for one opened file tab and its decode/view configuration."""

    options: DecodeOptions = field(default_factory=DecodeOptions)
    current_display: np.ndarray | None = None
    view: "ImageView | None" = None
    zoom_percent: int = 100


def normalize_recent_files(value: object, max_items: int = MAX_RECENT_FILES) -> list[str]:
    """Normalize recent-file values by trimming, deduplicating, and enforcing max length."""

    if value is None:
        return []
    if isinstance(value, str):
        files = [value]
    elif isinstance(value, (list, tuple)):
        files = [str(path) for path in value if str(path).strip()]
    else:
        files = []
    normalized: list[str] = []
    for path in files:
        trimmed = path.strip()
        if trimmed and trimmed not in normalized:
            normalized.append(trimmed)
        if len(normalized) >= max(1, max_items):
            break
    return normalized


def add_recent_file_entry(existing: object, path: str, max_items: int = MAX_RECENT_FILES) -> list[str]:
    """Insert one path at the front of recent files while keeping uniqueness and limits."""

    trimmed = path.strip()
    if not trimmed:
        return normalize_recent_files(existing, max_items)
    result = [trimmed]
    for item in normalize_recent_files(existing, max_items):
        if item != trimmed:
            result.append(item)
        if len(result) >= max(1, max_items):
            break
    return result


def normalize_ui_theme(theme: object) -> str:
    """Normalize UI theme key to one of supported values."""

    if theme is None:
        return "light"
    normalized = str(theme).strip().lower()
    return normalized if normalized in UI_THEMES else "light"


def build_ui_stylesheet(theme: str, font_size: int) -> str:
    normalized_theme = normalize_ui_theme(theme)
    palette = THEME_PALETTES[normalized_theme]

    return f"""
        QMainWindow {{
            background-color: {palette["main_bg"]};
            color: {palette["text_color"]};
        }}
        QWidget {{
            font-size: {font_size}px;
            color: {palette["text_color"]};
        }}
        #controlPanel {{
            background: {palette["panel_bg"]};
            border: 1px solid {palette["border_color"]};
            border-radius: 8px;
        }}
        QTabWidget::pane {{
            border: 1px solid {palette["border_color"]};
            background: {palette["panel_bg"]};
            border-radius: 8px;
        }}
        QComboBox, QSpinBox, QLineEdit {{
            border: 1px solid {palette["border_color"]};
            border-radius: 6px;
            padding: 6px 8px;
            background: {palette["input_bg"]};
            color: {palette["text_color"]};
        }}
        QPushButton {{
            border-radius: 6px;
            padding: 8px 14px;
            background: #2563EB;
            color: white;
            border: none;
        }}
        QPushButton:hover {{
            background: #1D4ED8;
        }}
    """


class AppSettings:
    def __init__(self) -> None:
        self._store = QSettings("yorelll", "raw-view")

    @staticmethod
    def _normalize_dirname(value: str | None) -> str:
        return (value or "out").strip() or "out"

    @property
    def default_output_dirname(self) -> str:
        return self._normalize_dirname(self._store.value("convert/default_output_dirname", "out"))

    @default_output_dirname.setter
    def default_output_dirname(self, value: str) -> None:
        self._store.setValue("convert/default_output_dirname", self._normalize_dirname(value))

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

    @property
    def ui_font_size(self) -> int:
        value = self._store.value("ui/font_size", 13)
        try:
            return max(10, min(24, int(value)))
        except (TypeError, ValueError):
            return 13

    @ui_font_size.setter
    def ui_font_size(self, value: int) -> None:
        self._store.setValue("ui/font_size", max(10, min(24, int(value))))

    @property
    def ui_theme(self) -> str:
        return normalize_ui_theme(self._store.value("ui/theme", "light"))

    @ui_theme.setter
    def ui_theme(self, value: str) -> None:
        self._store.setValue("ui/theme", normalize_ui_theme(value))

    @property
    def recent_files(self) -> list[str]:
        return normalize_recent_files(self._store.value("recent/files", []), MAX_RECENT_FILES)

    def add_recent_file(self, path: str) -> None:
        self._store.setValue("recent/files", add_recent_file_entry(self.recent_files, path, MAX_RECENT_FILES))

    def clear_recent_files(self) -> None:
        self._store.setValue("recent/files", [])


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
    def dragEnterEvent(self, event: QDragEnterEvent):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    # Qt override must keep camelCase method name.
    def dropEvent(self, event: QDropEvent):  # noqa: N802
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
    zoomChanged = pyqtSignal(int)
    contextMenuRequested = pyqtSignal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._pixmap_item = QGraphicsPixmapItem()
        self.scene().addItem(self._pixmap_item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._zoom_percent = 100
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap_item.setPixmap(pixmap)
        self.resetTransform()
        self._zoom_percent = 100
        self.zoomChanged.emit(self._zoom_percent)
        self.setSceneRect(self._pixmap_item.boundingRect())

    def zoom_in(self) -> None:
        self._apply_zoom_step(1.25)

    def zoom_out(self) -> None:
        self._apply_zoom_step(0.8)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom_percent = 100
        self.zoomChanged.emit(self._zoom_percent)

    def fit_image(self) -> None:
        if self.sceneRect().isNull():
            return
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self._zoom_percent = max(1, int(round(self.transform().m11() * 100)))
        self.zoomChanged.emit(self._zoom_percent)

    def has_image(self) -> bool:
        """Return whether the view currently contains a non-empty pixmap."""

        return not self._pixmap_item.pixmap().isNull()

    def current_pixmap(self) -> QPixmap:
        """Return the pixmap currently displayed in the view."""

        return self._pixmap_item.pixmap()

    def _apply_zoom_step(self, factor: float) -> None:
        self.scale(factor, factor)
        self._zoom_percent = max(1, int(round(self.transform().m11() * 100)))
        self.zoomChanged.emit(self._zoom_percent)

    # Qt override must keep camelCase method name to hook wheel events.
    def wheelEvent(self, event):  # noqa: N802
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            self._apply_zoom_step(1.25 if event.angleDelta().y() > 0 else 0.8)
            return
        super().wheelEvent(event)

    # Qt override must keep camelCase method name.
    def contextMenuEvent(self, event):  # noqa: N802
        self.contextMenuRequested.emit(self, event.globalPos())


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")

        self.output_dir_edit = QLineEdit(settings.default_output_dirname)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 2400)
        self.dpi_spin.setValue(settings.save_dpi)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(10, 24)
        self.font_size_spin.setValue(settings.ui_font_size)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        selected_theme_index = self.theme_combo.findData(settings.ui_theme)
        if selected_theme_index >= 0:
            self.theme_combo.setCurrentIndex(selected_theme_index)

        form = QFormLayout()
        form.addRow("Default convert output folder", self.output_dir_edit)
        form.addRow("Saved image DPI", self.dpi_spin)
        form.addRow("UI font size", self.font_size_spin)
        form.addRow("UI theme", self.theme_combo)

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
        self._settings.ui_font_size = self.font_size_spin.value()
        self._settings.ui_theme = str(self.theme_combo.currentData())
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
        except Exception as exc:  # pragma: no cover - UI path
            QMessageBox.critical(self, "Convert Failed", str(exc))
            return
        QMessageBox.information(self, "Convert", "Conversion completed")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RAW/YUV Viewer")
        self.options = DecodeOptions()
        self.raw_formats = ["RAW8", "RAW10", "RAW10 Packed", "RAW12", "RAW12 Packed", "RAW14 Packed", "RAW16", "RAW32"]
        self.yuv_formats = ["I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "NV16"]
        self.settings = AppSettings()
        self.items: list[ViewerItem] = []
        self._active_item_index = -1
        self._loading_item = False
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self) -> None:
        self._apply_theme()
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.file_status = QLabel("File: -")
        self.image_status = QLabel("Image: -")
        self.zoom_status = QLabel("Zoom: 100%")
        self.state_status = QLabel("Ready")
        self.status.addPermanentWidget(self.file_status, 2)
        self.status.addPermanentWidget(self.image_status, 2)
        self.status.addPermanentWidget(self.zoom_status, 1)
        self.status.addPermanentWidget(self.state_status, 1)
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
        panel.setObjectName("controlPanel")
        self.item_tabs = QTabWidget()
        self.item_tabs.setTabsClosable(True)
        self.item_tabs.tabCloseRequested.connect(self.close_item)
        self.item_tabs.currentChanged.connect(self._on_tab_changed)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(panel)
        layout.addWidget(self.item_tabs, 1)
        self.setCentralWidget(root)

        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        view_menu = menu.addMenu("View")
        tools_menu = menu.addMenu("Tools")
        help_menu = menu.addMenu("Help")

        open_action = QAction("Open...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.setStatusTip("Open one or more files")
        open_action.triggered.connect(self.open_file)
        save_action = QAction("Save As...", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.setStatusTip("Save current image")
        save_action.triggered.connect(self.save_display)
        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)
        close_item_action = QAction("Close Item", self)
        close_item_action.setShortcut(QKeySequence.Close)
        close_item_action.triggered.connect(self.close_current_item)
        self.recent_menu = QMenu("Recent Files", self)
        clear_recent_action = QAction("Clear Recent Files", self)
        clear_recent_action.triggered.connect(self._clear_recent_files)

        file_menu.addActions([open_action, save_action, close_item_action])
        file_menu.addSeparator()
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(clear_recent_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        self._refresh_recent_files_menu()

        zoom_in = QAction("Zoom In", self)
        zoom_in.setShortcut(QKeySequence.ZoomIn)
        zoom_in.triggered.connect(self._zoom_in_current)
        zoom_out = QAction("Zoom Out", self)
        zoom_out.setShortcut(QKeySequence.ZoomOut)
        zoom_out.triggered.connect(self._zoom_out_current)
        fit = QAction("Fit to Window", self)
        fit.setShortcut("Ctrl+0")
        fit.triggered.connect(self._fit_image)
        reset_zoom = QAction("Reset Zoom", self)
        reset_zoom.triggered.connect(self._reset_zoom_current)
        view_menu.addActions([zoom_in, zoom_out, fit])
        view_menu.addAction(reset_zoom)

        convert = QAction("Convert Image...", self)
        convert.triggered.connect(self.open_convert_dialog)
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        tools_menu.addActions([convert, settings_action])

        fmt_help = QAction("Format Help", self)
        fmt_help.triggered.connect(self.show_help)
        help_menu.addAction(fmt_help)

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        for action, icon_type in [
            (open_action, QStyle.SP_DialogOpenButton),
            (save_action, QStyle.SP_DialogSaveButton),
            (convert, QStyle.SP_ArrowRight),
            (settings_action, QStyle.SP_FileDialogDetailedView),
            (fmt_help, QStyle.SP_MessageBoxQuestion),
        ]:
            action.setIcon(self.style().standardIcon(icon_type))
            toolbar.addAction(action)

        self._on_type_changed(self.type_combo.currentText())
        self._sync_panel_enabled_state()

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

    def _set_file_path(self, item: ViewerItem, path: str) -> None:
        item.options.file_path = path
        ext = Path(path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            self.type_combo.setCurrentText("Standard Image")
            item.options.image_type = "Standard Image"
        elif ext == ".yuv":
            self.type_combo.setCurrentText("YUV")
            item.options.image_type = "YUV"
        else:
            item.options.image_type = "RAW"
        self.settings.add_recent_file(path)
        self._refresh_recent_files_menu()
        self.file_status.setText(f"File: {os.path.basename(path)} ({os.path.getsize(path)} bytes)")
        self.state_status.setText("Opened")

    def _fit_image(self) -> None:
        item = self._current_item()
        if item is None or item.view is None:
            return
        item.view.fit_image()

    # Qt override must keep camelCase method name.
    def dragEnterEvent(self, event: QDragEnterEvent):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    # Qt override must keep camelCase method name.
    def dropEvent(self, event: QDropEvent):  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path and os.path.isfile(path):
            self._open_item(path, decode=True)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def open_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open",
            "",
            "RAW/YUV/Image (*.raw *.bin *.yuv *.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if not paths:
            return
        for path in paths:
            self._open_item(path, decode=False)
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
        item = self._current_item()
        if item is None:
            return
        path = item.options.file_path
        if not path:
            return
        self._save_controls_to_item(item)
        opts = item.options
        self.options = DecodeOptions(
            file_path=opts.file_path,
            image_type=opts.image_type,
            format_name=opts.format_name,
            width=opts.width,
            height=opts.height,
            alignment=opts.alignment,
            endianness=opts.endianness,
            offset=opts.offset,
        )
        spec = ImageSpec(opts.width, opts.height, opts.offset)
        with open(path, "rb") as f:
            data = f.read()

        try:
            if opts.image_type == "RAW":
                expected = expected_frame_size_raw(opts.format_name, spec.width, spec.height)
                if not self._warn_size_mismatch(len(data) - spec.offset, expected):
                    return
                raw = decode_raw(data, spec, opts.format_name, opts.alignment, opts.endianness)
                raw8 = raw_to_display_gray(raw, opts.format_name)
                if self.raw_preview_combo.currentText().startswith("Bayer"):
                    try:
                        rgb = bayer8_to_rgb(raw8, pattern=self.bayer_pattern_combo.currentText())
                    except ValueError as exc:
                        self.state_status.setText(f"Bayer preview failed: {exc}")
                        qimg = self._qimage_from_gray(raw8)
                        item.current_display = raw8
                    else:
                        qimg = self._qimage_from_rgb(rgb)
                        item.current_display = rgb
                else:
                    qimg = self._qimage_from_gray(raw8)
                    item.current_display = raw8
            elif opts.image_type == "YUV":
                expected = expected_frame_size_yuv(opts.format_name, spec.width, spec.height)
                if not self._warn_size_mismatch(len(data) - spec.offset, expected):
                    return
                rgb = decode_yuv(data, spec, opts.format_name)
                qimg = self._qimage_from_rgb(rgb)
                item.current_display = rgb
            else:
                from .converter import load_bgr_image

                bgr = load_bgr_image(path)
                rgb = bgr[:, :, ::-1]
                qimg = self._qimage_from_rgb(rgb)
                item.current_display = rgb
            item.view.set_pixmap(QPixmap.fromImage(qimg))
            self._fit_image()
            self.file_status.setText(f"File: {os.path.basename(path)} ({len(data)} bytes)")
            self.image_status.setText(f"Image: {opts.width}x{opts.height} | Format: {opts.format_name}")
            self.state_status.setText("Decoded")
        except Exception as exc:
            QMessageBox.critical(self, "Decode Failed", str(exc))
            self.state_status.setText("Decode failed")

    def save_display(self) -> None:
        item = self._current_item()
        if item is None or item.current_display is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Image", "", "PNG (*.png);;JPEG (*.jpg *.jpeg)")
        if not path:
            return
        img = item.current_display
        if img.ndim == 2:
            qimg = self._qimage_from_gray(img)
        else:
            qimg = self._qimage_from_rgb(img)
        dpi = self.settings.save_dpi
        dpm = dpi_to_dots_per_meter(dpi)
        qimg.setDotsPerMeterX(dpm)
        qimg.setDotsPerMeterY(dpm)
        qimg.save(path)
        self.state_status.setText(f"Saved: {os.path.basename(path)} @ {dpi} DPI")

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
        if dlg.exec_():
            self._apply_theme()

    def _apply_theme(self) -> None:
        font_size = self.settings.ui_font_size
        selected_theme = self.settings.ui_theme
        self.setStyleSheet(build_ui_stylesheet(selected_theme, font_size))

    def _open_item(self, path: str, decode: bool) -> None:
        if not path or not os.path.isfile(path):
            return
        item = ViewerItem()
        item.view = ImageView()
        item.view.zoomChanged.connect(lambda zoom: self._on_item_zoom_changed(item, zoom))
        item.view.contextMenuRequested.connect(self._show_image_context_menu)
        self._set_file_path(item, path)
        item.options.format_name = self.raw_formats[0]
        ext = Path(path).suffix.lower()
        if ext == ".yuv":
            item.options.format_name = self.yuv_formats[0]
        elif ext in IMAGE_EXTENSIONS:
            item.options.format_name = "N/A"
        self.items.append(item)
        index = self.item_tabs.addTab(item.view, os.path.basename(path))
        self.item_tabs.setCurrentIndex(index)
        self._sync_panel_enabled_state()
        if decode:
            self.decode_current()

    def _on_tab_changed(self, index: int) -> None:
        if self._loading_item:
            return
        if 0 <= self._active_item_index < len(self.items):
            self._save_controls_to_item(self.items[self._active_item_index])
        self._active_item_index = index
        if 0 <= index < len(self.items):
            self._load_item_to_controls(self.items[index])
            self._sync_status_from_item(self.items[index])
        self._sync_panel_enabled_state()

    def _current_item(self) -> ViewerItem | None:
        index = self.item_tabs.currentIndex()
        if 0 <= index < len(self.items):
            return self.items[index]
        return None

    def close_current_item(self) -> None:
        index = self.item_tabs.currentIndex()
        if index >= 0:
            self.close_item(index)

    def close_item(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        self._loading_item = True
        self.item_tabs.removeTab(index)
        self.items.pop(index)
        self._loading_item = False
        if not self.items:
            self._active_item_index = -1
            self.file_status.setText("File: -")
            self.image_status.setText("Image: -")
            self.zoom_status.setText("Zoom: 100%")
            self.state_status.setText("No item")
        else:
            self._on_tab_changed(self.item_tabs.currentIndex())
        self._sync_panel_enabled_state()

    def _save_controls_to_item(self, item: ViewerItem) -> None:
        if item is None:
            return
        item.options.image_type = self.type_combo.currentText()
        item.options.format_name = self.format_combo.currentText()
        item.options.width = self.width_spin.value()
        item.options.height = self.height_spin.value()
        item.options.alignment = self.align_combo.currentText()
        item.options.endianness = self.endian_combo.currentText()
        item.options.offset = self.offset_spin.value()

    def _load_item_to_controls(self, item: ViewerItem) -> None:
        self._loading_item = True
        self.type_combo.setCurrentText(item.options.image_type)
        self._on_type_changed(item.options.image_type)
        if item.options.format_name:
            idx = self.format_combo.findText(item.options.format_name)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
        self.width_spin.setValue(item.options.width)
        self.height_spin.setValue(item.options.height)
        self.align_combo.setCurrentText(item.options.alignment)
        self.endian_combo.setCurrentText(item.options.endianness)
        self.offset_spin.setValue(item.options.offset)
        self.zoom_status.setText(f"Zoom: {item.zoom_percent}%")
        self._loading_item = False

    def _sync_status_from_item(self, item: ViewerItem) -> None:
        path = item.options.file_path
        if path:
            self.file_status.setText(f"File: {os.path.basename(path)}")
        self.image_status.setText(f"Image: {item.options.width}x{item.options.height} | Format: {item.options.format_name}")
        self.zoom_status.setText(f"Zoom: {item.zoom_percent}%")

    def _zoom_in_current(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.zoom_in()

    def _zoom_out_current(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.zoom_out()

    def _reset_zoom_current(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.reset_zoom()

    def _on_item_zoom_changed(self, item: ViewerItem, zoom: int) -> None:
        item.zoom_percent = zoom
        if item is self._current_item():
            self.zoom_status.setText(f"Zoom: {zoom}%")

    def _show_image_context_menu(self, view: ImageView, pos) -> None:
        menu = QMenu(self)
        zoom_in = menu.addAction("Zoom In")
        zoom_out = menu.addAction("Zoom Out")
        fit = menu.addAction("Fit to Window")
        reset = menu.addAction("Reset Zoom")
        copy_action = menu.addAction("Copy Image")
        selected = menu.exec_(pos)
        if selected == zoom_in:
            view.zoom_in()
        elif selected == zoom_out:
            view.zoom_out()
        elif selected == fit:
            view.fit_image()
        elif selected == reset:
            view.reset_zoom()
        elif selected == copy_action and view.has_image():
            QApplication.clipboard().setPixmap(view.current_pixmap())

    def _refresh_recent_files_menu(self) -> None:
        self.recent_menu.clear()
        files = self.settings.recent_files
        if not files:
            empty = QAction("(No recent files)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path in files:
            action = QAction(path, self)
            action.triggered.connect(lambda _checked=False, p=path: self._open_recent_file(p))
            self.recent_menu.addAction(action)

    def _open_recent_file(self, path: str) -> None:
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Recent File", f"File not found:\n{path}")
            return
        self._open_item(path, decode=True)

    def _clear_recent_files(self) -> None:
        self.settings.clear_recent_files()
        self._refresh_recent_files_menu()

    def _sync_panel_enabled_state(self) -> None:
        has_item = self._current_item() is not None
        for widget in [
            self.type_combo,
            self.format_combo,
            self.align_combo,
            self.endian_combo,
            self.raw_preview_combo,
            self.bayer_pattern_combo,
            self.width_spin,
            self.height_spin,
            self.offset_spin,
            self.frame_spin,
            self.apply_btn,
        ]:
            widget.setEnabled(has_item)


def run() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1200, 700)
    w.show()
    app.exec_()
