"""Batch image-to-RAW/YUV conversion dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import image_file_to_raw, image_file_to_yuv, load_bgr_image
from raw_view.formats import expected_frame_size_raw, expected_frame_size_yuv
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    format_output_template,
)
from raw_view.gui.widgets import FileDropLineEdit


class BatchConvertDialog(QDialog):
    """Modal dialog for batch-converting multiple images to RAW or YUV format."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Batch Convert")
        self.setMinimumSize(700, 520)

        # ── Source file list (drag-drop or browse) ──
        self.input_edit = FileDropLineEdit()
        self.input_edit.setPlaceholderText(
            "Drop files here or use Browse to add multiple images..."
        )
        self._add_btn = QPushButton("Add Files")
        self._clear_btn = QPushButton("Clear All")
        self._add_btn.clicked.connect(self._browse_files)
        self._clear_btn.clicked.connect(self._clear_files)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self._add_btn)
        input_row.addWidget(self._clear_btn)

        # ── File table ──
        self._file_table = QTableWidget(0, 4)
        self._file_table.setHorizontalHeaderLabels(["File", "Size", "Status", "Output"])
        self._file_table.horizontalHeader().setStretchLastSection(True)
        self._file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._file_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._file_table.verticalHeader().setVisible(False)

        # ── Output parameters ──
        params_group = QFrame()
        params_group.setFrameShape(QFrame.StyledPanel)
        params_layout = QHBoxLayout(params_group)
        params_layout.setContentsMargins(8, 8, 8, 8)

        self.target_type = QComboBox()
        self.target_type.addItems(["RAW", "YUV"])

        self.raw_type = QComboBox()
        self.raw_type.addItems(
            ["RAW8", "RAW10", "RAW12", "RAW10 Packed", "RAW12 Packed", "RAW14 Packed", "RAW16"]
        )

        self.yuv_type = QComboBox()
        self.yuv_type.addItems(["I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "NV16"])

        self.align = QComboBox()
        self.align.addItems(["lsb", "msb"])

        self.raw_source_mode = QComboBox()
        self.raw_source_mode.addItems(["bayer", "gray"])

        self.bayer_pattern = QComboBox()
        self.bayer_pattern.addItems(BAYER_PATTERNS)

        self.width = QSpinBox()
        self.width.setRange(1, 65535)
        self.width.setValue(640)

        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(480)

        self._same_dir_cb = QCheckBox("Same directory as input")
        self._same_dir_cb.setChecked(True)

        params_form = QFormLayout()
        params_form.addRow("Target", self.target_type)
        params_form.addRow("RAW type", self.raw_type)
        params_form.addRow("YUV format", self.yuv_type)
        params_form.addRow("Alignment", self.align)
        params_form.addRow("RAW source", self.raw_source_mode)
        params_form.addRow("Bayer pattern", self.bayer_pattern)
        params_form.addRow("Width", self.width)
        params_form.addRow("Height", self.height)

        params_right = QVBoxLayout()
        params_right.addLayout(params_form)
        params_right.addWidget(self._same_dir_cb)
        params_right.addStretch(1)

        params_layout.addLayout(params_right, 1)

        # ── Progress ──
        self._progress = QProgressDialog("Batch conversion in progress...", "Cancel", 0, 100, self)
        self._progress.setWindowTitle("Batch Convert")
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(True)
        self._progress.setAutoReset(True)
        self._progress.setModal(True)
        self._progress.canceled.connect(self._on_cancel_batch)
        self._progress.hide()
        self._batch_cancelled = False

        # ── Buttons ──
        self._run_btn = QPushButton("Start Batch Convert")
        self._run_btn.clicked.connect(self._run_batch)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(close_btn)

        # ── Main layout ──
        layout = QVBoxLayout(self)
        layout.addLayout(input_row)
        layout.addWidget(self._file_table, 1)
        layout.addWidget(params_group)
        layout.addLayout(btn_row)

        # Signals
        self.input_edit.fileDropped.connect(self._on_files_dropped)
        self.target_type.currentTextChanged.connect(self._sync_controls)
        self.raw_source_mode.currentTextChanged.connect(self._sync_controls)

        self._sync_controls()

    # ── File management ────────────────────────────────────────────────

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)",
        )
        if paths:
            self._add_files(paths)

    def _clear_files(self) -> None:
        self._file_table.setRowCount(0)

    def _on_files_dropped(self, path: str) -> None:
        """Handle dropped file (single path from FileDropLineEdit)."""
        if path:
            self._add_files([path])

    def _add_files(self, paths: list[str]) -> None:
        existing = set()
        for row in range(self._file_table.rowCount()):
            item = self._file_table.item(row, 0)
            if item:
                existing.add(item.text())

        for path in paths:
            if path in existing:
                continue
            row = self._file_table.rowCount()
            self._file_table.insertRow(row)
            self._file_table.setItem(row, 0, QTableWidgetItem(path))
            try:
                size = Path(path).stat().st_size
                size_str = f"{size:,} bytes"
            except OSError:
                size_str = "-"
            self._file_table.setItem(row, 1, QTableWidgetItem(size_str))
            self._file_table.setItem(row, 2, QTableWidgetItem("Pending"))
            self._file_table.setItem(row, 3, QTableWidgetItem(""))

        self.input_edit.setText(f"{self._file_table.rowCount()} file(s) loaded")

    # ── Control sync ───────────────────────────────────────────────────

    def _sync_controls(self) -> None:
        is_raw = self.target_type.currentText() == "RAW"
        is_bayer = self.raw_source_mode.currentText() == "bayer"
        self.raw_type.setEnabled(is_raw)
        self.align.setEnabled(is_raw)
        self.raw_source_mode.setEnabled(is_raw)
        self.bayer_pattern.setEnabled(is_raw and is_bayer)
        self.yuv_type.setEnabled(not is_raw)

    # ── Batch conversion ───────────────────────────────────────────────

    def _run_batch(self) -> None:
        rows = self._file_table.rowCount()
        if rows == 0:
            QMessageBox.information(self, "Batch Convert", "No files to convert.")
            return

        target_type = self.target_type.currentText()
        fmt = self.raw_type.currentText() if target_type == "RAW" else self.yuv_type.currentText()
        out_w = self.width.value()
        out_h = self.height.value()
        template = self._settings.output_template

        # Collect files
        files = []
        for row in range(rows):
            path_item = self._file_table.item(row, 0)
            if path_item:
                p = path_item.text().strip()
                if p:
                    files.append((row, p))

        if not files:
            return

        # Reset statuses
        for row, _ in files:
            self._file_table.item(row, 2).setText("Pending")
            self._file_table.item(row, 3).setText("")

        self._batch_cancelled = False
        self._progress.setMaximum(len(files))
        self._progress.setValue(0)
        self._progress.setLabelText("Batch conversion in progress...")
        self._progress.show()

        success_count = 0
        fail_count = 0

        try:
            for i, (row, input_path) in enumerate(files):
                if self._batch_cancelled:
                    self._file_table.item(row, 2).setText("Cancelled")
                    continue

                self._progress.setValue(i)
                self._progress.setLabelText(f"Converting {i + 1}/{len(files)}: {Path(input_path).name}")

                output_path = format_output_template(
                    template, input_path, out_w, out_h, target_type,
                )
                if self._same_dir_cb.isChecked():
                    # Use same directory as input instead of "out/"
                    src = Path(input_path)
                    out_name = Path(output_path).name
                    output_path = str(src.parent / out_name)

                self._file_table.item(row, 3).setText(output_path)

                try:
                    if target_type == "RAW":
                        image_file_to_raw(
                            input_path,
                            output_path,
                            self.raw_type.currentText(),
                            out_w,
                            out_h,
                            alignment=self.align.currentText(),
                            source_mode=self.raw_source_mode.currentText(),
                            bayer_pattern=self.bayer_pattern.currentText(),
                        )
                    else:
                        image_file_to_yuv(input_path, output_path, self.yuv_type.currentText(), out_w, out_h)

                    self._file_table.item(row, 2).setText("OK")
                    success_count += 1
                except Exception as exc:
                    self._file_table.item(row, 2).setText(f"Failed: {exc}")
                    fail_count += 1

                QApplication.processEvents()

                if self._batch_cancelled:
                    break
        finally:
            self._progress.close()

        # Report
        total = len(files)
        summary = f"Batch complete: {success_count}/{total} succeeded"
        if fail_count > 0:
            summary += f", {fail_count} failed"
        QMessageBox.information(self, "Batch Convert", summary)

    def _on_cancel_batch(self) -> None:
        self._batch_cancelled = True
