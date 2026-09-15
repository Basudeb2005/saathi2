"""
One shared secret, generated on the Pi, never typed by a human.

The console can join wifi networks, restart services and — if you leave
`CONSOLE_SHELL` on — run commands as root. It listens on the whole LAN,
because a phone has to reach it. So it needs a gate, and the gate has to
work with no screen attached to the thing being gated.

The answer is a random token generated on first boot and handed out over
Bluetooth, which you can only pair with by being in the same room. You
never read it, type it or choose it: the phone asks for it over Bluetooth
once and keeps it.

There is deliberately no default, no fallback and no "leave blank to
disable". A device that ships with a known password is a device that
ships with no password.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from saathi.config import CONSOLE_TOKEN_FILE
from saathi.logging_setup import get_logger

log = get_logger("console.auth")

TOKEN_BYTES = 24        # 32 characters of base64url


def token_path() -> Path:
    return Path(os.path.expanduser(CONSOLE_TOKEN_FILE))


def load_or_create() -> str:
    """The token, making one the first time.

    Written 0600 and its directory 0700 — on a Pi that is often the only
    file permission standing between this and the rest of the network.
    """
    path = token_path()
    if path.exists():
        existing = path.read_text().strip()
        if existing:
            return existing
        log.warning("%s was empty — generating a new token", path)

    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(token + "\n")
    path.chmod(0o600)
    log.info("Wrote a new console token to %s", path)
    return token


def matches(supplied: str, token: str) -> bool:
    """Constant-time, and false for anything empty.

    compare_digest rather than == because the timing of a string compare
    leaks the length of the common prefix, and this is reachable by
    anything on the wifi.
    """
    if not supplied or not token:
        return False
    return hmac.compare_digest(supplied, token)
