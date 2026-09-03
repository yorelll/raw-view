"""Checkbox grid for selecting multiple formats, bayer patterns, and sizes.

Used by the Convert / Batch Convert dialogs when the multi-variant generator
is enabled in Settings. A single source image can then be turned into many
outputs at once (e.g. RAW8/RAW10/RAW12 × RGGB/BGGR × several resolutions).
"""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from raw_view.formats import RAW_BITS, YUV_BYTES_PER_PIXEL
from raw_view.models import ACTION_ICON_COLOR, BAYER_PATTERNS, COMMON_SIZES

# Custom (user-added) sizes are tinted so they stand out from the presets.
_CUSTOM_SIZE_COLOR = "#5B8DEF"


def _info_icon(tooltip: str) -> QPushButton:
    """Return a keyboard-accessible info button with the same tooltip help."""
    button = QPushButton()
    button.setObjectName("variantInfoButton")
    button.setAccessibleName("More information")
    button.setAccessibleDescription(tooltip)
    button.setToolTip(tooltip)
    button.setCursor(Qt.WhatsThisCursor)
    button.setFixedSize(24, 24)
    try:
        import qtawesome as qta

        button.setIcon(qta.icon("fa5s.info-circle", color=ACTION_ICON_COLOR))
    except Exception:
        button.setText("i")
    button.setIconSize(QSize(16, 16))

    def _show_help() -> None:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.information(button, "More information", tooltip)

    button.clicked.connect(_show_help)
    return button


