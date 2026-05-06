"""QLineEdit subclass that accepts file drag-and-drop."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QLineEdit, QWidget


class FileDropLineEdit(QLineEdit):
    """A text input that accepts a single file dropped from the OS."""

    fileDropped = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    # Qt override — must keep camelCase.
    def dragEnterEvent(self, event: QDragEnterEvent):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    # Qt override — must keep camelCase.
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
