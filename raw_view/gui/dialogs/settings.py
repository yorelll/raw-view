"""Application preferences dialog."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

        self.output_dir_edit = QLineEdit(settings.default_output_dirname)
        self.template_edit = QLineEdit(settings.output_template)
        self._template_defaults = QLabel(
            "Placeholders: {date} {time} {input_stem} {width} {height} {ext}"
        )
        self._template_defaults.setWordWrap(True)
        self._template_defaults.setStyleSheet("font-size: 11px; color: gray;")
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 2400)
        self.dpi_spin.setValue(settings.save_dpi)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(10, 24)
        self.font_size_spin.setValue(settings.ui_font_size)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        selected = self.theme_combo.findData(settings.ui_theme)
        if selected >= 0:
            self.theme_combo.setCurrentIndex(selected)

        form = QFormLayout()
        form.addRow("Default convert output folder", self.output_dir_edit)
        template_widget = QWidget()
        template_layout = QVBoxLayout(template_widget)
        template_layout.setContentsMargins(0, 0, 0, 0)
        template_layout.setSpacing(2)
        template_layout.addWidget(self.template_edit)
        template_layout.addWidget(self._template_defaults)
        form.addRow("Output filename template", template_widget)
        form.addRow("Saved image DPI", self.dpi_spin)
        form.addRow("UI font size", self.font_size_spin)
        form.addRow("UI theme", self.theme_combo)

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(row)

    def _save(self) -> None:
        self._settings.default_output_dirname = self.output_dir_edit.text()
        self._settings.output_template = self.template_edit.text().strip()
        self._settings.save_dpi = self.dpi_spin.value()
        self._settings.ui_font_size = self.font_size_spin.value()
        self._settings.ui_theme = str(self.theme_combo.currentData())
        self.accept()
