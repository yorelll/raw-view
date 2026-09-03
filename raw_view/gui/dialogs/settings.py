"""Application preferences dialog."""

from __future__ import annotations

import os

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from raw_view.models import AppSettings, DEFAULT_OUTPUT_TEMPLATE


class SettingsDialog(QDialog):
    """Preferences dialog for output directory, DPI, font size, theme, and output template."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")
        # Drop the title-bar "?" (context help) button — it has no action here.
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.output_dir_edit = QLineEdit(settings.default_output_dirname)
        self.template_edit = QLineEdit(settings.output_template)
        # "Reset" snaps the template field back to the current built-in
        # default. Useful for users who upgraded from an older build and
        # never noticed their stored template was a stale earlier default.
        self.template_reset_btn = QPushButton("Reset")
        self.template_reset_btn.setObjectName("secondaryButton")
        self.template_reset_btn.setToolTip(
            f"Reset to the built-in default:\n{DEFAULT_OUTPUT_TEMPLATE}"
        )
        self.template_reset_btn.setMinimumWidth(72)
        self.template_reset_btn.clicked.connect(self._reset_template)
        # Full placeholder reference is shown on hover of an ⓘ icon instead of
        # a wall of small text below the field.
        template_help_text = (
            "Placeholders:\n"
            "  {input_stem} {width} {height} {ext}\n"
            "  {format} {bayer} {bits} {packed} {raw_type} {yuv_type}\n"
            "  {alignment} {endianness} {date} {time}\n\n"
            "Default: {input_stem}_{width}x{height}_{format}{ext}\n"
            "  e.g. image_2560x1440_BGGR10P.raw"
        )
        self.template_help_icon = self._info_icon(template_help_text)
        self.template_help_icon.setObjectName("templateHelp")
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 2400)
        self.dpi_spin.setValue(settings.save_dpi)
        self.dpi_spin.setSuffix(" DPI")
        self.dpi_spin.setToolTip("Allowed range: 72 – 2400")
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(10, 24)
        self.font_size_spin.setValue(settings.ui_font_size)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setToolTip("Allowed range: 10 – 24 px")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        selected = self.theme_combo.findData(settings.ui_theme)
        if selected >= 0:
            self.theme_combo.setCurrentIndex(selected)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setVerticalSpacing(12)

        # Inline buttons should match the height of the fields they sit beside
        # (colour already distinguishes them, so they needn't be taller).
        field_h = self.output_dir_edit.sizeHint().height()
        self.output_dir_browse = QPushButton("Browse...")
        self.output_dir_browse.setObjectName("secondaryButton")
        self.output_dir_browse.setFixedHeight(field_h)
        self.template_reset_btn.setFixedHeight(field_h)
        self.output_dir_browse.clicked.connect(self._browse_output_dir)
        output_dir_row = QHBoxLayout()
        output_dir_row.setContentsMargins(0, 0, 0, 0)
        output_dir_row.setSpacing(6)
        output_dir_row.addWidget(self.output_dir_edit, 1)
        output_dir_row.addWidget(self.output_dir_browse, 0)
        output_dir_widget = QWidget()
        output_dir_widget.setLayout(output_dir_row)
        form.addRow("Default output folder", output_dir_widget)
        # Template field: line-edit + Reset + hover-help ⓘ icon (no wall of
        # small text under the field).
        template_input_row = QHBoxLayout()
        template_input_row.setContentsMargins(0, 0, 0, 0)
        template_input_row.setSpacing(6)
        template_input_row.addWidget(self.template_edit, 1)
        template_input_row.addWidget(self.template_reset_btn, 0)
        template_input_row.addWidget(self.template_help_icon, 0)
        template_widget = QWidget()
        template_widget.setLayout(template_input_row)
        form.addRow("Output filename template", template_widget)
        form.addRow("Saved image DPI", self.dpi_spin)
        form.addRow("UI font size", self.font_size_spin)
        form.addRow("UI theme", self.theme_combo)

        # Multi-variant generation toggle. Short label + hover ⓘ for detail.
        self.multi_variant_cb = QCheckBox("Enable multi-variant generation")
        self.multi_variant_cb.setChecked(settings.multi_variant_enabled)
        variant_help = self._info_icon(
            "Generate many outputs from one source image at once:\n"
            "different formats × bayer patterns × sizes.\n"
            "Adds a checkbox grid to the Convert and Batch Convert dialogs."
        )
        variant_help.setObjectName("variantHelp")
        variant_row = QHBoxLayout()
        variant_row.setContentsMargins(0, 0, 0, 0)
        variant_row.setSpacing(6)
        variant_row.addWidget(self.multi_variant_cb, 0)
        variant_row.addWidget(variant_help, 0)
        variant_row.addStretch(1)
        variant_widget = QWidget()
        variant_widget.setLayout(variant_row)
        form.addRow("Convert variants", variant_widget)

        # Sensor preset management entry-point — opens a dedicated dialog so
        # this Settings window stays small.
        # A jump-to action, not a primary control — use a quiet text-link
        # style so it doesn't out-shout Save.
        self.manage_presets_btn = QPushButton("Manage sensor presets →")
        self.manage_presets_btn.setObjectName("textButton")
        self.manage_presets_btn.setToolTip(
            "Add, edit, rename, or delete saved sensor presets."
        )
        self.manage_presets_btn.clicked.connect(self._open_preset_manager)
        # Keep this secondary action from dominating: align left, natural width.
        manage_row = QHBoxLayout()
        manage_row.setContentsMargins(0, 0, 0, 0)
        manage_row.addWidget(self.manage_presets_btn, 0)
        manage_row.addStretch(1)
        manage_widget = QWidget()
        manage_widget.setLayout(manage_row)
        form.addRow("Sensor presets", manage_widget)

        # Primary action (Save) is filled; Cancel is a quieter outline button.
        save_btn = QPushButton("Save")
        save_btn.setObjectName("accentButton")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)

        # Separator between the content area and the action bar.
        divider = QFrame()
        divider.setObjectName("groupDivider")
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(divider)
        layout.addLayout(row)

        # Long templates get truncated by the field — reveal the full value on
        # hover and keep the start visible.
        self.template_edit.textChanged.connect(self._on_template_changed)
        self._on_template_changed(self.template_edit.text())

        # Snapshot for unsaved-change detection on close.
        self._initial_state = self._current_state()

    # ── unsaved-change guard ─────────────────────────────────────────

    def _current_state(self) -> tuple:
        return (
            self.output_dir_edit.text(),
            self.template_edit.text(),
            self.dpi_spin.value(),
            self.font_size_spin.value(),
            str(self.theme_combo.currentData()),
            self.multi_variant_cb.isChecked(),
        )

    def _is_dirty(self) -> bool:
        return self._current_state() != self._initial_state

    def _confirm_close(self) -> str:
        """Return 'close', 'saved', or 'stay' for the current close attempt."""
        if not self._is_dirty():
            return "close"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Unsaved changes")
        box.setText("You have unsaved changes. Save them before closing?")
        save_b = box.addButton("Save", QMessageBox.AcceptRole)
        discard_b = box.addButton("Don't Save", QMessageBox.DestructiveRole)
        cancel_b = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(save_b)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is cancel_b:
            return "stay"
        if clicked is save_b:
            self._save()  # persists + accept()
            return "saved"
        return "close"  # Don't Save

    def reject(self) -> None:  # Cancel button / Esc / dialog close
        result = self._confirm_close()
        if result == "close":
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 — window "X"
        result = self._confirm_close()
        if result == "stay":
            event.ignore()
        else:
            event.accept()

    def _on_template_changed(self, text: str) -> None:
        self.template_edit.setToolTip(text)

    def _info_icon(self, tooltip: str) -> QPushButton:
        """Return a keyboard-accessible info button with the same tooltip help."""
        button = QPushButton()
        button.setObjectName("infoButton")
        button.setFlat(True)
        button.setAccessibleName("More information")
        button.setAccessibleDescription(tooltip)
        button.setToolTip(tooltip)
        button.setCursor(Qt.WhatsThisCursor)
        button.setFixedSize(24, 24)
        try:
            import qtawesome as qta

            from raw_view.models import ACTION_ICON_COLOR

            button.setIcon(qta.icon("fa5s.info-circle", color=ACTION_ICON_COLOR))
        except Exception:
            button.setText("i")
        button.setIconSize(QSize(16, 16))
        button.clicked.connect(lambda: QMessageBox.information(button, "More information", tooltip))
        return button

    def _browse_output_dir(self) -> None:
        """Pick a default output folder via the system dialog."""
        start = self.output_dir_edit.text().strip() or ""
        # 相对目录名（如 "convert_out"/"out"）在对话框起始路径里会随 CWD 变化，
        # 统一先解析成绝对路径作为起始目录；保存值仍用用户输入，不受影响。
        if start and not os.path.isabs(start):
            start = os.path.abspath(start)
        chosen = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if chosen:
            self.output_dir_edit.setText(chosen)

    def _reset_template(self) -> None:
        """Replace the template field's text with the current built-in default.

        Asks for confirmation first (guards against accidental clicks). The
        change is only persisted when the user clicks Save.
        """
        confirm = QMessageBox.question(
            self,
            "Reset template",
            "Reset the output filename template to the built-in default?\n"
            f"{DEFAULT_OUTPUT_TEMPLATE}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.template_edit.setText(DEFAULT_OUTPUT_TEMPLATE)

    def _open_preset_manager(self) -> None:
        # Local import to avoid a circular dependency at module load time
        # (preset.py imports from raw_view.gui.panels which is fine, but
        # keeping it local makes the dependency chain explicit).
        from .preset import PresetManagerDialog

        dlg = PresetManagerDialog(self._settings, self)
        dlg.exec_()

    def _save(self) -> None:
        self._settings.default_output_dirname = self.output_dir_edit.text()
        self._settings.output_template = self.template_edit.text().strip()
        self._settings.save_dpi = self.dpi_spin.value()
        self._settings.ui_font_size = self.font_size_spin.value()
        self._settings.ui_theme = str(self.theme_combo.currentData())
        self._settings.multi_variant_enabled = self.multi_variant_cb.isChecked()
        self.accept()
