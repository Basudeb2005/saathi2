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
# Two Mopidys fighting over port 6680 is a confusing failure: whichever
# binds first wins, and the apt one restarting on boot would silently
# take the port back. Masked, not just disabled, so nothing re-enables it.
if systemctl list-unit-files 2>/dev/null | grep -q '^mopidy.service'; then
  sudo systemctl disable --now mopidy >/dev/null 2>&1 || true
  sudo systemctl mask mopidy >/dev/null 2>&1 || true
  ok "stopped and masked Debian's mopidy"
fi

# Anything still holding 6680 will make the new service fail to bind.
if ss -tlnp 2>/dev/null | grep -q ':6680'; then
  warn "something still on port 6680 — killing it"
  sudo fuser -k 6680/tcp >/dev/null 2>&1 || true
  sleep 2
fi

sudo apt-get install -y -qq python3-gi python3-gst-1.0 gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-alsa
ok "gstreamer plugins"

[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --upgrade -q pip
# Not quiet, and not one line. PyPI drops connections from this network
# often enough that a half-finished install is a real outcome, and a
# missing mopidy-youtube presents only as "no youtube backend" hours
# later.
"$VENV/bin/pip" install "mopidy>=4,<5" "mopidy-youtube>=4,<5" yt-dlp

for pkg in mopidy mopidy-youtube yt-dlp; do
  if ! "$VENV/bin/pip" show "$pkg" >/dev/null 2>&1; then
    warn "$pkg did NOT install — retrying once"
    "$VENV/bin/pip" install "$pkg" || true
  fi
done
"$VENV/bin/pip" list 2>/dev/null | grep -i -E "^(mopidy|yt-dlp)" | sed 's/^/  /'
ok "packages installed"

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
# A system unit running AS this user, not a `systemctl --user` one.
# Over SSH there may be no per-user systemd manager, and the user-unit
# version reported success while starting nothing and logging nothing.
# This also matches how saathi-agent@ and saathi@ are installed, so
# there is one place to look when something isn't running.
mkdir -p "$HOME/Music"   # silences a startup warning about media_dirs

sudo tee /etc/systemd/system/mopidy-venv.service >/dev/null <<UNIT
[Unit]
Description=Mopidy 4 (venv)
After=network-online.target sound.target
Wants=network-online.target
# In [Unit], not [Service] — systemd ignores it here otherwise, and the
# point is that it never gives up retrying.
StartLimitIntervalSec=0

[Service]
Type=simple
User=$USER
Group=audio
WorkingDirectory=$HOME
Environment=HOME=$HOME
ExecStart=$VENV/bin/mopidy
Restart=always
RestartSec=5
SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
UNIT

# Clean up the user-unit attempt so two of them can't race for the port.
systemctl --user disable --now mopidy >/dev/null 2>&1 || true
rm -f "$HOME/.config/systemd/user/mopidy.service" \
      "$HOME/.config/systemd/user/default.target.wants/mopidy.service"

sudo systemctl daemon-reload
sudo systemctl enable --now mopidy-venv
ok "service installed (mopidy-venv)"

# Mopidy 4 takes a while to come up on a Pi — it imports yt-dlp and
# scans media dirs before the HTTP frontend binds. 20s was not enough.
for _ in $(seq 1 45); do
  curl -fsS -m 1 -X POST http://127.0.0.1:6680/mopidy/rpc \
    -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}' >/dev/null 2>&1 && break
  sleep 1
done

VERSION_JSON="$(curl -fsS -m 2 -X POST http://127.0.0.1:6680/mopidy/rpc \
  -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}' 2>/dev/null || true)"

if [ -n "$VERSION_JSON" ]; then
  echo "  answering on 6680: $VERSION_JSON"
  # Which Mopidy answered matters more than that one did: the old 3.4.2
  # replies happily and is exactly what we are trying to get rid of.
  case "$VERSION_JSON" in
    *'"4.'*) ok "Mopidy 4 is the one serving" ;;
    *) warn "an OLD Mopidy is still serving port 6680 — the new one never bound" ;;
  esac
else
  warn "not responding. Its own log:"
  journalctl -u mopidy-venv -n 30 --no-pager 2>/dev/null | sed 's/^/    /' || true

  # An empty journal means the service never ran, not that it ran
  # quietly — so run it in the foreground where the error has nowhere to
  # hide. Ten seconds is plenty to fail at startup.
  echo
  warn "running it directly instead, to see the real error:"
  timeout 10 "$VENV/bin/mopidy" 2>&1 | tail -25 | sed 's/^/    /' || true
fi

echo
echo "  backends:  ./venv/bin/python -m saathi.music.cli backends"
echo "  logs:      journalctl -fu mopidy-venv"
