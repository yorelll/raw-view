"""Custom QGraphicsView subclass for image display with zoom and context menu."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)


class ImageView(QGraphicsView):
    """Zoomable, pannable image view with context menu and wheel-zoom support."""

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
        self._img_width = 0
        self._img_height = 0

    # ── public API ───────────────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap_item.setPixmap(pixmap)
        self._img_width = pixmap.width()
        self._img_height = pixmap.height()
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

    def zoom_to(self, percent: int) -> None:
        """Zoom to a specific percentage (clamped 10–1000)."""
        percent = max(10, min(1000, percent))
        factor = percent / self._zoom_percent
        self._apply_zoom_step(factor, emit=False)
        self._zoom_percent = percent
        self.zoomChanged.emit(self._zoom_percent)

    def has_image(self) -> bool:
        """Return whether the view currently contains a non-empty pixmap."""
        return not self._pixmap_item.pixmap().isNull()

    def current_pixmap(self) -> QPixmap:
        """Return the pixmap currently displayed in the view."""
        return self._pixmap_item.pixmap()

    @property
    def zoom_percent(self) -> int:
        return self._zoom_percent

    @property
    def image_size(self) -> tuple[int, int]:
        """Return (width, height) of the currently displayed image."""
        return (self._img_width, self._img_height)

    # ── internals ────────────────────────────────────────────────────

    def _apply_zoom_step(self, factor: float, *, emit: bool = True) -> None:
        old_pct = self._zoom_percent
        new_pct = max(10, min(1000, int(round(old_pct * factor))))
        actual_factor = new_pct / old_pct
        self.scale(actual_factor, actual_factor)
        self._zoom_percent = new_pct
        if emit:
            self.zoomChanged.emit(self._zoom_percent)

    # ── Qt event overrides ───────────────────────────────────────────

    def wheelEvent(self, event):  # noqa: N802
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            self._apply_zoom_step(1.25 if event.angleDelta().y() > 0 else 0.8)
            return
        super().wheelEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        self.contextMenuRequested.emit(self, event.globalPos())
