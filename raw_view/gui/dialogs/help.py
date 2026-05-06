"""Format help dialog displaying embedded HTML content."""

from __future__ import annotations

from PyQt5.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QWidget

from raw_view.help_content import HELP_HTML


class HelpDialog(QDialog):
    """Read-only dialog that explains RAW/YUV format layout rules."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Format Help")
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser)
        self.resize(760, 560)
