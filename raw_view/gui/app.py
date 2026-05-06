"""Main application window — ties together panels, tabs, decode, and background workers."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from PyQt5.QtCore import QThread, Qt
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QHBoxLayout,
    QLabel,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import load_bgr_image
from raw_view.formats import (
    ImageSpec,
    expected_frame_size_raw,
    expected_frame_size_yuv,
)
from raw_view.models import (
    AppSettings,
    ACTION_ICON_COLOR,
    ACTION_ICON_DISABLED_COLOR,
    ACTION_ICON_NAMES,
    IMAGE_EXTENSIONS,
    DecodeOptions,
    ViewerItem,
    build_ui_stylesheet,
    dpi_to_dots_per_meter,
    load_qdarkstyle_stylesheet,
)
from raw_view.gui.framenav import FrameNavBar
from raw_view.gui.imageview import ImageView
from raw_view.gui.panels import ControlPanel
from raw_view.gui.dialogs import ConvertDialog, SettingsDialog, HelpDialog
from raw_view.gui.worker import DecodeWorker


class MainWindow(QMainWindow):
    """Main application window with control panel, tabbed image views, menus, and toolbar."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RAW/YUV Viewer")
        self.settings = AppSettings()
        self.items: list[ViewerItem] = []
        self._active_item_index = -1
        self._loading_item = False
        self._thread: QThread | None = None
        self._worker: DecodeWorker | None = None

        self.setAcceptDrops(True)
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._apply_theme()

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.file_status = QLabel("File: -")
        self.image_status = QLabel("Image: -")
        self.zoom_status = QLabel("Zoom: 100%")
        self.frame_status = QLabel("Frame: -")
        self.state_status = QLabel("Ready")
        self.status.addPermanentWidget(self.file_status, 2)
        self.status.addPermanentWidget(self.image_status, 2)
        self.status.addPermanentWidget(self.frame_status, 1)
        self.status.addPermanentWidget(self.zoom_status, 1)
        self.status.addPermanentWidget(self.state_status, 1)

        # Control panel
        self.panel = ControlPanel()
        self.panel.applyClicked.connect(self.decode_current)
        self.panel.typeChanged.connect(self._on_panel_type_changed)
        self.panel.rawPreviewChanged.connect(self._on_panel_raw_preview_changed)
        self.panel.zoomChanged.connect(self._on_panel_zoom_changed)

        # Tab widget
        self.item_tabs = QTabWidget()
        self.item_tabs.setTabsClosable(True)
        self.item_tabs.tabCloseRequested.connect(self.close_item)
        self.item_tabs.currentChanged.connect(self._on_tab_changed)

        # Central layout
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(self.panel)
        layout.addWidget(self.item_tabs, 1)
        self.setCentralWidget(root)

        self._build_menus()
        self._build_toolbar()
        self._refresh_recent_files_menu()
        self.panel.set_enabled(False)

    def _build_menus(self) -> None:
        menu = self.menuBar()

        # ── File ──
        file_menu = menu.addMenu("File")
        self.open_action = QAction("Open...", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self.open_file)

        self.save_action = QAction("Save As...", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(self.save_display)

        close_item_action = QAction("Close Item", self)
        close_item_action.setShortcut(QKeySequence.Close)
        close_item_action.triggered.connect(self.close_current_item)

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)

        self.recent_menu = QMenu("Recent Files", self)
        clear_recent_action = QAction("Clear Recent Files", self)
        clear_recent_action.triggered.connect(self._clear_recent_files)

        file_menu.addActions([self.open_action, self.save_action, close_item_action])
        file_menu.addSeparator()
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(clear_recent_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # ── Navigate ──
        nav_menu = menu.addMenu("Navigate")
        next_tab = QAction("Next Tab", self)
        next_tab.setShortcut("Ctrl+Tab")
        next_tab.triggered.connect(self._next_tab)
        prev_tab = QAction("Previous Tab", self)
        prev_tab.setShortcut("Ctrl+Shift+Tab")
        prev_tab.triggered.connect(self._prev_tab)
        nav_menu.addActions([next_tab, prev_tab])

        # ── View ──
        view_menu = menu.addMenu("View")
        zoom_in = QAction("Zoom In", self)
        zoom_in.setShortcut(QKeySequence.ZoomIn)
        zoom_in.triggered.connect(self._zoom_in_current)
        zoom_out = QAction("Zoom Out", self)
        zoom_out.setShortcut(QKeySequence.ZoomOut)
        zoom_out.triggered.connect(self._zoom_out_current)
        fit = QAction("Fit to Window", self)
        fit.setShortcut("Ctrl+0")
        fit.triggered.connect(self._fit_image)
        reset_zoom = QAction("Reset Zoom (1:1)", self)
        reset_zoom.triggered.connect(self._reset_zoom_current)

        self.fullscreen_action = QAction("Fullscreen", self)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.triggered.connect(self._toggle_fullscreen)

        view_menu.addActions([zoom_in, zoom_out, fit, reset_zoom])
        view_menu.addSeparator()
        view_menu.addAction(self.fullscreen_action)
        view_menu.addSeparator()

        # ── Rotate / Flip ──
        rotate_cw = QAction("Rotate Clockwise", self)
        rotate_cw.setShortcut("Ctrl+R")
        rotate_cw.triggered.connect(self._rotate_cw_current)
        rotate_ccw = QAction("Rotate Counter-clockwise", self)
        rotate_ccw.setShortcut("Ctrl+Shift+R")
        rotate_ccw.triggered.connect(self._rotate_ccw_current)
        flip_h = QAction("Flip Horizontal", self)
        flip_h.triggered.connect(self._flip_h_current)
        flip_v = QAction("Flip Vertical", self)
        flip_v.triggered.connect(self._flip_v_current)
        view_menu.addActions([rotate_cw, rotate_ccw, flip_h, flip_v])

        # ── Tools ──
        tools_menu = menu.addMenu("Tools")
        self.convert_action = QAction("Convert Image...", self)
        self.convert_action.triggered.connect(self.open_convert_dialog)
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        tools_menu.addActions([self.convert_action, settings_action])

        # ── Help ──
        help_menu = menu.addMenu("Help")
        fmt_help = QAction("Format Help", self)
        fmt_help.triggered.connect(self.show_help)
        help_menu.addAction(fmt_help)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setObjectName("mainToolbar")
        for action, icon_name in [
            (self.open_action, ACTION_ICON_NAMES["open"]),
            (self.save_action, ACTION_ICON_NAMES["save"]),
            (self.convert_action, ACTION_ICON_NAMES["convert"]),
        ]:
            action.setIcon(self._build_action_icon(icon_name))
            toolbar.addAction(action)

        toolbar.addSeparator()

        settings_icon = self._build_action_icon(ACTION_ICON_NAMES["settings"])
        settings_action = toolbar.addAction(settings_icon, "Settings")
        settings_action.triggered.connect(self.open_settings_dialog)

        help_icon = self._build_action_icon(ACTION_ICON_NAMES["help"])
        help_action = toolbar.addAction(help_icon, "Help")
        help_action.triggered.connect(self.show_help)

    # ── Theme & icons ────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        font_size = self.settings.ui_font_size
        selected_theme = self.settings.ui_theme
        app = QApplication.instance()
        if app is not None:
            base = load_qdarkstyle_stylesheet(selected_theme)
            app.setStyleSheet(f"{base}\n{build_ui_stylesheet(selected_theme, font_size)}")
        else:
            self.setStyleSheet(build_ui_stylesheet(selected_theme, font_size))

    def _build_action_icon(self, icon_name: str) -> QIcon:
        import qtawesome as qta

        try:
            return qta.icon(icon_name, color=ACTION_ICON_COLOR, color_disabled=ACTION_ICON_DISABLED_COLOR)
        except (KeyError, TypeError, ValueError):
            return QIcon()

    # ── Image helpers ─────────────────────────────────────────────────

    @staticmethod
    def _qimage_from_gray(gray: np.ndarray) -> QImage:
        h, w = gray.shape
        return QImage(gray.data, w, h, gray.strides[0], QImage.Format_Grayscale8).copy()

    @staticmethod
    def _qimage_from_rgb(rgb: np.ndarray) -> QImage:
        h, w = rgb.shape[:2]
        return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()

    @staticmethod
    def _warn_size_mismatch(parent, actual: int, expected: int) -> bool:
        """Ask user whether to proceed despite size mismatch. Returns True to continue."""
        if actual == expected:
            return True
        result = QMessageBox.warning(
            parent,
            "File size mismatch",
            f"File size={actual}, expected frame size={expected}. "
            "Parse first frame anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return result == QMessageBox.Yes

    # ── Frame helpers ────────────────────────────────────────────────

    @staticmethod
    def _get_frame_size(opts: DecodeOptions) -> int:
        """Get the size of one frame in bytes for the current decode options."""
        try:
            if opts.image_type == "RAW" or opts.format_name in (
                "RAW8", "RAW10", "RAW12", "RAW16", "RAW32",
                "RAW10 Packed", "RAW12 Packed", "RAW14 Packed",
            ):
                return expected_frame_size_raw(opts.format_name, opts.width, opts.height)
            else:
                return expected_frame_size_yuv(opts.format_name, opts.width, opts.height)
        except Exception:
            return 0

    def _compute_frame_info(self, item: ViewerItem) -> None:
        """Calculate total frames from file size and frame size, store on item."""
        opts = item.options
        frame_size = self._get_frame_size(opts)
        if frame_size <= 0:
            item.total_frames = 1
            return
        try:
            file_size = os.path.getsize(opts.file_path) - opts.offset
        except OSError:
            item.total_frames = 1
            return
        if file_size <= 0:
            item.total_frames = 1
            return
        item.total_frames = max(1, file_size // frame_size)

    def _update_frame_display(self, item: ViewerItem) -> None:
        """Update frame nav bar and status bar from item state."""
        if item.frame_nav is None:
            return
        item.frame_nav.set_frame_info(item.current_frame, item.total_frames)
        item.frame_nav.setVisible(item.total_frames > 1)
        if item.total_frames > 1:
            self.frame_status.setText(f"Frame: {item.current_frame + 1}/{item.total_frames}")
        else:
            self.frame_status.setText("Frame: -")

    # ── Tab navigation ──────────────────────────────────────────────

    def _next_tab(self) -> None:
        count = self.item_tabs.count()
        if count > 1:
            idx = (self.item_tabs.currentIndex() + 1) % count
            self.item_tabs.setCurrentIndex(idx)

    def _prev_tab(self) -> None:
        count = self.item_tabs.count()
        if count > 1:
            idx = (self.item_tabs.currentIndex() - 1 + count) % count
            self.item_tabs.setCurrentIndex(idx)

    # ── Drag & drop ──────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

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

    # ── File open ─────────────────────────────────────────────────────

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
        if paths:
            self.decode_current()

    def _open_item(self, path: str, decode: bool) -> None:
        if not path or not os.path.isfile(path):
            return

        item = ViewerItem()
        item.view = ImageView()
        item.view.zoomChanged.connect(lambda zoom: self._on_item_zoom_changed(item, zoom))
        item.view.contextMenuRequested.connect(self._show_image_context_menu)
        item.view.framePrevRequested.connect(lambda: self._nav_frame(item, -1))
        item.view.frameNextRequested.connect(lambda: self._nav_frame(item, 1))

        # Frame navigation bar below the image
        item.frame_nav = FrameNavBar()
        item.frame_nav.frameChanged.connect(self._on_frame_changed)

        # Container: ImageView + FrameNavBar
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(item.view, 1)
        layout.addWidget(item.frame_nav)

        # Configure item options based on file extension
        item.options.file_path = path
        ext = Path(path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            item.options.image_type = "Standard Image"
            item.options.format_name = "N/A"
        elif ext == ".yuv":
            item.options.image_type = "YUV"
            item.options.format_name = "YUYV"
        else:
            item.options.image_type = "RAW"
            item.options.format_name = "RAW12"
            item.options.alignment = "msb"

        item.options.width = 2560
        item.options.height = 1440

        self.settings.add_recent_file(path)
        self._refresh_recent_files_menu()

        self.items.append(item)
        index = self.item_tabs.addTab(container, os.path.basename(path))
        self.item_tabs.setCurrentIndex(index)
        self.panel.set_enabled(True)

        if decode:
            self.decode_current()

    # ── Tab management ────────────────────────────────────────────────

    def _on_tab_changed(self, index: int) -> None:
        if self._loading_item:
            return
        if 0 <= self._active_item_index < len(self.items):
            self._save_panel_to_item(self.items[self._active_item_index])
        self._active_item_index = index
        if 0 <= index < len(self.items):
            self._load_item_to_panel(self.items[index])
            self._sync_status_from_item(self.items[index])
        self.panel.set_enabled(index >= 0)
        # set_enabled(True) enables all controls blindly, so re-sync
        # type-specific control states (e.g., disable bayer for YUV).
        if index >= 0:
            self.panel._sync_type_enabled()

    def close_current_item(self) -> None:
        idx = self.item_tabs.currentIndex()
        if idx >= 0:
            self.close_item(idx)

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
            self.frame_status.setText("Frame: -")
            self.state_status.setText("No item")
            self.panel.set_enabled(False)
        else:
            self._on_tab_changed(self.item_tabs.currentIndex())

    def _current_item(self) -> ViewerItem | None:
        idx = self.item_tabs.currentIndex()
        if 0 <= idx < len(self.items):
            return self.items[idx]
        return None

    # ── Panel ↔ Item sync ────────────────────────────────────────────

    def _save_panel_to_item(self, item: ViewerItem) -> None:
        if item is None:
            return
        vals = self.panel.get_values()
        item.options.image_type = vals["image_type"]
        item.options.format_name = vals["format_name"]
        item.options.width = vals["width"]
        item.options.height = vals["height"]
        item.options.alignment = vals["alignment"]
        item.options.endianness = vals["endianness"]
        item.options.offset = vals["offset"]

    def _load_item_to_panel(self, item: ViewerItem) -> None:
        self._loading_item = True
        opts = item.options
        self.panel.set_values(
            image_type=opts.image_type,
            format_name=opts.format_name,
            width=opts.width,
            height=opts.height,
            alignment=opts.alignment,
            endianness=opts.endianness,
            offset=opts.offset,
        )
        self.panel.set_zoom_percent(item.zoom_percent)
        self._update_frame_display(item)
        self.zoom_status.setText(f"Zoom: {item.zoom_percent}%")
        self._loading_item = False

    def _sync_status_from_item(self, item: ViewerItem) -> None:
        path = item.options.file_path
        if path:
            try:
                size = os.path.getsize(path)
                self.file_status.setText(f"File: {os.path.basename(path)} ({size} bytes)")
            except OSError:
                self.file_status.setText(f"File: {os.path.basename(path)}")
        # Show image data size (frame size)
        frame_size = self._get_frame_size(item.options)
        if frame_size > 0:
            self.image_status.setText(
                f"Image: {item.options.width}x{item.options.height} ({frame_size}) | Format: {item.options.format_name}"
            )
        else:
            self.image_status.setText(
                f"Image: {item.options.width}x{item.options.height} | Format: {item.options.format_name}"
            )
        self.zoom_status.setText(f"Zoom: {item.zoom_percent}%")
        if item.total_frames > 1:
            self.frame_status.setText(f"Frame: {item.current_frame + 1}/{item.total_frames}")
        else:
            self.frame_status.setText("Frame: -")

    # ── Panel signal handlers ────────────────────────────────────────

    def _on_panel_type_changed(self, image_type: str) -> None:
        pass

    def _on_panel_raw_preview_changed(self, value: str) -> None:
        pass

    def _on_frame_changed(self, frame_index: int) -> None:
        """User changed frame via nav bar buttons or spin box."""
        item = self._current_item()
        if item is None:
            return
        if frame_index == item.current_frame:
            return
        item.current_frame = max(0, min(frame_index, max(0, item.total_frames - 1)))
        self.decode_current()

    def _nav_frame(self, item: ViewerItem, delta: int) -> None:
        """Navigate frames by delta (-1 prev, +1 next) for any item (not just current)."""
        new_index = item.current_frame + delta
        if 0 <= new_index < item.total_frames:
            item.current_frame = new_index
            item.frame_nav.set_frame_index(new_index)
            # If this is the current visible tab, decode immediately
            if item is self._current_item():
                self.decode_current()

    def _on_panel_zoom_changed(self, percent: int) -> None:
        """Zoom slider changed."""
        item = self._current_item()
        if item and item.view:
            item.view.zoom_to(percent)
            item.zoom_percent = percent
            self.zoom_status.setText(f"Zoom: {percent}%")

    # ── Decode (async) ───────────────────────────────────────────────

    def decode_current(self) -> None:
        item = self._current_item()
        if item is None:
            return
        path = item.options.file_path
        if not path:
            return
        self._save_panel_to_item(item)

        opts = item.options

        # Compute effective offset = base offset + frame_index * frame_size
        frame_size = self._get_frame_size(opts)
        effective_offset = opts.offset
        if frame_size > 0 and item.current_frame > 0:
            effective_offset = opts.offset + item.current_frame * frame_size

        spec = ImageSpec(opts.width, opts.height, effective_offset)

        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            QMessageBox.critical(self, "Read Error", str(exc))
            return

        # Validate size
        try:
            if opts.image_type == "RAW" or opts.format_name in (
                "RAW8", "RAW10", "RAW12", "RAW16", "RAW32",
                "RAW10 Packed", "RAW12 Packed", "RAW14 Packed",
            ):
                expected = expected_frame_size_raw(opts.format_name, spec.width, spec.height)
            else:
                expected = expected_frame_size_yuv(opts.format_name, spec.width, spec.height)
        except Exception:
            expected = -1

        if expected > 0:
            remaining = len(data) - effective_offset
            # Only warn if remaining data is less than one frame (truncated).
            # Multi-frame files naturally have remaining > expected, which is fine.
            if remaining < expected and not self._warn_size_mismatch(self, remaining, expected):
                return

        # Compute and store total frames
        self._compute_frame_info(item)

        # Standard Image — decode synchronously
        if opts.image_type == "Standard Image":
            self._decode_standard_image(data, item, opts)
            return

        # RAW/YUV — async
        self._start_async_decode(data, item, opts, effective_offset)

    def _decode_standard_image(self, data: bytes, item: ViewerItem, opts: DecodeOptions) -> None:
        try:
            bgr = load_bgr_image(opts.file_path)
            rgb = bgr[:, :, ::-1]
            h, w = rgb.shape[:2]
            qimg = self._qimage_from_rgb(rgb)
            item.current_display = rgb
            item.options.width = w
            item.options.height = h
            item.total_frames = 1
            item.current_frame = 0
            self._on_decode_success(item, qimg, w, h, "Standard Image")
        except Exception as exc:
            QMessageBox.critical(self, "Decode Failed", str(exc))
            self.state_status.setText("Decode failed")

    def _start_async_decode(self, data: bytes, item: ViewerItem, opts: DecodeOptions, effective_offset: int) -> None:
        self._cancel_async_decode()

        spec = ImageSpec(opts.width, opts.height, effective_offset)
        preview_mode = self.panel.raw_preview_combo.currentText() if hasattr(self, "panel") else "Grayscale"
        bayer_pattern = self.panel.bayer_pattern_combo.currentText() if hasattr(self, "panel") else "RGGB"

        self._thread = QThread()
        self._worker = DecodeWorker()
        self._worker.moveToThread(self._thread)
        self._worker.configure(
            data,
            spec,
            opts.format_name,
            alignment=opts.alignment,
            endianness=opts.endianness,
            preview_mode=preview_mode,
            bayer_pattern=bayer_pattern,
        )

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_decode_finished)
        self._worker.error.connect(self._on_decode_error)
        self._worker.finished.connect(self._cleanup_thread)
        self._worker.error.connect(self._cleanup_thread)

        self.state_status.setText("Decoding...")
        self._thread.start()

    def _cancel_async_decode(self) -> None:
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(500)
            self._thread = None
            self._worker = None

    def _on_decode_finished(self, result) -> None:
        item = self._current_item()
        if item is None:
            return
        item.current_display = result.display_array
        item.options.width = result.width
        item.options.height = result.height
        self._on_decode_success(item, result.qimage, result.width, result.height, result.format_name)

    def _on_decode_success(self, item: ViewerItem, qimg: QImage, width: int, height: int, format_name: str) -> None:
        item.view.set_pixmap(QPixmap.fromImage(qimg))
        item.view.fit_image()
        path = item.options.file_path
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        self.file_status.setText(f"File: {os.path.basename(path)} ({size} bytes)")
        frame_size = self._get_frame_size(item.options)
        if frame_size > 0:
            self.image_status.setText(
                f"Image: {width}x{height} ({frame_size}) | Format: {format_name}"
            )
        else:
            self.image_status.setText(f"Image: {width}x{height} | Format: {format_name}")
        self.state_status.setText("Decoded")
        self._update_frame_display(item)

    def _on_decode_error(self, message: str) -> None:
        QMessageBox.critical(self, "Decode Failed", message)
        self.state_status.setText("Decode failed")

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(500)
            self._thread = None
            self._worker = None

    # ── Save ─────────────────────────────────────────────────────────

    def save_display(self) -> None:
        item = self._current_item()
        if item is None or item.current_display is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Image", "", "PNG (*.png);;JPEG (*.jpg *.jpeg)"
        )
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

    # ── Zoom ─────────────────────────────────────────────────────────

    def _fit_image(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.fit_image()
            self.panel.set_zoom_percent(item.view.zoom_percent)
            self.zoom_status.setText(f"Zoom: {item.view.zoom_percent}%")

    def _zoom_in_current(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.zoom_in()
            item.zoom_percent = item.view.zoom_percent
            self.panel.set_zoom_percent(item.zoom_percent)
            self.zoom_status.setText(f"Zoom: {item.zoom_percent}%")

    def _zoom_out_current(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.zoom_out()
            item.zoom_percent = item.view.zoom_percent
            self.panel.set_zoom_percent(item.zoom_percent)
            self.zoom_status.setText(f"Zoom: {item.zoom_percent}%")

    def _reset_zoom_current(self) -> None:
        item = self._current_item()
        if item and item.view:
            item.view.reset_zoom()
            item.zoom_percent = 100
            self.panel.set_zoom_percent(100)
            self.zoom_status.setText("Zoom: 100%")

    def _on_item_zoom_changed(self, item: ViewerItem, zoom: int) -> None:
        item.zoom_percent = zoom
        if item is self._current_item():
            self.zoom_status.setText(f"Zoom: {zoom}%")
            self.panel.set_zoom_percent(zoom)

    # ── Rotate / Flip ───────────────────────────────────────────────

    def _rotate_cw_current(self) -> None:
        item = self._current_item()
        if item and item.view and item.view.has_image():
            item.view.rotate_cw()

    def _rotate_ccw_current(self) -> None:
        item = self._current_item()
        if item and item.view and item.view.has_image():
            item.view.rotate_ccw()

    def _flip_h_current(self) -> None:
        item = self._current_item()
        if item and item.view and item.view.has_image():
            item.view.flip_horizontal()

    def _flip_v_current(self) -> None:
        item = self._current_item()
        if item and item.view and item.view.has_image():
            item.view.flip_vertical()

    # ── Fullscreen ──────────────────────────────────────────────────

    def _toggle_fullscreen(self, checked: bool) -> None:
        if checked:
            self.showFullScreen()
            self.menuBar().hide()
            tb = self.findChild(QWidget, "mainToolbar")
            if tb:
                tb.hide()
        else:
            self.showNormal()
            self.menuBar().show()
            tb = self.findChild(QWidget, "mainToolbar")
            if tb:
                tb.show()

    def keyPressEvent(self, event):  # noqa: N802
        """Handle keyboard: Up/Down for frame nav, Escape exits fullscreen."""
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.fullscreen_action.setChecked(False)
            self._toggle_fullscreen(False)
            event.accept()
            return
        if event.key() == Qt.Key_Up:
            item = self._current_item()
            if item:
                self._nav_frame(item, -1)
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            item = self._current_item()
            if item:
                self._nav_frame(item, 1)
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Context menu ─────────────────────────────────────────────────

    def _show_image_context_menu(self, view: ImageView, pos) -> None:
        menu = QMenu(self)
        zoom_in = menu.addAction("Zoom In")
        zoom_out = menu.addAction("Zoom Out")
        fit = menu.addAction("Fit to Window")
        reset = menu.addAction("Reset Zoom (1:1)")
        menu.addSeparator()
        rotate_cw = menu.addAction("Rotate CW")
        rotate_ccw = menu.addAction("Rotate CCW")
        flip_h = menu.addAction("Flip H")
        flip_v = menu.addAction("Flip V")
        menu.addSeparator()
        copy_action = menu.addAction("Copy Image")
        menu.addSeparator()
        if self.item_tabs.count() > 1:
            next_tab = menu.addAction("Next Tab (Ctrl+Tab)")
            prev_tab = menu.addAction("Previous Tab (Ctrl+Shift+Tab)")
        else:
            next_tab = prev_tab = None
        selected = menu.exec_(pos)
        if selected == zoom_in:
            view.zoom_in()
        elif selected == zoom_out:
            view.zoom_out()
        elif selected == fit:
            view.fit_image()
        elif selected == reset:
            view.reset_zoom()
        elif selected == rotate_cw:
            view.rotate_cw()
        elif selected == rotate_ccw:
            view.rotate_ccw()
        elif selected == flip_h:
            view.flip_horizontal()
        elif selected == flip_v:
            view.flip_vertical()
        elif selected == copy_action and view.has_image():
            QApplication.clipboard().setPixmap(view.current_pixmap())
        elif next_tab and selected == next_tab:
            self._next_tab()
        elif prev_tab and selected == prev_tab:
            self._prev_tab()

    # ── Recent files ─────────────────────────────────────────────────

    def _refresh_recent_files_menu(self) -> None:
        self.recent_menu.clear()
        files = self.settings.recent_files
        if not files:
            action = QAction("(No recent files)", self)
            action.setEnabled(False)
            self.recent_menu.addAction(action)
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

    # ── Dialogs ──────────────────────────────────────────────────────

    def show_help(self) -> None:
        dlg = HelpDialog(self)
        dlg.exec_()

    def open_convert_dialog(self) -> None:
        dlg = ConvertDialog(self.settings, self)
        dlg.exec_()

    def open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec_():
            self._apply_theme()


def run() -> None:
    """Application entry point — create QApplication and show MainWindow."""
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1200, 700)
    w.show()
    app.exec_()
