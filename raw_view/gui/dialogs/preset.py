"""Sensor preset management dialog.

Lets the user view, rename, edit, and delete saved sensor presets. Reads and
writes through :class:`~raw_view.models.AppSettings`.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from raw_view.gui.panels import ControlPanel
from raw_view.models import AppSettings, BAYER_PATTERNS, SensorPreset


class PresetManagerDialog(QDialog):
    """Manage saved sensor presets (rename, edit fields, delete).

    The dialog mutates an in-memory copy of the preset list and only persists
    changes when the user clicks *Save*. *Cancel* discards everything.
    """

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._presets: list[SensorPreset] = [
            SensorPreset.from_dict(p.to_dict()) for p in settings.sensor_presets
        ]
        self._current_index: int = -1
        self._loading: bool = False  # guards against edit-callback recursion

        self.setWindowTitle("Sensor Presets")
        self.resize(620, 420)

        # ── Left side: list + add/delete buttons ──────────────────────
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)

        self.add_btn = QPushButton("Add")
        self.delete_btn = QPushButton("Delete")
        self.rename_btn = QPushButton("Rename")
        for btn in (self.add_btn, self.rename_btn, self.delete_btn):
            btn.setMinimumWidth(72)
        self.add_btn.clicked.connect(self._on_add)
        self.delete_btn.clicked.connect(self._on_delete)
        self.rename_btn.clicked.connect(self._on_rename)

        list_btn_row = QHBoxLayout()
        list_btn_row.addWidget(self.add_btn)
        list_btn_row.addWidget(self.rename_btn)
        list_btn_row.addWidget(self.delete_btn)

        left = QVBoxLayout()
        left.addWidget(QLabel("Presets"))
        left.addWidget(self.list_widget, 1)
        left.addLayout(list_btn_row)

        # ── Right side: editable parameter form ───────────────────────
        self.type_combo = QComboBox()
        self.type_combo.addItems(["RAW", "YUV", "Standard Image"])
        self.format_combo = QComboBox()
        self.align_combo = QComboBox()
        self.align_combo.addItems(["lsb", "msb"])
        self.endian_combo = QComboBox()
        self.endian_combo.addItems(["little", "big"])
        self.preview_combo = QComboBox()
        self.preview_combo.addItems(["Bayer Color", "Grayscale"])
        self.bayer_combo = QComboBox()
        self.bayer_combo.addItems(BAYER_PATTERNS)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 65535)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 65535)
        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(0, 1_000_000_000)

        # Re-fill the format combo when the type changes — mirrors ControlPanel.
        self.type_combo.currentTextChanged.connect(self._on_type_changed)

        for w in (
            self.type_combo, self.format_combo, self.align_combo, self.endian_combo,
            self.preview_combo, self.bayer_combo, self.width_spin, self.height_spin,
            self.offset_spin,
        ):
            # Any change while a preset is selected immediately updates the
            # in-memory model so that switching rows preserves the edit.
            self._connect_change_signal(w)

        form = QFormLayout()
        form.addRow("Type", self.type_combo)
        form.addRow("Format", self.format_combo)
        form.addRow("Alignment", self.align_combo)
        form.addRow("Endianness", self.endian_combo)
        form.addRow("RAW preview", self.preview_combo)
        form.addRow("Bayer pattern", self.bayer_combo)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Offset", self.offset_spin)

        right = QVBoxLayout()
        right.addWidget(QLabel("Edit selected preset"))
        right.addLayout(form)
        right.addStretch(1)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 2)

        # ── Save / Cancel ─────────────────────────────────────────────
        self.save_btn = QPushButton("Save")
        self.cancel_btn = QPushButton("Cancel")
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.cancel_btn.clicked.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_btn)
        bottom.addWidget(self.save_btn)

        root = QVBoxLayout(self)
        root.addLayout(body, 1)
        root.addLayout(bottom)

        self._refresh_list(select_index=0 if self._presets else -1)

    # ── Wiring helpers ──────────────────────────────────────────────────

    def _connect_change_signal(self, widget) -> None:
        if isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(self._on_field_changed)
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(self._on_field_changed)

    def _populate_format_combo(self, image_type: str) -> None:
        """Refill format_combo for the given image type. Signals are blocked
        to keep this side-effect-free — callers decide when to fire updates.
        """
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        if image_type == "RAW":
            self.format_combo.addItems(ControlPanel.RAW_FORMATS)
        elif image_type == "YUV":
            self.format_combo.addItems(ControlPanel.YUV_FORMATS)
        else:
            self.format_combo.addItems(["N/A"])
        self.format_combo.blockSignals(False)

    def _on_type_changed(self, image_type: str) -> None:
        # Match what ControlPanel exposes so saved presets remain compatible.
        self._populate_format_combo(image_type)
        self._on_field_changed()

    # ── List management ─────────────────────────────────────────────────

    def _refresh_list(self, select_index: int = -1) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self._presets:
            self.list_widget.addItem(QListWidgetItem(p.name))
        self.list_widget.blockSignals(False)
        if 0 <= select_index < len(self._presets):
            self.list_widget.setCurrentRow(select_index)
        else:
            self._current_index = -1
            self._set_form_enabled(False)

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in (
            self.type_combo, self.format_combo, self.align_combo, self.endian_combo,
            self.preview_combo, self.bayer_combo, self.width_spin, self.height_spin,
            self.offset_spin, self.rename_btn, self.delete_btn,
        ):
            w.setEnabled(enabled)

    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._presets):
            self._current_index = -1
            self._set_form_enabled(False)
            return
        self._current_index = row
        self._set_form_enabled(True)
        self._load_form(self._presets[row])

    def _load_form(self, preset: SensorPreset) -> None:
        self._loading = True
        try:
            self.type_combo.setCurrentText(preset.image_type)
            # Always repopulate format_combo explicitly: setCurrentText above
            # is a no-op when the combo is already on that type (e.g. the
            # default "RAW"), so currentTextChanged never fires and
            # format_combo stays empty — which used to leave Format blank
            # after re-opening the dialog.
            self._populate_format_combo(preset.image_type)
            idx = self.format_combo.findText(preset.format_name)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
            self.align_combo.setCurrentText(preset.alignment)
            self.endian_combo.setCurrentText(preset.endianness)
            self.preview_combo.setCurrentText(preset.preview_mode)
            self.bayer_combo.setCurrentText(preset.bayer_pattern)
            self.width_spin.setValue(preset.width)
            self.height_spin.setValue(preset.height)
            self.offset_spin.setValue(preset.offset)
        finally:
            self._loading = False

    def _on_field_changed(self, *_) -> None:
        if self._loading or self._current_index < 0:
            return
        p = self._presets[self._current_index]
        p.image_type = self.type_combo.currentText()
        p.format_name = self.format_combo.currentText()
        p.alignment = self.align_combo.currentText()
        p.endianness = self.endian_combo.currentText()
        p.preview_mode = self.preview_combo.currentText()
        p.bayer_pattern = self.bayer_combo.currentText()
        p.width = self.width_spin.value()
        p.height = self.height_spin.value()
        p.offset = self.offset_spin.value()

    # ── Add / Rename / Delete ───────────────────────────────────────────

    def _unique_name(self, candidate: str) -> bool:
        candidate = (candidate or "").strip()
        return bool(candidate) and not any(p.name == candidate for p in self._presets)

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add preset", "Preset name:")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Add preset", "Preset name must not be empty.")
            return
        if not self._unique_name(name):
            QMessageBox.warning(self, "Add preset", f"Preset '{name}' already exists.")
            return
        self._presets.append(SensorPreset(name=name))
        self._refresh_list(select_index=len(self._presets) - 1)

    def _on_rename(self) -> None:
        if self._current_index < 0:
            return
        old = self._presets[self._current_index].name
        name, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=old)
        if not ok:
            return
        name = name.strip()
        if name == old:
            return
        if not name:
            QMessageBox.warning(self, "Rename", "Preset name must not be empty.")
            return
        if not self._unique_name(name):
            QMessageBox.warning(self, "Rename", f"Preset '{name}' already exists.")
            return
        self._presets[self._current_index].name = name
        self._refresh_list(select_index=self._current_index)

    def _on_delete(self) -> None:
        if self._current_index < 0:
            return
        p = self._presets[self._current_index]
        confirm = QMessageBox.question(
            self,
            "Delete preset",
            f"Delete preset '{p.name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        del self._presets[self._current_index]
        next_idx = min(self._current_index, len(self._presets) - 1)
        self._refresh_list(select_index=next_idx)

    # ── Save ────────────────────────────────────────────────────────────

    def _on_save_clicked(self) -> None:
        self._settings.replace_sensor_presets(self._presets)
        self.accept()
