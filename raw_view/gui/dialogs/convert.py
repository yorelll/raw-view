"""File-to-RAW/YUV conversion dialog.

The input may be an image (PNG/JPG/BMP, decoded and re-encoded) or an existing
YUV file (lossless byte rearrangement inside one chroma-sampling family).
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QBrush, QColor, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from raw_view.converter import (
    image_file_to_raw,
    image_file_to_yuv,
    load_bgr_image,
    resolve_output_path_collision,
)
from raw_view.formats import (
    expected_frame_size_raw,
    expected_frame_size_yuv,
    require_decode_size,
)
from raw_view.models import (
    AppSettings,
    BAYER_PATTERNS,
    THEME_PALETTES,
    format_output_template,
    normalize_ui_theme,
    resolve_output_dir,
)
from raw_view.gui.dialogs.variant_convert import VariantConvertPage
from raw_view.gui.image_utils import _format_size, qimage_from_rgb
from raw_view.gui.panels import ControlPanel
from raw_view.gui.widgets import FileDropLineEdit
from raw_view.transcode import YUV_TRANSCODE_FORMATS


# 与本项目主面板 (panels.py) 的默认输出分辨率保持一致（转换默认产出高清，
# 与查看端默认对齐）。仅影响对话框默认值，不改动既有 CLI 默认。
DEFAULT_CONVERT_WIDTH = 2560
DEFAULT_CONVERT_HEIGHT = 1440

#: Browse filter for the input file.  Images are listed first, but "All Files"
#: must stay: a YUV source may carry any suffix (``.nv16``, ``.yuyv``, ``.bin``,
#: none at all), so hard-coding a YUV extension list would make it unpickable.
INPUT_FILE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp);;All Files (*.*)"


def _is_file_in_use_error(exc: BaseException) -> bool:
    """True only for OS errors caused by another program holding the target.

    Windows spells a sharing/lock violation ``WinError 32``/``33``; POSIX uses
    ``EBUSY``.  ``EACCES``/``EPERM``/``WinError 5`` are *permission* problems,
    not "somebody has the file open": reporting those as an open file sends the
    user off to close a program that is not running (0.5.11 review M-2).
    """
    if getattr(exc, "winerror", None) in (32, 33):
        return True
    return getattr(exc, "errno", None) == errno.EBUSY


def _is_permission_denied_error(exc: BaseException) -> bool:
    """True for "you are not allowed to read/write here" OS errors."""
    if _is_file_in_use_error(exc):
        return False
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM)


def yuv_size_mismatch_hint(
    input_path: str,
    source_format: str,
    width: int,
    height: int,
    offset: int = 0,
) -> str:
    """Actionable guidance for a YUV file whose size is not a whole frame.

    Width/Height are the *source frame* dimensions for a YUV input, but the
    default 2560x1440 makes almost every real file fail the "integer multiple of
    one frame" check — and the raw transcoder error never says that changing
    Width/Height is the fix (0.5.11 review M-1).  Return text that names the
    three actions (real frame size, header -> Source offset, Width/Height is not
    an output size) plus the file's byte count and one frame's byte count at the
    entered dimensions, so the right value can be worked out.  Empty string when
    the size already fits, so callers can append it unconditionally.
    """
    try:
        total = int(os.path.getsize(input_path))
        frame_bytes = int(expected_frame_size_yuv(source_format, width, height))
    except (OSError, ValueError, KeyError):
        return ""
    if frame_bytes <= 0:
        return ""
    payload = total - max(int(offset), 0)
    if payload > 0 and payload % frame_bytes == 0:
        return ""
    if offset:
        reference = (
            f"this file is {total} bytes; after the {int(offset)}-byte Source "
            f"offset, {payload} bytes remain"
        )
    else:
        reference = f"this file is {total} bytes"
    return (
        "Width/Height are the SOURCE frame size for a YUV input (they are not "
        "an output size). If this file is not "
        f"{width}x{height}, enter its real frame dimensions; if the file has a "
        "header, set Source offset. For reference: "
        f"{reference}; one {source_format} frame at {width}x{height} is "
        f"{frame_bytes} bytes."
    )


class ConvertDialog(QDialog):
    """Modal dialog for converting an image or YUV file to RAW/YUV."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Convert File")
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.input_edit = FileDropLineEdit()
        self.input_edit.setAccessibleName("Input file")
        self.input_edit.setAccessibleDescription(
            "Select an image (PNG/JPG/BMP) or an existing YUV file, or drag one here."
        )
        self.input_edit.setPlaceholderText(
            "Select an input file (image or YUV), or drag one here..."
        )
        self.output_edit = QLineEdit()
        self.output_edit.setAccessibleName("Output file")
        self.output_edit.setAccessibleDescription(
            "Path for the RAW or YUV output file."
        )
        self.output_edit.setPlaceholderText(
            "Output path — auto-filled from the input name if left blank"
        )

        # ── Input source ──────────────────────────────────────────────
        # "Image" = historical behaviour (decode PNG/JPG/BMP, resize to the
        # requested output size).  "YUV" = lossless byte rearrangement inside the
        # YUV 4:2:2 / 4:2:0 family: width/height become the SOURCE frame
        # dimensions and nothing is resized.  "RAW" is reserved (no conversion
        # implemented yet); the entry stays visible but is genuinely disabled so
        # it can never be selected and then fail.
        self.input_type = QComboBox()
        self.input_type.addItems(["Image", "YUV", "RAW (coming soon)"])
        self.input_type.setItemData(
            2,
            "RAW input conversion (packed RAW, bit alignment, bit depth) is "
            "reserved for a future release.",
            Qt.ToolTipRole,
        )
        raw_input_item = self.input_type.model().item(2)
        if raw_input_item is not None:
            raw_input_item.setEnabled(False)
        # setEnabled(False) blocks selection but qt-material does not paint the
        # item grey, so a reserved entry looked selectable (user report).  Set
        # the theme's disabled text colour explicitly and refresh it whenever
        # the palette/style changes.
        self._refresh_reserved_input_item()
        self.input_type.setAccessibleName("Input type")
        self.input_type.setAccessibleDescription(
            "Image: decode a PNG/JPG/BMP and encode it. "
            "YUV: losslessly rearrange an existing YUV 4:2:2/4:2:0 file."
        )

        # Always-visible explanation of what the selected input type means, so
        # the user understands Image vs YUV *before* choosing (instead of being
        # told after the fact that a combination does not work).
        self._input_hint = QLabel()
        self._input_hint.setObjectName("hintLabel")
        self._input_hint.setWordWrap(True)

        # Source YUV list: only formats the transcoder can actually read.  YOnly
        # is deliberately excluded (4:0:0 has no chroma planes, so it cannot be
        # losslessly rearranged into 4:2:2/4:2:0); listing it as a source would
        # leave every target greyed out — a dead end.
        self.input_yuv_type = QComboBox()
        self.input_yuv_type.addItems(YUV_TRANSCODE_FORMATS)

        self.target_type = QComboBox()
        self.target_type.addItems(["RAW", "YUV"])

        self.raw_type = QComboBox()
        # RAW 类型列表与主面板 ControlPanel.RAW_FORMATS 保持一致（含 RAW16/RAW32）。
        self.raw_type.addItems(ControlPanel.RAW_FORMATS)

        self.yuv_type = QComboBox()
        # YUV 类型列表与主面板 ControlPanel.YUV_FORMATS 保持一致
        # （YOnly 是一个独立格式 + Bit depth 下拉，见 bit_depth_combo）。
        self.yuv_type.addItems(ControlPanel.YUV_FORMATS)

        # YOnly 位深（仅 Format=YOnly 时显示/可用），内部映射 YOnly<bit>。
        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.addItems(ControlPanel.YONLY_BIT_DEPTHS)
        self.bit_depth_combo.setCurrentText("12")

        self.align = QComboBox()
        self.align.addItems(["msb", "lsb"])

        # Endianness：RAW 与 YOnly 多 bit（16-bit 存储）可选用大小端；
        # 普通 YUV 无意义（_sync_controls 禁用）。
        self.endian = QComboBox()
        self.endian.addItems(["little", "big"])

        self.raw_source_mode = QComboBox()
        self.raw_source_mode.addItems(["bayer", "gray"])

        self.bayer_pattern = QComboBox()
        self.bayer_pattern.addItems(BAYER_PATTERNS)

        self.width = QSpinBox()
        self.width.setRange(1, 65535)
        self.width.setValue(DEFAULT_CONVERT_WIDTH)

        # Collision choice made with "Do this for all remaining files".
        # Scoped to one operation and reset at the start of every Convert /
        # Generate Variants run; it must never leak into the next operation.
        self._operation_collision_choice: str | None = None

        self.height = QSpinBox()
        self.height.setRange(1, 65535)
        self.height.setValue(DEFAULT_CONVERT_HEIGHT)

        # Bytes to skip before the first YUV frame (file header, padding, ...).
        # Mirrors the CLI's --offset; only meaningful for a YUV input, so it is
        # hidden and disabled for images (see _sync_controls_locked).
        self.offset = QSpinBox()
        self.offset.setRange(0, 2**31 - 1)
        self.offset.setValue(0)
        self.offset.setToolTip(
            "Bytes to skip before the first frame (file header / padding). "
            "YUV input only."
        )

        # Auto output path currently in the field, and the input path it was
        # rendered for.  The pair is what distinguishes "same input file, user
        # typed a custom name" (keep it) from "another input file" (reset to
        # the new automatic path) and lets a value typed back to the automatic
        # name resume following parameter changes (N-1).
        self._auto_output_path = ""
        self._auto_input = ""

        # ── Preview area ─────────────────────────────────────────────
        preview_group = QFrame()
        preview_group.setObjectName("card")
        preview_group.setFrameShape(QFrame.StyledPanel)
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)

        preview_title = QLabel("Source preview")
        preview_title.setAccessibleName("Source preview")
        preview_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        preview_title.setAlignment(Qt.AlignLeft)

        preview_content = QHBoxLayout()
        self._preview_thumb = QLabel("Drop a file\nor click Browse")
        self._preview_thumb.setObjectName("previewThumb")
        self._preview_thumb.setAccessibleName("Source preview")
        self._preview_thumb.setAccessibleDescription(
            "Thumbnail of the source image; it does not represent decoded output bytes."
        )
        self._preview_thumb.setFixedSize(160, 120)
        self._preview_thumb.setAlignment(Qt.AlignCenter)

        self._preview_info = QLabel(
            "Source: -\n"
            "Output specification: -\n"
            "Frame size: -"
        )
        self._preview_info.setAccessibleName("Output specification")
        self._preview_info.setAccessibleDescription(
            "Target dimensions and estimated encoded frame size."
        )
        self._preview_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # Long YUV errors carry actionable guidance; without wrapping the label
        # grew to ~3145 px and pushed the useful part out of view (0.5.11
        # review M-1).  Wrapping keeps the dialog at its normal width.
        self._preview_info.setWordWrap(True)

        preview_content.addWidget(self._preview_thumb)
        preview_content.addWidget(self._preview_info, 1)

        preview_layout.addWidget(preview_title)
        preview_layout.addLayout(preview_content)

        # ── Browse buttons live inline next to their fields ───────────
        # Match the field height so the inline buttons align cleanly.
        field_h = self.input_edit.sizeHint().height()
        btn_in = QPushButton("Browse...")
        btn_in.setObjectName("secondaryButton")
        btn_in.setFixedHeight(field_h)
        btn_out = QPushButton("Browse...")
        btn_out.setObjectName("secondaryButton")
        btn_out.setFixedHeight(field_h)
        btn_in.clicked.connect(self._browse_input)
        btn_out.clicked.connect(self._browse_output)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(btn_in, 0)
        input_row_w = QWidget()
        input_row_w.setLayout(input_row)

        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(6)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(btn_out, 0)
        output_row_w = QWidget()
        output_row_w.setLayout(output_row)

        # ── Layout construction ───────────────────────────────────────
        form = QFormLayout()
        # Kept for _apply_cond_rows(): hidden conditional rows are taken out of
        # the layout (setVisible alone still reserves a 6px row gap).
        self._form = form
        form.addRow("Input file", input_row_w)
        form.addRow("Input type", self.input_type)
        form.addRow("", self._input_hint)
        form.addRow("Input YUV format", self.input_yuv_type)
        form.addRow("Source offset (bytes)", self.offset)
        form.addRow("Output file", output_row_w)
        form.addRow("Target", self.target_type)
        form.addRow("RAW type", self.raw_type)
        form.addRow("YUV format", self.yuv_type)
        # YOnly 位深（YOnly 时显示）；其后为位对齐/大小端（YOnly 多 bit 需要）
        form.addRow("Bit depth", self.bit_depth_combo)
        form.addRow("Alignment", self.align)
        form.addRow("Endianness", self.endian)
        form.addRow("RAW source", self.raw_source_mode)
        form.addRow("Bayer pattern", self.bayer_pattern)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)

        # QFormLayout hides only the field, never the row label.  Cache every
        # conditional row's label so _apply_cond_rows() can move both together;
        # otherwise a hidden field leaves its label dangling with no control
        # next to it (0.5.10 review H-1).  Collected after all addRow() calls:
        # labelForField() only answers for a field already in the form.
        self._cond_row_labels = {
            field: form.labelForField(field)
            for field in (
                self.input_yuv_type,
                self.offset,
                self.raw_type,
                self.bit_depth_combo,
                self.align,
                self.endian,
                self.raw_source_mode,
                self.bayer_pattern,
            )
        }
        self._cond_row_labels = {
            field: label
            for field, label in self._cond_row_labels.items()
            if label is not None
        }
        # Named handle kept for readability/tests; visibility is driven through
        # _apply_cond_rows() so the label can never drift from its field.
        self._offset_label = self._cond_row_labels.get(self.offset)

        # Conditional rows grouped by the fixed row that follows them, so a
        # visible row is inserted back at its original position (0.5.10.1
        # review L-1).  ``None`` means "append after everything else".
        self._cond_row_groups = (
            (
                output_row_w, (
                    (self.input_yuv_type, "Input YUV format"),
                    (self.offset, "Source offset (bytes)"),
                ),
            ),
            (
                self.yuv_type, ((self.raw_type, "RAW type"),),
            ),
            (
                self.width, (
                    (self.bit_depth_combo, "Bit depth"),
                    (self.align, "Alignment"),
                    (self.endian, "Endianness"),
                    (self.raw_source_mode, "RAW source"),
                    (self.bayer_pattern, "Bayer pattern"),
                ),
            ),
        )

        # CONVERT is the primary action; disabled until an input is chosen.
        btn_run = QPushButton("Convert")
        btn_run.setObjectName("accentButton")
        btn_run.clicked.connect(self._convert)
        btn_run.setEnabled(bool(self.input_edit.text().strip()))
        self._btn_run = btn_run
        self.input_edit.textChanged.connect(
            lambda t: btn_run.setEnabled(bool(t.strip()))
        )

        # ── Convert page (single-file workflow only) ─────────────────
        # Wrap the form + preview in a scroll area so the dialog never grows
        # past the screen. The action button lives outside the scroll area and
        # stays pinned at the bottom of this page.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addLayout(form)
        content_layout.addWidget(preview_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)

        self._convert_page = QWidget()
        convert_page_layout = QVBoxLayout(self._convert_page)
        convert_page_layout.setContentsMargins(0, 0, 0, 0)
        convert_page_layout.addWidget(scroll, 1)
        convert_row = QHBoxLayout()
        convert_row.addStretch(1)
        convert_row.addWidget(btn_run)
        convert_page_layout.addLayout(convert_row)

        # ── Variant page (independent UI) ────────────────────────────
        # Variant generation is a separate workflow with its own input,
        # destination and action button; it is NOT appended to the Convert
        # form any more (user report: the two workflows were mixed together).
        self._variant_page = VariantConvertPage(settings, self)
        self._variant_page.generate_button.setObjectName("secondaryButton")

        # ── Mode selector (top of the dialog) ────────────────────────
        # Default is Convert.  "Variant Convert" is disabled (and painted grey)
        # while the feature is off, with the reason in the label itself, so the
        # user does not have to guess why it cannot be selected.
        self._VARIANT_MODE_INDEX = 1
        self.mode_selector = QComboBox()
        self.mode_selector.addItems(["Convert", "Variant Convert"])
        self.mode_selector.setAccessibleName("Conversion mode")
        # The disabled entry's text ("Variant Convert (enable in Settings)")
        # is wider than the dialog; a QComboBox reports that text as its
        # minimum size, which made QFormLayout collapse the Mode label column
        # instead of aligning the combo with the form fields.  Let the combo
        # shrink to the field column (its tooltip keeps the full reason).
        self.mode_selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.mode_selector.setCurrentIndex(0)
        self._sync_mode_item()
        self.mode_selector.currentIndexChanged.connect(self._on_mode_changed)

        # The Mode selector stays outside the page stack (it must remain
        # visible on both pages), but it has to line up with the Convert form:
        # a bare QHBoxLayout put the combo at x=74 while every form field
        # starts at x=238 (user report: "Mode 与 Input file 没有对齐").
        # A fixed-width "Mode" label reproduces the page form's label column;
        # ``_sync_mode_row_alignment()`` keeps that width equal to the form's
        # live label column (it grows/shrinks when conditional rows appear),
        # so both the label and the combo land in the same columns.
        self._mode_label = QLabel("Mode")
        self._mode_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._mode_form = QFormLayout()
        self._mode_form.addRow(self._mode_label, self.mode_selector)

        self._stack = QStackedWidget()
        self._stack.setAccessibleName("Conversion pages")
        self._stack.addWidget(self._convert_page)
        self._stack.addWidget(self._variant_page)
        self._stack.setCurrentIndex(0)
        if self._settings.multi_variant_enabled:
            self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.addLayout(self._mode_form)
        layout.addWidget(self._stack, 1)

        # Cap the dialog height to the available screen so the buttons never
        # get pushed off-screen, then size to the *current* page within that
        # cap.  QStackedWidget.sizeHint() is the maximum over all pages, so it
        # would let the tall Variant page stretch the Convert page.
        # NB: self.width / self.height are QSpinBox attributes on this dialog,
        # so QWidget.width()/height() are shadowed — use sizeHint() instead.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            max_h = int(screen.availableGeometry().height() * 0.9)
            self.setMaximumHeight(max_h)
            hint = self._stack.currentWidget().sizeHint()
            self.resize(hint.width(), min(hint.height(), max_h))

        # Signals
        self.input_edit.fileDropped.connect(self._sync_default_output)
        self.input_edit.fileDropped.connect(self._update_preview)
        self.input_edit.textChanged.connect(self._sync_default_output)
        self.input_edit.textChanged.connect(self._update_preview)
        self.target_type.currentTextChanged.connect(self._sync_controls)
        self.raw_source_mode.currentTextChanged.connect(self._sync_controls)
        self.input_type.currentTextChanged.connect(self._on_input_type_changed)
        self.input_yuv_type.currentTextChanged.connect(self._sync_controls)
        self.input_yuv_type.currentTextChanged.connect(self._update_preview)
        # 切换 YUV 格式（如 YUYV→YOnly）也要重跑条件显隐：YOnly 需显示位深/对齐/端序
        self.yuv_type.currentTextChanged.connect(self._sync_controls)
        self.target_type.currentTextChanged.connect(self._sync_default_output)
        self.target_type.currentTextChanged.connect(self._update_preview)
        self.width.valueChanged.connect(self._sync_default_output)
        self.width.valueChanged.connect(self._update_preview)
        self.height.valueChanged.connect(self._sync_default_output)
        self.height.valueChanged.connect(self._update_preview)
        # The source offset changes which bytes count as frame data.
        self.offset.valueChanged.connect(self._update_preview)
        self.raw_type.currentTextChanged.connect(self._update_preview)
        self.yuv_type.currentTextChanged.connect(self._update_preview)
        # YOnly 位深变化直接影响 YOnly<bit> 帧大小 → 预览帧大小需刷新
        self.bit_depth_combo.currentTextChanged.connect(self._update_preview)
        self.align.currentTextChanged.connect(self._update_preview)
        self.raw_source_mode.currentTextChanged.connect(self._update_preview)
        self.bayer_pattern.currentTextChanged.connect(self._update_preview)
        # Format-aware placeholders ({format}/{bayer}/{bits}/{packed}/...)
        # depend on these fields, so refresh the auto output path whenever
        # any of them changes.
        self.raw_type.currentTextChanged.connect(self._sync_default_output)
        self.yuv_type.currentTextChanged.connect(self._sync_default_output)
        # YOnly 位深决定 {format} 中的 YOnly<bit> 与 {bits} 占位 → 输出路径需刷新
        self.bit_depth_combo.currentTextChanged.connect(self._sync_default_output)
        self.align.currentTextChanged.connect(self._sync_default_output)
        self.raw_source_mode.currentTextChanged.connect(self._sync_default_output)
        self.bayer_pattern.currentTextChanged.connect(self._sync_default_output)
        self.output_edit.textEdited.connect(self._on_output_edited)

        self._sync_controls()

    # ── internal slots ───────────────────────────────────────────────

    def _on_input_type_changed(self) -> None:
        """Switching the input type resets the dimensions and re-syncs controls.

        Width/height mean different things per input type (output size for an
        image, source frame size for YUV), so carrying the *previous* value
        across would apply it to the wrong notion.  Both modes start from the
        documented 2560x1440 guess; for a YUV input it is only an initial
        value, and a file whose size does not match is reported when Convert
        starts rather than silently producing garbage (user request).
        """
        # A source offset belongs to the previous input kind; start clean.
        self.offset.setValue(0)
        if self._is_yuv_input():
            # Defensive: keep a source that actually has convertible targets
            # (the list is already YUV_TRANSCODE_FORMATS, so this is a no-op
            # unless the selection somehow drifted).
            if self._input_format() not in YUV_TRANSCODE_FORMATS:
                self.input_yuv_type.setCurrentText(YUV_TRANSCODE_FORMATS[0])
        self.width.setValue(DEFAULT_CONVERT_WIDTH)
        self.height.setValue(DEFAULT_CONVERT_HEIGHT)
        self._sync_controls()
        self._sync_default_output()
        self._update_preview()

    def _browse_input(self) -> None:
        # "All Files" is required: a YUV source can carry any (or no) suffix.
        path, _ = QFileDialog.getOpenFileName(
            self, "Input File", "", INPUT_FILE_FILTER
        )
        if path:
            self.input_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Output", self.output_edit.text(), "All Files (*.*)"
        )
        if path:
            self.output_edit.setText(path)

    def _sync_controls(self) -> None:
        # QComboBox.setItemData() re-emits currentTextChanged in this dialog
        # (PyQt5 5.15), which would call straight back into _sync_controls and
        # recurse without bound.  The guard makes the slot idempotent under
        # re-entry instead of relying on the widget's signal behaviour.
        if getattr(self, "_syncing_controls", False):
            return
        self._syncing_controls = True
        try:
            self._sync_controls_locked()
        finally:
            self._syncing_controls = False

    def _sync_controls_locked(self) -> None:
        is_yuv_input = self.input_type.currentText() == "YUV"
        # A YUV input is transcoded into another YUV layout only (RAW is a
        # different family), so pin the target FIRST and only then read is_raw.
        # Reading is_raw before the pin used the pre-switch value (0.5.10 review
        # M-1): setCurrentText() below re-enters _sync_controls, which the guard
        # swallows, so a stale is_raw=True kept Alignment/Endianness visible and
        # enabled for a YUV input even though transcoding ignores them.
        self.target_type.setEnabled(not is_yuv_input)
        if is_yuv_input and self.target_type.currentText() != "YUV":
            self.target_type.setCurrentText("YUV")
        is_raw = self.target_type.currentText() == "RAW"
        is_bayer = self.raw_source_mode.currentText() == "bayer"
        # YOnly 是一个独立格式 + Bit depth 下拉：选它时显示位深/对齐/大小端
        is_yonly = self.yuv_type.currentText() == "YOnly"
        show_raw = is_raw and not is_yuv_input
        show_bit = (not is_raw) and is_yonly and not is_yuv_input
        show_align_endian = is_raw or show_bit
        # One call drives visibility, layout membership and enabled state for
        # every conditional row.  Hidden rows are taken out of the QFormLayout
        # instead of only being hidden: setVisible(False) still reserves a 6px
        # row gap (0.5.10.1 review L-1).  Enabled state is derived here rather
        # than from isVisible(), which is always False before show() (0.2.2).
        self._apply_cond_rows({
            self.input_yuv_type: is_yuv_input,
            self.offset: is_yuv_input,
            self.raw_type: show_raw,
            self.bit_depth_combo: show_bit,
            self.align: show_align_endian,
            self.endian: show_align_endian,
            self.raw_source_mode: show_raw,
            self.bayer_pattern: show_raw and is_bayer,
        })
        # The form's label column now has its final width for this state;
        # keep the dialog-level Mode row in the same columns.
        self._sync_mode_row_alignment()
        self.yuv_type.setEnabled(True if is_yuv_input else not is_raw)
        self._refresh_target_formats()
        # Width/height mean "source frame size" (parse only) for a YUV input and
        # "output size" (resize) for an image; say which in the tooltips.
        self._sync_dimension_hints()
        self._sync_input_hint()

    # ── YUV input helpers ─────────────────────────────────────────────

    def _apply_cond_rows(self, desired: dict[QWidget, bool]) -> None:
        """Move conditional rows in/out of the QFormLayout to match *desired*.

        ``QFormLayout`` keeps the row label visible when only the field is
        hidden (0.5.10 review H-1), and still reserves a 6px gap for a row
        whose label and field are both hidden (0.5.10.1 review L-1).  Hidden
        rows are therefore ``takeRow``-ed out of the form and visible rows are
        ``insertRow``-ed back at their canonical position (the fixed row that
        followed them in ``__init__``).

        Rows that stay visible are deliberately **not** touched: hiding them
        even for an instant closes an open QComboBox popup and moves the
        keyboard focus to the next widget in the chain; re-showing afterwards
        does not undo either (0.5.10.2 review M-1).  Only rows that must
        disappear are detached, and only rows that must appear *and are not in
        the form yet* are inserted.

        ``_cond_row_groups`` is the only place the row order is declared and
        ``_cond_row_labels`` the only place label widgets are cached, so a
        label can never drift from its field.
        """
        form = self._form
        # 1) Detach only the rows that must be hidden.  Recompute the row index
        # each time: takeRow() shifts the rows below it.
        for _anchor, rows in self._cond_row_groups:
            for field, _label_text in rows:
                if desired.get(field, False):
                    continue
                row = form.getWidgetPosition(field)[0]
                if row >= 0:
                    taken = form.takeRow(row)
                    label = (
                        taken.labelItem.widget()
                        if taken.labelItem is not None else None
                    )
                    if label is not None:
                        self._cond_row_labels[field] = label
                        label.setVisible(False)
                else:
                    # Already outside the form (previous sync) but still a
                    # child widget; keep it hidden until re-inserted.
                    label = self._cond_row_labels.get(field)
                    if label is not None:
                        label.setVisible(False)
                field.setVisible(False)
                field.setEnabled(False)

        # 2) Insert the rows that must show but are not in the form yet, group
        # by group, in declared order.  Start at the *earliest* row this group
        # still occupies: a row that becomes visible again has to be inserted
        # before the first surviving sibling declared after it, not merely
        # before the anchor (otherwise ``Bit depth`` would land after the
        # already-present ``Alignment``/``Endianness``).
        for anchor, rows in self._cond_row_groups:
            insert_at = (
                form.rowCount() if anchor is None
                else form.getWidgetPosition(anchor)[0]
            )
            if insert_at < 0:
                insert_at = form.rowCount()
            for field, _label_text in rows:
                position = form.getWidgetPosition(field)[0]
                if position >= 0 and desired.get(field, False):
                    insert_at = min(insert_at, position)
            for field, label_text in rows:
                if not desired.get(field, False):
                    continue
                position = form.getWidgetPosition(field)[0]
                if position < 0:
                    label = self._cond_row_labels.get(field)
                    if label is not None:
                        form.insertRow(insert_at, label, field)
                    else:
                        form.insertRow(insert_at, label_text, field)
                        label = form.labelForField(field)
                        if label is not None:
                            self._cond_row_labels[field] = label
                    if label is not None:
                        label.setVisible(True)
                    field.setVisible(True)
                    position = insert_at
                # Enabled state is derived here rather than from isVisible(),
                # which is always False before show() (0.2.2).
                field.setEnabled(True)
                insert_at = position + 1

    def _sync_mode_row_alignment(self) -> None:
        """Match the Mode row columns to the *visible* page's label column.

        ``QFormLayout`` sizes its label column from the widest *currently
        present* label, so hiding conditional rows (Source offset, Bayer
        pattern, ...) narrows it.  The Mode row lives above the stack and is
        not part of either page form, so copy the live width onto its label;
        the combo then starts exactly where every field of *that page* does.
        Without this the Mode combo sat at x=74 while ``Input file``'s field
        started at x=238 (user report).

        The anchor must follow the current page: copying the Convert form's
        width while the Variant page (a much narrower form: only Input file /
        Input type) was visible left the combo 19-47 px to the right of that
        page's own Input file field (0.5.11.3 review L-1).
        """
        label = getattr(self, "_mode_label", None)
        if label is None:
            return
        form = getattr(self, "_form", None)
        stack = getattr(self, "_stack", None)
        variant_page = getattr(self, "_variant_page", None)
        if stack is not None and variant_page is not None:
            if stack.currentWidget() is variant_page:
                form = getattr(variant_page, "_top_form", None)
        if form is None:
            return
        widest = 0
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.LabelRole)
            row_label = item.widget() if item is not None else None
            if row_label is not None:
                widest = max(widest, row_label.sizeHint().width())
        if widest > 0 and (
            label.minimumWidth() != widest or label.maximumWidth() != widest
        ):
            # The form's column can grow (YUV-only rows appear) and shrink
            # again (they are removed); pinning both edges forces a real
            # re-layout, whereas a minimum-width-only change left a stale
            # label column here.
            label.setFixedWidth(widest)
            label.updateGeometry()

    def _is_yuv_input(self) -> bool:
        return self.input_type.currentText() == "YUV"

    def _refresh_reserved_input_item(self) -> None:
        """Paint "RAW (coming soon)" with the current theme's disabled colour.

        The item stays disabled (flags unchanged), but qt-material renders an
        enabled-looking entry otherwise.  ``text_secondary`` is the same token
        the stylesheet uses for every disabled control.
        """
        combo = getattr(self, "input_type", None)
        settings = getattr(self, "_settings", None)
        if combo is None or settings is None:
            return
        item = combo.model().item(2)
        if item is None:
            return
        palette = THEME_PALETTES[normalize_ui_theme(settings.ui_theme)]
        item.setForeground(QBrush(QColor(palette["text_secondary"])))

    def _sync_mode_item(self) -> None:
        """Label/enable/grey the "Variant Convert" mode entry.

        When multi-variant generation is off the entry must be genuinely
        disabled *and* look disabled: ``setEnabled(False)`` alone leaves
        qt-material painting it like a normal entry (the same defect the RAW
        reserved item had).  ``text_secondary`` is the theme's disabled token.
        """
        combo = getattr(self, "mode_selector", None)
        if combo is None:
            return
        index = getattr(self, "_VARIANT_MODE_INDEX", 1)
        item = combo.model().item(index)
        if item is None:
            return
        enabled = bool(self._settings.multi_variant_enabled)
        blocked = combo.blockSignals(True)
        try:
            if enabled:
                item.setText("Variant Convert")
                item.setEnabled(True)
                # Drop the disabled paint so the enabled entry uses the
                # normal text colour again.
                item.setData(None, Qt.ForegroundRole)
                combo.setItemData(index, None, Qt.ToolTipRole)
            else:
                item.setText("Variant Convert (enable in Settings)")
                item.setEnabled(False)
                palette = THEME_PALETTES[
                    normalize_ui_theme(self._settings.ui_theme)
                ]
                item.setForeground(
                    QBrush(QColor(palette["text_secondary"]))
                )
                combo.setItemData(
                    index,
                    "Variant Convert is off. Enable 'multi-variant generation' "
                    "in Settings, then reopen this dialog.",
                    Qt.ToolTipRole,
                )
        finally:
            combo.blockSignals(blocked)
        # The disabled entry's text is wider than the combo (which is pinned to
        # the form field column); widen only the popup view so the reason is
        # readable in full (0.5.11.3 review L-2).
        self._sync_mode_popup_width()

    def _sync_mode_popup_width(self) -> None:
        """Widen the Mode popup's item view to fit the longest entry.

        ``QSizePolicy.Ignored`` keeps the combo itself in the form's field
        column (needed by ``_sync_mode_row_alignment``), but Qt makes the
        popup the same width as the combo; the disabled entry
        ``Variant Convert (enable in Settings)`` was then elided to
        ``Variant Convert (enable in S...`` (0.5.11.3 review L-2).  The view is
        a child of the popup, not of the form, so its minimum width widens the
        popup without moving the combo.

        Recomputed whenever the entry text changes (enable/disable) or the
        style/font changes (``_refresh_reserved_items``), so the width follows
        the UI font size.
        """
        combo = getattr(self, "mode_selector", None)
        if combo is None:
            return
        view = combo.view()
        if view is None:
            return
        combo.ensurePolished()
        view.ensurePolished()
        metrics = view.fontMetrics()
        widest = max(
            (
                metrics.horizontalAdvance(combo.itemText(index))
                for index in range(combo.count())
            ),
            default=0,
        )
        # The item view's frame costs ~12 px (measured: view 473 / viewport
        # 461); 24 px keeps the full text inside the viewport at every font
        # size with room for the item delegate's margins.
        wanted = widest + 24
        if view.minimumWidth() != wanted:
            view.setMinimumWidth(wanted)
            view.updateGeometry()

    def _on_mode_changed(self, index: int) -> None:
        """Switch the visible page and carry the chosen input across.

        The input file is a dialog-level concept, not a page-local one: a user
        who picked ``a.png`` (or a YUV file) on one page and then switches mode
        wants to work on the same file, not browse for it again (0.5.11.1
        review M-1).  Only an empty target field is filled, so switching back
        to a page the user already configured never overwrites their choice.
        """
        if (
            index == getattr(self, "_VARIANT_MODE_INDEX", 1)
            and not self._settings.multi_variant_enabled
        ):
            combo = self.mode_selector
            blocked = combo.blockSignals(True)
            try:
                combo.setCurrentIndex(0)
            finally:
                combo.blockSignals(blocked)
            index = 0
        previous = self._stack.currentWidget()
        self._stack.setCurrentIndex(index)
        # The anchor form changed with the page; realign before the next paint
        # so the Mode row matches the page the user is now looking at.
        self._sync_mode_row_alignment()
        self._carry_input_between_pages(previous, self._stack.currentWidget())

    def _carry_input_between_pages(self, source, target) -> None:
        """Copy the selected input (and its type) to an empty target page."""
        if source is None or source is target:
            return
        source_variant = source is self._variant_page
        target_variant = target is self._variant_page
        source_input = (
            self._variant_page.input_edit if source_variant else self.input_edit
        )
        target_input = (
            self._variant_page.input_edit if target_variant else self.input_edit
        )
        if target_input.text().strip():
            return
        path = source_input.text().strip()
        if not path:
            return

        source_type = (
            self._variant_page.input_type if source_variant else self.input_type
        )
        target_type = (
            self._variant_page.input_type if target_variant else self.input_type
        )
        yuv_input = source_type.currentText() == "YUV"
        target_input.setText(path)
        # Setting the input type re-runs the page's reset slot (which resets
        # Width/Height and Source offset), so copy those values *afterwards*.
        if target_type.currentText() != source_type.currentText():
            target_type.setCurrentText(source_type.currentText())
        if not yuv_input:
            return

        source_yuv = (
            self._variant_page.input_yuv_type
            if source_variant else self.input_yuv_type
        )
        target_yuv = (
            self._variant_page.input_yuv_type
            if target_variant else self.input_yuv_type
        )
        target_yuv.setCurrentText(source_yuv.currentText())
        source_page = self._variant_page if source_variant else self
        target_page = self._variant_page if target_variant else self
        target_page.width.setValue(source_page.width.value())
        target_page.height.setValue(source_page.height.value())
        target_page.offset.setValue(source_page.offset.value())

    def _refresh_reserved_items(self) -> None:
        """Repaint every theme-dependent disabled entry (mode + RAW inputs)."""
        self._refresh_reserved_input_item()
        self._sync_mode_item()
        page = getattr(self, "_variant_page", None)
        if page is not None:
            page.refresh_disabled_items()

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            refresh = getattr(self, "_refresh_reserved_items", None)
            if refresh is not None:
                refresh()

    def _input_format(self) -> str:
        """Source YUV sub-format (only meaningful when input type is YUV)."""
        return self.input_yuv_type.currentText()

    def _refresh_target_formats(self) -> None:
        """Grey out targets the current source format cannot convert into.

        Non-convertible entries stay in the list (so "this cannot convert" is
        visible) but are disabled, with :func:`reject_reason` as their tooltip.
        """
        from raw_view.transcode import reject_reason

        model = self.yuv_type.model()
        # Mutating item data/flags re-emits currentTextChanged -> _sync_controls;
        # block that while rewriting the list (the state written here is exactly
        # what the re-entrant call would recompute).
        blocked = self.yuv_type.blockSignals(True)
        try:
            if not self._is_yuv_input():
                for index in range(self.yuv_type.count()):
                    item = model.item(index)
                    if item is not None:
                        item.setEnabled(True)
                    self.yuv_type.setItemData(index, None, Qt.ToolTipRole)
                return
            source = self._input_format()
            for index in range(self.yuv_type.count()):
                name = self.yuv_type.itemText(index)
                reason = reject_reason(source, name)
                item = model.item(index)
                if item is not None:
                    item.setEnabled(reason is None)
                self.yuv_type.setItemData(index, reason, Qt.ToolTipRole)
        finally:
            self.yuv_type.blockSignals(blocked)
        # Never leave an unselectable entry selected: pick the first convertible
        # target instead (the list keeps greyed entries visible).
        if reject_reason(source, self.yuv_type.currentText()) is not None:
            for index in range(self.yuv_type.count()):
                if reject_reason(source, self.yuv_type.itemText(index)) is None:
                    self.yuv_type.setCurrentIndex(index)
                    break

    def _sync_input_hint(self) -> None:
        """Explain the selected input type before the user commits to it."""
        if self._is_yuv_input():
            self._input_hint.setText(
                "YUV input: lossless byte rearrangement inside the same "
                "4:2:2 / 4:2:0 family. Nothing is resized — Width/Height are "
                "the SOURCE frame size."
            )
        else:
            self._input_hint.setText(
                "Image input: decode PNG/JPG/BMP and encode to RAW/YUV. "
                "Width/Height are the OUTPUT size (the image is resized)."
            )

    def _sync_dimension_hints(self) -> None:
        """Label width/height as source-frame size for a YUV input (no resize)."""
        if self._is_yuv_input():
            self.width.setToolTip(
                "Source frame width: used to parse the YUV byte layout. "
                "The frame is not resized."
            )
            self.height.setToolTip(
                "Source frame height: used to parse the YUV byte layout. "
                "The frame is not resized."
            )
        else:
            self.width.setToolTip("Output width (the image is resized to it).")
            self.height.setToolTip("Output height (the image is resized to it).")

    def _resolve_yuv_fmt(self) -> str:
        """把 UI 的 YOnly + Bit depth 映射为内部有效格式名（YOnly8/10/12/14/16）。

        worker/converter 按 YOnly<bit> 工作；普通 YUV 原样返回。
        """
        return ControlPanel._yonly_internal_name(
            self.yuv_type.currentText(), self.bit_depth_combo.currentText()
        )

    def _default_output_path(self, input_path: str) -> str:
        """Render the Settings template for the current conversion.

        Convert, Batch and the CLI share one template system.  ``{format}``
        expands to the target token for image input and to
        ``"{source}_to_{target}"`` for a YUV -> YUV conversion, which keeps
        different Bayer patterns, sizes and layouts on distinct filenames
        (0.5.11 review L-3).
        """
        yuv_input = self._is_yuv_input()
        if yuv_input:
            target_type = "YUV"
            yuv_type = self.yuv_type.currentText()
            source_yuv_type = self._input_format()
        else:
            target_type = self.target_type.currentText()
            yuv_type = (
                self._resolve_yuv_fmt() if target_type == "YUV" else ""
            )
            source_yuv_type = ""
        # Convert has no "Same directory as input" switch any more: the default
        # output is always the complete path next to the input file, rendered
        # through the shared template.  The user can edit Output file directly
        # (a manual edit is protected from later parameter changes by
        # _auto_output_path / _on_output_edited).
        out_dir = resolve_output_dir(
            True,
            input_path,
            self._settings.default_output_dirname,
        )
        return format_output_template(
            self._settings.output_template,
            input_path,
            self.width.value(),
            self.height.value(),
            target_type,
            output_dir=out_dir,
            raw_type=self.raw_type.currentText() if target_type == "RAW" else "",
            yuv_type=yuv_type,
            bayer_pattern=self.bayer_pattern.currentText(),
            source_mode=self.raw_source_mode.currentText(),
            alignment=self.align.currentText(),
            endianness=self.endian.currentText(),
            source_yuv_type=source_yuv_type,
        )

    def _sync_default_output(self) -> None:
        """Keep Output file in step with the input and the current parameters.

        Rules (user request, N-1):

        * **A different input file always resets** the field to that file's
          automatic path, even if the user typed a custom name for the previous
          file — an old custom name must never be carried to a new input.
        * **Within one input file** a custom name is kept, but the decision is
          a live *value* comparison: while the text still equals the last
          automatic path, parameter changes keep updating it.  Typing a custom
          name and deleting it back to the automatic value therefore resumes
          following (the old one-shot flag made that permanent).
        * Path spelling is not identity: ``a.png`` and ``A.png`` (Windows) or a
          relative and an absolute spelling of the same file are the *same*
          input, so the custom name survives (0.5.11.4 L-1).
        * Clearing the field, or a half-typed path that is not a file yet, is
          not a file switch: ``_auto_input`` is left untouched, so pasting and
          retyping the same path character by character behave identically
          (0.5.11.4 L-2).
        """
        input_path = self.input_edit.text().strip()
        if not input_path:
            # Clearing is not a switch; keep _auto_input so retyping the same
            # file is still recognised as "same input".
            return
        path = self._default_output_path(input_path)
        if not path:
            return
        same_input = bool(self._auto_input) and self._same_input_file(
            input_path, self._auto_input
        )
        if not same_input and not os.path.isfile(input_path):
            # A half-typed path (``C``, ``C:``, ``C:\\…``) is not a new input
            # file yet.  Do not reset the custom name and do not overwrite
            # ``_auto_input``: otherwise each keystroke would look like a file
            # switch and retyping a path would lose the manual name, while
            # pasting it would keep it (0.5.11.4 L-2).
            return
        current = self.output_edit.text().strip()
        if same_input and current and current != self._auto_output_path:
            # Same file, user-supplied value: leave it alone.
            return
        self._auto_input = input_path
        self._auto_output_path = path
        self.output_edit.setText(path)

    @staticmethod
    def _same_input_file(first: str, second: str) -> bool:
        """Whether two path spellings refer to the same input file (L-1).

        ``normcase`` folds Windows drive/back-slash/case differences and
        ``abspath`` resolves relative spellings, so ``a.png`` / ``A.png`` and
        ``sub\\a.png`` / ``./sub/a.png`` compare equal.  The user's original
        spelling is still what gets rendered into the output path.
        """
        def key(path: str) -> str:
            return os.path.normcase(os.path.abspath(path))

        return key(first) == key(second)

    def _on_output_edited(self) -> None:
        """Manual edits need no marker bookkeeping any more (N-1).

        The follow/keep decision is the live comparison in
        :meth:`_sync_default_output` (``current == _auto_output_path``), so this
        slot deliberately does **not** clear the automatic path: clearing it
        used to make "edit the path, then delete it back to the auto value"
        permanently disable automatic updates.
        """

    # ── P1-6 覆盖确认 ───────────────────────────────────────────────────

    def _confirm_output_collision(
        self, output_path: str, *, allow_sticky: bool = False
    ) -> str | None:
        """目标已存在时询问用户：跳过 / 覆盖 / 自动重命名。

        返回最终写入路径；None 表示用户选择跳过，调用方应中止本次写出。
        单文件 Convert 一次只处理一个文件，“Do this for all remaining
        files”没有任何作用域，因此默认不显示该复选框（0.5.11 review L-2）；
        将来的多输出场景可传 ``allow_sticky=True`` 启用（勾选后本次操作剩余
        文件沿用同一选择，操作结束时清空，绝不泄漏到下一次操作）。
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
            keep_b = box.addButton("Rename (_1)", QMessageBox.ActionRole)
            ovw_b = box.addButton("Overwrite", QMessageBox.AcceptRole)
            box.setDefaultButton(keep_b)
            sticky = None
            if allow_sticky:
                sticky = QCheckBox("Do this for all remaining files", box)
                box.setCheckBox(sticky)
            box.exec_()
            clicked = box.clickedButton()
            if clicked is skip_b:
                choice = "skip"
            elif clicked is keep_b:
                choice = "rename"
            else:
                choice = "overwrite"
            if sticky is not None and sticky.isChecked():
                self._operation_collision_choice = choice
        if choice == "skip":
            return None
        if choice == "rename":
            return resolve_output_path_collision(output_path, on_existing="rename")
        return output_path

    def _set_convert_busy(self, busy: bool) -> None:
        """统一转换按钮的 busy 禁用/文案状态。"""
        self._btn_run.setEnabled(not busy and bool(self.input_edit.text().strip()))
        wanted = "Convert" if not busy else "Converting…"
        if self._btn_run.text() != wanted:
            self._btn_run.setText(wanted)

    def _update_preview(self) -> None:
        """Update the thumbnail preview and info labels."""
        import cv2

        input_path = self.input_edit.text().strip()
        if not input_path or not Path(input_path).is_file():
            self._preview_thumb.setText("Drop a file\nor click Browse")
            self._preview_info.setText("Source: -\nOutput specification: -\nFrame size: -")
            return

        if self._is_yuv_input():
            self._update_yuv_preview(input_path)
            return

        try:
            bgr = load_bgr_image(input_path)
            src_h, src_w = bgr.shape[:2]

            # Build thumbnail
            max_thumb_w, max_thumb_h = 158, 118
            scale = min(max_thumb_w / src_w, max_thumb_h / src_h, 1.0)
            thumb_w = max(1, int(src_w * scale))
            thumb_h = max(1, int(src_h * scale))
            if scale < 1.0:
                thumb = cv2.resize(bgr, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
            else:
                thumb = bgr
            rgb_thumb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
            qimg = qimage_from_rgb(rgb_thumb)
            self._preview_thumb.setPixmap(QPixmap.fromImage(qimg))

            # Frame size info
            target_type = self.target_type.currentText()
            out_w = self.width.value()
            out_h = self.height.value()
            if target_type == "RAW":
                fmt = self.raw_type.currentText()
                try:
                    fsize = expected_frame_size_raw(fmt, out_w, out_h)
                except Exception:
                    fsize = 0
            else:
                fmt = self._resolve_yuv_fmt()
                try:
                    # alignment/endianness 对 YOnly 多 bit 决定 16-bit 存储帧大小
                    fsize = expected_frame_size_yuv(
                        fmt, out_w, out_h,
                        alignment=self.align.currentText(),
                        endianness="little",  # 预览仅估算大小，端序不影响字节数
                    )
                except Exception:
                    fsize = 0

            source_info = f"Source: {Path(input_path).name} ({src_w}x{src_h})"
            output_info = f"Output specification: {out_w}x{out_h}"
            frame_info = f"Frame size: {_format_size(fsize)} ({fsize:,} B)" if fsize > 0 else "Frame size: -"
            self._preview_info.setText(f"{source_info}\n{output_info}\n{frame_info}")
        except Exception:
            self._preview_thumb.setText("(preview unavailable)")
            self._preview_info.setText("Source: -\nOutput specification: -\nFrame size: -")

    def _update_yuv_preview(self, input_path: str) -> None:
        """Preview for a YUV input: geometry, frame count, frame size.

        There is no decoded thumbnail (the input is raw YUV bytes, not an
        image), so the panel reports what the transcode will do instead.
        """
        from raw_view.transcode import (
            TranscodeError,
            TranscodeSpec,
            frame_count,
            frame_size,
        )

        source = self._input_format()
        target = self.yuv_type.currentText()
        self._preview_thumb.setText("YUV input\n(no preview)")
        w, h = self.width.value(), self.height.value()
        try:
            spec = TranscodeSpec(
                source, target, w, h, offset=self.offset.value()
            )
            fsize = frame_size(spec)
            frames = frame_count(Path(input_path).stat().st_size, spec)
        except (TranscodeError, OSError) as exc:
            hint = yuv_size_mismatch_hint(
                input_path, source, w, h, self.offset.value()
            )
            detail = f"{exc}\n\n{hint}" if hint else str(exc)
            self._preview_info.setText(
                f"Source: {Path(input_path).name} (YUV)\n"
                f"Source spec: {source} {w}x{h}\n"
                f"Frame size: -\n{detail}"
            )
            return
        self._preview_info.setText(
            f"Source: {Path(input_path).name} (YUV)\n"
            f"Source spec: {source} {w}x{h} (no resize)\n"
            f"Target spec: {target} {w}x{h}\n"
            f"Frame size: {_format_size(fsize)} ({fsize:,} B) x {frames} frame(s)"
        )

    def _convert(self) -> None:
        # A "do this for all remaining files" choice belongs to one operation
        # only; reset it before anything can ask about a collision.
        self._operation_collision_choice = None
        # Busy feedback: disable the button and show progress text so the user
        # knows work is happening (conversion is synchronous).
        self._set_convert_busy(True)
        QApplication.processEvents()
        try:
            self._do_convert()
        finally:
            self._set_convert_busy(False)

    def _report_output_error(
        self, exc: BaseException, output_path: str, *, in_use: bool = True
    ) -> None:
        """Report a failure to write the target with the right cause.

        "Open in another program" is reserved for a real sharing/lock
        violation; a read-only file or folder gets its own permission message
        (0.5.11 review M-2).
        """
        if in_use and _is_file_in_use_error(exc):
            # Windows refuses to replace a file another program has open.
            # Tell the user exactly what to close instead of surfacing a
            # bare "[WinError 32]" (user request).
            QMessageBox.warning(
                self,
                "Output file is in use",
                f"❌ Could not write the output file:\n{output_path}\n\n"
                "The file may be open in another program (for example an "
                "image or YUV viewer). Close the program that has the file "
                "open and try again.\n\n"
                f"Details: {exc}",
            )
        elif _is_permission_denied_error(exc):
            QMessageBox.critical(
                self,
                "Cannot write output",
                f"❌ Cannot write to:\n{output_path}\n\n"
                "Permission denied. Check that the file is not read-only and "
                "that its folder is writable, or choose another output "
                f"folder.\n\nDetails: {exc}",
            )
        else:
            QMessageBox.critical(self, "Convert Failed", f"❌ {exc}")

    def _do_convert(self) -> None:
        # Validation only: no side effects before the size check / overwrite
        # prompt, so an impossible request cannot leave an empty folder (N2).
        try:
            input_path = self.input_edit.text().strip()
            target_type = self.target_type.currentText()
            yuv_input = self._is_yuv_input()
            output_path = self.output_edit.text().strip()
            if not output_path:
                output_path = self._default_output_path(input_path)
            if not input_path:
                raise ValueError("input path is required")
            if not output_path:
                raise ValueError("output path is required")

            # 无副作用的校验必须排在建目录 / 覆盖询问之前：否则超限参数会先
            # 让用户回答"要覆盖吗"，再告诉他"帧太大"，并留下空目录（N2）。
            # 这里只构造 spec 并校验大小，真正的写盘在覆盖确认之后。
            spec = None
            if yuv_input:
                from raw_view.transcode import TranscodeSpec, frame_size

                spec = TranscodeSpec(
                    source_format=self._input_format(),
                    target_format=self.yuv_type.currentText(),
                    width=self.width.value(),
                    height=self.height.value(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                    offset=self.offset.value(),
                )
                # Same 512MB single-frame limit the CLI applies: a 16384x16385
                # NV16 frame used to land on disk through the GUI while the CLI
                # refused it.
                require_decode_size(spec.width, spec.height, frame_size(spec))
        except Exception as exc:
            QMessageBox.critical(self, "Convert Failed", f"❌ {exc}")
            return

        # The source is read inside the conversion call.  Check it first so a
        # missing/unreadable input is reported as an input problem, never as
        # "the output file may be open in another program" (0.5.11 review M-2).
        try:
            with open(input_path, "rb"):
                pass
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Cannot read input",
                f"❌ Cannot read the input file:\n{input_path}\n\n"
                "Check that the file exists and that you are allowed to read "
                f"it.\n\nDetails: {exc}",
            )
            return

        # Creating the output folder is not a file-sharing operation: a
        # read-only parent is a permission problem, so never classify it as
        # "in use" (0.5.11 review M-2).
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._report_output_error(exc, output_path, in_use=False)
            return

        # P1-6 覆盖确认：目标已存在时弹"跳过/覆盖/全部覆盖/重命名"。
        # 返回 None 表示用户选择跳过 → 直接结束本次转换。
        resolved = self._confirm_output_collision(output_path)
        if resolved is None:
            self._set_convert_busy(False)
            return
        output_path = resolved

        frame_count = None
        try:
            if yuv_input:
                from raw_view.transcode import transcode_file

                # ``spec`` was built and size-checked before mkdir/overwrite
                # prompt above; this is the actual conversion.
                frame_count, size = transcode_file(
                    input_path, output_path, spec
                )
            elif target_type == "RAW":
                image_file_to_raw(
                    input_path,
                    output_path,
                    self.raw_type.currentText(),
                    self.width.value(),
                    self.height.value(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                    source_mode=self.raw_source_mode.currentText(),
                    bayer_pattern=self.bayer_pattern.currentText(),
                )
            else:
                image_file_to_yuv(
                    input_path,
                    output_path,
                    self._resolve_yuv_fmt(),
                    self.width.value(),
                    self.height.value(),
                    alignment=self.align.currentText(),
                    endianness=self.endian.currentText(),
                )
        except Exception as exc:
            if _is_file_in_use_error(exc) or _is_permission_denied_error(exc):
                self._report_output_error(exc, output_path)
            else:
                detail = str(exc)
                if yuv_input:
                    # A size mismatch is almost always a wrong Width/Height,
                    # not a corrupt file; say how to fix it (0.5.11 M-1).
                    hint = yuv_size_mismatch_hint(
                        input_path, self._input_format(),
                        self.width.value(), self.height.value(),
                        self.offset.value(),
                    )
                    if hint:
                        detail = f"{detail}\n\n{hint}"
                QMessageBox.critical(self, "Convert Failed", f"\u274c {detail}")
            return

        self.output_edit.setText(output_path)
        try:
            size = Path(output_path).stat().st_size
            size_str = f" ({_format_size(size)})"
        except OSError:
            size_str = ""
        if frame_count is not None:
            size_str = f" ({frame_count} frame(s){size_str})"
        QMessageBox.information(
            self, "Convert",
            f"\u2705 Converted:\n{output_path}{size_str}",
        )
