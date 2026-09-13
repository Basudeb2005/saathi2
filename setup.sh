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
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
ok "python dependencies"

[ -f contacts.json ] || cp contacts.json.example contacts.json

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
