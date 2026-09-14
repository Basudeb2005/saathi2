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

import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from saathi.config import (
    BUTTON_DEBOUNCE_S,
    BUTTON_DEVICE,
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
