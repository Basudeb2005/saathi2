#!/usr/bin/env bash
# Install and enable both services for the current user.
set -euo pipefail

USER_NAME="$(id -un)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for unit in saathi-agent saathi; do
  sudo cp "$HERE/$unit.service" "/etc/systemd/system/$unit@.service"
done

sudo systemctl daemon-reload
sudo systemctl enable --now "saathi-agent@$USER_NAME" "saathi@$USER_NAME"

echo
echo "Installed. Useful commands:"
echo "  systemctl status  saathi@$USER_NAME"
echo "  journalctl -fu    saathi@$USER_NAME"
echo "  sudo systemctl restart saathi@$USER_NAME"
