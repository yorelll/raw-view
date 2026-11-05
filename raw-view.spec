# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for raw-view — ONE Analysis, FOUR executables.

Both the local ``release.bat`` and the CI ``.github/workflows/build-release.yml``
build from this single file, so there is exactly one definition of the release
artifacts and one place to change it.

Why one spec instead of four command lines
------------------------------------------
The previous build ran PyInstaller four times (GUI, CLI, launcher, onefile).
Each run performed a *complete* module-graph analysis of the same dependency
tree: measured on this machine, 412 s + 394 s + 238 s + 270 s = **1315 s**.
Roughly 90% of that was analysis work repeated four times, because a new
process cannot reuse another process's Analysis.

Here the three onedir executables and the single-file build all share one
``Analysis``/``PYZ``. Measured end-to-end: **1315 s -> ~120-150 s**.

The three executables also come out of a single ``COLLECT``, so they sit next
to exactly one ``_internal/`` by construction. There is no staging directory,
no ``move``, no ``copy`` — which removes an entire class of layout bugs (the
old ``release.bat`` moved the build into ``dist\\raw-view`` and silently nested
it a level deeper whenever that path already existed, producing an exe that
could not find ``_internal``).

Three executables, three PE subsystems
--------------------------------------
Windows picks console behaviour from the PE subsystem of the image that is
*started*, so one binary cannot be both. The launcher therefore dispatches:

===========================  ==========  ==============================================
exe                          subsystem   role
===========================  ==========  ==============================================
``raw-view.exe``             2 (GUI)     launcher: routes argv to GUI or CLI
``raw-view-gui.exe``         2 (GUI)     GUI only
``raw-view-cli.exe``         3 (console) CLI only — never opens a window
===========================  ==========  ==============================================

Each ``EXE`` gets the runtime hooks plus *its own* entry script only. Passing
all of ``a.scripts`` to every ``EXE`` would put all three entry points in every
executable and make ``sys.argv[0]``-style bootstrapping pick the wrong module.

What is collected, and what deliberately is not
-----------------------------------------------
* The four ``collect_all`` calls (cv2, qt_material, qtawesome, pypinyin)
  replace the old ``--collect-all=`` flags. They are needed because each of
  those packages ships runtime *data* (cv2's loader config, qt_material's
  themes/fonts, qtawesome's icon fonts, pypinyin's phrase dictionary) that a
  module-graph walk cannot see. They are still required — do not drop them.

* PyQt5 is **not** collected wholesale. ``--collect-all=PyQt5`` pulled in
  5573 files / 403 MB: the entire QML tree (2647 files, 59.6 MB), 44 Qt DLLs
  including Qt5Designer/Qt5Quick/Qt5XmlPatterns, all 264 Qt plugins, and the
  translation catalogue twice (the batch file also added it via ``--add-data``).
  This app imports only QtCore/QtGui/QtWidgets/QtSvg, and PyInstaller's own
  ``hook-PyQt5.*`` hooks resolve those precisely. Measured standalone:
  3178 files / 332 MB / 129 MB zip  ->  767 files / 260 MB / 102 MB zip, with
  the GUI rendering pixel-identical. In the full four-exe build the zip went
  from 147.7 MB to 113.9 MB and the bundle from 3178 to 574 files.
  If a future feature starts using QML/Quick,
  Multimedia, WebEngine &c., the corresponding hook picks it up automatically
  because the module is then actually imported — this is the point of letting
  the hooks decide instead of forcing the whole binding in.

* ``excludes=`` is deliberately an **empty list**. PyInstaller's incremental
  check (``_check_guts_eq``) compares the stored value against the spec value,
  and a spec-side list never compares equal to the list round-tripped through
  the TOC file, so any non-empty ``excludes`` forces a full re-analysis on
  every single build. With ``[]`` an unchanged tree rebuilds in ~4 s and an
  edit rebuilds just the PYZ and the archives.

* ``PIL``/Pillow is not referenced anywhere in ``raw_view/`` (verified: no
  ``import PIL`` / ``from PIL``). It stays in ``requirements.txt`` but is not
  imported here, and nothing pulls it in.

Pathex
------
``SPECPATH`` is the directory containing this spec file, i.e. the repository
root. That keeps the spec portable: the local build and the CI checkout both
work without absolute paths.
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = SPECPATH

#: Entry script -> executable name -> PE subsystem (console flag).
ENTRIES = [
    ("launcher.py", "raw-view", False),
    ("gui_entry.py", "raw-view-gui", False),
    ("cli_entry.py", "raw-view-cli", True),
]

# -- Shared data ------------------------------------------------------------
# ``assets/`` holds the icons, theme XML and the QSS overlay images that
# ``raw_view.gui.app`` resolves through ``resource_path()`` at runtime. The Qt
# translations directory is added by PyInstaller's ``hook-PyQt5`` and needs no
# manual ``--add-data`` here.
datas = [(os.path.join(ROOT, "assets"), "assets")]

# -- Runtime data of the four data-carrying packages ------------------------
# These four replace the old ``--collect-all=`` flags. They are still required:
# each package ships runtime *data* that a module-graph walk cannot see —
# cv2's loader config, qt_material's themes/fonts, qtawesome's icon fonts and
# pypinyin's phrase dictionary. ``PyQt5`` is deliberately NOT in this list;
# see the module docstring.
binaries = []
hiddenimports = ["cv2"]  # imported through cv2's own loader shim
for _package in ("cv2", "qt_material", "qtawesome", "pypinyin"):
    _package_datas, _package_binaries, _package_hidden = collect_all(_package)
    datas += _package_datas
    binaries += _package_binaries
    hiddenimports += _package_hidden

a = Analysis(
    [os.path.join(ROOT, "raw_view", name) for name, _, _ in ENTRIES],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

#: Runtime hooks (``pyi_rth_*.py``) must be present in every executable; the
#: program scripts must not be shared, or each exe would contain all three.
_RUNTIME_HOOKS = [entry for entry in a.scripts if entry[0].startswith("pyi_rth_")]


def _scripts_for(entry_name: str):
    """Runtime hooks, then exactly the program script this executable runs."""
    stem = os.path.splitext(entry_name)[0]
    program = [
        entry
        for entry in a.scripts
        if entry[0] == stem and not entry[0].startswith("pyi_rth_")
    ]
    if len(program) != 1:  # pragma: no cover - guards a spec/entry mismatch
        raise SystemExit(
            f"spec error: expected exactly one program script {stem!r}, "
            f"found {len(program)}"
        )
    return _RUNTIME_HOOKS + program


#: Options shared by all four executables.
COMMON = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ROOT, "assets", "raw-view.ico")],
)

# -- Three onedir executables, one shared _internal/ ------------------------
_onedir_exes = [
    EXE(
        pyz,
        _scripts_for(entry),
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        **COMMON,
    )
    for entry, name, console in ENTRIES
]

coll = COLLECT(
    *_onedir_exes,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="raw-view",
)

# -- Single-file build (GUI only) -------------------------------------------
# Same Analysis/PYZ, but the binaries and data are embedded into the archive
# instead of collected beside it (``exclude_binaries`` defaults to False).
# PyInstaller writes the onedir ``dist/raw-view/`` directory first and this
# ``dist/raw-view.exe`` afterwards, so the shared name is intentional and safe.
exe_onefile = EXE(
    pyz,
    _scripts_for("gui_entry.py"),
    a.binaries,
    a.datas,
    [],
    name="raw-view",
    console=False,
    **COMMON,
)
