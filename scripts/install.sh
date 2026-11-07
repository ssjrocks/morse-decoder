#!/usr/bin/env bash
# Installs Morse Decoder for the current user from this checkout: a venv in
# ~/.local/share/morse-decoder that uses the system's numpy, scipy and PyQt6
# (python3-numpy, python3-scipy, python3-pyqt6), plus the app-menu entry.
#
#   scripts/install.sh              install / update
#   scripts/install.sh --uninstall  remove (keeps ~/.local/state/morse-decoder)
set -euo pipefail

APP_ID=io.github.ssjrocks.MorseDecoder
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
ROOT="$DATA/morse-decoder"
VENV="$ROOT/venv"
DESKTOP="$DATA/applications/$APP_ID.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$ROOT"
  rm -f "$DESKTOP"
  update-desktop-database -q "$DATA/applications" 2>/dev/null || true
  echo "Morse Decoder removed."
  exit 0
fi

command -v pw-record >/dev/null || { echo "pw-record not found: sudo apt install pipewire-bin" >&2; exit 1; }
src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v uv >/dev/null; then
  uv venv -q --clear --system-site-packages --python /usr/bin/python3 "$VENV"
  uv pip install -q --python "$VENV/bin/python" "$src"
else
  python3 -m venv --clear --system-site-packages "$VENV"
  "$VENV/bin/pip" install -q "$src"
fi

mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Version=1.1
Type=Application
Name=Morse Decoder
Comment=Decode Talkpod Morse (MCW) from any audio playing on this computer
Exec=$VENV/bin/python -m morse_decoder
Icon=/usr/share/icons/Adwaita/scalable/devices/audio-headphones.svg
Terminal=false
Categories=AudioVideo;Audio;HamRadio;
StartupWMClass=$APP_ID
DESK
update-desktop-database -q "$DATA/applications" 2>/dev/null || true
echo "Installed. Open Morse Decoder from the app menu, or run: $VENV/bin/python -m morse_decoder"
