"""Sensor preset management dialog.

Lets the user view, rename, edit, and delete saved sensor presets. Reads and
writes through :class:`~raw_view.models.AppSettings`.
"""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
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
        self.import_btn = QPushButton("Import")
        self.import_btn.setToolTip(
            "Import presets from a JSON file. Existing presets are kept; "
            "duplicates can be overwritten or skipped."
        )
        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip(
            "Export all current presets to a JSON file for sharing or backup."
        )
        for btn in (
            self.add_btn, self.rename_btn, self.delete_btn,
            self.import_btn, self.export_btn,
        ):
            btn.setMinimumWidth(72)
        self.add_btn.clicked.connect(self._on_add)
        self.delete_btn.clicked.connect(self._on_delete)
        self.rename_btn.clicked.connect(self._on_rename)
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn.clicked.connect(self._on_export)

        list_btn_row = QHBoxLayout()
        list_btn_row.addWidget(self.add_btn)
        list_btn_row.addWidget(self.rename_btn)
        list_btn_row.addWidget(self.delete_btn)

        share_btn_row = QHBoxLayout()
        share_btn_row.addWidget(self.import_btn)
        share_btn_row.addWidget(self.export_btn)
        share_btn_row.addStretch(1)

        left = QVBoxLayout()
        left.addWidget(QLabel("Presets"))
        left.addWidget(self.list_widget, 1)
        left.addLayout(list_btn_row)
        left.addLayout(share_btn_row)

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

    # ── Import / Export ─────────────────────────────────────────────────
    #
    # Both operate on the dialog's in-memory preset list (``self._presets``)
    # so that unsaved edits made in the form are preserved. Persistence to
    # QSettings still only happens when the user clicks Save.

    def _on_export(self) -> None:
        if not self._presets:
            QMessageBox.information(
                self, "Export presets", "There are no presets to export."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export presets",
            "raw-view-presets.json",
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return
        try:
            import json
            from pathlib import Path

            data = [p.to_dict() for p in self._presets]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Export presets",
            f"Exported {len(self._presets)} preset(s) to:\n{path}",
        )

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import presets",
            "",
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return
        try:
            import json

            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", f"Cannot read JSON:\n{exc}")
            return
        if not isinstance(raw, list):
            QMessageBox.critical(
                self, "Import failed",
                "Preset JSON must be a list of preset objects.",
            )
            return

        incoming = [
            SensorPreset.from_dict(d) for d in raw
            if isinstance(d, dict) and str(d.get("name", "")).strip()
        ]
        if not incoming:
            QMessageBox.information(
                self, "Import presets",
                "No valid presets found in the file.",
            )
            return

        existing_names = {p.name for p in self._presets}
        conflicts = [p.name for p in incoming if p.name in existing_names]

        on_conflict = "overwrite"
        if conflicts:
            preview = ", ".join(conflicts[:5]) + ("..." if len(conflicts) > 5 else "")
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Conflict")
            box.setText(
                f"{len(conflicts)} preset name(s) already exist:\n  {preview}\n\n"
                "How would you like to handle them?"
            )
            overwrite_btn = box.addButton("Overwrite", QMessageBox.AcceptRole)
            skip_btn = box.addButton("Skip duplicates", QMessageBox.AcceptRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(overwrite_btn)
            box.exec_()
            clicked = box.clickedButton()
            if clicked is cancel_btn:
                return
            on_conflict = "skip" if clicked is skip_btn else "overwrite"

        # Apply merge in-place. Existing-order presets stay first; truly new
        # presets are appended in the order they appear in the JSON file.
        by_name = {p.name: p for p in self._presets}
        added = overwritten = skipped = 0
        for p in incoming:
            if p.name in by_name:
                if on_conflict == "skip":
                    skipped += 1
                    continue
                by_name[p.name] = p
                overwritten += 1
            else:
                by_name[p.name] = p
                added += 1
        # Rebuild list preserving prior order, then append new names.
        prior_order = [p.name for p in self._presets]
        new_order: list[str] = list(prior_order)
        for p in incoming:
            if p.name not in prior_order and p.name in by_name:
                new_order.append(p.name)
        # Deduplicate (paranoid; merge logic should already prevent this).
        seen: set[str] = set()
        ordered: list[SensorPreset] = []
        for name in new_order:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(by_name[name])
        self._presets = ordered

        # Pick a sensible row to highlight after refresh.
        target_index = 0
        if incoming:
            try:
                target_index = next(
                    i for i, p in enumerate(self._presets) if p.name == incoming[-1].name
                )
            except StopIteration:
                target_index = 0
        self._refresh_list(select_index=target_index)

        QMessageBox.information(
            self,
            "Import presets",
            f"Imported {added + overwritten} preset(s) "
            f"(added {added}, overwritten {overwritten}, skipped {skipped}).\n\n"
            "Click Save to persist the changes.",
        )

    # ── Save ────────────────────────────────────────────────────────────

    def _on_save_clicked(self) -> None:
        self._settings.replace_sensor_presets(self._presets)
        self.accept()
