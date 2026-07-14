"""FourCC Lookup dialog — browse, search, and manage FourCC format entries."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from raw_view.fourcc_data import FourCCEntry, FourCCStore
from raw_view.models import AppSettings


_COL_FOURCC = 0
_COL_ALIAS = 1
_COL_DESC = 2
_COL_MBUS = 3
_COL_VALUE = 4
_COL_COUNT = 5

_COL_HEADERS = ["FourCC", "Alias", "Description", "MBUS Name", "MBUS Value"]


class FourCCEditDialog(QDialog):
    """Sub-dialog for adding or editing a single FourCC entry."""

    def __init__(self, parent: QWidget | None = None,
                 entry: FourCCEntry | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setWindowTitle("Edit FourCC Entry" if entry else "Add FourCC Entry")
        self.setMinimumWidth(480)

        self.fourcc_edit = QLineEdit()
        self.alias_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        self.mbus_edit = QLineEdit()
        self.mbus_combo = QComboBox()
        self.mbus_combo.setEditable(True)
        # Populate common MBUS prefixes
        common_mbus = [
            "MEDIA_BUS_FMT_YUYV8_2X8",
            "MEDIA_BUS_FMT_SBGGR8_1X8",
            "MEDIA_BUS_FMT_SGBRG8_1X8",
            "MEDIA_BUS_FMT_SGRBG8_1X8",
            "MEDIA_BUS_FMT_SRGGB8_1X8",
            "MEDIA_BUS_FMT_SBGGR10_1X10",
            "MEDIA_BUS_FMT_SGBRG10_1X10",
            "MEDIA_BUS_FMT_SGRBG10_1X10",
            "MEDIA_BUS_FMT_SRGGB10_1X10",
            "MEDIA_BUS_FMT_SBGGR12_1X12",
            "MEDIA_BUS_FMT_SGBRG12_1X12",
            "MEDIA_BUS_FMT_SGRBG12_1X12",
            "MEDIA_BUS_FMT_SRGGB12_1X12",
            "MEDIA_BUS_FMT_SBGGR16_1X16",
            "MEDIA_BUS_FMT_SGBRG16_1X16",
            "MEDIA_BUS_FMT_SGRBG16_1X16",
            "MEDIA_BUS_FMT_SRGGB16_1X16",
        ]
        self.mbus_combo.addItems(common_mbus)

        self.value_spin = QSpinBox()
        self.value_spin.setRange(0x0000, 0xFFFF)
        self.value_spin.setDisplayIntegerBase(16)
        self.value_spin.setPrefix("0x")
        self.value_spin.setFixedWidth(120)

        # Pre-fill
        if entry:
            self.fourcc_edit.setText(entry.fourcc)
            self.alias_edit.setText(", ".join(entry.aliases))
            self.desc_edit.setText(entry.description)
            self.mbus_combo.setEditText(entry.mbus_name)
            self.value_spin.setValue(entry.mbus_value)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("FourCC", self.fourcc_edit)
        form.addRow("Aliases (comma-separated)", self.alias_edit)
        form.addRow("Description", self.desc_edit)
        form.addRow("MBUS Name", self.mbus_combo)
        form.addRow("MBUS Value (hex)", self.value_spin)
        form.addRow(buttons)

    def _validate(self) -> None:
        fourcc = self.fourcc_edit.text().strip()
        if not fourcc:
            QMessageBox.warning(self, "Validation", "FourCC is required.")
            self.fourcc_edit.setFocus()
            return
        if not self.desc_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Description is required.")
            self.desc_edit.setFocus()
            return
        self.accept()

    def get_entry(self) -> FourCCEntry:
        aliases_raw = self.alias_edit.text().strip()
        aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
        return FourCCEntry(
            fourcc=self.fourcc_edit.text().strip(),
            aliases=aliases,
            description=self.desc_edit.text().strip(),
            mbus_name=self.mbus_combo.currentText().strip(),
            mbus_value=self.value_spin.value(),
            builtin=False,
        )


class FourCCDialog(QDialog):
    """Main FourCC lookup dialog with table, search, and custom format management."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store = FourCCStore(
            custom_entries=settings.fourcc_custom_formats,
        )
        self.setWindowTitle("FourCC Lookup")
        self.resize(880, 500)

        self._build_ui()
        self._populate_table()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Search bar
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search by FourCC, alias, description, MBUS name, or value…")
        self._search_edit.textChanged.connect(self._on_search)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_search)
        search_row.addWidget(self._search_edit, 1)
        search_row.addWidget(clear_btn)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(_COL_COUNT)
        self._table.setHorizontalHeaderLabels(_COL_HEADERS)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.horizontalHeader().setSectionResizeMode(_COL_FOURCC, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_ALIAS, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_DESC, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_COL_MBUS, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_COL_VALUE, QHeaderView.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # Buttons
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("Add Custom")
        self._edit_btn = QPushButton("Edit")
        self._delete_btn = QPushButton("Delete")
        close_btn = QPushButton("Close")

        self._add_btn.clicked.connect(self._add_custom)
        self._edit_btn.clicked.connect(self._edit_custom)
        self._delete_btn.clicked.connect(self._delete_custom)
        close_btn.clicked.connect(self.accept)

        self._edit_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)

        # Explicit button styling: visible border in both light and dark mode.
        for btn in (self._add_btn, self._edit_btn, self._delete_btn, close_btn):
            btn.setStyleSheet(
                "QPushButton {"
                "  border: 1px solid palette(mid); border-radius: 6px;"
                "  padding: 6px 16px;"
                "}"
                "QPushButton:hover { background: rgba(128, 128, 128, 0.15); }"
            )

        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._edit_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        # Status label
        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #64748B;")

        # Main layout
        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)
        layout.addLayout(btn_row)

    # ── Table population ───────────────────────────────────────────────

    def _populate_table(self, entries: list[FourCCEntry] | None = None) -> None:
        if entries is None:
            entries = self._store.all_formats
        self._table.setRowCount(0)
        self._table.setRowCount(len(entries))

        custom_count = 0
        for row, entry in enumerate(entries):
            self._set_row(row, entry)
            if not entry.builtin:
                custom_count += 1

        self._status_label.setText(
            f"{len(entries)} format(s) shown ({custom_count} custom)"
        )

    def _set_row(self, row: int, entry: FourCCEntry) -> None:
        alias_str = ", ".join(entry.aliases) if entry.aliases else "-"

        items = [
            QTableWidgetItem(entry.fourcc),
            QTableWidgetItem(alias_str),
            QTableWidgetItem(entry.description),
            QTableWidgetItem(entry.mbus_name),
            QTableWidgetItem(f"0x{entry.mbus_value:04X}"),
        ]
        for col, item in enumerate(items):
            if not entry.builtin:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            self._table.setItem(row, col, item)

    # ── Search ─────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        query = self._search_edit.text()
        results = self._store.search(query) if query else self._store.all_formats
        self._populate_table(results)

    def _clear_search(self) -> None:
        self._search_edit.clear()
        self._populate_table()

    # ── Selection ──────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            self._edit_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            return
        # Determine if the selected row is a custom entry by checking
        # against the currently filtered results.
        entry = self._entry_at_row(row)
        is_custom = entry is not None and not entry.builtin
        self._edit_btn.setEnabled(is_custom)
        self._delete_btn.setEnabled(is_custom)

    def _entry_at_row(self, row: int) -> FourCCEntry | None:
        """Map a table row index back to its FourCCEntry."""
        entries = self._store.search(self._search_edit.text())
        if 0 <= row < len(entries):
            return entries[row]
        return None

    def _current_entry(self) -> FourCCEntry | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        return self._entry_at_row(row)

    # ── Context menu ───────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        entry = self._current_entry()
        menu = QMenu(self)
        add_action = menu.addAction("Add Custom")
        add_action.triggered.connect(self._add_custom)
        if entry is not None:
            if not entry.builtin:
                edit_action = menu.addAction("Edit")
                edit_action.triggered.connect(self._edit_custom)
                menu.addSeparator()
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(self._delete_custom)
        menu.exec_(self._table.mapToGlobal(pos))

    # ── CRUD ───────────────────────────────────────────────────────────

    def _add_custom(self) -> None:
        dlg = FourCCEditDialog(self, entry=None)
        if dlg.exec_() != QDialog.Accepted:
            return
        entry = dlg.get_entry()

        # Check for duplicate FourCC (case-SENSITIVE — ABC ≠ abc)
        if self._store.has_fourcc_exact(entry.fourcc):
            QMessageBox.warning(
                self, "Duplicate",
                f"FourCC '{entry.fourcc}' already exists.",
            )
            return

        self._store.add_custom(entry)
        self._persist_custom()
        self._refresh_after_change()

    def _edit_custom(self) -> None:
        entry = self._current_entry()
        if entry is None or entry.builtin:
            return
        dlg = FourCCEditDialog(self, entry=entry)
        if dlg.exec_() != QDialog.Accepted:
            return
        updated = dlg.get_entry()

        # Find the index of this entry in the custom list
        custom_list = self._store.custom_formats
        idx = -1
        for i, c in enumerate(custom_list):
            if c.fourcc == entry.fourcc and c.builtin == entry.builtin:
                idx = i
                break
        if idx >= 0:
            self._store.update_custom(idx, updated)
            self._persist_custom()
            self._refresh_after_change()

    def _delete_custom(self) -> None:
        entry = self._current_entry()
        if entry is None or entry.builtin:
            return
        confirm = QMessageBox.question(
            self, "Delete",
            f"Delete custom entry '{entry.fourcc}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        custom_list = self._store.custom_formats
        idx = -1
        for i, c in enumerate(custom_list):
            if c.fourcc == entry.fourcc and c.builtin == entry.builtin:
                idx = i
                break
        if idx >= 0:
            self._store.delete_custom(idx)
            self._persist_custom()
            self._refresh_after_change()

    # ── Persistence ────────────────────────────────────────────────────

    def _persist_custom(self) -> None:
        self._settings.save_fourcc_custom_list(self._store.custom_formats)

    def _refresh_after_change(self) -> None:
        # Re-run search to refresh table
        self._on_search()
