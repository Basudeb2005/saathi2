"""Push-to-talk from evdev — the one that works with no terminal.

The terminal version (`saathi.keyboard`) has to infer "held" from key
repeat because a tty reports no key-up. evdev reports both edges, so this
is the exact version, and it's what runs under systemd on a headless Pi
with a USB keyboard or a wearable plugged in.
"""
import pytest

from saathi.button import HoldState, PressGate, PushToTalk

DOWN, UP, REPEAT = HoldState.KEY_DOWN, HoldState.KEY_UP, HoldState.KEY_HOLD


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def state(clock, keys=None, hangover_s=0.35):
    return HoldState(keys=keys if keys is not None else [], hangover_s=hangover_s, clock=clock)


# ---- hold ---------------------------------------------------------------

def test_starts_closed():
    assert state(Clock()).open is False


def test_key_down_opens_it():
    s = state(Clock())
    assert s.consider("KEY_SPACE", DOWN) == "down"
    assert s.open is True


def test_it_stays_open_however_long_you_hold():
    """No inference, no timeout — this is the whole advantage over the
    terminal version."""
    clock = Clock()
    s = state(clock)
    s.consider("KEY_SPACE", DOWN)
    clock.advance(600)
    assert s.open is True


def test_autorepeat_changes_nothing():
    s = state(Clock())
    s.consider("KEY_SPACE", DOWN)
    assert s.consider("KEY_SPACE", REPEAT) is None
    assert s.open is True


def test_key_up_closes_it_after_the_hangover():
    """People let go on the last syllable, so the tail is the word."""
    clock = Clock()
    s = state(clock, hangover_s=0.35)
    s.consider("KEY_SPACE", DOWN)
    assert s.consider("KEY_SPACE", UP) == "up"
    assert s.open is True
    clock.advance(0.34)
    assert s.open is True
    clock.advance(0.02)
    assert s.open is False


def test_a_key_up_we_never_saw_go_down_is_ignored():
    """Which is what arrives when the process starts with a key already
    held, or a device reconnects mid-press."""
    s = state(Clock())
    assert s.consider("KEY_SPACE", UP) is None
    assert s.open is False


def test_release_closes_it_immediately():
    s = state(Clock())
    s.consider("KEY_SPACE", DOWN)
    s.release()
    assert s.open is False


# ---- which key ----------------------------------------------------------

def test_no_keys_configured_means_any_key():
    """Right for a one-button remote, where whatever it sends is the press."""
    s = state(Clock(), keys=[])
    s.consider("KEY_PLAYPAUSE", DOWN)
    assert s.open is True


def test_a_configured_key_ignores_the_rest():
    """Wrong for a remote, essential for a keyboard with a hundred of them."""
    s = state(Clock(), keys=["KEY_SPACE"])
    s.consider("KEY_A", DOWN)
    assert s.open is False
    s.consider("KEY_SPACE", DOWN)
    assert s.open is True


def test_key_names_are_matched_case_insensitively():
    s = state(Clock(), keys=["key_space"])
    s.consider("KEY_SPACE", DOWN)
    assert s.open is True


# ---- the handle device.py holds ----------------------------------------

def test_push_to_talk_has_the_same_shape_as_the_keyboard():
    """device.py holds one of these without knowing whether the key is on
    a wrist, on a USB keyboard, or on a laptop over ssh — so the two have
    to answer the same questions."""
    from saathi.keyboard import Keyboard

    for name in ("open", "quit_requested", "take_quit", "release", "wait_for_press", "stop"):
        assert hasattr(PushToTalk, name), name
        assert hasattr(Keyboard, name), name


def test_quit_is_consumed_once():
    ptt = PushToTalk(device=object())
    ptt._quit = True
    assert ptt.take_quit() is True
    assert ptt.take_quit() is False


def test_press_gate_still_debounces():
    """The press-to-start path is unchanged — a BLE remote sends its press
    twice and a button bounces."""
    clock = Clock()
    gate = PressGate(keys=[], debounce_s=0.6, clock=clock)
    assert gate.consider("KEY_A", True) is not None
    assert gate.consider("KEY_A", True) is None
    clock.advance(0.7)
    assert gate.consider("KEY_A", True) is not None
