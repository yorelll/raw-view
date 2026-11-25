"""Shared dialog form/action helpers (0.6.4 batch 4).

The four parameter dialogs (Convert / Variant / Batch / Settings / Preset)
used to build their own labels, spacing and action rows, which produced four
different densities (M-2) and five different action-bar layouts (M-3).  This
module is the single source for:

* ``_make_subcard()`` — one bordered form section with the shared density
  (label left-aligned, 6 px row spacing, 12/10 padding, 8 px paragraph gap).
* ``_make_subcard_frame()`` — the same section without assuming the content is
  a form (Batch's Output section, Variant's destination section).
* ``_dialog_footer()`` — one accent primary + one secondary Cancel/Close,
  right-aligned, with optional local (non-accent) actions on the left.
* ``_fit_dialog_height()`` — one dialog height policy: at most
  ``min(screen * 0.85, 720)`` and only as tall as the content needs.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


def _make_subcard_frame(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Build one bordered section and return its content layout.

    ``QFrame#subcard`` is the nested surface in the stylesheet (transparent
    fill + 1 px border, radius 8), so the card reads as a group without
    introducing a second background grey.
    """
    frame = QFrame()
    frame.setObjectName("subcard")
    # A styled QFrame otherwise paints its palette Base (input_bg) and ignores
    # the QSS background, which put every outlined button inside a card on the
    # wrong surface (T-4).
    frame.setAttribute(Qt.WA_StyledBackground, True)
    frame.setFrameShape(QFrame.StyledPanel)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    heading = QLabel(title)
    heading.setObjectName("groupTitle")
    layout.addWidget(heading)
    return frame, layout


def _make_subcard(title: str) -> tuple[QFrame, QFormLayout]:
    """Build one bordered form section (M-2 shared density).

    The Convert page used to be one 17-row flat form; splitting it into
    ``Input`` / ``Output`` / ``Format`` lets the eye find a parameter without
    reading every label.  Every dialog now uses the same label alignment and
    row spacing, so switching between Settings and Convert no longer makes the
    label column jump left/right or the rows double in height.
    """
    frame, layout = _make_subcard_frame(title)
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(6)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    layout.addLayout(form)
    return frame, form


def _footer_with(
    primary: QPushButton | None,
    cancel: QPushButton,
    local_actions: tuple[QWidget, ...] = (),
) -> QHBoxLayout:
    """One action row: local actions left, Cancel/Close + primary right.

    ``primary`` may be ``None`` for informational dialogs (Help / About /
    Keyboard Shortcuts), whose only action is the secondary Close.
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    for widget in local_actions:
        row.addWidget(widget)
    row.addStretch(1)
    row.addWidget(cancel)
    if primary is not None:
        row.addWidget(primary)
    return row


def _dialog_footer(
    primary_text: str,
    *,
    cancel_text: str = "Cancel",
    on_primary,
    on_cancel,
    local_actions: tuple[QWidget, ...] = (),
) -> QHBoxLayout:
    """Build the standard footer with exactly one accent primary button.

    ``on_primary`` / ``on_cancel`` are connected here; callers that already
    own a configured primary button use :func:`_footer_with` instead (Convert's
    action is enabled/disabled from its input field, for example).
    """
    primary = QPushButton(primary_text)
    primary.setObjectName("accentButton")
    primary.clicked.connect(on_primary)
    cancel = QPushButton(cancel_text)
    cancel.setObjectName("secondaryButton")
    cancel.clicked.connect(on_cancel)
    return _footer_with(primary, cancel, local_actions)


def _dialog_height_cap(dialog: QWidget) -> int:
    """Largest allowed dialog height: ``min(screen * 0.85, 720)``."""
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is None:
        return 720
    return min(int(screen.availableGeometry().height() * 0.85), 720)


def _page_content_height(page: QWidget) -> int:
    """Preferred height of *page*, including content inside its QScrollArea.

    ``QScrollArea.sizeHint()`` is a fixed viewport guess, so on its own it made
    tall forms open in a ~400 px window even though their contents need 800+.
    Prefer the real content height to the scroll area's own hint.
    """
    height = page.sizeHint().height()
    scroll = page.findChild(QScrollArea)
    if scroll is not None and scroll.widget() is not None:
        height = max(height, scroll.widget().sizeHint().height())
    # A QTableWidget's sizeHint is a small fixed guess; a dialog that exists to
    # show a table would otherwise open shorter than its own content.  Cap the
    # contribution (the screen cap trims it anyway) so 10k rows stay cheap.
    table = page.findChild(QTableWidget)
    if table is not None and table.rowCount() > 0:
        rows = min(table.rowCount(), 20)
        header = table.horizontalHeader().height()
        row_h = table.verticalHeader().defaultSectionSize()
        table_h = header + rows * row_h + 2 * table.frameWidth()
        height = max(height, table_h)
    return height


def _fit_dialog_height(dialog: QDialog, page: QWidget | None = None) -> int:
    """Apply the unified dialog height policy (L-3) and return the new height.

    The dialog grows to its content (including chrome outside *page*) but
    never past ``min(screen * 0.85, 720)``.  ``page`` is the page whose
    content should drive the height — for a stack it is the *current* page,
    not the maximum over every page.

    ``QDialog`` subclasses in this project sometimes shadow ``width()`` with a
    QSpinBox attribute, so the width is read from ``size()``.
    """
    cap = _dialog_height_cap(dialog)
    dialog.setMaximumHeight(cap)
    target = page if page is not None else dialog
    content = _page_content_height(target)
    try:
        target_hint = target.sizeHint().height()
        chrome = max(dialog.sizeHint().height() - target_hint, 0)
    except RuntimeError:  # deleted C++ object during teardown
        chrome = 0
    floor = dialog.minimumSizeHint().height()
    height = min(max(content + chrome, floor), cap)
    # Never shrink the width below what the layout needs (a forced width made
    # Settings' 340 px template field overflow its row and paint over the
    # Reset button).  ``layout().activate()`` makes the hints authoritative
    # before the first show.
    layout = dialog.layout()
    if layout is not None:
        layout.activate()
    width = max(
        dialog.size().width(),
        dialog.minimumSizeHint().width(),
        dialog.sizeHint().width(),
    )
    dialog.resize(width, height)
    dialog._dialog_height_cap = cap
    return height
