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


# ---- subprocess cleanup ------------------------------------------------

class FakeProc:
    """Stands in for an arecord/aplay Popen."""

    def __init__(self, dies_on_terminate=True, already_dead=False):
        self.dies_on_terminate = dies_on_terminate
        self.terminated = False
        self.killed = False
        self._dead = already_dead

    def poll(self):
        return 0 if self._dead else None

    def terminate(self):
        self.terminated = True
        if self.dies_on_terminate:
            self._dead = True

    def kill(self):
        self.killed = True
        self._dead = True

    def wait(self, timeout=None):
        import subprocess
        if self._dead:
            return 0
        raise subprocess.TimeoutExpired("arecord", timeout or 0)


def test_stop_terminates_a_live_process():
    from saathi.wake import _stop
    p = FakeProc()
    _stop(p)
    assert p.terminated and not p.killed


def test_stop_kills_one_that_ignores_sigterm():
    """The whole point: a survivor keeps the ALSA device open, and every
    later attempt fails with 'Device or resource busy'."""
    from saathi.wake import _stop
    p = FakeProc(dies_on_terminate=False)
    _stop(p)
    assert p.terminated and p.killed


def test_stop_leaves_an_already_dead_process_alone():
    from saathi.wake import _stop
    p = FakeProc(already_dead=True)
    _stop(p)
    assert not p.terminated and not p.killed


# ---- half duplex --------------------------------------------------------

def test_far_end_is_silent_before_anything_arrives():
    from saathi.device import FarEnd
    assert FarEnd(clock=Clock()).speaking is False


def test_far_end_is_speaking_right_after_a_frame():
    from saathi.device import FarEnd
    c = Clock()
    f = FarEnd(hangover_s=0.4, clock=c)
    f.heard()
    assert f.speaking is True


def test_far_end_goes_quiet_after_the_hangover():
    """The tail covers the speaker's decay and the room's reverb — end it
    too early and the mic catches the last syllable of the agent's own
    reply."""
    from saathi.device import FarEnd
    c = Clock()
    f = FarEnd(hangover_s=0.4, clock=c)
    f.heard()
    c.advance(0.5)
    assert f.speaking is False


def test_far_end_stays_speaking_across_a_gap_between_frames():
    from saathi.device import FarEnd
    c = Clock()
    f = FarEnd(hangover_s=0.4, clock=c)
    f.heard()
    c.advance(0.2)
    f.heard()
    c.advance(0.3)
    assert f.speaking is True


def test_silence_from_the_far_end_does_not_count_as_speaking():
    """LiveKit streams frames continuously, silence included. Treating
    those as the agent talking mutes our mic permanently."""
    assert is_speech(pcm(0)) is False
    assert is_speech(pcm(3)) is False


def test_extend_resets_the_hard_cap_too():
    """Music holding the session open is deliberate, not stuck — and an
    album is longer than the ten-minute cap."""
    c = Clock()
    t = timer(c, max_s=600.0)
    for _ in range(700):
        c.advance(1.0)
        t.extend()
    assert t.expired is False


def test_poke_alone_does_not_reset_the_cap():
    c = Clock()
    t = timer(c, max_s=600.0)
    for _ in range(700):
        c.advance(1.0)
        t.poke()
    assert t.expired is True


# ---- the button --------------------------------------------------------

def gate(clock=None, keys=None, debounce_s=0.6):
    from saathi.button import PressGate
    return PressGate(keys=keys if keys is not None else [], debounce_s=debounce_s,
                     clock=clock or Clock())


def test_a_key_down_is_a_press():
    assert gate().consider("KEY_PLAYPAUSE", down=True) is not None


def test_a_key_up_is_not():
    assert gate().consider("KEY_PLAYPAUSE", down=False) is None


def test_no_configured_keys_means_any_key():
    """Right for a single-button remote: whatever it happens to send is
    the press, and nobody should have to look that up first."""
    assert gate().consider("KEY_ANYTHING", down=True) is not None


def test_a_configured_key_excludes_the_others():
    g = gate(keys=["KEY_PLAYPAUSE"])
    assert g.consider("KEY_VOLUMEUP", down=True) is None
    assert g.consider("KEY_PLAYPAUSE", down=True) is not None


def test_key_matching_ignores_case():
    assert gate(keys=["key_playpause"]).consider("KEY_PLAYPAUSE", down=True) is not None


def test_a_bounce_is_not_a_second_press():
    """Buttons bounce, and BLE remotes often send the press twice."""
    c = Clock()
    g = gate(c)
    assert g.consider("KEY_A", down=True) is not None
    c.advance(0.1)
    assert g.consider("KEY_A", down=True) is None


def test_a_deliberate_second_press_still_counts():
    c = Clock()
    g = gate(c)
    g.consider("KEY_A", down=True)
    c.advance(1.0)
    assert g.consider("KEY_A", down=True) is not None
