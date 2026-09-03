"""Main application window — ties together panels, tabs, decode, and background workers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import load_bgr_image
from raw_view.formats import (
    FormatError,
    ImageSpec,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    require_decode_size,
)
from raw_view.logger import get_logger
from raw_view.models import (
    AppSettings,
    ACTION_ICON_COLOR,
    ACTION_ICON_DISABLED_COLOR,
    ACTION_ICON_NAMES,
    IMAGE_EXTENSIONS,
    MATERIAL_EXTRA,
    THEME_XML,
    DecodeOptions,
    SensorPreset,
    ViewerItem,
    build_ui_stylesheet,
    dpi_to_dots_per_meter,
)

logger = get_logger(__name__)


def resource_path(relative: str) -> str:
    """Resolve a bundled resource path in both dev and PyInstaller runs."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        # raw_view/gui/app.py -> project root is two levels up.
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)


def app_icon() -> QIcon:
    """Return the application window/taskbar icon (SVG, falls back gracefully)."""
    svg = resource_path(os.path.join("assets", "logo.svg"))
    if os.path.isfile(svg):
        return QIcon(svg)
    png = resource_path(os.path.join("assets", "logo.png"))
    return QIcon(png) if os.path.isfile(png) else QIcon()


from raw_view.gui.framenav import FrameNavBar
from raw_view.gui.image_utils import qimage_from_grayscale, qimage_from_rgb
from raw_view.gui.imageview import ImageView
from raw_view.gui.panels import ControlPanel
from raw_view.gui.dialogs import (
    AboutDialog,
    BatchConvertDialog,
    ConvertDialog,
    FourCCDialog,
    HelpDialog,
    KeyboardShortcutsDialog,
    PresetManagerDialog,
    SettingsDialog,
)
from raw_view.gui.worker import DecodeCache, DecodeWorker


class DropCentralWidget(QWidget):
    """Central widget that paints a drag-drop highlight border when active
    and handles drag-drop events directly, emitting ``filesDropped``.

    Widgets under the cursor that do not accept drops propagate the event
    up the hierarchy, so this widget receives and handles all drag-drop
    events transparently — no separate overlay needed.
    """

    filesDropped = pyqtSignal(list, bool)  # list[str], scanned_too_many

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_hover = False
        self.setAcceptDrops(True)

    # ── drag-drop event handlers ────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self._drag_hover = True
            self.update()
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._drag_hover:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drag_hover = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._drag_hover = False
        self.update()
        urls = event.mimeData().urls()
        if not urls:
            return
        paths, scanned_too_many = handle_drop_paths(urls)
        if not paths:
            super().dropEvent(event)
            return
        self.filesDropped.emit(paths, scanned_too_many)
        event.acceptProposedAction()

    # ── paint ────────────────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not self._drag_hover:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Border highlight
        pen = painter.pen()
        pen.setColor(QColor("#3B82F6"))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 12, 12)

        # Center label
        painter.setPen(QColor("#3B82F6"))
        font = painter.font()
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, "Drop files or folders here")

        font.setPointSize(10)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(
            self.rect().adjusted(0, 40, 0, 0),
            Qt.AlignCenter,
            "Supports RAW, YUV, PNG, JPG, BMP",
        )


_YUV_EXTS: dict[str, str] = {
    ".yuv": "YUYV",
    ".nv12": "NV12",
    ".nv21": "NV21",
    ".i420": "I420",
    ".yv12": "YV12",
    ".yuyv": "YUYV",
    ".uyvy": "UYVY",
    ".yvyu": "YVYU",
    ".vyuy": "VYUY",
    ".nv16": "NV16",
    ".nv61": "NV61",
    # Y-only (YUV 4:0:0) 后缀：打开后面板 Format=YOnly + Bit depth 由用户选择
    ".y": "YOnly",
    ".grey": "YOnly",
}

# Extensions the viewer can actually open / decode. Directory drag-drop scans
# are filtered to this set so we never pull in unrelated files (configs, caches).
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | set(_YUV_EXTS) | {".raw", ".bin"}

# When a dropped directory resolves to more than this many supported files,
# the user must confirm before they are all opened.
DIR_DROP_MAX_FILES = 50


def _scan_directory(path: str) -> list[str]:
    """Recursively scan a directory for supported files, returning sorted paths.

    Files whose extension is not in ``SUPPORTED_EXTENSIONS`` are skipped, so a
    dropped folder doesn't pull in unrelated files.
    """
    results: list[str] = []
    try:
        for root, _dirs, files in os.walk(path):
            for fname in sorted(files):
                if not _is_supported_file(fname):
                    continue
                results.append(str(Path(root) / fname))
    except OSError:
        logger.warning("Failed to scan directory: %s", path)
    return results


def _is_supported_file(path: str) -> bool:
    """Whether *path* looks like a file the viewer supports (by extension)."""
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def handle_drop_paths(urls, max_files: int = DIR_DROP_MAX_FILES) -> list[str]:
    """Extract file/directory paths from MIME urls, resolving to supported files.

    Directories are scanned recursively but filtered to supported extensions;
    if a single dropped directory expands beyond *max_files*, ``scanned_too_many``
    is returned so the caller can ask the user before opening everything.
    Returns ``(files, scanned_too_many)``.
    """
    paths: list[str] = []
    scanned_too_many = False
    for url in urls:
        local_path = url.toLocalFile()
        if not local_path:
            continue
        if os.path.isdir(local_path):
            scanned = _scan_directory(local_path)
            if len(scanned) > max_files:
                scanned_too_many = True
            paths.extend(scanned)
        elif os.path.isfile(local_path) and _is_supported_file(local_path):
            paths.append(local_path)
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for p in paths:
        norm = os.path.normcase(os.path.normpath(p))
        if norm not in seen:
            seen.add(norm)
            deduped.append(p)
    return deduped, scanned_too_many


