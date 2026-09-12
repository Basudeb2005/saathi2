import pytest

from saathi.wake import WakeGate


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def gate(clock=None, **thresholds):
    return WakeGate(
        thresholds=thresholds or {"hey saathi": 0.5},
        refractory_s=2.0,
        clock=clock or Clock(),
    )


def test_fires_above_threshold():
    detection = gate().consider({"hey saathi": 0.9})
    assert detection is not None and detection.word == "hey saathi"


def test_silent_below_threshold():
    assert gate().consider({"hey saathi": 0.4}) is None


def test_threshold_is_inclusive():
    assert gate().consider({"hey saathi": 0.5}) is not None


def test_unknown_model_is_ignored():
    assert gate().consider({"hey jarvis": 0.99}) is None


def test_refractory_suppresses_the_same_utterance_twice():
    clock = Clock()
    g = gate(clock)
    assert g.consider({"hey saathi": 0.9}) is not None
    clock.advance(0.08)
    assert g.consider({"hey saathi": 0.95}) is None


def test_fires_again_once_refractory_has_passed():
    clock = Clock()
    g = gate(clock)
    g.consider({"hey saathi": 0.9})
    clock.advance(2.1)
    assert g.consider({"hey saathi": 0.9}) is not None


def test_reset_clears_the_cooldown():
    clock = Clock()
    g = gate(clock)
    g.consider({"hey saathi": 0.9})
    g.reset()
    assert g.consider({"hey saathi": 0.9}) is not None


def test_highest_scorer_wins_when_overlapping_words_both_cross():
    g = gate(None, **{"hey boy": 0.75, "hello boy": 0.75})
    detection = g.consider({"hey boy": 0.80, "hello boy": 0.93})
    assert detection.word == "hello boy"


def test_each_word_keeps_its_own_threshold():
    g = gate(None, **{"hey saathi": 0.5, "hey boy": 0.75})
    # 0.6 clears the permissive word but not the strict one.
    assert g.consider({"hey boy": 0.6}) is None
    assert g.consider({"hey saathi": 0.6}) is not None


def test_weak_wake_words_are_held_to_a_higher_bar():
    from saathi.config import WAKE_THRESHOLDS

    assert WAKE_THRESHOLDS["hey boy"] > WAKE_THRESHOLDS["hey saathi"]
    assert WAKE_THRESHOLDS["hello boy"] > WAKE_THRESHOLDS["hey saathi"]
