#!/usr/bin/env bash
# Construit l'executable sous macOS ou Linux.
set -euo pipefail
echo
echo "  Construction de tubeiso"
echo "  -----------------------"
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt pyinstaller -q
python3 tests/test_tubeiso.py
pyinstaller tubeiso.spec --noconfirm --clean
echo
echo "  Termine : dist/tubeiso/tubeiso"
