# Tiled Multiboxer

`multiboxer` is a simple tiled desktop window manager for running multiple EverQuest instances. It positions game windows on your desktop in a main/preview layout and allows quick swapping with Alt+Tab.

## Features

- **Tiled Layout**: Main window takes 75% of screen, preview in top-right corner
- **Alt+Tab Swap**: Instantly swap which window is main/preview
- **Borderless Windows**: Clean borderless game windows
- **No Embedding**: Windows stay on desktop - stable and reliable
- **Auto-Refresh**: Keeps windows in position

## Quick Start

1. Launch two EverQuest clients
2. Run `multiboxer.exe`
3. Click "Grab" to assign windows to Main and Preview slots
4. Press **Alt+Tab** to swap windows

## System Requirements

### Operating System
- **Windows 10/11** (64-bit) - **Required for full functionality**
  - Uses Win32 APIs for window management and control
  - EverQuest window embedding and decoration removal
- **Linux/macOS** - Limited functionality (UI simulation mode only)
  - Cannot control EverQuest windows on non-Windows systems
  - Useful for development and testing UI components

### Python Environment  
- **Python 3.8 or higher** (Python 3.10+ recommended)
- **pip** package manager
- **Virtual environment support** (venv, conda, etc.)

### Hardware Requirements
- **Memory**: 4GB RAM minimum (8GB+ recommended for multiple EverQuest instances)
- **Storage**: 100MB for application + EverQuest installation space
- **Display**: 1280x720 minimum resolution (1920x1080+ recommended)
- **CPU**: Dual-core processor minimum (Quad-core+ recommended for multiboxing)

### Required Software
- **EverQuest** installation with `everquest.exe` accessible
- **Visual Studio C++ Redistributable** (usually installed with Windows)
- **Windows Defender** exceptions may be needed for window manipulation

## Prerequisites Installation

### 1. Install Python
Download and install Python from [python.org](https://www.python.org/downloads/)
- ✅ Check "Add Python to PATH" during installation
- ✅ Verify installation: `python --version`

### 2. Install Git (Optional)
For development or cloning the repository:
```bash
# Download from https://git-scm.com/download/win
git --version  # Verify installation
```

### 3. Windows-Specific Setup
Ensure Windows features are enabled:
- **Windows API Access**: Automatically available on Windows 10/11
- **User Account Control**: May require "Run as Administrator" for some games
- **Antivirus Exceptions**: Add multiboxer to antivirus exclusions if needed

## Features

- Minimal dark-grey GUI with top menu bar only:
  - **Settings**: save game path and session IDs (Session 1/2/3)
  - **View**: switch standard window resolutions
- Main borderless game area attached directly under the top menu
- If game path is unknown, search/select `everquest.exe`
- Launch EverQuest in the large container
- Compact Main/Preview status boxes and swap controls in the top menu corner
- Session ID persistence in local config

## Installation

### 1. Clone or Download Repository
```bash
git clone <repository-url>
cd multiboxer-master
```

### 2. Create Python Virtual Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate
```

### 3. Install Python Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Verify Installation
```bash
python run.py
```

### Dependencies Explanation
- **PySide6 (≥6.7.0)**: Qt GUI framework for cross-platform interface
- **psutil (≥6.0.0)**: Process and system monitoring utilities  
- **pywin32 (≥306)**: Windows API access (Windows only)
  - Enables window manipulation, embedding, and decoration removal
  - Critical for EverQuest window management functionality

## Troubleshooting Installation

### Common Issues
1. **"PySide6 not found"**: Ensure virtual environment is activated
2. **"pywin32 installation failed"**: Update pip and try again: `pip install --upgrade pip`
3. **"Python not recognized"**: Add Python to system PATH
4. **Permission errors**: Run terminal as Administrator on Windows

### Windows Defender / Antivirus
If Windows Defender blocks the application:
1. Open **Windows Security** > **Virus & threat protection**
2. Add **Exclusion** > **Folder** 
3. Select the multiboxer installation directory
4. Add **Process** exclusion for `multiboxer.exe`

## Quick Start (Alternative)

For experienced Python users:

```bash
python3 -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Usage

```bash
python run.py
```

## Build Executables (PyInstaller)

Build on each target OS natively:

- Build `multiboxer.exe` on **Windows**
- Build `multiboxer` on **Linux**

Cross-compiling Windows from Linux (or Linux from Windows) is not supported in this setup.

### Linux

```bash
chmod +x scripts/build_linux.sh
./scripts/build_linux.sh
```

Output: `dist/multiboxer`

### Windows (PowerShell)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./scripts/build_windows.ps1
```

Output: `dist\\multiboxer.exe`

## Notes

- Full external window control is implemented for **Windows** using Win32 APIs, including floating attachment under the app menu.
- On non-Windows systems, the app still runs in simulation mode for UI/state flow but cannot truly control `everquest.exe` windows this way.


powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1