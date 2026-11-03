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
REM The three zip executables share ONE _internal directory.  Building each one
REM separately with its own _internal would triple the download (measured:
REM 136.5 MB -> 382.8 MB), so the build strategy is:
REM   1. build onedir  --windowed  (produces _internal + raw-view-gui.exe)
REM   2. build onedir  --console   (produces an equivalent _internal + cli exe)
REM   3. save step 1's _internal, keep step 2's, add step 1's exe back to it
REM Verified byte-for-byte: 3176 of 3177 files are identical between the two
REM builds, and the one difference (base_library.zip) is content-equivalent, so
REM either _internal runs either executable.
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
echo [1/6] Running full tests...
python -m pytest tests\ -q
if errorlevel 1 (
    echo [ERROR] Tests failed. Packaging aborted.
    goto :fail
)

echo.
echo [2/6] Preparing staging directories...
if exist ".release-staging" rmdir /s /q ".release-staging"
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
mkdir ".release-staging"
mkdir ".release-staging\gui"
mkdir ".release-staging\cli"
mkdir ".release-staging\bundle"

set "COMMON_ARGS=--noconfirm --clean --paths "%CD%" --hidden-import=cv2 --hidden-import=PIL --collect-all=cv2 --collect-all=PyQt5 --collect-all=qt_material --collect-all=qtawesome --collect-all=pypinyin --icon assets\raw-view.ico --add-data "assets;assets""
set "QT_TRANSLATIONS=.venv\Lib\site-packages\PyQt5\Qt5\translations"

if exist "%QT_TRANSLATIONS%" (
    set "TRANSLATION_ARG=--add-data "%QT_TRANSLATIONS%;Qt5/translations""
) else (
    set "TRANSLATION_ARG="
    echo [WARN] PyQt5 translations not found; package will use Qt built-in English fallback.
)

REM ---------------------------------------------------------------------------
REM [3/6] onedir bundle #1 -- --windowed.  Produces the GUI executable AND the
REM _internal tree that the whole zip will share.
REM ---------------------------------------------------------------------------
echo.
echo [3/6] Building zipped bundle (windowed half: _internal + GUI exe)...
python -m PyInstaller %COMMON_ARGS% %TRANSLATION_ARG% --windowed --name raw-view-gui --distpath ".release-staging\gui" --workpath "build\gui" raw_view\gui_entry.py
if errorlevel 1 goto :fail
if not exist ".release-staging\gui\raw-view-gui\raw-view-gui.exe" goto :fail

REM ---------------------------------------------------------------------------
REM [4/6] onedir bundle #2 -- --console.  Produces the CLI executable; its own
REM _internal is discarded in favour of build #1's (see the header comment).
REM ---------------------------------------------------------------------------
echo.
echo [4/6] Building zipped bundle (console half: CLI exe)...
python -m PyInstaller %COMMON_ARGS% %TRANSLATION_ARG% --console --name raw-view-cli --distpath ".release-staging\cli" --workpath "build\cli" raw_view\cli_entry.py
if errorlevel 1 goto :fail
if not exist ".release-staging\cli\raw-view-cli\raw-view-cli.exe" goto :fail

REM ---------------------------------------------------------------------------
REM [5/6] launcher (--windowed, third onedir build) + assemble the shared bundle.
REM ---------------------------------------------------------------------------
echo.
echo [5/6] Building zipped bundle (launcher) and assembling shared _internal...
python -m PyInstaller %COMMON_ARGS% %TRANSLATION_ARG% --windowed --name raw-view --distpath ".release-staging\bundle" --workpath "build\launcher" raw_view\launcher.py
if errorlevel 1 goto :fail
if not exist ".release-staging\bundle\raw-view\raw-view.exe" goto :fail

REM Assemble dist\raw-view\ : launcher's _internal (any of the three works),
REM plus all three executables side by side.
move /y ".release-staging\bundle\raw-view" "dist\raw-view" >nul
copy /y ".release-staging\gui\raw-view-gui\raw-view-gui.exe" "dist\raw-view\raw-view-gui.exe" >nul
copy /y ".release-staging\cli\raw-view-cli\raw-view-cli.exe" "dist\raw-view\raw-view-cli.exe" >nul
if errorlevel 1 goto :fail

REM ---------------------------------------------------------------------------
REM [6/6] single-file EXE (GUI only) + zip + checksums.
REM ---------------------------------------------------------------------------
echo.
echo [6/6] Building single-file EXE (GUI only)...
python -m PyInstaller %COMMON_ARGS% %TRANSLATION_ARG% --onefile --windowed --name raw-view --distpath "dist" --workpath "build\onefile" raw_view\gui_entry.py
if errorlevel 1 goto :fail

echo.
echo Creating ZIP and SHA-256 checksums...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\raw-view' -DestinationPath 'dist\raw-view-%VERSION%-windows-x64.zip' -Force"
if errorlevel 1 goto :fail

powershell -NoProfile -ExecutionPolicy Bypass -Command "$files = @('dist\raw-view.exe', 'dist\raw-view-%VERSION%-windows-x64.zip'); foreach ($file in $files) { $hash = (Get-FileHash -Algorithm SHA256 -Path $file).Hash.ToLower(); $name = Split-Path -Leaf $file; Set-Content -Encoding ascii -NoNewline -Path ($file + '.sha256') -Value ($hash + ' *' + $name) }"
if errorlevel 1 goto :fail

if exist ".release-staging" rmdir /s /q ".release-staging"

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
