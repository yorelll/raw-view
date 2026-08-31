"""Custom QGraphicsView subclass for image display with zoom and context menu."""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)


def _clamp_zoom_percent(percent: float) -> int:
    """把缩放百分比夹到合法显示范围 (10–1000) 并取整。"""
    return max(10, min(1000, int(round(percent))))


def _fit_scale_percent(scene_rect: QRectF, view_size: tuple[int, int]) -> int:
    """根据场景矩形与视图可用像素尺寸，数学上计算 Fit 的实际缩放百分比。

    返回以 1% 为下界的整数百分比。直接从矩形尺寸推导，不依赖 QTransform
    的 m11 —— m11 在旋转/翻转后不再等于纯比例，会导致滚动区和双击
    Fit/1:1 切换误判。
    """
    if scene_rect.isNull() or scene_rect.width() <= 0 or scene_rect.height() <= 0:
        return 100
    vw, vh = view_size
    if vw <= 0 or vh <= 0:
        return 100
    fit_scale = min(vw / scene_rect.width(), vh / scene_rect.height())
    return max(1, int(round(fit_scale * 100)))


class ImageView(QGraphicsView):
    """Zoomable, pannable image view with context menu and wheel-zoom support."""

    zoomChanged = pyqtSignal(int)
    contextMenuRequested = pyqtSignal(object, object)
    framePrevRequested = pyqtSignal()
    frameNextRequested = pyqtSignal()

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
        # 当前生效的旋转角度（90° 的整数倍），与 QTransform 一起维护，
        # 保证 fit/reset/zoom_to 重建变换时旋转不被丢失。
        self._rotation = 0

    # ── public API ───────────────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap_item.setPixmap(pixmap)
        self._img_width = pixmap.width()
        self._img_height = pixmap.height()
        self.resetTransform()
        self._zoom_percent = 100
        self._rotation = 0
        self.zoomChanged.emit(self._zoom_percent)
        self.setSceneRect(self._pixmap_item.boundingRect())

    def zoom_in(self) -> None:
        self._apply_zoom_step(1.25)

    def zoom_out(self) -> None:
        self._apply_zoom_step(0.8)

    def reset_zoom(self) -> None:
        """重置为 1:1 —— 只清掉缩放，保留用户已设置的旋转方向。"""
        self._apply_rotation_then_scale(1.0)
        self._zoom_percent = 100
        self.zoomChanged.emit(self._zoom_percent)

    def fit_image(self) -> None:
        if self.sceneRect().isNull():
            return
        # 先用 Fit 的目标视图区与场景矩形数学算出缩放比例（不受旋转影响），
        # 再整体重建变换：应用已有旋转后直接按该比例缩放，避免 fitInView
        # 在旋转变换上叠加 m11 语义错乱。viewport 尺寸不含滚动条。
        percent = _fit_scale_percent(
            self.sceneRect(),
            (self.viewport().contentsRect().width(), self.viewport().contentsRect().height()),
        )
        self._rotation = self._rotation % 360
        self._apply_rotation_then_scale(percent / 100.0)
        self._zoom_percent = _clamp_zoom_percent(percent)
        self.zoomChanged.emit(self._zoom_percent)

    def zoom_to(self, percent: int) -> None:
        """Zoom to a specific percentage (clamped 10–1000)."""
        percent = _clamp_zoom_percent(percent)
        self._apply_rotation_then_scale(percent / 100.0)
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

    # ── Rotate / Flip ───────────────────────────────────────────────

    def rotate_cw(self) -> None:
        """Rotate 90 degrees clockwise."""
        self.rotate(90)
        self._rotation = (self._rotation + 90) % 360

    def rotate_ccw(self) -> None:
        """Rotate 90 degrees counter-clockwise."""
        self.rotate(-90)
        self._rotation = (self._rotation - 90) % 360

    def flip_horizontal(self) -> None:
        """Flip the view horizontally."""
        self.scale(-1, 1)

    def flip_vertical(self) -> None:
        """Flip the view vertically."""
        self.scale(1, -1)

    # ── internals ────────────────────────────────────────────────────

    def _apply_zoom_step(self, factor: float, *, emit: bool = True) -> None:
        old_pct = self._zoom_percent
        new_pct = _clamp_zoom_percent(old_pct * factor)
        actual_factor = new_pct / old_pct
        self.scale(actual_factor, actual_factor)
        self._zoom_percent = new_pct
        if emit:
            self.zoomChanged.emit(self._zoom_percent)

    def _apply_rotation_then_scale(self, factor: float) -> None:
        """重建变换：先复位，再应用已跟踪的旋转，最后按 *factor* 等比缩放。

        统一绘制冻结期间（QGraphicsView 的 ``transform()`` 在视图未显示时
        可能仍是恒等变换）的做法：旋转先于缩放，保证旋转角度始终生效、
        且等比缩放叠加在旋转上的比例是纯数值（与是否旋转无关）。
        供 fit_image / zoom_to / reset_zoom 重建完整变换时使用。
        """
        self.resetTransform()
        if self._rotation:
            self.rotate(self._rotation)
        if factor != 1.0:
            self.scale(factor, factor)

    # ── Qt event overrides ───────────────────────────────────────────

    def wheelEvent(self, event):  # noqa: N802
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            self._apply_zoom_step(1.25 if event.angleDelta().y() > 0 else 0.8)
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        """Double-click toggles between Fit to Window and 1:1 zoom."""
        if self.has_image():
            # 依据当前记录的缩放百分比判断（不再读 transform().m11()，
            # 它在旋转后不代表纯比例）。接近 Fit 就切到 1:1，否则 Fit。
            if self._zoom_percent < 110:  # close to fit view
                self.reset_zoom()
            else:
                self.fit_image()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Up:
            self.framePrevRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self.frameNextRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        self.contextMenuRequested.emit(self, event.globalPos())
