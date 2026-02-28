@echo off
setlocal

:: ============================================================
::  Multiboxer - One-click launcher for Windows
::
::  This script will:
::    1. Check for Python
::    2. Create a virtual environment if one does not exist
::    3. Install dependencies if they are missing
::    4. Build multiboxer.exe via PyInstaller if not yet built
::    5. Launch multiboxer.exe
:: ============================================================

title Multiboxer Launcher

:: Move to the directory where this .bat lives
cd /d "%~dp0"

:: -------------------------------------------------------
::  If a built exe already exists, just run it
:: -------------------------------------------------------
if exist "dist\multiboxer.exe" (
    echo [Multiboxer] Found dist\multiboxer.exe - launching...
    start "" "dist\multiboxer.exe"
    goto :end
)

:: -------------------------------------------------------
::  Locate Python
:: -------------------------------------------------------
echo [Multiboxer] No pre-built exe found. Setting up from source...

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
    goto :found_python
)

where python >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
    goto :found_python
)

echo.
echo [ERROR] Python was not found on your system.
echo         Please install Python 3.8+ from https://www.python.org/downloads/
echo         Make sure to check "Add Python to PATH" during installation.
echo.
pause
goto :end

:found_python
echo [Multiboxer] Using: %PYTHON_CMD%

:: -------------------------------------------------------
::  Create virtual environment if missing
:: -------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [Multiboxer] Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        goto :end
    )
)

:: Activate the venv
call .venv\Scripts\activate.bat

:: -------------------------------------------------------
::  Install / upgrade dependencies
:: -------------------------------------------------------
echo [Multiboxer] Checking dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements-build.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    goto :end
)

:: -------------------------------------------------------
::  Build the executable with PyInstaller
:: -------------------------------------------------------
echo [Multiboxer] Building multiboxer.exe with PyInstaller...
pyinstaller --noconfirm --clean multiboxer.spec
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed.
    echo         Falling back to running from source...
    echo.
    python run.py
    goto :end
)

:: -------------------------------------------------------
::  Launch the freshly built exe
:: -------------------------------------------------------
if exist "dist\multiboxer.exe" (
    echo [Multiboxer] Build complete! Launching multiboxer.exe...
    start "" "dist\multiboxer.exe"
) else (
    echo [Multiboxer] Running from source...
    python run.py
)

:end
endlocal
