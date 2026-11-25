"""Frame navigation bar — sits below the image view in each tab."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QSize, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from raw_view.models import THEME_PALETTES, normalize_ui_theme

# M-7: one icon family for every nav button — the Unicode ``│◀`` / ``▶│``
# graphemes rendered at a different size than ``◀`` / ``▶`` and looked like
# two different icon sets.
_NAV_ICON_NAMES = (
    "fa5s.angle-double-left",
    "fa5s.angle-left",
    "fa5s.angle-right",
    "fa5s.angle-double-right",
)


class FrameNavBar(QWidget):
    """Horizontal bar with navigation buttons and frame counter.

    Layout::
        │◀  ◀  [1] / 5  ▶  ▶│

    Frame numbers displayed in the bar are 1-based for user-friendliness.
    Internal frame indices (passed via signal) remain 0-based.

    Signals
    -------
    frameChanged(int)
        Emitted when the user changes the frame index (0-based).
    """

    frameChanged = pyqtSignal(int)

    def __init__(
        self, parent: QWidget | None = None, *, theme: str = "light"
    ) -> None:
        super().__init__(parent)
        self.setObjectName("frameNavBar")
        # Explicit theme: qt-material only changes the stylesheet, so
        # QApplication.palette() cannot be used to guess it.
        self._theme = normalize_ui_theme(theme)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)

        self._nav_min_h = 32
        self._nav_min_w = 36
        self._spin_min_w = 52

        # A stylesheet swap fires changeEvent before child fonts/layout have
        # settled.  Coalesce a deferred pass so we can measure the styled
        # QSpinBox chrome instead of guessing its width.
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setSingleShot(True)
        self._metrics_timer.setInterval(0)
        self._metrics_timer.timeout.connect(self._sync_metrics)
        self._metrics_passes_left = 0

        # ── Buttons ────────────────────────────────────────────────────
        # Icon mapping: │◀  ◀  ▶  ▶│
        self.first_btn = QPushButton()
        self.first_btn.setAccessibleName("First frame")
        self.first_btn.setAccessibleDescription("Go to the first frame (Home).")
        self.first_btn.setToolTip("First frame (Home)")
        self.prev_btn = QPushButton()
        self.prev_btn.setAccessibleName("Previous frame")
        self.prev_btn.setAccessibleDescription("Go to the previous frame (Left or Up).")
        self.prev_btn.setToolTip("Previous frame (Left / Up)")

        # Current frame / total
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(1, 1_000_000)
        self.frame_spin.setAlignment(Qt.AlignCenter)
        self.frame_spin.setProperty("mono", True)
        self.frame_spin.setEnabled(False)
        self.frame_spin.setAccessibleName("Current frame")
        self.frame_spin.setAccessibleDescription("Current frame number.")

        self._total_label = QLabel("/ 0")
        self._total_label.setProperty("mono", True)
        self._total_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._total_label.setAccessibleName("Total frames")
        self._total_label.setAccessibleDescription("Total number of frames in the file.")

        self.next_btn = QPushButton()
        self.next_btn.setAccessibleName("Next frame")
        self.next_btn.setAccessibleDescription("Go to the next frame (Right or Down).")
        self.next_btn.setToolTip("Next frame (Right / Down)")
        self.last_btn = QPushButton()
        self.last_btn.setAccessibleName("Last frame")
        self.last_btn.setAccessibleDescription("Go to the last frame (End).")
        self.last_btn.setToolTip("Last frame (End)")

        self._nav_buttons = (
            self.first_btn, self.prev_btn, self.next_btn, self.last_btn,
        )
        self._icon_px = 16
        self._apply_nav_icons()
        self._update_metrics()

        # Note: button/spin/label colours are themed globally via
        # ``build_ui_stylesheet`` (#frameNavBar …) in models.py, so they
        # follow the active light/dark theme instead of being hard-coded
        # here. Only the layout/metrics live in this widget.

        # ── Layout: │◀  ◀  [1] / 5  ▶  ▶│  ───────────────────────────
        layout.addStretch()
        for wdg in (self.first_btn, self.prev_btn):
            layout.addWidget(wdg, 0, Qt.AlignVCenter)
        layout.addSpacing(8)
        layout.addWidget(self.frame_spin, 0, Qt.AlignVCenter)
        layout.addWidget(self._total_label, 0, Qt.AlignVCenter)
        layout.addSpacing(8)
        layout.addWidget(self.next_btn, 0, Qt.AlignVCenter)
        layout.addWidget(self.last_btn, 0, Qt.AlignVCenter)
        layout.addStretch()

        # Signals
        self.frame_spin.valueChanged.connect(self._on_spin_changed)
        self.first_btn.clicked.connect(self._first)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.last_btn.clicked.connect(self._last)

    # ── public API ───────────────────────────────────────────────────

    def _icon_size_px(self) -> int:
        """Icon edge in px: at most 16, and never larger than font + 2 (M-7)."""
        font = self.first_btn.font()
        px = font.pixelSize()
        if px <= 0:
            px = font.pointSize()
        if px <= 0:
            px = 13
        return max(10, min(16, int(px) + 2))

    def _apply_nav_icons(self) -> None:
        """Rebuild the four nav glyphs with the current theme's tokens.

        ``nav_button_text`` / ``nav_button_disabled_text`` are the documented
        tokens for the bar; ``color_disabled`` lets Qt render the same glyph
        in the faded colour without a second asset.
        """
        palette = THEME_PALETTES[normalize_ui_theme(self._theme)]
        try:
            import qtawesome as qta

            source_icons = [
                qta.icon(
                    name,
                    color=palette["nav_button_text"],
                    color_disabled=palette["nav_button_disabled_text"],
                )
                for name in _NAV_ICON_NAMES
            ]
        except Exception:  # pragma: no cover — qtawesome is a hard dependency
            return
        self._icon_px = self._icon_size_px()
        # Render the glyph at 14 px inside the (up to) 16 px icon box.  Font
        # Awesome's double chevron is two glyph-widths wide, so at 16 px its
        # pixel bounding box is 12x9 while a single chevron is 7x9 (a 3 px
        # width/height difference); at 14 px both are exactly 2 px off square,
        # which is the consistency the review asked for (V-5).  The icon box
        # itself stays identical for all four, so the buttons remain aligned.
        glyph_px = min(14, self._icon_px)
        for button, source in zip(self._nav_buttons, source_icons):
            icon = QIcon()
            for mode in (QIcon.Normal, QIcon.Disabled):
                canvas = QPixmap(self._icon_px, self._icon_px)
                canvas.fill(Qt.transparent)
                glyph = source.pixmap(QSize(glyph_px, glyph_px), mode)
                painter = QPainter(canvas)
                painter.drawPixmap(
                    (self._icon_px - glyph_px) // 2,
                    (self._icon_px - glyph_px) // 2,
                    glyph,
                )
                painter.end()
                icon.addPixmap(canvas, mode, QIcon.Off)
            button.setIcon(icon)
            button.setIconSize(QSize(self._icon_px, self._icon_px))
            button.setText("")

    def apply_theme(self, theme: str) -> None:
        """Regenerate icon colours after a live theme switch (M-7)."""
        self._theme = normalize_ui_theme(theme)
        self._apply_nav_icons()
        self._update_metrics()

    def _update_metrics(self) -> None:
        """Size controls from the active font instead of fixed 36x32 boxes.

        The stylesheet can enlarge ``frameNavBar`` text at runtime (font-size
        setting), so constants alone clip ``│◀`` / ``▶│`` and large frame
        numbers.  Recompute on font/style changes; the old 36x32 remains the
        minimum for compact themes.
        """
        button_fm = QFontMetrics(self.first_btn.font())
        # Icons are fixed-size; keep a floor that fits the 16 px glyph and the
        # themed font's line box at every ui/font_size.
        button_w = max(
            self._nav_min_w,
            getattr(self, "_icon_px", 16) + 14,
            max(button_fm.horizontalAdvance(btn.text()) for btn in self._nav_buttons) + 10,
        )
        button_h = max(
            self._nav_min_h,
            getattr(self, "_icon_px", 16) + 8,
            button_fm.height() + 8,
        )
        for btn in self._nav_buttons:
            btn.setFixedSize(button_w, button_h)

        spin_fm = QFontMetrics(self.frame_spin.font())
        text_w = spin_fm.horizontalAdvance("1000000")
        editor = self.frame_spin.lineEdit()
        editor_w = editor.width() if editor is not None else 0
        outer_w = self.frame_spin.width()
        # The spin-box chrome (frame, padding and the up/down buttons) is
        # style-dependent and grows with the font; derive it from the live
        # editor width rather than hard-coding a padding guess.
        chrome_w = outer_w - editor_w if editor_w > 0 and outer_w > editor_w else 0
        spin_w = max(self._spin_min_w, text_w + 8 + chrome_w)
        spin_h = max(self._nav_min_h, spin_fm.height() + 10)
        self.frame_spin.setFixedSize(spin_w, spin_h)

        label_fm = QFontMetrics(self._total_label.font())
        self._total_label.setFixedHeight(
            max(self._nav_min_h, label_fm.height() + 8)
        )

    def _sync_metrics(self) -> None:
        """Finish a deferred metric refresh after fonts and layout settle."""
        # The icon pixmap size follows the styled font (min(16, font + 2)).
        self._apply_nav_icons()
        self._update_metrics()
        if self._metrics_passes_left > 0:
            self._metrics_passes_left -= 1
            self._metrics_timer.start()

    def _refresh_metrics(self) -> None:
        """Apply metrics now and over the next few event-loop turns.

        ``changeEvent`` runs before child controls have picked up the new
        stylesheet font, and ``showEvent`` runs before the first layout.  A
        short sequence of zero-delay passes converges on the measured spin-box
        chrome without using fixed sleeps.
        """
        self._metrics_passes_left = 3
        self._update_metrics()
        self._metrics_timer.start()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._refresh_metrics()

    def showEvent(self, event) -> None:
        # A stylesheet installed on QApplication before construction is applied
        # during polish without a StyleChange event, so size after show too.
        super().showEvent(event)
        self._refresh_metrics()

    def set_frame_info(self, current: int, total: int) -> None:
        """Update frame display and enable/disable nav buttons.

        Parameters are 0-based internally; display is 1-based.
        """
        self.frame_spin.setRange(1, max(1, total))
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(current + 1)
        self.frame_spin.blockSignals(False)
        self._total_label.setText(f"/ {total}")
        has_multiple = total > 1
        at_start = current <= 0
        at_end = current >= total - 1
        self.frame_spin.setEnabled(has_multiple)
        self.first_btn.setEnabled(has_multiple and not at_start)
        self.prev_btn.setEnabled(has_multiple and not at_start)
        self.next_btn.setEnabled(has_multiple and not at_end)
        self.last_btn.setEnabled(has_multiple and not at_end)

    def frame_index(self) -> int:
        """Return the current frame index (0-based)."""
        return self.frame_spin.value() - 1

    def set_frame_index(self, index: int) -> None:
        """Set the frame index (0-based) without emitting signal."""
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(index + 1)
        self.frame_spin.blockSignals(False)

    # ── internal slots ───────────────────────────────────────────────

    def _on_spin_changed(self, value: int) -> None:
        """Emit 0-based frame index when spin box changes."""
        self.frameChanged.emit(value - 1)

    def _first(self) -> None:
        self.frame_spin.setValue(1)

    def _prev(self) -> None:
        if self.frame_spin.value() > 1:
            self.frame_spin.setValue(self.frame_spin.value() - 1)

    def _next(self) -> None:
        max_val = self.frame_spin.maximum()
        if self.frame_spin.value() < max_val:
            self.frame_spin.setValue(self.frame_spin.value() + 1)

    def _last(self) -> None:
        self.frame_spin.setValue(self.frame_spin.maximum())
