#!/usr/bin/env bash
# Sets up this checkout to run: Python packages with CUDA, the capture library, the app in
# the local venv and a desktop launcher.
#
# Usage: scripts/install-dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"
command -v uv >/dev/null || { echo "uv not found" >&2; exit 1; }

[[ -x .venv/bin/python ]] || uv venv -q --seed --python 3.12 .venv
uv pip install -q --python .venv/bin/python --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple torch
uv pip install -q --python .venv/bin/python -e .

cmake -S tools/ferret-capture -B tools/ferret-capture/build >/dev/null
cmake --build tools/ferret-capture/build -j4 >/dev/null

rules=/etc/udev/rules.d/60-ferret-scanner.rules
if [[ ! -f "$rules" ]]; then
  echo "Scanner USB access needs: sudo install -m 644 tools/ferret-capture/60-ferret-scanner.rules $rules"
fi

data="${XDG_DATA_HOME:-$HOME/.local/share}"
mkdir -p "$data/applications"
cat > "$data/applications/io.github.ssjrocks.UbuntuFerretScanner.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Ubuntu Ferret Scanner
Comment=3D scanning for the Creality Ferret
Exec=$root/.venv/bin/ubuntu-ferret-scanner
Terminal=false
Categories=Graphics;3DGraphics;
DESKTOP
update-desktop-database -q "$data/applications" 2>/dev/null || true
echo "Installed. Start it from the app menu or run $root/.venv/bin/ubuntu-ferret-scanner"
