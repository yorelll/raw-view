@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================================
REM raw-view local release packager (Windows)
REM
REM Double-click this file from the repository root to run tests and produce:
REM   dist\raw-view.exe
REM   dist\raw-view-<version>-windows-x64.zip
REM   dist\*.sha256
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
echo [1/5] Running full tests...
python -m pytest tests\ -q
if errorlevel 1 (
    echo [ERROR] Tests failed. Packaging aborted.
    goto :fail
)

echo.
echo [2/5] Preparing staging directories...
if exist ".release-staging" rmdir /s /q ".release-staging"
if exist "dist" rmdir /s /q "dist"
mkdir ".release-staging"

set "COMMON_ARGS=--noconfirm --clean --windowed --name raw-view --paths "%CD%" --hidden-import=cv2 --hidden-import=PIL --collect-all=cv2 --collect-all=PyQt5 --collect-all=qt_material --collect-all=qtawesome --icon assets\raw-view.ico --add-data "assets;assets""
set "QT_TRANSLATIONS=.venv\Lib\site-packages\PyQt5\Qt5\translations"

if exist "%QT_TRANSLATIONS%" (
    set "TRANSLATION_ARG=--add-data "%QT_TRANSLATIONS%;Qt5/translations""
) else (
    set "TRANSLATION_ARG="
    echo [WARN] PyQt5 translations not found; package will use Qt built-in English fallback.
)

echo.
echo [3/5] Building onedir bundle for the unzip-and-run ZIP...
python -m PyInstaller %COMMON_ARGS% %TRANSLATION_ARG% raw_view\__main__.py
if errorlevel 1 goto :fail
move /y "dist\raw-view" ".release-staging\raw-view" >nul

REM --clean removes dist/build, so build onefile after staging the onedir output.
echo.
echo [4/5] Building single-file EXE...
python -m PyInstaller %COMMON_ARGS% %TRANSLATION_ARG% --onefile raw_view\__main__.py
if errorlevel 1 goto :fail
move /y "dist\raw-view.exe" ".release-staging\raw-view.exe" >nul

mkdir "dist"
move /y ".release-staging\raw-view.exe" "dist\raw-view.exe" >nul
move /y ".release-staging\raw-view" "dist\raw-view" >nul

echo.
echo [5/5] Creating ZIP and SHA-256 checksums...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\raw-view' -DestinationPath 'dist\raw-view-%VERSION%-windows-x64.zip' -Force"
if errorlevel 1 goto :fail

powershell -NoProfile -ExecutionPolicy Bypass -Command "$files = @('dist\raw-view.exe', 'dist\raw-view-%VERSION%-windows-x64.zip'); foreach ($file in $files) { $hash = (Get-FileHash -Algorithm SHA256 -Path $file).Hash.ToLower(); $name = Split-Path -Leaf $file; Set-Content -Encoding ascii -NoNewline -Path ($file + '.sha256') -Value ($hash + ' *' + $name) }"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo Packaging complete.
echo   dist\raw-view.exe
echo   dist\raw-view-%VERSION%-windows-x64.zip
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
