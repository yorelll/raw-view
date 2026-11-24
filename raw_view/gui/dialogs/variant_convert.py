"""Variant Convert page — the second mode of the Convert dialog.

Variant generation used to be a checkbox panel appended below the single-file
Convert form, with its own "Generate Variants" button next to "Convert"; the
two workflows were visually mixed and easy to confuse (user report).  This
module builds the *separate* page that the Convert dialog shows when its mode
selector is set to **Variant Convert**:

* Image input: the existing :class:`VariantSelector` (RAW/YUV formats × Bayer
  patterns × sizes) plus the same "Same directory as input" destination logic
  Batch uses.
* YUV input: lossless same-family rearrangement into **several** target YUV
  layouts at once.  There is no Bayer pattern, no output size and no bit depth
  here — nothing is resized or re-quantised, so Width/Height are the SOURCE
  frame dimensions and "Source offset" handles a file header.

Non-convertible YUV targets are greyed out with :func:`reject_reason` as their
tooltip, so the user never gets to pick a combination that then fails.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import (
    generate_image_variants,
    plan_image_variants,
    resolve_output_path_collision,
)
from raw_view.formats import require_decode_size
from raw_view.models import (
    AppSettings,
    THEME_PALETTES,
    format_output_template,
    normalize_ui_theme,
    resolve_output_dir,
)
from raw_view.gui.widgets import FileDropLineEdit, VariantSelector
from raw_view.transcode import (
    YUV_TRANSCODE_FORMATS,
    TranscodeError,
    TranscodeSpec,
    frame_count,
    frame_size,
    reject_reason,
    transcode_file,
)

# Same initial guess as the single-file Convert page (and the main panel).
DEFAULT_CONVERT_WIDTH = 2560
DEFAULT_CONVERT_HEIGHT = 1440

# A YUV source may carry any (or no) suffix, so "All Files" must stay.
INPUT_FILE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)"

_RESERVED_INPUT_ITEM = 2  # "RAW (coming soon)" index in input_type


class YuvVariantSelector(QWidget):
    """Checkbox grid of YUV target layouts for the current source layout.

    Only same-family targets are selectable; every other entry stays visible
    but disabled, with :func:`reject_reason` as its tooltip.  This mirrors the
    single-file Convert target list so the two pages explain refusals the same
    way.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._boxes: dict[str, QCheckBox] = {}
        self._source = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Target YUV formats (same chroma family)")
        title.setStyleSheet("font-weight: bold; border: none;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        root.addLayout(title_row)

        grid = QGridLayout()
        grid.setSpacing(4)
        for index, name in enumerate(YUV_TRANSCODE_FORMATS):
            box = QCheckBox(name)
            self._boxes[name] = box
            grid.addWidget(box, index // 4, index % 4)
        root.addLayout(grid)

    def source_format(self) -> str:
        return self._source

    def set_source(self, source: str) -> None:
        """Refresh enabled/checked state for *source*.

        Defaults to every same-family target being checked, which is what the
        user asked for ("a YUYV file can be converted to UYVY, YVYU, … at the
        same time").  The source layout itself and other families stay
        unchecked and disabled with an explanation.
        """
        self._source = source
        for name, box in self._boxes.items():
            reason = reject_reason(source, name)
            blocked = box.blockSignals(True)
            try:
                box.setEnabled(reason is None)
                box.setToolTip(reason or "")
                box.setChecked(reason is None)
            finally:
                box.blockSignals(blocked)

    def selected_formats(self) -> list[str]:
        """Selected, actually convertible targets (source/other family excluded)."""
        return [
            name
            for name, box in self._boxes.items()
            if box.isEnabled() and box.isChecked()
        ]


class VariantConvertPage(QWidget):
    """The independent Variant Convert page used by :class:`ConvertDialog`."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._generating = False
        # "Do this for all remaining files" choice; scoped to one Variant
        # operation and cleared in the finally block of generate().
        self._operation_collision_choice: str | None = None
        self._rewritten_outputs: dict[str, str] = {}
        self._last_yuv_source: str | None = None

        # ── Input file ────────────────────────────────────────────────
        self.input_edit = FileDropLineEdit()
        self.input_edit.setAccessibleName("Variant input file")
        self.input_edit.setAccessibleDescription(
            "Select the image (PNG/JPG/BMP) or YUV file used to generate variants."
        )
        self.input_edit.setPlaceholderText(
            "Select an image or YUV file, or drag one here..."
        )
        field_h = self.input_edit.sizeHint().height()
        self._input_browse_btn = QPushButton("Browse...")
        self._input_browse_btn.setObjectName("secondaryButton")
        self._input_browse_btn.setFixedHeight(field_h)
        self._input_browse_btn.clicked.connect(self._browse_input)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self._input_browse_btn, 0)
        input_row_w = QWidget()
        input_row_w.setLayout(input_row)

        # ── Input type (Image / YUV / reserved RAW) ───────────────────
        self.input_type = QComboBox()
        self.input_type.addItems(["Image", "YUV", "RAW (coming soon)"])
        self.input_type.setItemData(
            _RESERVED_INPUT_ITEM,
            "RAW input conversion (packed RAW, bit alignment, bit depth) is "
            "reserved for a future release.",
            Qt.ToolTipRole,
        )
        reserved_item = self.input_type.model().item(_RESERVED_INPUT_ITEM)
        if reserved_item is not None:
            reserved_item.setEnabled(False)
        self.input_type.setAccessibleName("Variant input type")
        self.input_type.setAccessibleDescription(
            "Image: decode a PNG/JPG/BMP and generate image variants. "
            "YUV: losslessly rearrange an existing YUV file into several "
            "same-family layouts."
        )

        self._input_hint = QLabel()
        self._input_hint.setObjectName("hintLabel")
        self._input_hint.setWordWrap(True)

        # ── Image-only mode ───────────────────────────────────────────
        self.image_card = QFrame()
        self.image_card.setObjectName("card")
        self.image_card.setFrameShape(QFrame.StyledPanel)
        image_layout = QVBoxLayout(self.image_card)
        image_layout.setContentsMargins(8, 8, 8, 8)

        image_form = QFormLayout()
        self.raw_source_mode = QComboBox()
        self.raw_source_mode.addItems(["bayer", "gray"])
        self.align = QComboBox()
        self.align.addItems(["msb", "lsb"])
        self.endian = QComboBox()
        self.endian.addItems(["little", "big"])
        image_form.addRow("RAW source", self.raw_source_mode)
        image_form.addRow("Alignment", self.align)
        image_form.addRow("Endianness", self.endian)
        image_layout.addLayout(image_form)

        self.image_selector = VariantSelector()
        image_layout.addWidget(self.image_selector)

        # ── YUV-only mode ─────────────────────────────────────────────
        self.yuv_card = QFrame()
        self.yuv_card.setObjectName("card")
        self.yuv_card.setFrameShape(QFrame.StyledPanel)
        yuv_layout = QVBoxLayout(self.yuv_card)
        yuv_layout.setContentsMargins(8, 8, 8, 8)

        yuv_form = QFormLayout()
        # Only formats the transcoder can read: YOnly has no chroma planes and
        # would leave every target greyed out (a dead end).
        self.input_yuv_type = QComboBox()
        self.input_yuv_type.addItems(YUV_TRANSCODE_FORMATS)
        self.input_yuv_type.setAccessibleName("Variant source YUV format")
        self.offset = QSpinBox()
        self.offset.setRange(0, 2**31 - 1)
        self.offset.setValue(0)
        self.offset.setToolTip(
            "Bytes to skip before the first frame (file header / padding)."
        )
        self.offset.setAccessibleName("Variant source offset")
        yuv_form.addRow("Source YUV format", self.input_yuv_type)
        yuv_form.addRow("Source offset (bytes)", self.offset)
        yuv_layout.addLayout(yuv_form)

        self.yuv_selector = YuvVariantSelector()
        yuv_layout.addWidget(self.yuv_selector)

        # ── Dimensions (output size for Image, source size for YUV) ──
        self.width = QSpinBox()
        self.width.setRange(1, 65535)
        self.width.setValue(DEFAULT_CONVERT_WIDTH)
        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(DEFAULT_CONVERT_HEIGHT)
        self.width.setAccessibleName("Variant width")
        self.height.setAccessibleName("Variant height")
        self._width_label = QLabel("Width")
        self._height_label = QLabel("Height")
        # Width/Height are only meaningful for YUV input (source frame size).
        # In Image mode the Size checkbox panel decides every output size, so
        # these two spin boxes were dead controls (user report: "这个框干嘛的?").
        # Kept as an attribute so _sync_controls_locked() can detach/attach
        # both rows together with their labels.
        self._dims_form = QFormLayout()
        self._dims_form.addRow(self._width_label, self.width)
        self._dims_form.addRow(self._height_label, self.height)

        # ── Output destination (same "always judgeable" rule as Batch) ─
        self._same_dir_cb = QCheckBox("Same directory as input")
        self._same_dir_cb.setChecked(True)
        self._same_dir_cb.setToolTip(
            "Checked: every variant is written next to the input file. "
            "Unchecked: use the output folder below."
        )
        self.output_dir_edit = QLineEdit(settings.default_output_dirname)
        self.output_dir_edit.setAccessibleName("Variant output folder")
        self.output_dir_edit.setAccessibleDescription(
            "Folder for variant outputs when 'Same directory as input' is off. "
            "A relative name is created next to the input file."
        )
        if not self.output_dir_edit.text().strip():
            self.output_dir_edit.setText("convert_out")
        self.output_dir_edit.setPlaceholderText(
            "Output folder, e.g. convert_out or an absolute path"
        )
        self._output_dir_browse_btn = QPushButton("Browse...")
        self._output_dir_browse_btn.setObjectName("secondaryButton")
        self._output_dir_browse_btn.setFixedHeight(
            self.output_dir_edit.sizeHint().height()
        )
        self._output_dir_browse_btn.clicked.connect(self._browse_output_dir)
        output_dir_row = QHBoxLayout()
        output_dir_row.setContentsMargins(0, 0, 0, 0)
        output_dir_row.setSpacing(6)
        output_dir_row.addWidget(self.output_dir_edit, 1)
        output_dir_row.addWidget(self._output_dir_browse_btn, 0)
        self._output_dir_widget = QWidget()
        self._output_dir_widget.setLayout(output_dir_row)
        self._output_dir_hint = QLabel()
        self._output_dir_hint.setObjectName("hintLabel")
        self._output_dir_hint.setWordWrap(True)

        output_folder_row = QHBoxLayout()
        output_folder_row.setContentsMargins(0, 0, 0, 0)
        output_folder_row.addWidget(QLabel("Output folder"), 0)
        output_folder_row.addWidget(self._same_dir_cb, 0)
        output_folder_row.addStretch(1)

        # ── Generate button (pinned below the scroll area) ────────────
        self.generate_button = QPushButton("Generate Variants")
        self.generate_button.setObjectName("accentButton")
        self.generate_button.setEnabled(False)
        self.generate_button.setToolTip(
            "Choose an input file before generating variants."
        )
        self.generate_button.clicked.connect(self.generate)

        # ── Layout ────────────────────────────────────────────────────
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Kept as an attribute: ConvertDialog's Mode row (above the page stack)
        # aligns its label column to whichever page is currently visible
        # (0.5.11.3 review L-1).
        self._top_form = QFormLayout()
        self._top_form.addRow("Input file", input_row_w)
        self._top_form.addRow("Input type", self.input_type)
        self._top_form.addRow("", self._input_hint)
        content_layout.addLayout(self._top_form)
        content_layout.addWidget(self.image_card)
        content_layout.addWidget(self.yuv_card)
        content_layout.addLayout(self._dims_form)
        content_layout.addLayout(output_folder_row)
        content_layout.addWidget(self._output_dir_widget)
        content_layout.addWidget(self._output_dir_hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.generate_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll, 1)
        root.addLayout(button_row)

        # ── Signals ───────────────────────────────────────────────────
        self.input_edit.textChanged.connect(self._on_input_text_changed)
        self.input_edit.fileDropped.connect(self._on_input_text_changed)
        self.input_type.currentTextChanged.connect(self._on_input_type_changed)
        self.input_yuv_type.currentTextChanged.connect(self._sync_controls)
        self._same_dir_cb.toggled.connect(self._sync_output_dir_controls)
        self.output_dir_edit.textChanged.connect(self._sync_output_dir_controls)

        self._sync_controls()
        self._sync_output_dir_controls()

    # ── reserved item painting ────────────────────────────────────────

    def refresh_disabled_items(self) -> None:
        """Paint the reserved RAW entry with the theme's disabled colour.

        ``setEnabled(False)`` blocks selection but qt-material does not grey the
        entry (same defect class as the single-file page's "RAW (coming
        soon)").  ``text_secondary`` is the token the stylesheet uses for
        disabled controls.
        """
        combo = getattr(self, "input_type", None)
        settings = getattr(self, "_settings", None)
        if combo is None or settings is None:
            return
        item = combo.model().item(_RESERVED_INPUT_ITEM)
        if item is None:
            return
        palette = THEME_PALETTES[normalize_ui_theme(settings.ui_theme)]
        item.setForeground(QBrush(QColor(palette["text_secondary"])))

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self.refresh_disabled_items()

    # ── input handling ────────────────────────────────────────────────

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Variant Input File", "", INPUT_FILE_FILTER
        )
        if path:
            self.input_edit.setText(path)

    def _browse_output_dir(self) -> None:
        start = self.output_dir_edit.text().strip()
        if not start or not Path(start).is_absolute():
            start = str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Output Folder", start)
        if path:
            self.output_dir_edit.setText(path)
            self._sync_output_dir_controls()

    def _is_yuv_input(self) -> bool:
        return self.input_type.currentText() == "YUV"

    def _on_input_text_changed(self, *_args) -> None:
        self._sync_generate_button()

    def _sync_generate_button(self) -> None:
        has_input = bool(self.input_edit.text().strip())
        self.generate_button.setEnabled(has_input and not self._generating)
        self.generate_button.setToolTip(
            "Generating variants..." if self._generating else
            "" if has_input else
            "Choose an input file before generating variants."
        )

    def _on_input_type_changed(self) -> None:
        """Switching Image/YUV resets fields whose meaning changed."""
        self.offset.setValue(0)
        if self._is_yuv_input():
            if self.input_yuv_type.currentText() not in YUV_TRANSCODE_FORMATS:
                self.input_yuv_type.setCurrentText(YUV_TRANSCODE_FORMATS[0])
        self.width.setValue(DEFAULT_CONVERT_WIDTH)
        self.height.setValue(DEFAULT_CONVERT_HEIGHT)
        # Re-arm the YUV target list even when the source layout text is the
        # same as before (Image -> YUV -> Image -> YUV).
        self._last_yuv_source = None
        self._sync_controls()

    def _sync_controls(self) -> None:
        if getattr(self, "_syncing_controls", False):
            return
        self._syncing_controls = True
        try:
            self._sync_controls_locked()
        finally:
            self._syncing_controls = False

    def _sync_controls_locked(self) -> None:
        is_yuv = self._is_yuv_input()
        self.image_card.setVisible(not is_yuv)
        self.yuv_card.setVisible(is_yuv)
        self._set_dimension_rows_visible(is_yuv)
        if is_yuv:
            source = self.input_yuv_type.currentText()
            if source != self._last_yuv_source:
                self.yuv_selector.set_source(source)
                self._last_yuv_source = source
            self._width_label.setText("Source width")
            self._height_label.setText("Source height")
            self.width.setToolTip(
                "Source frame width: used to parse the YUV byte layout. "
                "Variants are not resized."
            )
            self.height.setToolTip(
                "Source frame height: used to parse the YUV byte layout. "
                "Variants are not resized."
            )
            self._input_hint.setText(
                "YUV input: lossless byte rearrangement. Select several target "
                "layouts from the SAME chroma family (4:2:2 or 4:2:0); nothing "
                "is resized or re-quantised, so Width/Height are the SOURCE "
                "frame size."
            )
        else:
            self._width_label.setText("Width")
            self._height_label.setText("Height")
            self.width.setToolTip("Variant output width (the image is resized).")
            self.height.setToolTip("Variant output height (the image is resized).")
            self._input_hint.setText(
                "Image input: decode PNG/JPG/BMP and generate every selected "
                "RAW/YUV × Bayer × size combination. Width/Height are the "
                "OUTPUT size (the image is resized)."
            )
        self._sync_generate_button()

    def _set_dimension_rows_visible(self, visible: bool) -> None:
        """Show Width/Height (and their labels) only for YUV input.

        ``QFormLayout`` hides only the field, never the row label, and a hidden
        row still reserves layout height; take the rows out of the layout when
        they do not apply, exactly like the Convert page's
        ``_apply_cond_rows()``.  Image mode takes its sizes from the Size
        checkbox panel, so the spin boxes there did nothing.
        """
        rows = (
            (self._width_label, self.width),
            (self._height_label, self.height),
        )
        if visible:
            for index, (label, field) in enumerate(rows):
                if self._dims_form.getWidgetPosition(field)[0] < 0:
                    self._dims_form.insertRow(index, label, field)
                label.setVisible(True)
                field.setVisible(True)
                field.setEnabled(True)
            return
        for label, field in rows:
            row = self._dims_form.getWidgetPosition(field)[0]
            if row >= 0:
                taken = self._dims_form.takeRow(row)
                taken_label = (
                    taken.labelItem.widget()
                    if taken.labelItem is not None else None
                )
                if taken_label is not None:
                    taken_label.setVisible(False)
            label.setVisible(False)
            field.setVisible(False)
            field.setEnabled(False)

    def _sync_output_dir_controls(self) -> None:
        """Keep the destination visible/explained whatever the checkbox says."""
        same_dir = self._same_dir_cb.isChecked()
        self._output_dir_widget.setVisible(not same_dir)
        self._output_dir_browse_btn.setVisible(not same_dir)
        if same_dir:
            self._output_dir_hint.setText(
                "Output folder: next to the input file (the input's own folder)."
            )
        else:
            folder = (
                self.output_dir_edit.text().strip()
                or self._settings.default_output_dirname
            )
            self._output_dir_hint.setText(
                f"Output folder: {folder} "
                "(a relative name is created next to the input file)."
            )
        self._output_dir_hint.setVisible(True)

    def _output_dir_for(self, input_path: str) -> str:
        """Folder for this operation, via the shared resolver.

        Reuses :func:`raw_view.models.resolve_output_dir` so Variant uses the
        same ``os.path.abspath`` semantics as Convert and Batch instead of
        ``Path.resolve()`` (which expands Windows 8.3 short names and follows
        symbolic links; 0.5.11.2 review L-2).
        """
        folder = (
            self.output_dir_edit.text().strip()
            or self._settings.default_output_dirname
        )
        return resolve_output_dir(
            self._same_dir_cb.isChecked(), input_path, folder
        )

    # ── Collision prompt ─────────────────────────────────────────────

    def _confirm_output_collision(
        self,
        output_path: str,
        *,
        allow_sticky: bool = True,
        default: str = "rename",
    ) -> str | None:
        """Ask Skip / Rename (_1) / Overwrite for an existing target.

        Variant generation produces several files in one operation, so the
        prompt offers the "Do this for all remaining files" checkbox by
        default.  A ticked choice is remembered only until :meth:`generate`
        returns; it never leaks into the next operation.
        """
        if not os.path.exists(output_path):
            return output_path
        choice = self._operation_collision_choice
        if choice is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Output file exists")
            box.setText(
                f"Output already exists:\n{output_path}\n\n"
                "How do you want to continue?"
            )
            skip_b = box.addButton("Skip", QMessageBox.RejectRole)
            rename_b = box.addButton("Rename (_1)", QMessageBox.ActionRole)
            ovw_b = box.addButton("Overwrite", QMessageBox.AcceptRole)
            box.setDefaultButton(
                ovw_b if default == "overwrite" else rename_b
            )
            sticky = None
            if allow_sticky:
                sticky = QCheckBox("Do this for all remaining files", box)
                box.setCheckBox(sticky)
            box.exec_()
            clicked = box.clickedButton()
            if clicked is skip_b:
                choice = "skip"
            elif clicked is ovw_b:
                choice = "overwrite"
            else:
                choice = "rename"
            if sticky is not None and sticky.isChecked():
                self._operation_collision_choice = choice
        if choice == "skip":
            return None
        if choice == "rename":
            return resolve_output_path_collision(output_path, on_existing="rename")
        return output_path

    # ── Generation ───────────────────────────────────────────────────

    def generate(self) -> None:
        """Run the generator for the current input type and selection."""
        if self._generating:  # defence in depth: button is disabled too
            return
        input_path = self.input_edit.text().strip()
        if not input_path or not Path(input_path).is_file():
            QMessageBox.warning(
                self, "Generate Variants", "Please choose a valid input file."
            )
            return
        self._operation_collision_choice = None
        self._rewritten_outputs = {}
        self._generating = True
        self.generate_button.setEnabled(False)
        self.generate_button.setText("Generating…")
        QApplication.processEvents()
        written: list[str] = []
        planned = 0
        try:
            if self._is_yuv_input():
                result = self._generate_yuv(input_path)
            else:
                result = self._generate_image(input_path)
            if result is None:
                # Validation already showed exactly one message ("select at
                # least one format/size/target"); do not append a second,
                # contradictory "nothing was written because everything was
                # skipped" information box (0.5.11.1 review L-3).
                return
            written, planned = result
        except Exception as exc:  # pylint: disable=broad-except
            QMessageBox.critical(
                self, "Generate Variants Failed", f"❌ {exc}"
            )
            return
        finally:
            self._generating = False
            self.generate_button.setText("Generate Variants")
            # The sticky choice is scoped to this operation only.
            self._operation_collision_choice = None
            self._rewritten_outputs = {}
            self._sync_generate_button()
        if written:
            folder = str(Path(written[0]).parent)
            message = (
                f"✅ Generated {len(written)} file(s) from {planned} "
                f"planned variant(s).\nOutput folder:\n{folder}"
            )
        else:
            message = (
                "No files were written "
                "(every planned output already existed and was skipped)."
            )
        QMessageBox.information(self, "Generate Variants", message)

    def _map_output(self, out: str) -> str | None:
        """Resolve a planned path, asking once per collision (sticky-aware)."""
        if out in self._rewritten_outputs:
            return self._rewritten_outputs[out]
        resolved = self._confirm_output_collision(out, allow_sticky=True)
        if resolved is None:
            return None
        if resolved != out:
            self._rewritten_outputs[out] = resolved
        return resolved

    def _generate_image(
        self, input_path: str
    ) -> tuple[list[str], int] | None:
        """Generate image variants; ``None`` means validation already warned."""
        formats = self.image_selector.selected_formats()
        sizes = self.image_selector.selected_sizes()
        bayer = self.image_selector.selected_bayer()
        if not formats:
            QMessageBox.warning(
                self, "Generate Variants", "Select at least one format."
            )
            return None
        if not sizes:
            QMessageBox.warning(
                self, "Generate Variants", "Select at least one size."
            )
            return None
        source_mode = self.raw_source_mode.currentText()
        alignment = self.align.currentText()
        endianness = self.endian.currentText()
        out_dir = self._output_dir_for(input_path)
        plans = plan_image_variants(
            input_path,
            formats,
            sizes,
            bayer,
            source_mode=source_mode,
            alignment=alignment,
            endianness=endianness,
            output_dir=out_dir,
            template=self._settings.output_template,
        )
        self.generate_button.setText(f"Generating 0/{len(plans)}…")
        QApplication.processEvents()
        done = {"n": 0}

        def _progress(_path: str) -> None:
            done["n"] += 1
            self.generate_button.setText(
                f"Generating {done['n']}/{len(plans)}…"
            )
            QApplication.processEvents()

        written = generate_image_variants(
            input_path,
            formats,
            sizes,
            bayer,
            source_mode=source_mode,
            alignment=alignment,
            endianness=endianness,
            output_dir=out_dir,
            template=self._settings.output_template,
            on_output=_progress,
            output_paths=self._map_output,
        )
        return written, len(plans)

    def _generate_yuv(
        self, input_path: str
    ) -> tuple[list[str], int] | None:
        """Generate YUV variants; ``None`` means validation already warned."""
        source = self.input_yuv_type.currentText()
        targets = self.yuv_selector.selected_formats()
        if not targets:
            QMessageBox.warning(
                self, "Generate Variants", "Select at least one target format."
            )
            return None
        width, height = self.width.value(), self.height.value()
        offset = self.offset.value()
        # Validate every target before writing anything: a wrong frame size or
        # an over-limit frame must not leave a partial set behind.
        specs: list[tuple[str, TranscodeSpec]] = []
        try:
            total = os.path.getsize(input_path)
            for target in targets:
                spec = TranscodeSpec(
                    source_format=source,
                    target_format=target,
                    width=width,
                    height=height,
                    offset=offset,
                )
                require_decode_size(width, height, frame_size(spec))
                frame_count(total, spec)
                specs.append((target, spec))
        except (TranscodeError, OSError, ValueError) as exc:
            from raw_view.gui.dialogs.convert import yuv_size_mismatch_hint

            hint = yuv_size_mismatch_hint(
                input_path, source, width, height, offset
            )
            detail = f"{exc}\n\n{hint}" if hint else str(exc)
            QMessageBox.critical(
                self, "Generate Variants Failed", f"❌ {detail}"
            )
            # Same contract as _generate_image(): None means "validation has
            # already shown exactly one message", so generate() must not append
            # the contradictory "nothing was written because everything was
            # skipped" information box (0.5.11.2 review M-1).
            return None

        out_dir = self._output_dir_for(input_path)
        written: list[str] = []
        failures: list[str] = []
        for index, (target, spec) in enumerate(specs, start=1):
            self.generate_button.setText(
                f"Generating {index - 1}/{len(specs)}…"
            )
            QApplication.processEvents()
            planned = format_output_template(
                self._settings.output_template,
                input_path,
                width,
                height,
                "YUV",
                output_dir=out_dir,
                yuv_type=target,
                source_yuv_type=source,
            )
            resolved = self._confirm_output_collision(
                planned, allow_sticky=True
            )
            if resolved is None:
                continue
            try:
                Path(resolved).parent.mkdir(parents=True, exist_ok=True)
                transcode_file(input_path, resolved, spec)
            except OSError as exc:
                from raw_view.gui.dialogs.convert import (
                    _is_file_in_use_error,
                    _is_permission_denied_error,
                )

                if _is_file_in_use_error(exc):
                    why = "the file is open in another program"
                elif _is_permission_denied_error(exc):
                    why = "permission denied"
                else:
                    why = str(exc)
                failures.append(f"{resolved}: {why}")
                continue
            written.append(resolved)
            self.generate_button.setText(
                f"Generating {index}/{len(specs)}…"
            )
            QApplication.processEvents()
        if failures:
            QMessageBox.critical(
                self,
                "Generate Variants",
                "❌ Some variants could not be written:\n\n"
                + "\n".join(failures),
            )
        return written, len(specs)
