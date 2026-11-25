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
    # Floor, never round up: rounding 96.4% to 96% is fine, but rounding
    # 96.6% up to 97% makes the fitted image wider than the viewport and brings
    # the scrollbars straight back (H-5).
    return max(1, int(fit_scale * 100))


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
        # True while the user is in "Fit to Window" mode: a later viewport
        # change (frame-nav bar appearing, window resize, readout strip
        # growing) must re-fit instead of leaving the old scale in place.
        self._fit_active = False

    # ── public API ───────────────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap_item.setPixmap(pixmap)
        self._img_width = pixmap.width()
        self._img_height = pixmap.height()
        self.resetTransform()
        self._zoom_percent = 100
        self._rotation = 0
        # A new image starts at 1:1, not in fit mode.
        self._fit_active = False
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
        self._fit_active = False
        self.zoomChanged.emit(self._zoom_percent)

    def fit_image(self) -> None:
        if self.sceneRect().isNull():
            return
        # 先用 Fit 的目标视图区与场景矩形数学算出缩放比例（不受旋转影响），
        # 再整体重建变换：应用已有旋转后直接按该比例缩放，避免 fitInView
        # 在旋转变换上叠加 m11 语义错乱。viewport 尺寸不含滚动条。
        # H-5: leave a 2 px safety margin — Qt's scrollbar trigger is inclusive
        # at the viewport edge, so an image exactly as wide as the viewport can
        # still show a horizontal bar on some styles.
        viewport = self.viewport().contentsRect()
        percent = _fit_scale_percent(
            self.sceneRect(),
            (max(1, viewport.width() - 2), max(1, viewport.height() - 2)),
        )
        self._rotation = self._rotation % 360
        self._apply_rotation_then_scale(percent / 100.0)
        self._zoom_percent = _clamp_zoom_percent(percent)
        self._fit_active = True
        self.zoomChanged.emit(self._zoom_percent)

    def _refit_if_needed(self) -> None:
        """Re-apply Fit when the viewport shrank after the last fit (H-5).

        The reported failure is a fit computed at 956x526 followed by the
        frame-nav bar/readout strip taking more space, leaving the image 17x21
        px larger than the viewport.  Only overflow triggers a recompute, so
        enlarging the window keeps the current fit rather than re-centering.
        """
        if not getattr(self, "_fit_active", False):
            return
        scene_rect = self.sceneRect()
        if scene_rect.isNull():
            return
        viewport = self.viewport().contentsRect()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        mapped = self.mapFromScene(scene_rect).boundingRect()
        if (
            mapped.width() > viewport.width() - 1
            or mapped.height() > viewport.height() - 1
        ):
            self.fit_image()

    def zoom_to(self, percent: int) -> None:
        """Zoom to a specific percentage (clamped 10–1000)."""
        percent = _clamp_zoom_percent(percent)
        self._apply_rotation_then_scale(percent / 100.0)
        self._zoom_percent = percent
        self._fit_active = False
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
        # An explicit zoom step leaves "fit" mode: a later window resize must
        # not snap the image back to the fitted scale.
        self._fit_active = False
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

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        # H-5: the viewport can shrink after Fit (frame-nav bar, readout strip,
        # window resize).  Re-check fit after the layout has settled.
        self._refit_if_needed()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # A fit performed before the first layout used a placeholder viewport;
        # re-check once the real geometry exists.
        self._refit_if_needed()

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
