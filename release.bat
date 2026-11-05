@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================================
REM raw-view local release packager (Windows)
REM
REM Double-click this file from the repository root to run tests and produce:
REM   dist\raw-view.exe                          single-file EXE (GUI only)
REM   dist\raw-view\raw-view.exe                 zip bundle launcher (routes args)
REM   dist\raw-view\raw-view-gui.exe             zip bundle GUI
REM   dist\raw-view\raw-view-cli.exe             zip bundle CLI (console)
REM   dist\raw-view\_internal\                   one shared dependency tree
REM   dist\raw-view-<version>-windows-x64.zip
REM   dist\*.sha256
REM
REM Everything is built from ONE PyInstaller spec (raw-view.spec): a single
REM Analysis feeds all four executables, and one COLLECT produces the three
REM side-by-side executables with a single shared _internal. Reasons:
REM
REM   * Speed. Four separate PyInstaller runs re-analyse the same dependency
REM     tree four times. Measured before: 412 + 394 + 238 + 270 = 1315 s.
REM     One Analysis: ~120 s.
REM   * Correct layout, by construction. The executables are written straight
REM     into dist\raw-view\ next to _internal\, so there is no staging folder to
REM     assemble and no `move` that can nest a directory one level too deep.
REM     (The old script moved a build into dist\raw-view; when that path already
REM     existed -- e.g. because a previous dist could not be deleted -- `move`
REM     nested the tree, the exe ended up a level away from _internal, and it
REM     died with "Failed to load Python DLL ...\_internal\python312.dll".)
REM   * One definition. CI builds from the very same raw-view.spec.
REM
REM This is a LOCAL packaging helper. Official GitHub Releases are still made
REM by committing, pushing, then pushing a version tag (vX.Y.Z), which runs
REM .github\workflows\build-release.yml.
REM ============================================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe was not found.
    echo Create the project virtual environment and install dependencies first.
    goto :fail
)

if not exist "raw-view.spec" (
    echo [ERROR] raw-view.spec was not found. It is the single build definition
    echo         for all four executables and must sit in the repository root.
    goto :fail
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate .venv.
    goto :fail
)

for /f %%V in ('python -c "from raw_view.models import APP_VERSION; print(APP_VERSION)"') do set "VERSION=%%V"
if "%VERSION%"=="" (
    echo [ERROR] Failed to read raw_view.models.APP_VERSION.
    goto :fail
)

echo.
echo ============================================================
echo raw-view local release packager - version %VERSION%
echo ============================================================

echo.
echo [1/4] Running full tests...
python -m pytest tests\ -q
if errorlevel 1 (
    echo [ERROR] Tests failed. Packaging aborted.
    goto :fail
)

REM ---------------------------------------------------------------------------
REM [2/4] Remove previous output.
REM
REM This HAS to be fatal on failure. A stale dist\raw-view that could not be
REM deleted is what used to make the old assembly step nest the tree one level
REM deeper, and the resulting exe could not find _internal. Better to stop with
REM a message than to ship a layout nobody expects.
REM ---------------------------------------------------------------------------
echo.
echo [2/4] Cleaning previous build output...
if exist "build" (
    rmdir /s /q "build"
    if exist "build" (
        echo [ERROR] Could not remove "build". Close any program using it and retry.
        goto :fail
    )
)
if exist "dist" (
    rmdir /s /q "dist"
    if exist "dist" (
        echo [ERROR] Could not remove "dist".
        echo         Something is holding a file open -- close Explorer windows,
        echo         terminals or running raw-view copies inside dist\ and retry.
        goto :fail
    )
)

REM ---------------------------------------------------------------------------
REM [3/4] Build all four executables from the single spec.
REM
REM PyInstaller writes:
REM   dist\raw-view\{raw-view.exe, raw-view-gui.exe, raw-view-cli.exe, _internal\}
REM   dist\raw-view.exe
REM The executables sit next to one shared _internal because they all come out
REM of the same COLLECT; nothing is moved or copied afterwards.
REM ---------------------------------------------------------------------------
echo.
echo [3/4] Building all executables from raw-view.spec (one Analysis)...
python -m PyInstaller --noconfirm --clean --distpath "dist" --workpath "build" raw-view.spec
if errorlevel 1 goto :fail

for %%F in (
    "dist\raw-view\raw-view.exe"
    "dist\raw-view\raw-view-gui.exe"
    "dist\raw-view\raw-view-cli.exe"
    "dist\raw-view\_internal\python312.dll"
    "dist\raw-view.exe"
) do (
    if not exist "%%~F" (
        echo [ERROR] Expected artifact missing: %%~F
        goto :fail
    )
)

REM ---------------------------------------------------------------------------
REM [4/4] ZIP + SHA-256 checksums.
REM ---------------------------------------------------------------------------
echo.
echo [4/4] Creating ZIP and SHA-256 checksums...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\raw-view' -DestinationPath 'dist\raw-view-%VERSION%-windows-x64.zip' -Force"
if errorlevel 1 goto :fail

REM Checksums are computed in Python, not via PowerShell's Get-FileHash: that
REM cmdlet is PowerShell 7+ only, and `powershell` on a stock Windows resolves
REM to Windows PowerShell 5.1, where it does not exist. The old one-liner then
REM wrote a checksum file containing just " *raw-view.exe" and still exited 0,
REM so releases shipped with silently empty .sha256 files.
python -c "import hashlib,os,sys; files=[os.path.join('dist','raw-view.exe'), os.path.join('dist','raw-view-%VERSION%-windows-x64.zip')]; [open(f+'.sha256','wb').write((hashlib.sha256(open(f,'rb').read()).hexdigest()+' *'+os.path.basename(f)).encode('ascii')) for f in files]"
if errorlevel 1 goto :fail

REM A checksum file must never ship empty: 64 hex digits + ' *' + name.
for %%F in ("dist\raw-view.exe.sha256" "dist\raw-view-%VERSION%-windows-x64.zip.sha256") do (
    for %%S in (%%~zF) do (
        if %%~S LSS 70 (
            echo [ERROR] checksum file looks empty: %%~F
            goto :fail
        )
    )
)

echo.
echo ============================================================
echo Packaging complete.
echo   dist\raw-view.exe                (single file, GUI only)
echo   dist\raw-view\raw-view.exe       (zip bundle: launcher)
echo   dist\raw-view\raw-view-gui.exe   (zip bundle: GUI)
echo   dist\raw-view\raw-view-cli.exe   (zip bundle: CLI)
echo   dist\raw-view-!VERSION!-windows-x64.zip
echo   dist\*.sha256
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo Packaging failed. See the messages above.
echo.
pause
exit /b 1
