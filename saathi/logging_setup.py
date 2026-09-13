"""
One shared logging setup for the whole app.

Every outbound call to Mopidy, Radio Browser and LiveKit SIP logs its
arguments and result here — that's the trail you want when music "just
doesn't play" or a call never connects on hardware you're not sitting in
front of. Logs go to both the console and logs/saathi.log (rotating, so
it doesn't grow unbounded on a Pi's SD card).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from saathi.config import LOG_DIR, LOG_LEVEL

_configured = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the shared 'saathi' namespace, configuring
    the shared handlers on first use."""
    global _configured
    root = logging.getLogger("saathi")

    if not _configured:
        root.setLevel(LOG_LEVEL)

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

        file_handler = RotatingFileHandler(
            LOG_DIR / "saathi.log", maxBytes=2_000_000, backupCount=5
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

        _configured = True

    return root.getChild(name)


def quiet_console() -> None:
    """Drop the console handler to warnings only, keeping the log file at
    full detail.

    Interactive commands print their own, better-worded output; an INFO
    line about loading contacts.json interleaved with a setup prompt is
    just noise. The file still records everything, which is what you
    actually want when something failed an hour ago.
    """
    get_logger("")  # ensure handlers exist
    for handler in logging.getLogger("saathi").handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            handler.setLevel(logging.WARNING)
