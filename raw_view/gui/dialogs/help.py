"""Format help dialog displaying embedded HTML content."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from raw_view.gui.dialogs._form import (
    _dialog_height_cap,
    _fit_dialog_height,
    _footer_with,
)
from raw_view.help_content import HELP_HTML, SHORTCUTS
from raw_view.models import (
    APP_VERSION,
    THEME_PALETTES,
    normalize_ui_theme,
)


def build_help_document_css(theme: str, font_size: int) -> str:
    """Return theme-aware document CSS for the Format Help browser."""
    palette = THEME_PALETTES[normalize_ui_theme(theme)]
    size = int(font_size)
    return f"""
        body {{
            color: {palette["text_color"]};
            background-color: {palette["panel_bg"]};
            border: 1px solid {palette["border_color"]};
            padding: 12px;
            font-size: {size}px;
        }}
        p.help-h2 {{
            color: {palette["text_color"]};
            font-size: {size + 3}px;
            font-weight: 600;
        }}
        p.help-h3 {{
            color: {palette["text_color"]};
            font-size: {size + 1}px;
            font-weight: 600;
        }}
        p, li {{
            color: {palette["text_color"]};
            font-size: {size}px;
        }}
        code {{
            font-family: "Cascadia Mono", "Consolas", monospace;
            color: {palette["text_secondary"]};
            font-size: {size}px;
        }}
        a {{
            color: {palette["accent"]};
        }}
    """


def build_help_document_html(theme: str, font_size: int) -> str:
    """Return HELP_HTML with heading tags mapped to explicit CSS classes.

    Qt's rich-text engine applies its built-in h2/h3 sizes after
    ``setDefaultStyleSheet``, so a stylesheet rule alone cannot shrink them.
    Mapping the headings to ``<p class="help-h2|h3">`` makes the requested
    pixel sizes authoritative while leaving the visible text untouched.
    """
    html = HELP_HTML.replace("<h2>", '<p class="help-h2">').replace("</h2>", "</p>")
    return html.replace("<h3>", '<p class="help-h3">').replace("</h3>", "</p>")


class KeyboardShortcutsDialog(QDialog):
    """Standalone reference for supported keyboard shortcuts (0.4.1).

    Shortcuts live in a structured ``SHORTCUTS`` constant in help_content.py so
    they can be tested against the real ``setShortcut`` calls in app.py and kept
    out of the Format Help HTML (which is about RAW/YUV byte layout).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(460)
        self.resize(520, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel("Supported keyboard shortcuts:")
        intro.setObjectName("groupTitle")
        layout.addWidget(intro)

        content = QWidget()
        content.setObjectName("transparentPage")
        content_grid = QVBoxLayout(content)
        content_grid.setContentsMargins(0, 0, 0, 0)
        content_grid.setSpacing(10)
        for category, items in SHORTCUTS:
            box = QGroupBox(category)
            box.setObjectName("card")
            grid = QGridLayout(box)
            grid.setColumnStretch(1, 1)
            for row, (keys, desc) in enumerate(items):
                key_label = QLabel(keys)
                key_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(key_label, row, 0)
                desc_label = QLabel(desc)
                desc_label.setWordWrap(True)
                grid.addWidget(desc_label, row, 1)
            content_grid.addWidget(box)
        content_grid.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # M-3: the Close button belongs to the same secondary/right-aligned
        # family as Help and About instead of qt-material's default button.
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.setAccessibleName("Close keyboard shortcuts")
        close_btn.clicked.connect(self.accept)
        layout.addLayout(_footer_with(None, close_btn))
        _fit_dialog_height(self)


class HelpDialog(QDialog):
    """Read-only dialog that explains RAW/YUV format layout rules."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        theme: str | None = None,
        font_size: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Format Help")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        settings = getattr(parent, "settings", None)
        if theme is None:
            theme = getattr(settings, "ui_theme", "light")
        if font_size is None:
            font_size = getattr(settings, "ui_font_size", 13)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setObjectName("helpBrowser")
        browser.setAccessibleName("Format help")
        browser.setAccessibleDescription("Read-only help for RAW and YUV format layout rules.")
        browser.document().setDefaultStyleSheet(
            build_help_document_css(theme, int(font_size))
        )
        browser.setHtml(build_help_document_html(theme, int(font_size)))
        layout.addWidget(browser, 1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.setAccessibleName("Close format help")
        close_btn.clicked.connect(self.accept)
        layout.addLayout(_footer_with(None, close_btn))
        self.resize(760, 560)
        # A help browser is a scroll area whose sizeHint is a fixed viewport
        # guess, so keep a readable floor and let the shared cap trim it on a
        # short screen (L-3).
        self.setMinimumHeight(min(520, _dialog_height_cap(self)))
        _fit_dialog_height(self, browser)


class AboutDialog(QDialog):
    """P2-4：About 对话框 —— 从单一版本常量 (models.APP_VERSION) 读取版本号。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About raw-view")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # M-12: one product name everywhere — the window title, the Help menu
        # entry and this body used to spell it two different ways.
        title = QLabel("raw-view")
        title.setObjectName("aboutTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        version = QLabel(f"Version {APP_VERSION}")
        version.setObjectName("aboutVersion")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)

        desc = QLabel(
            "RAW / YUV image viewer and format converter.\n"
            "Supports RAW8–32 (aligned & MIPI packed), YOnly (4:0:0)\n"
            "and common YUV 4:2:0 / 4:2:2 sub-formats."
        )
        # L-4: centered text wrapped ``(4:0:0)`` onto a line of its own.  Left
        # alignment inside a fixed 320 px block gives natural line breaks; the
        # block itself stays horizontally centered in the dialog.
        desc.setAlignment(Qt.AlignLeft)
        desc.setWordWrap(True)
        desc.setFixedWidth(320)
        layout.addWidget(desc, 0, Qt.AlignHCenter)

        layout.addStretch(1)
        # M-3: the About Close was a full-width solid qt-material button, three
        # times the size of Help's.  It is a secondary action, right-aligned.
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.setAccessibleName("Close about")
        close_btn.clicked.connect(self.accept)
        layout.addLayout(_footer_with(None, close_btn))
        # L-4: keep the dialog narrow around the fixed 320 px description block.
        self.resize(380, 240)
        # L-3: shared dialog height policy.
        _fit_dialog_height(self)
