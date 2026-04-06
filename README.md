# multiboxer

`multiboxer` is a minimal desktop launcher/container for running multiple EverQuest instances with a primary full-size game area and compact session controls near the top menu.

## Features

- Minimal dark-grey GUI with top menu bar only:
  - **Settings**: save game path and session IDs (Session 1/2/3)
  - **View**: switch standard window resolutions
- Main borderless game area attached directly under the top menu
- If game path is unknown, search/select `everquest.exe`
- Launch EverQuest in the large container
- Compact Main/Preview status boxes and swap controls in the top menu corner
- Session ID persistence in local config

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

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
