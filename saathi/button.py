"""
A physical button as the way in.

The wake word has to survive a speaker playing music into the microphone
a foot away, with no echo cancellation. A button doesn't. It also removes
the thing a care facility will ask about first — nothing listens until
someone asks it to.

Any device that presents as a keyboard works: a five-pound Bluetooth
shutter remote, or an ESP32 running the sketch in firmware/. Both arrive
through evdev as key events, so nothing here knows the difference, and a
change of hardware costs no code.

Matched by name rather than /dev/input/eventN — that number changes every
time the device reconnects, which for something worn on a wrist is
several times a day.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from saathi.config import (
    BUTTON_DEBOUNCE_S,
    BUTTON_DEVICE,
    BUTTON_HANGOVER_S,
    BUTTON_KEYS,
    BUTTON_NAME,
)
from saathi.logging_setup import get_logger

log = get_logger("button")


class ButtonError(Exception):
    """No button found, or evdev isn't available."""


@dataclass
class Press:
    key: str
    at: float = field(default_factory=time.monotonic)


class PressGate:
    """Decides whether a key event counts as a press.

    Separated from evdev so the awkward parts — which keys count, and how
    fast is too fast — are testable without a Bluetooth device in hand.
    """

    def __init__(
        self,
        keys: Optional[List[str]] = None,
        debounce_s: float = BUTTON_DEBOUNCE_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        # Empty means any key. Right for a single-button remote, where
        # whatever it happens to send is the press.
        self.keys = [k.upper() for k in (keys if keys is not None else BUTTON_KEYS)]
        self.debounce_s = debounce_s
        self._clock = clock
        self._last: Optional[float] = None

    def consider(self, key: str, down: bool) -> Optional[Press]:
        """A Press on a key-down that isn't a bounce."""
        if not down:
            return None
        if self.keys and key.upper() not in self.keys:
            return None

        now = self._clock()
        if self._last is not None and (now - self._last) < self.debounce_s:
            return None

        self._last = now
        log.info("Button pressed (%s)", key)
        return Press(key=key, at=now)


def find_device(name: str = "", path: str = ""):
    """The input device to listen on.

    Prefers an explicit path, then a name match, then anything that looks
    like a keyboard. That last fallback is what makes a brand-new remote
    work without configuring anything.
    """
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError as e:
        raise ButtonError(
            "evdev isn't installed — sudo apt install python3-evdev, "
            "or pip install evdev"
        ) from e

    if path:
        try:
            return InputDevice(path)
        except Exception as e:
            raise ButtonError(f"Couldn't open {path}: {e}") from e

    candidates = []
    for dev_path in list_devices():
        try:
            device = InputDevice(dev_path)
        except Exception:
            continue

        if name and name.lower() not in device.name.lower():
            continue

        # Anything that can emit key events. A shutter remote reports
        # itself as a keyboard, and so does the ESP32 sketch.
        if ecodes.EV_KEY in device.capabilities():
            candidates.append(device)

    if not candidates:
        known = ", ".join(sorted({InputDevice(p).name for p in list_devices()})) or "(none)"
        raise ButtonError(
            f"No button found{f' matching {name!r}' if name else ''}. "
            f"Input devices present: {known}. Pair it, then set BUTTON_NAME."
        )

    chosen = candidates[0]
    log.info("Button: %s (%s)", chosen.name, chosen.path)
    return chosen


def presses(device=None, gate: Optional[PressGate] = None) -> Iterator[Press]:
    """Yield a Press every time the button is pressed. Blocks between."""
    from evdev import categorize, ecodes

    device = device or find_device(BUTTON_NAME, BUTTON_DEVICE)
    gate = gate or PressGate()

    for event in device.read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        key_event = categorize(event)
        keycode = key_event.keycode
        if isinstance(keycode, list):
            # evdev reports aliased codes as a list; any name identifies it.
            keycode = keycode[0]
        press = gate.consider(str(keycode), key_event.keystate == key_event.key_down)
        if press:
            yield press


def wait_for_press(device=None, gate: Optional[PressGate] = None) -> bool:
    """Block until one press. False if the device went away — unplugged,
    out of range, or a flat battery on someone's wrist."""
    try:
        for _ in presses(device, gate):
            return True
    except ButtonError:
        raise
    except Exception as e:
        log.warning("Button device stopped: %s", e)
    return False


# ---- push to talk -------------------------------------------------------

class HoldState:
    """Held or not, from key-down and key-up.

    The terminal version of this (`saathi.keyboard`) has to guess, because
    a tty reports no key-up at all and "held" has to be inferred from
    auto-repeat. evdev reports both edges exactly, so this is the honest
    version — and the reason a USB keyboard plugged into the Pi is a
    better push-to-talk button than the same keyboard over ssh.

    The only softness is the hangover: the microphone stays open a
    fraction after release, because people let go of a button on the last
    syllable and clipping it costs you the word.
    """

    KEY_UP, KEY_DOWN, KEY_HOLD = 0, 1, 2

    def __init__(
        self,
        keys: Optional[List[str]] = None,
        hangover_s: float = BUTTON_HANGOVER_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.keys = [k.upper() for k in (keys if keys is not None else BUTTON_KEYS)]
        self.hangover_s = hangover_s
        self._clock = clock
        self._down = False
        self._released_at: Optional[float] = None

    def _ours(self, key: str) -> bool:
        return not self.keys or key.upper() in self.keys

    def consider(self, key: str, keystate: int) -> Optional[str]:
        """One evdev key event. Returns "down", "up" or None."""
        if not self._ours(key):
            return None
        if keystate == self.KEY_DOWN:
            self._down = True
            self._released_at = None
            return "down"
        if keystate == self.KEY_UP:
            if not self._down:
                return None
            self._down = False
            self._released_at = self._clock()
            return "up"
        return None          # KEY_HOLD — autorepeat, nothing new

    @property
    def open(self) -> bool:
        if self._down:
            return True
        if self._released_at is None:
            return False
        return (self._clock() - self._released_at) < self.hangover_s

    def release(self) -> None:
        self._down = False
        self._released_at = None


class PushToTalk:
    """A held key on a real keyboard, read on its own thread.

    Deliberately the same shape as `saathi.keyboard.Keyboard` — `open`,
    `release()`, `take_quit()`, `wait_for_press()` — so `device.py` holds
    one of these without caring whether the key is on a wearable, a USB
    keyboard plugged into the Pi, or a laptop three hundred miles away
    over ssh.

    This is the one that works headless: evdev reads the Pi's own input
    devices, so it needs no terminal, no monitor and no shell, and runs
    perfectly happily under systemd at boot.
    """

    # Ends the conversation rather than talking. Escape because it is what
    # people press to get out of things, and a keyboard has one.
    QUIT_KEYS = ("KEY_ESC", "KEY_Q")

    def __init__(self, device=None, state: Optional[HoldState] = None):
        self.state = state or HoldState()
        self._device = device
        self._pressed = threading.Event()
        self._quit = False
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "PushToTalk":
        if self._thread is None:
            self._device = self._device or find_device(BUTTON_NAME, BUTTON_DEVICE)
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
        return self

    def _read_loop(self) -> None:
        from evdev import categorize, ecodes

        try:
            for event in self._device.read_loop():
                if self._stopping.is_set():
                    return
                if event.type != ecodes.EV_KEY:
                    continue
                key_event = categorize(event)
                keycode = key_event.keycode
                if isinstance(keycode, list):
                    keycode = keycode[0]
                keycode = str(keycode)

                if keycode.upper() in self.QUIT_KEYS and key_event.keystate == 1:
                    self._quit = True
                    self._pressed.set()
                    continue

                if self.state.consider(keycode, key_event.keystate) == "down":
                    self._pressed.set()
        except Exception as e:
            # A wearable goes out of range and a USB keyboard gets
            # unplugged. Neither should take the whole box down — the
            # session ends, and run_forever tries to find it again.
            log.warning("Button device stopped: %s", e)

    # ---- what the audio loop asks ---------------------------------------

    @property
    def open(self) -> bool:
        return self.state.open

    @property
    def quit_requested(self) -> bool:
        return self._quit

    def take_quit(self) -> bool:
        was, self._quit = self._quit, False
        return was

    def release(self) -> None:
        self.state.release()
        self._pressed.clear()

    def stop(self) -> None:
        self._stopping.set()

    def wait_for_press(self, poll_s: float = 0.25) -> bool:
        self._pressed.clear()
        self._quit = False
        while not self._stopping.is_set():
            if self._pressed.wait(poll_s):
                return True
        return False


def main() -> int:
    """`python -m saathi.button` — print presses.

    The first thing to run after pairing, and the only way to find out
    what a remote actually sends before wiring it to anything."""
    from saathi.logging_setup import quiet_console

    quiet_console()
    try:
        device = find_device(BUTTON_NAME, BUTTON_DEVICE)
    except ButtonError as e:
        print(f"error: {e}")
        return 1

    print(f"Listening on {device.name}. Press it. (ctrl-c to stop)")
    try:
        for press in presses(device):
            print(f"  press: {press.key}")
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
