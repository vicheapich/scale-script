#!/usr/bin/env bash
# Build a standalone macOS executable (dist/test_scale).
# The binary only runs on Macs with the same CPU type as the build machine
# (arm64 = Apple Silicon, x86_64 = Intel).
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/pyinstaller --onefile --console --clean --name test_scale test_scale.py

echo
echo "Done: dist/test_scale  ($(uname -m))"
echo "Run:  ./dist/test_scale --port /dev/cu.usbserial-XXXX"