class MainWindow(QMainWindow):
    """Main application window with control panel, tabbed image views, menus, and toolbar."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RAW/YUV Viewer")
        self.setWindowIcon(app_icon())
        self.settings = AppSettings()
        self.items: list[ViewerItem] = []
        self._active_item_index = -1
        # 对象身份引用：结构变更（拖拽/关闭）后用 `it is ref` 定位真实当前
        # item，避免位置索引在 items 重排后指向邻居（0.2.1-M-1）。
        self._active_item_ref: ViewerItem | None = None
        self._loading_item = False
        self._thread: QThread | None = None
        self._worker: DecodeWorker | None = None
        # Generation counter for async decodes. Incremented on every start, so
        # results/errors from an older (cancelled/abandoned) worker, whose
        # ``finished`` signal still arrives late, can be recognised and dropped.
        self._decode_generation = 0
        # The item that the latest async decode was started for. Combined with
        # the generation counter, this lets us drop results that belong to a
        # different tab (item identity check in ``_should_apply_decode``).
        self._pending_decode_item: ViewerItem | None = None
        # P1-1 解码缓存：同一 (文件/格式/尺寸/位对齐/端序/帧) 的重复解码直接复用，
        # 避免来回翻帧时反复全量解码。数据在后台线程计算，缓存键由主线程生成并
        # 于结果返回后写入（缓存本身只在主线程访问，无需跨线程锁）。
        self.decode_cache = DecodeCache()
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
        self.state_status = QLabel()
        self._set_state("Ready", "ok")
        self.status.addPermanentWidget(self.file_status, 2)
        self.status.addPermanentWidget(self.image_status, 2)
        self.status.addPermanentWidget(self.frame_status, 1)
        self.status.addPermanentWidget(self.zoom_status, 1)
        self.status.addPermanentWidget(self.state_status, 1)

        # Control panel
        self.panel = ControlPanel()
        self.panel.applyClicked.connect(self._on_apply_clicked)
        self.panel.typeChanged.connect(self._on_panel_type_changed)
        self.panel.rawPreviewChanged.connect(self._on_panel_raw_preview_changed)
        self.panel.zoomChanged.connect(self._on_panel_zoom_changed)
        self.panel.presetSelected.connect(self._on_preset_selected)
        self.panel.savePresetRequested.connect(self._on_save_preset_clicked)
        self.panel.managePresetsRequested.connect(self._open_preset_manager_dialog)
        self.panel.valuesChanged.connect(self._on_panel_values_changed)
        self._refresh_preset_combo()

        # Tab widget
        self.item_tabs = QTabWidget()
        # 每个标签右侧显示关闭 X 按钮（点击关闭）——用户既可拖入文件，也可直接
        # 点 X 关闭单个标签页。Qt 5.15 会把"点 X"与"按住标签主体拖动"区分开：
        # 拖动从标签名称区域按下、X 按钮独立可点，二者可共存。
        self.item_tabs.setTabsClosable(True)
        # 允许通过点击标签名称并左右拖动来调整标签页顺序（5.15 原生支持）。
        # QTabWidget 移动标签后不会自动同步 ``self.items``，由 tabBar.tabMoved
        # 信号（见 :meth:`_on_tab_moved`）负责把 items 重排成与视觉顺序一致。
        self.item_tabs.setMovable(True)
        # 标签名称区域右键 → "Close All Items" / "Close Items to the Right"
        # （需求 5）。setContextMenuPolicy(CustomContextMenu) 只接管标签栏上的
        # 右键，不干扰标签右侧的关闭 X 按钮（QTabBar::close-button 仍由
        # tabCloseRequested 处理）与拖拽排序（可见 tabBar 自身事件）。
        tab_bar = self.item_tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_context_menu)
        self.item_tabs.tabCloseRequested.connect(self.close_item)
        tab_bar.tabMoved.connect(self._on_tab_moved)
        self.item_tabs.currentChanged.connect(self._on_tab_changed)

        # Right side: a stack that shows an empty-state placeholder until the
        # first file is opened, then swaps to the tabbed image views.
        self.empty_state = self._build_empty_state()
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.empty_state)   # index 0
        self.center_stack.addWidget(self.item_tabs)     # index 1
        self.center_stack.setCurrentIndex(0)

        # Central layout — DropCentralWidget handles drag-highlight painting
        root = DropCentralWidget()
        root.setObjectName("centralRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)
        # A draggable splitter lets the user resize the control panel vs the
        # image area. The panel keeps its width when the window resizes; the
        # image area absorbs the extra space.
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.addWidget(self.panel)
        self.splitter.addWidget(self.center_stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([360, 880])
        layout.addWidget(self.splitter)
        self.setCentralWidget(root)
        root.filesDropped.connect(self._on_files_dropped)

        self._build_menus()
        self._build_toolbar()
        self._refresh_recent_files_menu()
        self.panel.set_enabled(False)

    def _build_empty_state(self) -> QWidget:
        """Placeholder shown in the viewer area before any file is opened."""
        widget = QWidget()
        widget.setObjectName("emptyState")
        vbox = QVBoxLayout(widget)
        vbox.setAlignment(Qt.AlignCenter)
        vbox.setSpacing(14)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        try:
            import qtawesome as qta

            pix = qta.icon("fa5s.image", color="#B9B4D0").pixmap(72, 72)
            icon_label.setPixmap(pix)
        except Exception:
            icon_label.setText("🖼")

        title = QLabel("No image loaded")
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("emptyStateTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")

        hint = QLabel("Open a RAW / YUV / image file, or drag and drop it here")
        hint.setAlignment(Qt.AlignCenter)
        hint.setObjectName("statusPlaceholder")

        open_btn = QPushButton("Open File...")
        open_btn.setObjectName("accentButton")
        open_btn.setMinimumWidth(200)
        open_btn.clicked.connect(self.open_file)

        vbox.addStretch(1)
        vbox.addWidget(icon_label)
        vbox.addWidget(title)
        vbox.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(open_btn)
        btn_row.addStretch(1)
        vbox.addLayout(btn_row)
        vbox.addStretch(1)
        return widget

    def _update_center_stack(self) -> None:
        """Show the tabbed views when items exist, else the empty state."""
        self.center_stack.setCurrentIndex(1 if self.items else 0)

    _STATE_DOT_COLORS = {
        "idle": "#9E9E9E",
        "busy": "#F59E0B",
        "ok": "#2E7D32",
        "error": "#D32F2F",
    }

    def _set_state(self, text: str, kind: str = "idle") -> None:
        """Update the status-bar state label with a colored status dot."""
        dot = self._STATE_DOT_COLORS.get(kind, "#9E9E9E")
        self.state_status.setText(f'<span style="color:{dot};">&#9679;</span> {text}')

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
        nav_menu.addSeparator()
        # UI-6：上一文件/下一文件（同目录文件组之间切换）。
        self.prev_file_action = QAction("Previous File", self)
        self.prev_file_action.setShortcut("Ctrl+Left")
        self.prev_file_action.setEnabled(False)
        self.prev_file_action.triggered.connect(lambda: self._nav_file_by_dir(-1))
        self.next_file_action = QAction("Next File", self)
        self.next_file_action.setShortcut("Ctrl+Right")
        self.next_file_action.setEnabled(False)
        self.next_file_action.triggered.connect(lambda: self._nav_file_by_dir(1))
        nav_menu.addActions([self.prev_file_action, self.next_file_action])

        # ── View ── (grouped: zoom / display mode / transform)
        view_menu = menu.addMenu("View")

        # Zoom group
        zoom_in = QAction(self._menu_icon("fa5s.search-plus"), "Zoom In", self)
        zoom_in.setShortcut(QKeySequence.ZoomIn)
        zoom_in.triggered.connect(self._zoom_in_current)
        zoom_out = QAction(self._menu_icon("fa5s.search-minus"), "Zoom Out", self)
        zoom_out.setShortcut(QKeySequence.ZoomOut)
        zoom_out.triggered.connect(self._zoom_out_current)
        fit = QAction(self._menu_icon("fa5s.expand-arrows-alt"), "Fit to Window", self)
        fit.setShortcut("Ctrl+0")
        fit.triggered.connect(self._fit_image)
        reset_zoom = QAction(self._menu_icon("fa5s.compress"), "Actual Size", self)
        reset_zoom.setShortcut("Ctrl+1")
        reset_zoom.setToolTip("Reset zoom to 100% (1:1)")
        reset_zoom.triggered.connect(self._reset_zoom_current)
        view_menu.addActions([zoom_in, zoom_out, fit, reset_zoom])

        view_menu.addSeparator()

        # Display-mode group
        self.fullscreen_action = QAction(self._menu_icon("fa5s.expand"), "Fullscreen", self)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)

        view_menu.addSeparator()

        # Transform group
        rotate_cw = QAction(self._menu_icon("fa5s.redo"), "Rotate CW", self)
        rotate_cw.setShortcut("Ctrl+R")
        rotate_cw.triggered.connect(self._rotate_cw_current)
        rotate_ccw = QAction(self._menu_icon("fa5s.undo"), "Rotate CCW", self)
        rotate_ccw.setShortcut("Ctrl+Shift+R")
        rotate_ccw.triggered.connect(self._rotate_ccw_current)
        flip_h = QAction(self._menu_icon("fa5s.arrows-alt-h"), "Flip Horizontal", self)
        flip_h.setShortcut("Ctrl+H")
        flip_h.triggered.connect(self._flip_h_current)
        flip_v = QAction(self._menu_icon("fa5s.arrows-alt-v"), "Flip Vertical", self)
        flip_v.setShortcut("Ctrl+Shift+V")
        flip_v.triggered.connect(self._flip_v_current)
        view_menu.addActions([rotate_cw, rotate_ccw, flip_h, flip_v])

        # ── Tools ──
        tools_menu = menu.addMenu("Tools")
        self.convert_action = QAction(
            self._menu_icon(ACTION_ICON_NAMES["convert"]), "Convert Image...", self
        )
        self.convert_action.triggered.connect(self.open_convert_dialog)
        self.batch_convert_action = QAction(
            self._menu_icon("fa5s.layer-group"), "Batch Convert...", self
        )
        self.batch_convert_action.triggered.connect(self.open_batch_convert_dialog)
        settings_action = QAction(
            self._menu_icon(ACTION_ICON_NAMES["settings"]), "Settings...", self
        )
        settings_action.triggered.connect(self.open_settings_dialog)
        tools_menu.addActions([self.convert_action, self.batch_convert_action])
        tools_menu.addSeparator()
        fourcc_action = QAction(
            self._menu_icon("fa5s.search"), "FourCC Lookup...", self
        )
        fourcc_action.triggered.connect(self.open_fourcc_dialog)
        tools_menu.addAction(fourcc_action)
        tools_menu.addSeparator()
        tools_menu.addAction(settings_action)

        # ── Help ──
        help_menu = menu.addMenu("Help")
        fmt_help = QAction("Format Help", self)
        fmt_help.triggered.connect(self.show_help)
        help_menu.addAction(fmt_help)
        kb_help = QAction("Keyboard Shortcuts", self)
        kb_help.triggered.connect(self.show_shortcuts)
        help_menu.addAction(kb_help)
        about_action = QAction("About raw-view", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setObjectName("mainToolbar")

        # ── Brand mark (logo only — the window title already says the name) ──
        logo_label = QLabel()
        logo_label.setPixmap(app_icon().pixmap(22, 22))
        logo_label.setContentsMargins(8, 0, 10, 0)
        logo_label.setToolTip("RAW/YUV Viewer")
        toolbar.addWidget(logo_label)
        toolbar.addSeparator()

        # Distinct icons + tooltips so single-image vs multi-image convert
        # are easy to tell apart.
        self.open_action.setToolTip("Open file (Ctrl+O)")
        self.save_action.setToolTip("Save current view as image (Ctrl+S)")
        self.convert_action.setToolTip("Convert one image to RAW/YUV")
        self.batch_convert_action.setToolTip("Batch convert multiple images")
        for action, icon_name in [
            (self.open_action, ACTION_ICON_NAMES["open"]),
            (self.save_action, ACTION_ICON_NAMES["save"]),
            (self.convert_action, "fa5s.exchange-alt"),
            (self.batch_convert_action, "fa5s.layer-group"),
        ]:
            action.setIcon(self._build_action_icon(icon_name))
            toolbar.addAction(action)

        # 上一/下一文件的入口保留在 Navigate 菜单（Ctrl+Left/Right），不再是
        # 工具栏常驻箭头按钮（需求 4）；状态栏和菜单是唯一入口。
        # 给动作保留 tooltip，便于菜单 hover 时提示快捷键（工具栏不展示它们）。
        self.prev_file_action.setToolTip("Previous file in the same folder (Ctrl+Left)")
        self.next_file_action.setToolTip("Next file in the same folder (Ctrl+Right)")

        toolbar.addSeparator()

        settings_icon = self._build_action_icon(ACTION_ICON_NAMES["settings"])
        settings_action = toolbar.addAction(settings_icon, "Settings")
        settings_action.triggered.connect(self.open_settings_dialog)

        help_icon = self._build_action_icon(ACTION_ICON_NAMES["help"])
        help_action = toolbar.addAction(help_icon, "Help")
        help_action.triggered.connect(self.show_help)

    # ── Theme & icons ────────────────────────────────────────────────

    def _apply_titlebar_theme(self) -> None:
        """Match the native Windows title bar to the app theme (dark/light).

        Uses DWM's immersive-dark-mode attribute so the title bar isn't a
        bright white strip above the dark toolbar. No-op off Windows or if the
        attribute isn't supported (older Windows 10 builds).
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            dark = ctypes.c_int(1 if self.settings.ui_theme == "dark" else 0)
            # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 20H1+); 19 = older.
            for attr in (20, 19):
                res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)
                )
                if res == 0:
                    break
        except Exception:
            logger.debug("Could not set title bar theme", exc_info=True)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._apply_titlebar_theme()

    def _apply_theme(self) -> None:
        font_size = self.settings.ui_font_size
        selected_theme = self.settings.ui_theme
        app = QApplication.instance()
        if app is not None:
            # qt-material base (custom indigo theme) + our thin card overlay.
            from qt_material import apply_stylesheet

            theme_file = resource_path(
                os.path.join("assets", THEME_XML.get(selected_theme, THEME_XML["dark"]))
            )
            apply_stylesheet(
                app,
                theme=theme_file,
                invert_secondary=False,
                extra=MATERIAL_EXTRA,
            )
            # Image-based decorations (tick, dropdown arrow, tab close) need
            # runtime-resolved paths (PyInstaller/dev) with forward slashes.
            def _asset(name: str) -> str:
                return resource_path(os.path.join("assets", name)).replace("\\", "/")

            image_qss = (
                "QCheckBox::indicator:checked, QRadioButton::indicator:checked {"
                f" image: url('{_asset('check.png')}'); }}\n"
                "QComboBox::down-arrow {"
                f" image: url('{_asset('chevron_down.png')}'); width: 12px; height: 12px; }}\n"
                "QTabBar::close-button {"
                f" image: url('{_asset('close.png')}'); }}\n"
                "QTabBar::close-button:hover {"
                f" image: url('{_asset('close_hover.png')}'); }}"
            )
            app.setStyleSheet(
                f"{app.styleSheet()}\n{build_ui_stylesheet(selected_theme, font_size)}\n{image_qss}"
            )
        else:
            self.setStyleSheet(build_ui_stylesheet(selected_theme, font_size))

    def _build_action_icon(self, icon_name: str) -> QIcon:
        import qtawesome as qta

        try:
            return qta.icon(icon_name, color=ACTION_ICON_COLOR, color_disabled=ACTION_ICON_DISABLED_COLOR)
        except (KeyError, TypeError, ValueError):
            return QIcon()

    def _menu_icon(self, icon_name: str) -> QIcon:
        """Icon for a menu action (same style as toolbar icons)."""
        return self._build_action_icon(icon_name)

    # ── Image helpers ─────────────────────────────────────────────────

    @staticmethod
    def _qimage_from_gray(gray: np.ndarray) -> QImage:
        return qimage_from_grayscale(gray)

    @staticmethod
    def _qimage_from_rgb(rgb: np.ndarray) -> QImage:
        return qimage_from_rgb(rgb)

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
                # alignment/endianness 对 YOnly 多 bit（16-bit 存储）决定帧大小。
                return expected_frame_size_yuv(
                    opts.format_name, opts.width, opts.height,
                    alignment=opts.alignment, endianness=opts.endianness,
                )
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

    # ── UI-6：同目录文件组切换 ────────────────────────────────────────

    def _same_dir_items(self) -> list[str]:
        """当前项同目录下按名称排序的支持文件列表（排除了自身）。"""
        item = self._current_item()
        path = item.options.file_path if item else ""
        directory = os.path.dirname(path)
        try:
            candidates = [
                p for p in os.listdir(directory)
                if _is_supported_file(p) and p != os.path.basename(path)
            ]
        except OSError:
            return []
        return sorted(candidates)

    def _nav_file_by_dir(self, delta: int) -> None:
        """打开同目录文件组中的上一个/下一个支持文件（UI-6）。

        名称排序后按 delta=-1/+1 取相邻项；已在最前/最后时忽略。新文件以
        ``_open_item(decode=True)`` 打开（沿用默认参数），不弹尺寸警告。
        """
        item = self._current_item()
        if item is None:
            return
        directory = os.path.dirname(item.options.file_path)
        siblings = self._same_dir_items()
        if not siblings:
            return
        cur_name = os.path.basename(item.options.file_path)
        # siblings 已排除自身。rank = 当前文件在「含自身完整有序列表」中的位置
        # （= 字典序小于自身的兄弟数量）。上一/下一兄弟在 siblings 中的下标与
        # rank 的关系：
        #   delta=-1 → siblings[rank-1]（当前之前一位的兄弟）
        #   delta=+1 → siblings[rank]  （当前之后一位的兄弟，因为 rank 已扣掉自身）
        # 不能用 rank+delta 直接当下标（rank 是完整列表位置，siblings 少一个元素）。
        rank = sum(1 for s in siblings if s < cur_name)
        new_idx = rank - 1 if delta == -1 else rank
        if not (0 <= new_idx < len(siblings)):
            return
        self._open_item(os.path.join(directory, siblings[new_idx]), decode=True)

    def _refresh_file_nav_actions(self) -> None:
        """打开/关闭标签后刷新上一/下一文件动作的可用性（0.3.0-L-1 / 0.3.1-L-2）。

        按当前文件在「同目录有序支持文件列表」中的精确位置启用：至少有一个位于
        当前文件前者才启用 prev，至少有一个后者才启用 next——首尾边界不再出现
        “键可点但无任何操作”的无效动作。
        """
        item = self._current_item()
        if item is None or not item.options.file_path:
            self.prev_file_action.setEnabled(False)
            self.next_file_action.setEnabled(False)
            return
        siblings = self._same_dir_items()
        rank = sum(1 for s in siblings if s < os.path.basename(item.options.file_path))
        self.prev_file_action.setEnabled(rank > 0)
        self.next_file_action.setEnabled(rank < len(siblings))

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

    # ── Drag & drop (signal from DropCentralWidget) ──────────────────

    def _on_files_dropped(self, paths: list[str], scanned_too_many: bool = False) -> None:
        """Handle files dropped via drag-and-drop (emitted by DropCentralWidget).

        When a dropped directory expanded beyond the confirmation threshold,
        ask the user before opening everything at once.
        """
        if scanned_too_many and paths:
            reply = QMessageBox.question(
                self,
                "Open many files",
                f"Opening {len(paths)} files from the dropped folder(s). Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                logger.info("Drop aborted by user (%d files)", len(paths))
                return
        logger.info("Drop: %d file(s) resolved from drag", len(paths))
        for path in paths:
            self._open_item(path, decode=False)
        if paths:
            self.decode_current()

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
        logger.info("Opening %d file(s)", len(paths))
        for path in paths:
            self._open_item(path, decode=False)
        if paths:
            self.decode_current()

    def _open_item(self, path: str, decode: bool) -> None:
        if not path or not os.path.isfile(path):
            logger.warning("File not found: %s", path)
            return
        logger.info("Opening item: %s (decode=%s)", path, decode)

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
        elif ext in _YUV_EXTS:
            item.options.image_type = "YUV"
            item.options.format_name = _YUV_EXTS[ext]
        else:
            # Default: RAW (covers .raw, .bin, no extension, unknown extension)
            item.options.image_type = "RAW"
            item.options.format_name = "RAW12"
            item.options.alignment = "msb"

        item.options.width = 2560
        item.options.height = 1440

        self.settings.add_recent_file(path)
        self._refresh_recent_files_menu()

        self.items.append(item)
        index = self.item_tabs.addTab(container, os.path.basename(path))
        # UI-4：标签 tooltip 显示完整路径（悬停即见）。
        self.item_tabs.setTabToolTip(index, path)
        self.item_tabs.setCurrentIndex(index)
        self._refresh_file_nav_actions()
        self.panel.set_enabled(True)
        self._update_center_stack()

        if decode:
            self.decode_current()

    # ── Tab management ────────────────────────────────────────────────

    def _reorder_items(self, from_index: int, to_index: int) -> None:
        """把 ``self.items`` 中的一项移动到新位置（keep item identity intact）。

        与 QTabBar 移动标签的语义保持一致：等价于
        ``items.insert(to_index, items.pop(from_index))``，因此调用后
        ``self.items`` 的顺序与标签视觉顺序一一对应。序号越界时直接忽略，
        保证拖拽排序不会破坏 items 与 tab 的对应关系。
        """
        if not (0 <= from_index < len(self.items)) or not (0 <= to_index < len(self.items)):
            return
        item = self.items.pop(from_index)
        self.items.insert(to_index, item)

    def _on_tab_moved(self, from_index: int, to_index: int) -> None:
        """标签被拖动后同步 ``self.items``，保证两者顺序完全一致。

        QTabWidget.setMovable(True) 只移动了标签本身，不会自动重排
        ``self.items``；若不同步，close_item / _current_item / decode 都会按
        错误的索引取到别的 item。注意：

        * 只同步顺序，不改变当前选中的 item——Qt 移动标签时选中的 *page
          widget* 始终跟随用户拖动的那一项，且之后必然发出一次
          ``currentChanged``，由 ``_on_tab_changed`` 重新校准
          ``_active_item_index`` 并重载面板状态。
        * ``_loading_item`` 在拖拽期间为 False，若选中项确实改变会走正常的
          保存/加载同步路径，不会触发误保存/误解码。
        """
        if from_index == to_index:
            return
        self._reorder_items(from_index, to_index)

    def _on_tab_changed(self, index: int) -> None:
        if self._loading_item:
            return
        # 保存面板到"上一焦点 item 对象"（对象身份），而不是按位置索引——
        # 否则标签被拖动/关闭后，旧 `_active_item_index` 会指向邻居 item，
        # 把未 Apply 参数写错对象（0.2.1 review M-1 / 0.2.0-M-3）。
        prev = self._active_item() if self._active_item_index >= 0 else None
        if prev is not None:
            self._save_panel_to_item(prev)
        self._active_item_index = index
        self._active_item_ref = self.items[index] if 0 <= index < len(self.items) else None
        if 0 <= index < len(self.items):
            self._load_item_to_panel(self.items[index])
            self._sync_status_from_item(self.items[index])
        self.panel.set_enabled(index >= 0)
        # set_enabled(True) enables all controls blindly, so re-sync
        # type-specific control states (e.g., disable bayer for YUV) and
        # re-evaluate the frame-size gate (set_values→set_enabled 会把超大帧
        # 时被禁用的 Apply 重新启用，这里再按当前参数恢复门禁)。
        if index >= 0:
            self.panel._sync_type_enabled()
            self.panel._refresh_frame_size_hint()

    def _active_item(self) -> ViewerItem | None:
        """当前活动引用的 item（对象身份），结构变更后仍能定位真实对象。"""
        ref = getattr(self, "_active_item_ref", None)
        for it in self.items:
            if it is ref:
                return it
        return None

    def close_current_item(self) -> None:
        idx = self.item_tabs.currentIndex()
        if idx >= 0:
            self.close_item(idx)

    def close_item(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        # 被关闭项即将销毁：**不要**把它当“当前项”执行 _save_panel_to_item——
        # 面板此刻可能正显示其它/新当前标签的值（对象身份已随 _active_item_ref
        # 走，见 _on_tab_changed）；对它“保存未 Apply 参数”既无意义（对象要销毁）
        # 也会把面板误写进不相关的 item。未 Apply 编辑的丢弃已在关闭前由标签 ●
        # 标记提示（UI-4），此处不做静默写回（0.2.2-M-1）。
        closing = self.items[index]
        self._loading_item = True
        self.item_tabs.removeTab(index)
        self.items.pop(index)
        self._loading_item = False
        # 0.2.2-L-1 / 0.3.1-M-1 / 0.4.1-M-2：若被关闭项正是当前在途解码目标，
        # 断开其 worker 的 finished/error 槽——否则其迟到结果会画到新当前标签上
        # （_should_apply_decode 依对象身份/pending 判定，但关闭后新标签接管）。
        self._cancel_decode_for(closing)
        if not self.items:
            self._active_item_index = -1
            self._active_item_ref = None
            self.file_status.setText("File: -")
            self.image_status.setText("Image: -")
            self.zoom_status.setText("Zoom: 100%")
            self.frame_status.setText("Frame: -")
            self._set_state("No item", "idle")
            self.panel.set_enabled(False)
        else:
            self._active_item_ref = None
            self._on_tab_changed(self.item_tabs.currentIndex())
        self._update_center_stack()
        self._refresh_file_nav_actions()

    # ── 标签右键菜单（需求 5）────────────────────────────────────────

    def _show_tab_context_menu(self, pos) -> None:
        """标签名称区域右键 → Close All Items / Close Items to the Right。

        被右击的标签通过 ``tabBar().tabAt(pos)`` 定位，并在菜单弹出**前先选中
        它**（明确操作对象，也保证 close-items-to-the-right 的语义与右键目标
        一致）。``pos`` 来自 ``customContextMenuRequested``，为 tabBar 本地坐标，
        ``tabAt`` 直接用即可；``exec_`` 返回被点选的 QAction，用对象身份对比
        分派（不经 triggered 信号，避免与动作上的连接重复执行）。
        """
        tab_bar = self.item_tabs.tabBar()
        index = tab_bar.tabAt(pos)
        if index >= 0:
            self.item_tabs.setCurrentIndex(index)
        # 用 __dict__.get 而非 getattr(…, None)：__new__ 构造的测试桩（未调用
        # QObject.__init__）上 getattr 会抛 RuntimeError 而非返回默认值。
        menu = self.__dict__.get("_tabRMenu")
        if menu is None:
            menu = self._build_tab_context_menu()
        selected = menu.exec_(tab_bar.mapToGlobal(pos))
        self._run_tab_menu_action(selected, index)

    def _run_tab_menu_action(self, action, index: int) -> None:
        """按 exec_ 返回的 QAction 对象身份分派右键菜单命令。

        拆成独立可测方法：真实流程里 ``menu.exec_`` 会阻塞等待交互，测试直接
        调用本方法验证分派与行为，无需弹出菜单。
        """
        if action is None:
            return
        if action is self.__dict__.get("_acTabCloseAll"):
            self.close_all_items()
        elif action is self.__dict__.get("_acTabCloseRight"):
            self.close_items_to_the_right(index)

    def _build_tab_context_menu(self) -> QMenu:
        """创建并缓存标签右键菜单（英文文案，无快捷键）。

        动作与菜单都以 ``item_tabs``（真实 QObject）为父对象：真实窗口与
        ``MainWindow.__new__`` 测试桩（不带完整 Qt 初始化）下都能安全构造。
        两个 action 不带 triggered 连接：真正分派在 ``_show_tab_context_menu``
        里按 exec_ 返回的对象身份完成，避免 `QMenu.exec_` 触发动作再叠加一次。
        """
        parent = self.item_tabs
        close_all = QAction("Close All Items", parent)
        close_right = QAction("Close Items to the Right", parent)
        m = QMenu(parent)
        m.addAction(close_all)
        m.addAction(close_right)
        self._tabRMenu = m
        self._acTabCloseAll = close_all
        self._acTabCloseRight = close_right
        return m

    def close_all_items(self) -> None:
        """安全关闭所有标签与 item（倒序 close 避免索引错位）。"""
        while self.items:
            self.close_item(len(self.items) - 1)

    def close_items_to_the_right(self, index: int) -> None:
        """把 *index* 右侧的所有标签倒序关闭，*index* 本身与左侧不受影响。

        从最右侧倒序 close，被关闭项（``items[-1]``）的索引始终稳定有效，不会
        因先关左侧而错位。越界索引（如右键在空白区得到的 -1）直接忽略。
        """
        count = len(self.items)
        if not (0 <= index < count):
            return
        while count > index + 1:
            count -= 1
            self.close_item(count)

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
        # Preview/bayer are stored per-item even for non-RAW types; they are
        # only meaningful for RAW decoding, which decides by ``image_type``.
        item.options.preview_mode = vals["preview_mode"]
        item.options.bayer_pattern = vals["bayer_pattern"]
        # 注意：这里**不能**顺便清掉标签的未保存标记（●）。本函数还被
        # _on_tab_changed（保存上一标签的面板值）与 _on_preset_selected 调用，
        # 那些场景并没有真正 Apply/解码 → 清 ● 会让用户误以为已生效。
        # 清除标记放在解码真正成功之后（_on_decode_success）。

    def _load_item_to_panel(self, item: ViewerItem) -> None:
        self._loading_item = True
        # Return the preset combo to its placeholder: the panel now reflects
        # this item's own options, which may differ from any saved preset.
        # Leaving a stale preset name selected is what made the combo appear
        # inconsistent with the actual decode parameters.
        self.panel.reset_preset_selection()
        opts = item.options
        self.panel.set_values(
            image_type=opts.image_type,
            format_name=opts.format_name,
            width=opts.width,
            height=opts.height,
            alignment=opts.alignment,
            endianness=opts.endianness,
            offset=opts.offset,
            preview_mode=opts.preview_mode,
            bayer_pattern=opts.bayer_pattern,
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
                self.file_status.setText(f"File: {os.path.basename(path)} ({size:,} bytes)")
                self.file_status.setToolTip(path)
            except OSError:
                self.file_status.setText(f"File: {os.path.basename(path)}")
                self.file_status.setToolTip(path)
        # Show image data size (frame size) as a concrete byte count.
        frame_size = self._get_frame_size(item.options)
        if frame_size > 0:
            self.image_status.setText(
                f"Image: {item.options.width}x{item.options.height} ({frame_size:,} bytes) | Format: {item.options.format_name}"
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

    def _on_panel_values_changed(self) -> None:
        """A decode parameter was edited — flag that Apply is needed."""
        if self._loading_item:
            return
        item = self._current_item()
        if item is not None:
            self._set_state("Unapplied changes — click Apply", "busy")
            # UI-4：标签显示未保存标记（"●"直到 Apply 应用）。
            self._set_tab_dirty(item, True)

    def _set_tab_dirty(self, item: ViewerItem, dirty: bool) -> None:
        """UI-4：当前项对应标签的 tooltip 显示完整路径；未 Apply 时加 ● 前缀。

        只更新与 *item* 对象身份一致的标签（拖拽重排后不误改邻居）。
        """
        items = self.__dict__.get("items") or []
        tabs = self.__dict__.get("item_tabs")
        if tabs is None:
            # 测试常用 MainWindow.__new__ 构造（无标签控件），保持既有契约。
            return
        for i, it in enumerate(items):
            if it is item:
                index = i
                break
        else:
            return
        if dirty:
            self.item_tabs.setTabToolTip(index, f"● {item.options.file_path or ''}")
            self.item_tabs.setTabText(
                index, f"● {os.path.basename(item.options.file_path)}"
            )
        else:
            self.item_tabs.setTabToolTip(index, item.options.file_path or "")
            self.item_tabs.setTabText(
                index, os.path.basename(item.options.file_path)
            )

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

    def _on_apply_clicked(self) -> None:
        """Apply button — decode with size-mismatch warnings enabled.

        Only an explicit Apply validates the file size against the current
        parameters. Opening/dropping a file (or picking a preset) never warns,
        since the default parameters won't match arbitrary inputs until the
        user has actually configured them and pressed Apply.
        """
        self.decode_current(warn_mismatch=True)

    @staticmethod
    def _remaining_bytes(path: str, offset: int) -> int | None:
        """Return the number of file bytes from *offset* to EOF, or None on error."""
        try:
            return max(0, os.path.getsize(path) - offset)
        except OSError:
            return None

    def _read_frame_data(self, path: str, offset: int, read_len: int) -> bytes:
        """Read only the bytes needed for one frame, starting at *offset*.

        Replaces the old ``f.read()``-whole-file approach (H-2): for a huge
        multi-frame capture we seek straight to the current frame's interval
        and read just that slice. Returns ``b""`` if the file can't be opened.
        """
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                return f.read(max(0, read_len))
        except OSError:
            return b""

    def _compute_effective_offset(self, item: ViewerItem) -> tuple[int, int]:
        """Return ``(frame_size, effective_offset)`` for *item*'s current frame.

        ``effective_offset`` is the base offset plus ``frame_index * frame_size``
        so multi-frame files seek into the right frame rather than reading from
        the file start.
        """
        opts = item.options
        frame_size = self._get_frame_size(opts)
        effective_offset = opts.offset
        if frame_size > 0 and item.current_frame > 0:
            effective_offset = opts.offset + item.current_frame * frame_size
        return frame_size, effective_offset

    def _expected_frame_size(self, opts: DecodeOptions, width: int, height: int) -> int:
        """Size of one frame for *opts*'s format, or -1 when unknown/invalid."""
        try:
            if opts.image_type == "RAW" or opts.format_name in (
                "RAW8", "RAW10", "RAW12", "RAW16", "RAW32",
                "RAW10 Packed", "RAW12 Packed", "RAW14 Packed",
            ):
                return expected_frame_size_raw(opts.format_name, width, height)
            return expected_frame_size_yuv(
                opts.format_name, width, height,
                alignment=opts.alignment, endianness=opts.endianness,
            )
        except Exception:
            return -1

    def decode_current(self, warn_mismatch: bool = False) -> None:
        item = self._current_item()
        if item is None:
            return
        path = item.options.file_path
        if not path:
            return
        self._save_panel_to_item(item)

        opts = item.options

        logger.debug(
            "Decode request: %s (type=%s, format=%s, %dx%d, frame=%d)",
            path, opts.image_type, opts.format_name,
            opts.width, opts.height, item.current_frame,
        )

        # Recompute total frames with current parameters BEFORE computing
        # effective offset, so that the frame count is up to date when
        # the user changes width/height/format (see #1.0).
        self._compute_frame_info(item)
        # Clamp current_frame — the new parameters may support fewer frames.
        item.current_frame = max(
            0, min(item.current_frame, max(0, item.total_frames - 1))
        )

        frame_size, effective_offset = self._compute_effective_offset(item)
        expected = self._expected_frame_size(opts, opts.width, opts.height)

        # Standard Image — decode synchronously; load_bgr_image() reads and
        # decodes the file itself, so we never read the raw bytes here.
        if opts.image_type == "Standard Image":
            self._decode_standard_image(item, opts)
            return

        # RAW / YUV: validate remaining bytes against one frame, then seek-read
        # only the current frame interval instead of slurping the whole file.
        actual = self._remaining_bytes(path, effective_offset)
        if actual is None:
            QMessageBox.critical(self, "Read Error", f"Failed to read file size: {path}")
            return

        # 单帧内存上限（512MB）——GUI 与 CLI/batch 统一保护，防超大宽高 OOM
        # （65535×65535 + RAW32 单帧约 16 GiB）。超限提示且不启动读取/worker。
        if expected > 0:
            try:
                require_decode_size(opts.width, opts.height, expected)
            except FormatError as exc:
                QMessageBox.critical(
                    self, "Frame Too Large",
                    f"{exc}\n\n请降低宽度/高度，或换用更低位深/非 32-bit 格式。",
                )
                self._set_state("Decode rejected: frame too large", "error")
                return

        # Only an explicit Apply pops the size-mismatch dialog; open/drop and
        # frame navigation never interrupt with the same question.
        if warn_mismatch and expected > 0 and actual < expected:
            if not self._warn_size_mismatch(self, actual, expected):
                return

        # P1-1 缓存命中：同一帧/同参数已解码过 → 直接复用显示结果，跳过
        # 读取与后台线程。只有与最新参数、当前项一致的缓存才被消费
        # （缓存键覆盖全部参数 + 帧号，天然隔离不同项/不同参数）。
        # 注：测试常用 ``MainWindow.__new__``（不跑 __init__）构造，没有
        # ``decode_cache`` 属性；且 getattr 在该类实例上可能因 Qt 包装层抛
        # RuntimeError 而非 AttributeError，故用 ``__dict__`` 探测并惰性补建，
        # 保持既有测试契约不变。
        cache = self.__dict__.get("decode_cache")
        if cache is None:
            cache = self.decode_cache = DecodeCache()
        cache_key = DecodeCache.key(opts, item.current_frame)
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "Decode cache hit: frame=%d format=%s %dx%d",
                item.current_frame, opts.format_name, opts.width, opts.height,
            )
            # 与 _on_decode_finished 一致：命中时也要回填当前显示数组，
            # 否则 save_display / 后续帧缓存写回会读到过期数据。
            item.current_display = cached.display_array
            # 关键顺序：先落宽/高、再 _on_decode_success。_on_decode_success 内用
            # _get_frame_size(item.options) 计算状态栏帧字节数，若仍读旧宽高，
            # 缓存命中的状态栏会显示过期帧大小（0.3.0-M-3 / 0.4.0-M-1 /
            # 0.4.1-M-1 三版沿用）；先更新 options 与 _on_decode_finished 一致。
            item.options.width = cached.width
            item.options.height = cached.height
            # 0.3.0-L-3：命中时若先前有同参数 worker 仍在跑，其完成信号会因
            # pending 置空而不再绘制（_should_apply_decode 拒绝），但让旧线程
            # 继续跑完 + 重复写缓存仍是浪费；解引用/断开它，避免重复消费。
            self._cancel_async_decode()
            self._pending_decode_item = None
            self._on_decode_success(item, cached.qimage, cached.width, cached.height, cached.format_name)
            self._set_state("Decoded (cached)", "ok")
            return

        read_len = frame_size if frame_size > 0 else actual
        data = self._read_frame_data(path, effective_offset, read_len)
        if not data and actual > 0:
            QMessageBox.critical(
                self, "Read Error",
                f"Failed to read {read_len} bytes at offset {effective_offset}: {path}",
            )
            return

        # RAW/YUV — async
        self._start_async_decode(data, item, opts, effective_offset)

    def _decode_standard_image(self, item: ViewerItem, opts: DecodeOptions) -> None:
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
            logger.debug("Standard image decoded: %s (%dx%d)", opts.file_path, w, h)
            self._on_decode_success(item, qimg, w, h, "Standard Image")
        except Exception as exc:
            logger.exception("Failed to decode standard image: %s", opts.file_path)
            QMessageBox.critical(self, "Decode Failed", str(exc))
            self._set_state("Decode failed", "error")
        finally:
            # A standard-image decode is synchronous and takes over the
            # display; an in-flight async result must not clobber it later.
            self._pending_decode_item = None

    def _start_async_decode(self, data: bytes, item: ViewerItem, opts: DecodeOptions, effective_offset: int) -> None:
        # Detach from any in-flight decode, then bump the generation so its
        # (unavoidably late) results are recognised as stale and dropped.
        self._cancel_async_decode()
        self._decode_generation += 1
        self._pending_decode_item = item

        # Preview/bayer come from the item's own saved options — not whatever
        # the shared panel currently shows — so tab switches can't leak values
        # between items (M-1). They only affect RAW; YUV ignores them.
        preview_mode = opts.preview_mode
        bayer_pattern = opts.bayer_pattern

        # IMPORTANT: `data` is already the single frame extracted at
        # `effective_offset` (decode_current -> _read_frame_data seeks to that
        # offset and reads frame_size bytes). The decode spec must therefore use
        # offset=0 — otherwise decode_raw's _slice_frame would offset into an
        # already-sliced buffer a second time, making every frame after the
        # first report "data too short: need 2x bytes" (regression from the H-2
        # seek-read optimisation). The real source offset is remembered only
        # for error messages via `source_offset`.
        spec = ImageSpec(opts.width, opts.height, 0)

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
            generation=self._decode_generation,
            file_path=opts.file_path,
            frame_index=item.current_frame,
            source_offset=effective_offset,
        )

        # Lifecycle: when THIS worker finishes/errors, quit its thread's event
        # loop and hand the worker to Qt via deleteLater (processed on the
        # worker thread as the loop winds down); the QThread wrapper is deleted
        # by deleteLater once the thread has stopped. Both lambdas close over
        # the exact worker/thread objects they were created for, so a stale
        # worker signalling late can never clean up — or deleteLater — a newer,
        # still-active worker.
        worker = self._worker
        thread = self._thread
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_decode_finished)
        self._worker.error.connect(self._on_decode_error)
        # deleteLater() is queued on the worker thread BEFORE quit(), so the
        # deferred delete is processed as the event loop unwinds; the QThread
        # wrapper is deleteLater'd once the thread has fully stopped.
        self._worker.finished.connect(lambda *_: worker.deleteLater())
        self._worker.finished.connect(lambda *_: thread.quit())
        self._worker.error.connect(lambda *_: worker.deleteLater())
        self._worker.error.connect(lambda *_: thread.quit())
        self._thread.finished.connect(thread.deleteLater)

        self._set_state("Decoding...", "busy")
        self._thread.start()

    def _cancel_async_decode(self) -> None:
        """Detach from any in-flight decode without blocking the UI thread.

        设计取舍（0.1.1-M-1）：worker 是纯 CPU 计算且无事件循环，`quit()` 无法
        中止；numpy/OpenCV 解码也不提供安全的分块取消点。因此这里"取消"= 停止
        跟踪该 worker。其迟到的 `finished`/`error` 由主线程 generation check
        （`_should_apply_decode`）丢弃；线程与 worker 的生命周期始终由
        finished/error → thread.quit()/deleteLater 连接保证，**绝不能在 worker
        内 suppress 信号**（否则线程/worker 永久泄漏——复查已确认该坑）。

        若未来需要真正停止 CPU 工作：把 decode 拆成分块循环并检查
        ``threading.Event``（各循环间是一个安全的中断点）。
        """
        # 用 __dict__.get：MainWindow.__new__ 测试桩可能没有 _thread/_worker /
        # _pending_decode_item 属性（直接 self._thread 会因 Qt 包装层未初始化抛
        # RuntimeError）。
        if self.__dict__.get("_thread") is not None:
            logger.debug("Detached from in-flight decode thread")
        self._thread = None
        self._worker = None
        self._pending_decode_item = None

    def _disconnect_decode(self) -> None:
        """彻底断开主窗口与在途解码 worker/thread 的连接。

        关闭标签/窗口时调用：先解引用（``_cancel_async_decode`` 本体），再断开
        曾经连回 ``_on_decode_finished``/``_on_decode_error`` 的槽——防止被关闭
        item 的 worker 迟到信号把结果画到**新**的当前标签上。``thread.deleteLater``
        /``thread.quit()`` 的连接保留，线程与 worker 的生命周期仍由
        finished/error → quit()/deleteLater 保证（绝不 suppress 信号，否则线程
        /worker 永久泄漏，0.1.1-M-1 复查结论）。断开只在存在 worker 值时执行，
        避免在 PyQt 包装层之外（``MainWindow.__new__`` 测试桩）触碰 ``Signal``
        对象抛 RuntimeError。
        """
        worker = self._worker
        self._cancel_async_decode()
        if worker is not None:
            try:
                worker.finished.disconnect(self._on_decode_finished)
                worker.error.disconnect(self._on_decode_error)
            except (TypeError, RuntimeError):
                pass

    def _cancel_decode_for(self, item: ViewerItem | None) -> None:
        """若 *item* 正是当前在途解码的目标，则断开其 worker 的连接。

        ``_should_apply_decode`` 用「代数一致 + item 对象身份」判定结果是否
        应用；一旦断开并把 ``_pending_decode_item`` 置空（``_cancel_async_decode``
        内部），代数不需要变化也能让迟到的信号被丢弃（``item is None`` 或
        ``id(pending) != id(item)``）。这里不递增代数，避免无谓地让其它在途
        结果作废；等价于 0.3.0 审查 L-3 建议的“命中/关闭时也清理在途引用”。
        """
        if item is None:
            return
        # 用 __dict__.get：MainWindow.__new__ 测试桩可能没有该属性。
        if self.__dict__.get("_pending_decode_item") is item:
            self._disconnect_decode()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        """窗口关闭/退出：断开在途解码的信号（0.2.2-L-1 / 0.3.1-L-1 / 0.4.x-M-2）。

        worker 是纯 CPU 计算且无事件循环，``quit()`` 无法中止（见
        ``_cancel_async_decode`` 注释）；这里做的三件事：
        1. 断开 finished/error → ``_on_decode_*`` 连接，防止被关闭窗口的迟到
           结果在窗口销毁期间触碰即将失效的 QLabel/QWidget（Qt 对象销毁后的
           访问是崩溃点）。
        2. 解引用（``_cancel_async_decode``），让 ``_pending_decode_item`` 置空。
        3. 对仍在跑的线程做**有界**等待（短超时）——小图帧在窗口关闭时即完成并
           走正常的 quit()/deleteLater 清理；大帧超时则不做无界阻塞（不在 UI
           线程长时间等待），线程/worker 生命周期仍由 finished/error →
           quit()/deleteLater 连接保证继续收尾。
        """
        thread = self.__dict__.get("_thread")
        self._disconnect_decode()
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.wait(400)
            except RuntimeError:
                # 已完成解码的 QThread 经 deleteLater 释放 C++ 对象后，Python 侧
                # 包装仍可能被 self._thread 引用（_on_decode_finished 不置空）；
                # 对已删除对象调用 isRunning/wait 会抛 RuntimeError，这里静默跳过
                # ——线程已退出，无需等待。
                pass
        super().closeEvent(event)

    def _should_apply_decode(self, generation: int, item: ViewerItem | None) -> bool:
        """Whether a worker result/error carrying *generation* should be applied.

        True only when the request that produced it is still the latest one
        (generation matches) AND it targeted the currently displayed item
        (identity check via ``id()``). Anything else is dropped, which is what
        stops an abandoned (but still running) decode from overwriting the
        visible tab (H-1).
        """
        if item is None:
            return False
        if generation != self._decode_generation:
            return False
        return id(item) == id(self._pending_decode_item)

    def _on_decode_finished(self, generation: int, result) -> None:
        item = self._current_item()
        if not self._should_apply_decode(generation, item):
            logger.debug(
                "Discarding stale decode result (gen=%d, current=%d)",
                generation, self._decode_generation,
            )
            return
        self._pending_decode_item = None
        item.current_display = result.display_array
        item.options.width = result.width
        item.options.height = result.height
        # P1-1：把这份刚算好的帧写入解码缓存，供前后翻帧复用
        # （键覆盖当前参数，只对相同配置的再次请求命中）。
        # 测试（MainWindow.__new__）未建缓存属性 → 用 __dict__ 探测并惰性补建。
        cache = self.__dict__.get("decode_cache")
        if cache is not None:
            cache.store(DecodeCache.key(item.options, item.current_frame), result)
        self._on_decode_success(item, result.qimage, result.width, result.height, result.format_name)

    def _on_decode_success(self, item: ViewerItem, qimg: QImage, width: int, height: int, format_name: str) -> None:
        # UI-4：解码真正成功后，用户看到的即当前参数 → 清除标签未保存标记 ●
        # （缓存命中与后台线程结果都汇聚到这里；不能放在 _save_panel_to_item，
        #   那会被 tab 切换/预设选择误触发）。
        self._set_tab_dirty(item, False)
        item.view.set_pixmap(QPixmap.fromImage(qimg))
        item.view.fit_image()
        path = item.options.file_path
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        self.file_status.setText(f"File: {os.path.basename(path)} ({size:,} bytes)")
        self.file_status.setToolTip(path)
        frame_size = self._get_frame_size(item.options)
        if frame_size > 0:
            self.image_status.setText(
                f"Image: {width}x{height} ({frame_size:,} bytes) | Format: {format_name}"
            )
        else:
            self.image_status.setText(f"Image: {width}x{height} | Format: {format_name}")
        self._set_state("Decoded", "ok")
        self._update_frame_display(item)

    def _on_decode_error(self, generation: int, message: str) -> None:
        item = self._current_item()
        if not self._should_apply_decode(generation, item):
            logger.debug(
                "Discarding stale decode error (gen=%d, current=%d)",
                generation, self._decode_generation,
            )
            return
        self._pending_decode_item = None
        QMessageBox.critical(self, "Decode Failed", message)
        self._set_state("Decode failed", "error")

    # ── Save ─────────────────────────────────────────────────────────

    def save_display(self) -> None:
        item = self._current_item()
        if item is None or item.current_display is None:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save Image", "", "PNG (*.png);;JPEG (*.jpg *.jpeg)"
        )
        if not path:
            return
        # If the user typed a path without an extension, append one that
        # matches the filter they picked from the dialog.
        if not Path(path).suffix.lower():
            if "JPEG" in (selected_filter or ""):
                path = f"{path}.jpg"
            else:
                path = f"{path}.png"
        ext = Path(path).suffix.lower()
        img = item.current_display
        if img.ndim == 2:
            qimg = self._qimage_from_gray(img)
        else:
            qimg = self._qimage_from_rgb(img)
        dpi = self.settings.save_dpi
        dpm = dpi_to_dots_per_meter(dpi)
        qimg.setDotsPerMeterX(dpm)
        qimg.setDotsPerMeterY(dpm)
        fmt = "JPEG" if ext in (".jpg", ".jpeg") else "PNG"
        ok = qimg.save(path, fmt.upper())
        if not ok:
            QMessageBox.critical(
                self, "Save Failed",
                f"Could not save image to:\n{path}\n\n"
                "The file may be in use, the directory may be read-only, "
                "or the format may be unsupported.",
            )
            self._set_state("Save failed", "error")
            return
        self._set_state(f"Saved: {os.path.basename(path)} @ {dpi} DPI", "ok")

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
        # Reflect the toggle state in the menu label (checkmark shows too).
        self.fullscreen_action.setText("Exit Fullscreen" if checked else "Fullscreen")

    def keyPressEvent(self, event):  # noqa: N802
        """Handle keyboard: Up/Down for frame nav, Escape exits fullscreen."""
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.fullscreen_action.setChecked(False)
            self._toggle_fullscreen(False)
            event.accept()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Left):
            item = self._current_item()
            if item:
                self._nav_frame(item, -1)
            event.accept()
            return
        if event.key() in (Qt.Key_Down, Qt.Key_Right):
            item = self._current_item()
            if item:
                self._nav_frame(item, 1)
            event.accept()
            return
        if event.key() == Qt.Key_Home:
            item = self._current_item()
            if item and item.total_frames > 1 and item.current_frame != 0:
                item.current_frame = 0
                if item.frame_nav is not None:
                    item.frame_nav.set_frame_index(0)
                self.decode_current()
            event.accept()
            return
        if event.key() == Qt.Key_End:
            item = self._current_item()
            if item and item.total_frames > 1 and item.current_frame != item.total_frames - 1:
                item.current_frame = item.total_frames - 1
                if item.frame_nav is not None:
                    item.frame_nav.set_frame_index(item.current_frame)
                self.decode_current()
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

    def show_shortcuts(self) -> None:
        dlg = KeyboardShortcutsDialog(self)
        dlg.exec_()

    def show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec_()

    def open_convert_dialog(self) -> None:
        dlg = ConvertDialog(self.settings, self)
        dlg.exec_()

    def open_batch_convert_dialog(self) -> None:
        dlg = BatchConvertDialog(self.settings, self)
        dlg.exec_()

    def open_fourcc_dialog(self) -> None:
        dlg = FourCCDialog(self.settings, self)
        dlg.exec_()

    def open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec_():
            self._apply_theme()
            self._apply_titlebar_theme()
        # Presets may have been edited inside the Settings dialog — keep the
        # panel combo in sync regardless of accept/reject so deletions show up.
        self._refresh_preset_combo()

    # ── Sensor presets ───────────────────────────────────────────────

    def _refresh_preset_combo(self, current: str | None = None) -> None:
        """Reload the preset combo from persisted settings."""
        names = [p.name for p in self.settings.sensor_presets]
        self.panel.set_preset_names(names, current=current)

    def _on_preset_selected(self, name: str) -> None:
        """User picked a preset — populate the panel and trigger Apply."""
        preset = self.settings.get_sensor_preset(name)
        if preset is None:
            logger.warning("Preset '%s' not found in settings", name)
            self._refresh_preset_combo()
            return
        # Push every value from the preset onto the panel. set_values follows
        # the same key names as the preset dataclass, so we can splat directly.
        self.panel.set_values(
            image_type=preset.image_type,
            format_name=preset.format_name,
            width=preset.width,
            height=preset.height,
            alignment=preset.alignment,
            endianness=preset.endianness,
            offset=preset.offset,
            preview_mode=preset.preview_mode,
            bayer_pattern=preset.bayer_pattern,
        )
        # Selecting a preset only populates the panel — the user must press
        # Apply for it to take effect. Persist the values onto the current
        # item so the panel and the item stay in sync (and so switching tabs
        # back and forth doesn't silently revert the just-picked preset),
        # but do NOT decode here.
        item = self._current_item()
        if item is not None:
            self._save_panel_to_item(item)
        self._set_state(f"Preset '{name}' loaded — click Apply", "busy")

    def _on_save_preset_clicked(self) -> None:
        """Persist the panel's current values as a named preset."""
        existing = [p.name for p in self.settings.sensor_presets]
        default_name = ""
        name, ok = QInputDialog.getText(
            self,
            "Save sensor preset",
            "Preset name:",
            text=default_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Save preset", "Preset name must not be empty.")
            return
        if name in existing:
            confirm = QMessageBox.question(
                self,
                "Overwrite preset",
                f"Preset '{name}' already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        vals = self.panel.get_values()
        preset = SensorPreset(
            name=name,
            image_type=vals["image_type"],
            format_name=vals["format_name"],
            width=int(vals["width"]),
            height=int(vals["height"]),
            alignment=vals["alignment"],
            endianness=vals["endianness"],
            offset=int(vals["offset"]),
            preview_mode=vals["preview_mode"],
            bayer_pattern=vals["bayer_pattern"],
        )
        try:
            self.settings.save_sensor_preset(preset)
        except ValueError as exc:
            QMessageBox.warning(self, "Save preset", str(exc))
            return
        self._refresh_preset_combo(current=name)
        self._set_state(f"Preset '{name}' saved", "ok")

    def _open_preset_manager_dialog(self) -> None:
        dlg = PresetManagerDialog(self.settings, self)
        dlg.exec_()
        # Re-read regardless of dialog result — the dialog only persists on
        # Save anyway, but this also picks up any external edits.
        self._refresh_preset_combo()


# ENG-4：未捕获异常的全局兜底 —— 把 Python 主线程与 Qt 槽（pyqtSlot 抛出的
# 异常不会冒泡到解释器）里的异常写入独立 crash log，避免静默崩溃无从排查。
_CRASH_ORIGINAL_EXCEPTHOOK = None


def _install_crash_hooks() -> None:
    global _CRASH_ORIGINAL_EXCEPTHOOK
    if _CRASH_ORIGINAL_EXCEPTHOOK is not None:
        return
    _CRASH_ORIGINAL_EXCEPTHOOK = sys.excepthook

    def _crash_hook(exc_type, exc_value, exc_tb) -> None:
        try:
            logger.critical(
                "Uncaught exception: %s: %s",
                exc_type.__name__, exc_value,
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:
            pass
        if callable(_CRASH_ORIGINAL_EXCEPTHOOK):
            _CRASH_ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_hook

    try:
        from PyQt5.QtCore import qInstallMessageHandler

        def _qt_message(msg_type, context, message) -> None:
            if msg_type in (2, 3):  # QtWarningMsg / QtCriticalMsg
                logger.warning("Qt message: %s", message)
            elif msg_type == 4:  # QtFatalMsg
                logger.critical("Qt fatal: %s", message)

        qInstallMessageHandler(_qt_message)
    except Exception:
        pass  # 非 Qt 环境（测试用 offscreen）可忽略


def run(files: list[str] | None = None) -> None:
    """Application entry point — create QApplication and show MainWindow.

    Parameters
    ----------
    files : list of str, optional
        File paths to open on startup (from CLI arguments).
    """
    _install_crash_hooks()
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(app_icon())
    w = MainWindow()
    w.resize(1200, 700)
    w.show()
    if files:
        for path in files:
            w._open_item(path, decode=False)
        if files:
            w.decode_current()
    app.exec_()
