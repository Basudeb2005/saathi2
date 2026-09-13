#!/usr/bin/env bash
# One command to get Saathi ready on a fresh Pi.
#
#   bash setup.sh
#
# Clones the repo if you're not already in it, makes a venv, installs
# everything, then opens the key-entry prompt. Safe to re-run — each step
# is skipped if it's already done, so this doubles as "fix my install".
set -euo pipefail

REPO_URL="${SAATHI_REPO:-https://github.com/Basudeb2005/saathi2.git}"
DIR="${SAATHI_DIR:-$HOME/saathi2}"

# set -e exits silently on the first failure, which on a long install
# leaves the last screenful of apt output on screen and no indication
# that anything went wrong or what to do next.
trap 'code=$?; [ $code -ne 0 ] && {
  printf "\n\033[31m✗ setup failed (exit %s)\033[0m\n" "$code"
  printf "  Re-running is safe — finished steps are skipped:\n"
  printf "    cd %s && git pull && bash setup.sh\n\n" "${DIR:-~/saathi2}"
}' EXIT

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
dim()  { printf '\033[2m%s\033[0m\n' "$1"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[33m!\033[0m %s\n' "$1"; }

bold "Saathi setup"

# --- system packages ---------------------------------------------------
# mopidy and alsa-utils are apt-only; nothing here is pip-installable.
if command -v apt-get >/dev/null 2>&1; then
  MISSING=()
  for pkg in python3-venv alsa-utils mopidy espeak-ng; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
  done
  if [ ${#MISSING[@]} -gt 0 ]; then
    dim "installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING[@]}"
  fi
  ok "system packages"
else
  warn "not a Debian system — install python3-venv, alsa-utils and mopidy yourself"
fi

# --- the repo ----------------------------------------------------------
if [ -f "saathi/setup.py" ]; then
  DIR="$(pwd)"                      # already inside a clone
elif [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only || warn "couldn't fast-forward; using what's on disk"
else
  git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"
ok "repo at $DIR"

# --- python ------------------------------------------------------------
# Deliberately NOT quiet. This step downloads and builds a few hundred MB
# on a slow ARM board and can take 15 minutes; with no output people
# reasonably conclude it has hung and kill it.
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q --upgrade pip

dim "installing python dependencies — this takes 5-15 minutes on a Pi,"
dim "and pip goes quiet for long stretches while it builds. Let it run."
echo
./venv/bin/pip install --progress-bar on -r requirements.txt
ok "python dependencies"

[ -f contacts.json ] || cp contacts.json.example contacts.json

# Music-by-name. Best-effort on purpose: Mopidy-YouTube tracks a moving
# target and breaks periodically, and radio — which needs nothing — is
# the stable floor. A failure here must not fail the install.
if command -v mopidy >/dev/null 2>&1; then
  sudo pip3 install --break-system-packages -q Mopidy-YouTube 2>/dev/null \
    && ok "mopidy-youtube (songs by name)" \
    || warn "mopidy-youtube didn't install — radio still works, songs by name won't"
fi

# --- mopidy ------------------------------------------------------------
# Installing the package is not enough: the HTTP interface we drive it
# through is off by default, and the service is not enabled. Skipping
# this is why "music never plays" with Mopidy sitting right there.
MOPIDY_CONF=/etc/mopidy/mopidy.conf
if command -v mopidy >/dev/null 2>&1; then
  # Edited with configparser rather than appended to. Mopidy's config is
  # ini, and a second [http] section in one file is a parse error, not a
  # merge — so appending breaks the service on any machine whose conf
  # already has the section, which is most of them.
  sudo cp "$MOPIDY_CONF" "$MOPIDY_CONF.bak.$(date +%s)" 2>/dev/null || true
  sudo python3 - "$MOPIDY_CONF" <<'CONF'
import configparser, sys

path = sys.argv[1]
config = configparser.ConfigParser()
config.read(path)

# Bound to localhost on purpose: the JSON-RPC API has no authentication,
# so it must not be reachable off-box.
if not config.has_section("http"):
    config.add_section("http")
config["http"]["enabled"] = "true"
config["http"]["hostname"] = "127.0.0.1"
config["http"]["port"] = "6680"

# Only declare [youtube] when the extension is actually installed —
# Mopidy refuses to start on config for an extension it doesn't have.
try:
    import mopidy_youtube  # noqa: F401
    if not config.has_section("youtube"):
        config.add_section("youtube")
    config["youtube"]["enabled"] = "true"
except ImportError:
    pass

with open(path, "w") as f:
    config.write(f)
CONF
  ok "configured mopidy"

  sudo systemctl enable --now mopidy >/dev/null 2>&1 || true
  sudo systemctl restart mopidy >/dev/null 2>&1 || true

  for _ in $(seq 1 15); do
    curl -fsS -m 1 -X POST http://localhost:6680/mopidy/rpc \
      -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}' >/dev/null 2>&1 && break
    sleep 1
  done
  if curl -fsS -m 2 -X POST http://localhost:6680/mopidy/rpc \
      -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}' >/dev/null 2>&1; then
    ok "mopidy responding"
  else
    warn "mopidy not responding — see: journalctl -u mopidy -n 30"
  fi
fi

# --- mic check ---------------------------------------------------------
# Worth failing loudly on: no capture device is the single most common
# reason everything else appears broken later.
if command -v arecord >/dev/null 2>&1; then
  if arecord -l 2>/dev/null | grep -q '^card'; then
    ok "microphone detected"
  else
    warn "no capture device found — plug in a USB mic or HAT before testing voice"
  fi
fi

# --- keys --------------------------------------------------------------
echo
./venv/bin/python -m saathi.setup || true

# --- check ---------------------------------------------------------------
echo
./venv/bin/python -m saathi.doctor || true

# --- run on boot ---------------------------------------------------------
echo
read -rp "Start Saathi automatically on boot? [y/N] " REPLY
case "$REPLY" in
  [yY]*) bash systemd/install.sh ;;
  *) dim "skipped — run it yourself with: ./venv/bin/python -m saathi.device" ;;
esac
