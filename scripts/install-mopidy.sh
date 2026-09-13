#!/usr/bin/env bash
# Install Mopidy 4 into its own venv, replacing Debian's package.
#
# Debian Trixie ships Mopidy 3.4.2 against GStreamer 1.26, and the two
# don't work together — playback dies with
#   AttributeError: 'StructureWrapper' object has no attribute 'get_name'
# which presents as Mopidy happily reporting "playing" while the speaker
# stays silent. Mopidy 4 fixed it, requires Python >= 3.13 (which Trixie
# has), and isn't packaged for Debian yet.
#
# The venv is created --system-site-packages so it can see the
# apt-installed python3-gi and python3-gst-1.0. Building PyGObject from
# source on a Pi needs a toolchain and half an hour; borrowing the
# system's copy takes neither.
set -euo pipefail

VENV="${MOPIDY_VENV:-$HOME/mopidy-venv}"
CONF_DIR="$HOME/.config/mopidy"
CONF="$CONF_DIR/mopidy.conf"
ALSA_DEVICE="${AUDIO_OUTPUT_DEVICE:-}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[33m!\033[0m %s\n' "$1"; }

bold "Installing Mopidy 4"

# --- the broken one has to go first ------------------------------------
# Two Mopidys fighting over port 6680 is a confusing failure, and the apt
# one restarting on boot would silently win.
if systemctl list-unit-files 2>/dev/null | grep -q '^mopidy.service'; then
  sudo systemctl disable --now mopidy >/dev/null 2>&1 || true
  ok "stopped Debian's mopidy"
fi

sudo apt-get install -y -qq python3-gi python3-gst-1.0 gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-alsa
ok "gstreamer plugins"

[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "mopidy>=4,<5" "mopidy-youtube>=4,<5" yt-dlp
ok "mopidy $("$VENV/bin/mopidy" --version 2>/dev/null | tail -1)"

# --- config ------------------------------------------------------------
mkdir -p "$CONF_DIR" "$HOME/.local/share/mopidy"
python3 - "$CONF" "$ALSA_DEVICE" <<'PY'
import configparser, sys
path, device = sys.argv[1], sys.argv[2]
c = configparser.ConfigParser()
c.read(path)

for section, values in {
    # Bound to localhost: the JSON-RPC API has no authentication.
    "http": {"enabled": "true", "hostname": "127.0.0.1", "port": "6680"},
    # alsasink, never autoaudiosink — the latter picks HDMI on a Pi and
    # you get a player that says "playing" into a screen nobody is using.
    "audio": {"output": f"alsasink device={device}" if device else "alsasink"},
    "youtube": {"enabled": "true", "api_enabled": "false"},
}.items():
    if not c.has_section(section):
        c.add_section(section)
    c[section].update(values)

with open(path, "w") as f:
    c.write(f)
PY
ok "config at $CONF"

# --- service -----------------------------------------------------------
# A user service, not a system one: it reads ~/.config/mopidy and plays
# to this user's audio, which is what the agent shares a speaker with.
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/mopidy.service" <<UNIT
[Unit]
Description=Mopidy 4 (venv)
After=network-online.target sound.target

[Service]
Type=simple
ExecStart=$VENV/bin/mopidy
Restart=always
RestartSec=5
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now mopidy
# Survive logout, so the speaker keeps working when nobody is SSH'd in.
sudo loginctl enable-linger "$USER" >/dev/null 2>&1 || warn "couldn't enable linger"

for _ in $(seq 1 20); do
  curl -fsS -m 1 -X POST http://127.0.0.1:6680/mopidy/rpc \
    -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}' >/dev/null 2>&1 && break
  sleep 1
done

if curl -fsS -m 2 -X POST http://127.0.0.1:6680/mopidy/rpc \
    -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}' >/dev/null 2>&1; then
  ok "mopidy responding"
  echo
  echo "  check backends:  ./venv/bin/python -m saathi.music.cli backends"
  echo "  watch logs:      journalctl --user -fu mopidy"
else
  warn "not responding — journalctl --user -u mopidy -n 40"
fi
