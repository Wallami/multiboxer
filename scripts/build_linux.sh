#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv

.venv/bin/python -m pip install --upgrade pip
PIP_PROGRESS_BAR=off .venv/bin/python -m pip install -r requirements-build.txt --retries 25 --timeout 120

.venv/bin/pyinstaller --noconfirm --clean multiboxer.spec

echo "Build complete. Linux binary: dist/multiboxer"
