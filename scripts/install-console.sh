#!/usr/bin/env bash
# Make the Pi reachable with no monitor, no keyboard and no known address.
#
# After this, from a phone:
#   * pair over Bluetooth and ask it anything, with no network at all
#   * open its web console once it is on one
#   * put it on a new network by tapping the name and typing the password
#   * start and stop Saathi with a button
#
# And if it ever boots somewhere it can't reach a saved network, it turns
# itself into one so the phone can still get in.
#
# Safe to re-run. Every step checks before it changes anything.
set -euo pipefail

USER_NAME="${SUDO_USER:-$(id -un)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }

if [[ "$USER_NAME" == "root" ]]; then
  echo "Run this as your normal user (it will ask for sudo), not as root." >&2
  exit 1
fi

# ---------------------------------------------------------------- packages
say "1/7  Packages"
NEEDED=()
command -v bt-agent   >/dev/null || NEEDED+=(bluez-tools)
command -v sdptool    >/dev/null || NEEDED+=(bluez)
command -v nmcli      >/dev/null || NEEDED+=(network-manager)
if ((${#NEEDED[@]})); then
  note "installing: ${NEEDED[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${NEEDED[@]}"
else
  note "already installed"
fi

# --------------------------------------------------------- network manager
say "2/7  NetworkManager"
if systemctl is-active --quiet NetworkManager; then
  note "running"
else
  # Raspberry Pi OS moved to NetworkManager in Bookworm. On an older
  # image dhcpcd owns the interface and the two fight, so this is worth
  # being loud about rather than silently enabling both.
  note "not running — enabling it"
  if systemctl is-enabled --quiet dhcpcd 2>/dev/null; then
    note "WARNING: dhcpcd is enabled too. Two managers on one interface will"
    note "         fight. Consider: sudo systemctl disable --now dhcpcd"
  fi
  sudo systemctl enable --now NetworkManager
fi

# -------------------------------------------------------------- bluetooth
say "3/7  Bluetooth"
# --compat turns the deprecated SDP interface back on, which is what
# sdptool needs to advertise a serial port. Without it a phone can pair
# with the Pi and then find no services on it, which looks like the Pi is
# broken rather than like one missing flag.
DROPIN=/etc/systemd/system/bluetooth.service.d/compat.conf
if [[ ! -f "$DROPIN" ]]; then
  sudo mkdir -p "$(dirname "$DROPIN")"
  BLUETOOTHD="$(systemctl cat bluetooth.service | sed -n 's/^ExecStart=\(.*\)$/\1/p' | head -1)"
  BLUETOOTHD="${BLUETOOTHD%% *}"
  sudo tee "$DROPIN" >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=${BLUETOOTHD:-/usr/libexec/bluetooth/bluetoothd} --compat
EOF
  note "enabled bluetoothd --compat"
else
  note "--compat already set"
fi

# Discoverable forever, pairable forever. The defaults time out after
# three minutes, which is fine for headphones and wrong for a device
# whose whole job is being findable when something has gone wrong.
MAIN=/etc/bluetooth/main.conf
sudo python3 - "$MAIN" <<'PYEOF'
import configparser, sys, pathlib
path = pathlib.Path(sys.argv[1])
parser = configparser.ConfigParser()
parser.optionxform = str
if path.exists():
    parser.read(path)
for section, values in {
    "General": {
        "DiscoverableTimeout": "0",
        "PairableTimeout": "0",
        "AlwaysPairable": "true",
        # 0x100100 = computer / desktop, object transfer. Makes phones
        # show it with a sensible icon rather than "unknown device".
        "Class": "0x100100",
    },
    "Policy": {"AutoEnable": "true"},
}.items():
    if not parser.has_section(section):
        parser.add_section(section)
    for key, value in values.items():
        parser.set(section, key, value)
with path.open("w") as handle:
    parser.write(handle, space_around_delimiters=True)
print("  wrote", path)
PYEOF

sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo hciconfig hci0 up 2>/dev/null || true
sudo bluetoothctl discoverable-timeout 0 >/dev/null 2>&1 || true
sudo bluetoothctl pairable on            >/dev/null 2>&1 || true
sudo bluetoothctl discoverable on        >/dev/null 2>&1 || true
note "adapter: $(hciconfig hci0 name 2>/dev/null | sed -n 's/.*Name: //p' | head -1 || echo unknown)"

# ------------------------------------------------------------------ units
say "4/7  Services"
sudo mkdir -p /etc/saathi
sudo cp "$HERE/systemd/saathi-pair.service" /etc/systemd/system/saathi-pair.service
sudo cp "$HERE/systemd/saathi-console.service" /etc/systemd/system/saathi-console@.service
sudo systemctl daemon-reload
sudo systemctl enable --now saathi-pair.service
sudo systemctl enable --now "saathi-console@$USER_NAME"
note "saathi-console@$USER_NAME enabled — it starts at boot from now on"

# ------------------------------------------------------- saathi at boot
say "5/7  Saathi at boot"
if [[ -f /etc/systemd/system/saathi@.service ]]; then
  sudo systemctl enable "saathi-agent@$USER_NAME" "saathi@$USER_NAME"
  note "Saathi is enabled at boot"
else
  note "Saathi's own units aren't installed yet — run: bash systemd/install.sh"
fi

# ------------------------------------------------------------------ token
say "6/7  The key"
sleep 2
TOKEN="$(sudo cat /etc/saathi/console-token 2>/dev/null || true)"
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(sudo "$HERE/venv/bin/python" -m saathi.console --token 2>/dev/null || true)"
fi

# ------------------------------------------------------------------ done
say "7/7  Done"
ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

  On your phone, right now:

    1. Bluetooth settings -> pair with "$(hostname)"
    2. Install "Serial Bluetooth Terminal" (free, Play Store)
    3. Connect to it, type:  status

  Or over wifi, if it's on one:

    http://${ADDRESS:-<no address yet>}:8765
    http://$(hostname).local:8765

  The web console asks for this key once:

    ${TOKEN:-(run: sudo cat /etc/saathi/console-token)}

  You never need to remember it — ask over Bluetooth any time by typing:  token

  If it ever boots somewhere with no network it knows, it becomes one
  after 90 seconds. Join "Saathi-Setup" and open http://10.42.0.1:8765

  Check on it:   systemctl status saathi-console@$USER_NAME
  Watch it:      journalctl -fu saathi-console@$USER_NAME

EOF
