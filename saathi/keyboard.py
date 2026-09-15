"""
The spacebar as a push-to-talk button.

Same idea as `saathi.button`, none of the hardware: hold space to talk,
let go to stop. It exists because the wearable is the answer to the real
problem — a speaker that can't hear you over its own music — and you
shouldn't have to wait for an ESP32 to arrive in the post to find out
whether push-to-talk actually feels better to use than a wake word.

It also works over SSH, which evdev does not. `saathi.button` reads the
Pi's own input devices; the keyboard you are typing on is attached to
your laptop, several hundred miles away, and the Pi will never see it.
What the Pi does see is the characters your terminal sends down the ssh
connection, and that is what this reads.

The awkward part, and the reason there is a class here rather than four
lines of `input()`:

**A terminal has no key-up.** Press and hold a key and you get the
character once, then nothing for the system's repeat delay (about half a
second on macOS), then a fast stream of repeats — and on release, no
event whatsoever. So "held" cannot be observed, only inferred: a key is
held if one arrived recently, where "recently" has to be wider than that
initial gap or the microphone shuts in the pause before the repeats
start. The cost is a tail of the same length after you genuinely let go.
That tail is harmless and mildly useful: it is the difference between
cutting someone off on their last word and not.

If key repeat is switched off at the OS level, "hold" degrades to a
single 1-second window, which is useless. PTT_STYLE=toggle is the escape
hatch: tap to open, tap to close, no repeat needed.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

from saathi.config import (
    PTT_RELEASE_S,
    PTT_STYLE,
    PTT_TOGGLE_DEBOUNCE_S,
)
from saathi.logging_setup import get_logger

log = get_logger("keyboard")

SPACE = " "
# What ends the session. 'q' because it is what everything else uses, and
# escape because it is what people press when they want out.
QUIT_KEYS = ("q", "Q", "\x1b")
# Enter closes the microphone immediately instead of waiting out the
# release window — for when you have finished talking and want the reply
# now rather than a second from now.
END_TURN_KEYS = ("\r", "\n")


class KeyboardError(Exception):
    """No terminal to read from."""


class HoldTracker:
    """Characters in, a held/not-held signal out.

    Pure and clock-injected: every decision this makes depends on timing,
    and timing is the one thing you cannot test by pressing a key.
    """

    def __init__(
        self,
        release_after_s: float = PTT_RELEASE_S,
        style: str = PTT_STYLE,
        debounce_s: float = PTT_TOGGLE_DEBOUNCE_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        if style not in ("hold", "toggle"):
            raise KeyboardError(f"PTT_STYLE must be hold or toggle — got {style!r}")
        self.release_after_s = release_after_s
        self.style = style
        self.debounce_s = debounce_s
        self._clock = clock
        self._last_press: Optional[float] = None
        self._toggled_on = False
        self.quit_requested = False

    def feed(self, char: str) -> Optional[str]:
        """One character from the terminal. Returns what it meant, if
        anything: "press", "end", "quit", or None for a key we ignore."""
        if char in QUIT_KEYS:
            self.quit_requested = True
            return "quit"

        if char in END_TURN_KEYS:
            self.release()
            return "end"

        if char != SPACE:
            return None

        now = self._clock()
        if self.style == "toggle":
            # Key repeat would otherwise flip this thirty times a second,
            # so a repeat inside the debounce window is not a second tap.
            if self._last_press is not None and (now - self._last_press) < self.debounce_s:
                self._last_press = now
                return None
            self._toggled_on = not self._toggled_on
            self._last_press = now
            log.info("Microphone %s", "open" if self._toggled_on else "closed")
            return "press"

        self._last_press = now
        return "press"

    def release(self) -> None:
        """Close the microphone now, without waiting out the window."""
        self._last_press = None
        self._toggled_on = False

    def is_open(self, now: Optional[float] = None) -> bool:
        if self.style == "toggle":
            return self._toggled_on
        if self._last_press is None:
            return False
        now = self._clock() if now is None else now
        return (now - self._last_press) < self.release_after_s

    def take_quit(self) -> bool:
        """True once per quit, then False. Otherwise one press of `q`
        would end every session that followed it, forever."""
        was, self.quit_requested = self.quit_requested, False
        return was


class Keyboard:
    """A HoldTracker fed by the real terminal, on its own thread.

    The thread is where the awkwardness lives. Reading a character blocks
    until one arrives, and the audio loop cannot afford to block on
    anything — so the reading happens over here and the loop only ever
    looks at a boolean.
    """

    def __init__(self, tracker: Optional[HoldTracker] = None, stream=None):
        self.tracker = tracker or HoldTracker()
        self._stream = stream or sys.stdin
        self._pressed = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._saved_termios = None

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> "Keyboard":
        if self._thread is not None:
            return self

        if not (hasattr(self._stream, "isatty") and self._stream.isatty()):
            raise KeyboardError(
                "WAKE_MODE=space needs a terminal to read from, and this process "
                "hasn't got one. Run it in the foreground — `python -m saathi.device` "
                "— rather than under systemd, where stdin is /dev/null."
            )

        self._enter_cbreak()
        # Daemon: the read blocks in the kernel and there is no portable
        # way to interrupt it. Letting the process exit out from under it
        # is the honest option, and this is a testing mode.
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        self._restore_termios()

    def _enter_cbreak(self) -> None:
        """Characters as they are typed, rather than a line at a time.

        cbreak rather than raw: raw also turns off signal generation, and
        a push-to-talk demo you cannot ctrl-c out of is a demo you have
        to reboot the Pi to escape.
        """
        try:
            import termios
            import tty
        except ImportError as e:      # pragma: no cover - Windows only
            raise KeyboardError("No terminal control available on this platform") from e

        fd = self._stream.fileno()
        self._saved_termios = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    def _restore_termios(self) -> None:
        """Put the terminal back. Skipping this leaves the shell with no
        echo after we exit, which looks like the ssh session has hung."""
        if self._saved_termios is None:
            return
        try:
            import termios

            termios.tcsetattr(self._stream.fileno(), termios.TCSADRAIN, self._saved_termios)
        except Exception:
            pass
        self._saved_termios = None

    def _read_loop(self) -> None:
        import os

        fd = self._stream.fileno()
        while not self._stopping.is_set():
            try:
                chunk = os.read(fd, 64)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            for char in chunk.decode("utf-8", "ignore"):
                action = self.tracker.feed(char)
                if action in ("press", "quit"):
                    self._pressed.set()

    # ---- what the audio loop asks -----------------------------------

    @property
    def open(self) -> bool:
        return self.tracker.is_open()

    @property
    def quit_requested(self) -> bool:
        return self.tracker.quit_requested

    def take_quit(self) -> bool:
        return self.tracker.take_quit()

    def release(self) -> None:
        self.tracker.release()
        self._pressed.clear()

    def wait_for_press(self, poll_s: float = 0.25) -> bool:
        """Block until space is pressed. Cleared first, so a press left
        over from the last conversation doesn't start the next one."""
        self._pressed.clear()
        self.tracker.take_quit()
        while not self._stopping.is_set():
            if self._pressed.wait(poll_s):
                return True
        return False


def main() -> int:
    """`python -m saathi.keyboard` — hold space and watch it open and close.

    Worth ten seconds before wiring it to audio: if the bar below doesn't
    stay solid while you hold space, key repeat is off and you want
    PTT_STYLE=toggle.
    """
    from saathi.logging_setup import quiet_console

    quiet_console()
    try:
        kb = Keyboard().start()
    except KeyboardError as e:
        print(f"error: {e}")
        return 1

    print(f"Hold SPACE (style={kb.tracker.style}). q to quit.\n")
    try:
        while not kb.quit_requested:
            bar = "#" * 30 if kb.open else "-" * 30
            state = "OPEN " if kb.open else "muted"
            print(f"\r  mic {state} [{bar}]", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        kb.stop()
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
