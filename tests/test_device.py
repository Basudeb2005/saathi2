import struct

import pytest

from saathi.device import IdleTimer, is_speech


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def pcm(amplitude: int, samples: int = 160) -> bytes:
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


# ---- speech detection ---------------------------------------------------

def test_silence_is_not_speech():
    assert is_speech(pcm(0)) is False


def test_loud_audio_is_speech():
    assert is_speech(pcm(5000)) is True


def test_empty_frame_is_not_speech():
    assert is_speech(b"") is False


def test_quiet_room_noise_is_not_speech():
    assert is_speech(pcm(100)) is False


def test_threshold_is_adjustable():
    assert is_speech(pcm(100), threshold=50) is True


# ---- idle timer ---------------------------------------------------------

def timer(clock, timeout_s=12.0, max_s=600.0):
    return IdleTimer(timeout_s=timeout_s, max_s=max_s, clock=clock)


def test_fresh_timer_has_not_expired():
    assert timer(Clock()).expired is False


def test_expires_after_the_quiet_period():
    c = Clock()
    t = timer(c)
    c.advance(12.1)
    assert t.expired is True


def test_activity_resets_the_countdown():
    c = Clock()
    t = timer(c)
    c.advance(11.0)
    t.poke()
    c.advance(11.0)
    assert t.expired is False


def test_a_long_conversation_still_hits_the_hard_cap():
    c = Clock()
    t = timer(c, max_s=600.0)
    # Someone talking non-stop: activity every second, so the idle timer
    # would never fire on its own.
    for _ in range(700):
        c.advance(1.0)
        t.poke()
    assert t.expired is True


def test_expiry_is_inclusive_at_the_boundary():
    c = Clock()
    t = timer(c, timeout_s=12.0)
    c.advance(12.0)
    assert t.expired is True
