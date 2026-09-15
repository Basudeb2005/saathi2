"""
Recording and playing, without caring which machine this is.

Everything here exists so the conversation can be worked on somewhere
with a fast edit-run loop instead of over a network to a Pi that has to
be found first. The interesting half of this project — the prompt, the
turn shape, the tools, the latency — has nothing to do with the Pi. It
was pinned to one only because `arecord` and `aplay` were typed directly
into four files.

Two backends, one shape. ALSA on Linux; sox (or ffmpeg) on a Mac. Both
speak raw signed 16-bit PCM on a pipe, which is the entire contract: the
rest of the code reads bytes from stdout and writes bytes to stdin and
has no idea which one it got.

What does NOT become portable, and shouldn't:

  * the button (evdev reads Linux input devices)
  * the console (RFCOMM sockets, nmcli, systemd)
  * knowing who holds the microphone (/proc)

Those are all about running on a device in someone's front room, and
there is nothing to develop about them on a laptop. The conversation is
the part worth iterating on, and it is now the part that runs anywhere.
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from typing import List, Optional

from saathi.logging_setup import get_logger

log = get_logger("audio")


class AudioError(Exception):
    """No way to record or play on this machine."""


@dataclass
class Backend:
    name: str
    record: str          # the executable that captures
    play: str            # the executable that plays
    install: str         # how to get them


ALSA = Backend("alsa", "arecord", "aplay", "sudo apt install alsa-utils")
SOX = Backend("sox", "rec", "play", "brew install sox")
FFMPEG = Backend("ffmpeg", "ffmpeg", "ffmpeg", "brew install ffmpeg")


def detect(system: Optional[str] = None, which=shutil.which) -> Optional[Backend]:
    """The best available backend, or None.

    Ordered by preference per platform rather than by what happens to be
    installed: a Mac with both sox and an alsa-utils from somewhere
    should still use sox.
    """
    system = system or platform.system()
    order = (SOX, FFMPEG, ALSA) if system == "Darwin" else (ALSA, SOX, FFMPEG)
    for backend in order:
        if which(backend.record) and which(backend.play):
            return backend
    return None


def require(system: Optional[str] = None, which=shutil.which) -> Backend:
    found = detect(system, which)
    if found:
        return found
    system = system or platform.system()
    hint = SOX.install if system == "Darwin" else ALSA.install
    raise AudioError(
        f"No audio tools on this machine ({system}). Install them with: {hint}"
    )


# ---- command construction (pure, tested) --------------------------------

def capture_command(device: Optional[str], rate: int, backend: Backend) -> List[str]:
    """Record mono 16-bit PCM at `rate` to stdout."""
    if backend is ALSA:
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-t", "raw"]
        return cmd + (["-D", device] if device else [])

    if backend is SOX:
        # sox picks its input from the AUDIODEV environment variable
        # rather than a flag, which `capture_env` sets. The trailing "-"
        # is the output file, meaning stdout.
        return ["rec", "-q", "-t", "raw", "-b", "16", "-e", "signed-integer",
                "-r", str(rate), "-c", "1", "-"]

    return ["ffmpeg", "-loglevel", "quiet", "-f", "avfoundation",
            "-i", device or ":default", "-ar", str(rate), "-ac", "1",
            "-f", "s16le", "-"]


def playback_command(device: Optional[str], rate: int, channels: int,
                     backend: Backend) -> List[str]:
    """Play raw 16-bit PCM arriving on stdin."""
    if backend is ALSA:
        cmd = ["aplay", "-q", "-f", "S16_LE", "-r", str(rate), "-c", str(channels)]
        return cmd + (["-D", device] if device else [])

    if backend is SOX:
        return ["play", "-q", "-t", "raw", "-b", "16", "-e", "signed-integer",
                "-r", str(rate), "-c", str(channels), "-"]

    return ["ffmpeg", "-loglevel", "quiet", "-f", "s16le", "-ar", str(rate),
            "-ac", str(channels), "-i", "-", "-f", "audiotoolbox", "-"]


def device_env(device: Optional[str], backend: Backend) -> dict:
    """Extra environment the command needs to find the right device.

    ALSA and ffmpeg take one as an argument; sox takes it as AUDIODEV,
    which is why this exists at all rather than being another flag.
    """
    if backend is SOX and device:
        return {**os.environ, "AUDIODEV": device}
    return dict(os.environ)


def describe() -> str:
    found = detect()
    if not found:
        return f"no audio backend ({platform.system()})"
    return f"{found.name} ({found.record} / {found.play})"