class VariantSelector(QWidget):
    """Grouped checkboxes for RAW/YUV formats, bayer patterns, and sizes."""

    RAW_FORMATS = list(RAW_BITS.keys())
    # YUV 列表收敛为单个 YOnly（+位深多选），去掉 YOnly8/10/12/14/16 独立条目。
    YUV_FORMATS = [f for f in YUV_BYTES_PER_PIXEL if not (f.startswith("YOnly") and f != "YOnly")]
    _SIZE_COLS = 3
    # YOnly 位深多选：勾选 YOnly 格式后按这些位深展开成 YOnly<bit> 变体
    YONLY_BITS = ["8", "10", "12", "14", "16"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._format_boxes: dict[str, QCheckBox] = {}
        self._bayer_boxes: dict[str, QCheckBox] = {}
        self._yonly_bit_boxes: dict[str, QCheckBox] = {}
        # (checkbox, w, h, is_custom)
        self._size_boxes: list[tuple[QCheckBox, int, int, bool]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ── Formats ──
        root.addWidget(self._make_group(
            "Formats (RAW)", self.RAW_FORMATS, self._format_boxes, columns=4,
            checked={"RAW8", "RAW10", "RAW12"},
        ))
        root.addWidget(self._make_group(
            "Formats (YUV)", self.YUV_FORMATS, self._format_boxes, columns=4,
        ))
        # ── YOnly 位深（仅当 YOnly 勾选时参与展开）──
        root.addWidget(self._make_group(
            "YOnly Bit Depth", self.YONLY_BITS, self._yonly_bit_boxes, columns=5,
            checked={"8", "12"},
            help_text="When YOnly is selected above, these bit depths are fanned "
                      "out into YOnly8/10/12/14/16 variants.",
        ))

        # ── Bayer patterns ── (short title + hover help)
        root.addWidget(self._make_group(
            "Bayer Patterns", BAYER_PATTERNS, self._bayer_boxes, columns=4,
            checked={"RGGB"},
            help_text="Only applies to RAW formats with a bayer source; "
                      "ignored for YUV and gray-source RAW.",
        ))

        # ── Sizes ──
        root.addWidget(self._make_size_group())

    # ── group builders ────────────────────────────────────────────────

    def _title_row(self, title: str, help_text: str | None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(title)
        label.setStyleSheet("font-weight: bold; border: none;")
        row.addWidget(label)
        if help_text:
            row.addWidget(_info_icon(help_text))
        row.addStretch(1)
        return row

    def _make_group(
        self,
        title: str,
        names: list[str],
        registry: dict[str, QCheckBox],
        *,
        columns: int = 4,
        checked: set[str] | None = None,
        help_text: str | None = None,
    ) -> QFrame:
        checked = checked or set()
        frame = QFrame()
        frame.setObjectName("card")
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addLayout(self._title_row(title, help_text))

        grid = QGridLayout()
        grid.setSpacing(4)
        for i, name in enumerate(names):
            cb = QCheckBox(name)
            cb.setChecked(name in checked)
            registry[name] = cb
            grid.addWidget(cb, i // columns, i % columns)
        layout.addLayout(grid)
        return frame

    def _make_size_group(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addLayout(self._title_row(
            "Sizes",
            "Right-click here to add a custom size, or to delete a "
            "custom size you added. Preset sizes cannot be deleted.",
        ))

        self._size_grid = QGridLayout()
        self._size_grid.setSpacing(4)
        for i, (w, h) in enumerate(COMMON_SIZES):
            self._add_size_checkbox(w, h, checked=(i == 0), is_custom=False)
        layout.addLayout(self._size_grid)

        # Right-click anywhere in the sizes card to add/delete custom sizes.
        frame.setContextMenuPolicy(Qt.CustomContextMenu)
        frame.customContextMenuRequested.connect(
            lambda pos: self._show_size_menu(frame, pos)
        )
        return frame

    # ── size management ───────────────────────────────────────────────

    def _relayout_sizes(self) -> None:
        for i, (cb, _w, _h, _c) in enumerate(self._size_boxes):
            self._size_grid.addWidget(cb, i // self._SIZE_COLS, i % self._SIZE_COLS)

    def _add_size_checkbox(self, w: int, h: int, *, checked: bool, is_custom: bool) -> None:
        # Duplicate → just (re)check and flash the existing box.
        for cb, cw, ch, _c in self._size_boxes:
            if (cw, ch) == (w, h):
                cb.setChecked(True)
                self._flash(cb)
                return
        cb = QCheckBox(f"{w}x{h}")
        cb.setChecked(checked)
        if is_custom:
            cb.setStyleSheet(f"color: {_CUSTOM_SIZE_COLOR}; font-weight: 600;")
            cb.setToolTip("Custom size — right-click to delete")
        self._size_boxes.append((cb, w, h, is_custom))
        self._relayout_sizes()

    def _delete_size(self, target: QCheckBox) -> None:
        self._size_boxes = [t for t in self._size_boxes if t[0] is not target]
        self._size_grid.removeWidget(target)
        target.deleteLater()
        self._relayout_sizes()

    def _flash(self, cb: QCheckBox) -> None:
        """Briefly highlight an existing checkbox to signal 'already added'."""
        original = cb.styleSheet()
        cb.setStyleSheet(f"color: {_CUSTOM_SIZE_COLOR}; font-weight: 700; "
                         "background: rgba(91,141,239,0.25); border-radius: 4px;")
        QTimer.singleShot(450, lambda: cb.setStyleSheet(original))

    def _show_size_menu(self, frame: QFrame, pos) -> None:
        menu = QMenu(self)
        add_action = menu.addAction("Add custom size…")
        # If the click landed on a custom-size checkbox, offer to delete it.
        child = frame.childAt(pos)
        delete_target = None
        for cb, w, h, is_custom in self._size_boxes:
            if is_custom and (child is cb or (child is not None and child.parent() is cb)):
                delete_target = cb
                delete_action = menu.addAction(f"Delete {w}x{h}")
                break
        chosen = menu.exec_(frame.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is add_action:
            self._prompt_custom_size()
        elif delete_target is not None and chosen is delete_action:
            self._delete_size(delete_target)

    def _prompt_custom_size(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Add custom size")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        w_spin = QSpinBox(); w_spin.setRange(1, 65535); w_spin.setValue(1920)
        w_spin.setAlignment(Qt.AlignCenter)
        h_spin = QSpinBox(); h_spin.setRange(1, 65535); h_spin.setValue(1080)
        h_spin.setAlignment(Qt.AlignCenter)
        form = QFormLayout()
        form.addRow("Width", w_spin)
        form.addRow("Height", h_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        lay.addWidget(buttons)
        if dlg.exec_() == QDialog.Accepted:
            self._add_size_checkbox(w_spin.value(), h_spin.value(), checked=True, is_custom=True)

    # ── public accessors ──────────────────────────────────────────────

    def selected_formats(self) -> list[str]:
        """所选格式；勾选 YOnly 时按选中的位深展开为 YOnly<bit> 内部名。"""
        out: list[str] = []
        for name, cb in self._format_boxes.items():
            if not cb.isChecked():
                continue
            if name == "YOnly":
                bits = [b for b, bcb in self._yonly_bit_boxes.items() if bcb.isChecked()]
                if not bits:
                    bits = ["8"]
                out.extend(f"YOnly{b}" for b in bits)
            else:
                out.append(name)
        return out

    def selected_bayer(self) -> list[str]:
        return [name for name, cb in self._bayer_boxes.items() if cb.isChecked()]

    def selected_sizes(self) -> list[tuple[int, int]]:
        return [(w, h) for cb, w, h, _c in self._size_boxes if cb.isChecked()]
