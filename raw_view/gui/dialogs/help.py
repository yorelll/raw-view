"""Format help dialog displaying embedded HTML content."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from raw_view.help_content import HELP_HTML
from raw_view.models import APP_VERSION


class HelpDialog(QDialog):
    """Read-only dialog that explains RAW/YUV format layout rules."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Format Help")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setAccessibleName("Format help")
        browser.setAccessibleDescription("Read-only help for RAW and YUV format layout rules.")
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser)
        close_btn = QPushButton("Close")
        close_btn.setAccessibleName("Close format help")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        self.resize(760, 560)


class AboutDialog(QDialog):
    """P2-4：About 对话框 —— 从单一版本常量 (models.APP_VERSION) 读取版本号。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About raw-view")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("RAW/YUV Viewer")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        version = QLabel(f"Version {APP_VERSION}")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet("color: #9AA0AC;")
        layout.addWidget(version)

        desc = QLabel(
            "RAW / YUV image viewer and format converter.\n"
            "Supports RAW8–32 (aligned & MIPI packed), YOnly (4:0:0)\n"
            "and common YUV 4:2:0 / 4:2:2 sub-formats."
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setAccessibleName("Close about")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
