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

# One word on the PATH, so none of the above has to be remembered.
sudo ln -sf "$HERE/../scripts/saathi" /usr/local/bin/saathi
sudo chmod +x "$HERE/../scripts/saathi"

cat <<EOF

Installed. From anywhere, now:

  saathi           start it, and say where it is
  saathi talk      talk to it now — hold SPACE
  saathi status    what's running
  saathi logs      follow the log
  saathi doctor    check every moving part
  saathi help      the rest

EOF
