import pytest

from saathi.keyboard import HoldTracker, Keyboard, KeyboardError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def hold_tracker(clock, **kw):
    kw.setdefault("release_after_s", 1.0)
    kw.setdefault("style", "hold")
    return HoldTracker(clock=clock, **kw)


# ---- hold ---------------------------------------------------------------

def test_starts_closed():
    assert hold_tracker(Clock()).is_open() is False


def test_space_opens_it():
    t = hold_tracker(Clock())
    assert t.feed(" ") == "press"
    assert t.is_open() is True


def test_closes_after_the_release_window():
    clock = Clock()
    t = hold_tracker(clock)
    t.feed(" ")
    clock.advance(0.99)
    assert t.is_open() is True
    clock.advance(0.02)
    assert t.is_open() is False


def test_key_repeat_holds_it_open_past_the_window():
    """The whole point: a terminal sends no key-up, so a held key is only
    visible as a stream of repeats."""
    clock = Clock()
    t = hold_tracker(clock)
    t.feed(" ")
    for _ in range(40):
        clock.advance(0.05)
        t.feed(" ")
    assert t.is_open() is True
    clock.advance(1.1)
    assert t.is_open() is False


def test_the_window_covers_the_initial_repeat_delay():
    """macOS waits about half a second before the repeats start. A window
    narrower than that gap closes the mic mid-word."""
    clock = Clock()
    t = hold_tracker(clock)
    t.feed(" ")
    clock.advance(0.6)          # the gap before auto-repeat kicks in
    assert t.is_open() is True
    t.feed(" ")                 # first repeat
    clock.advance(0.6)
    assert t.is_open() is True


def test_enter_closes_it_immediately():
    clock = Clock()
    t = hold_tracker(clock)
    t.feed(" ")
    assert t.feed("\r") == "end"
    assert t.is_open() is False


def test_other_keys_are_ignored():
    t = hold_tracker(Clock())
    assert t.feed("x") is None
    assert t.is_open() is False


# ---- quit ---------------------------------------------------------------

@pytest.mark.parametrize("key", ["q", "Q", "\x1b"])
def test_quit_keys(key):
    t = hold_tracker(Clock())
    assert t.feed(key) == "quit"
    assert t.quit_requested is True


def test_quit_is_consumed_once():
    """Otherwise one press of q ends every session that follows it."""
    t = hold_tracker(Clock())
    t.feed("q")
    assert t.take_quit() is True
    assert t.take_quit() is False


# ---- toggle -------------------------------------------------------------

def toggle_tracker(clock, **kw):
    kw.setdefault("style", "toggle")
    kw.setdefault("debounce_s", 0.5)
    return HoldTracker(clock=clock, **kw)


def test_toggle_flips_on_and_off():
    clock = Clock()
    t = toggle_tracker(clock)
    t.feed(" ")
    assert t.is_open() is True
    clock.advance(1.0)
    t.feed(" ")
    assert t.is_open() is False


def test_toggle_stays_open_without_repeats():
    clock = Clock()
    t = toggle_tracker(clock)
    t.feed(" ")
    clock.advance(30.0)
    assert t.is_open() is True


def test_toggle_ignores_key_repeat():
    """Without the debounce, auto-repeat flips the mic thirty times a
    second and it is open or shut at random."""
    clock = Clock()
    t = toggle_tracker(clock)
    t.feed(" ")
    for _ in range(30):
        clock.advance(0.03)
        t.feed(" ")
    assert t.is_open() is True


def test_toggle_release_closes_it():
    t = toggle_tracker(Clock())
    t.feed(" ")
    t.release()
    assert t.is_open() is False


# ---- construction -------------------------------------------------------

def test_unknown_style_is_rejected():
    with pytest.raises(KeyboardError):
        HoldTracker(style="wiggle")


class NotATerminal:
    def isatty(self):
        return False


def test_needs_a_terminal():
    """Under systemd stdin is /dev/null, and the failure should say so
    rather than silently never registering a press."""
    with pytest.raises(KeyboardError) as e:
        Keyboard(stream=NotATerminal()).start()
    assert "terminal" in str(e.value)


# ---- the real reader ----------------------------------------------------

def test_reads_from_a_real_terminal():
    """The pure tracker is where the logic lives, but the thread, the
    termios switch and the decode are only exercised by a real tty — and
    that is the half that leaves your shell with no echo when it breaks."""
    pty = pytest.importorskip("pty")
    import os
    import time

    master, slave = pty.openpty()

    class Tty:
        def isatty(self):
            return True

        def fileno(self):
            return slave

    kb = Keyboard(tracker=HoldTracker(release_after_s=0.4), stream=Tty()).start()
    try:
        assert kb.open is False

        os.write(master, b" ")
        time.sleep(0.05)
        assert kb.open is True

        # Key repeat, which is the only evidence a terminal gives that a
        # key is still down.
        for _ in range(8):
            time.sleep(0.05)
            os.write(master, b" ")
        time.sleep(0.05)
        assert kb.open is True

        time.sleep(0.5)
        assert kb.open is False

        os.write(master, b"q")
        time.sleep(0.05)
        assert kb.take_quit() is True
        assert kb.take_quit() is False
    finally:
        kb.stop()
        os.close(master)
        os.close(slave)


def test_wait_for_press_returns_on_a_press():
    pty = pytest.importorskip("pty")
    import os
    import threading
    import time

    master, slave = pty.openpty()

    class Tty:
        def isatty(self):
            return True

        def fileno(self):
            return slave

    kb = Keyboard(stream=Tty()).start()
    try:
        threading.Timer(0.2, lambda: os.write(master, b" ")).start()
        started = time.monotonic()
        assert kb.wait_for_press(poll_s=0.05) is True
        assert time.monotonic() - started < 3.0
    finally:
        kb.stop()
        os.close(master)
        os.close(slave)
