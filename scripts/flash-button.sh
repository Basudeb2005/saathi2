#!/usr/bin/env bash
# Compile and flash the button firmware — from the Pi, over USB.
#
# No Arduino IDE, no Create Agent, no Mac. arduino-cli is a single binary
# and the Pi is already a Linux box with USB ports, so the shortest path
# to a flashed ESP32 doesn't involve a second computer at all.
#
#   bash scripts/flash-button.sh              # auto-detect the board
#   FQBN=esp32:esp32:esp32c3 bash scripts/flash-button.sh
set -euo pipefail

SKETCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/firmware"
CLI="$HOME/.local/bin/arduino-cli"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$1"; exit 1; }

bold "Flashing the Saathi button"

# --- arduino-cli --------------------------------------------------------
if [ ! -x "$CLI" ]; then
  mkdir -p "$HOME/.local/bin"
  curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
    | BINDIR="$HOME/.local/bin" sh
fi
ok "arduino-cli $("$CLI" version | head -1)"

# --- esp32 core + library ----------------------------------------------
"$CLI" config init --overwrite >/dev/null 2>&1 || true
"$CLI" config add board_manager.additional_urls \
  https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json >/dev/null 2>&1 || true

# The ESP32 core is a few hundred MB of toolchain. First run is slow.
"$CLI" core update-index
"$CLI" core install esp32:esp32
ok "esp32 core"

"$CLI" lib install "ESP32 BLE Keyboard" 2>/dev/null \
  || warn "ESP32 BLE Keyboard not in the index — installing from GitHub" \
  && "$CLI" lib install --git-url https://github.com/T-vK/ESP32-BLE-Keyboard.git 2>/dev/null || true
ok "libraries"

# --- find the board -----------------------------------------------------
PORT="${PORT:-}"
if [ -z "$PORT" ]; then
  PORT="$("$CLI" board list --format json 2>/dev/null \
    | python3 -c "
import json,sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
rows = rows.get('detected_ports', rows) if isinstance(rows, dict) else rows
for r in rows:
    port = (r.get('port') or {}).get('address', '')
    # USB-serial bridges: CP210x, CH340, or the ESP32's native USB.
    if port.startswith(('/dev/ttyUSB', '/dev/ttyACM')):
        print(port); break
" || true)"
fi
[ -n "$PORT" ] || die "No board found. Plug the ESP32 in, then: $CLI board list"
ok "board on $PORT"

# --- which ESP32 --------------------------------------------------------
# Guessing wrong here produces a board that flashes and never boots, so
# it's worth being explicit when auto-detection isn't sure.
FQBN="${FQBN:-}"
if [ -z "$FQBN" ]; then
  FQBN="$("$CLI" board list --format json 2>/dev/null \
    | python3 -c "
import json,sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
rows = rows.get('detected_ports', rows) if isinstance(rows, dict) else rows
for r in rows:
    for b in r.get('matching_boards') or []:
        if b.get('fqbn'):
            print(b['fqbn']); raise SystemExit
" || true)"
fi
if [ -z "$FQBN" ]; then
  FQBN="esp32:esp32:esp32"
  warn "couldn't identify the board — assuming $FQBN"
  warn "if it flashes but never appears over Bluetooth, set FQBN and re-run:"
  warn "  esp32:esp32:esp32c3   esp32:esp32:esp32s3   esp32:esp32:XIAO_ESP32C3"
fi
ok "fqbn $FQBN"

# --- serial permissions -------------------------------------------------
if ! groups | grep -qw dialout; then
  sudo usermod -aG dialout "$USER" || true
  warn "added you to 'dialout' — log out and back in if the upload is denied"
fi

# --- go -----------------------------------------------------------------
"$CLI" compile --fqbn "$FQBN" "$SKETCH_DIR"
ok "compiled"

"$CLI" upload -p "$PORT" --fqbn "$FQBN" "$SKETCH_DIR"
ok "flashed"

cat <<NEXT

  Next:
    bluetoothctl                 # scan on / pair / trust "Saathi Button"
    ./venv/bin/python -m saathi.button      # press it, watch for output

  The board deep-sleeps immediately, so it only advertises while the
  button is held — hold it down while pairing.

NEXT
