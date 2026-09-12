import numpy as np
import pytest

from saathi.wake import (
    OpenWakeWordEngine,
    PorcupineEngine,
    WakeGate,
    WakeWordError,
    build_engine,
)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def gate(clock=None, **thresholds):
    return WakeGate(
        thresholds=thresholds or {"hey_jarvis": 0.5},
        refractory_s=2.0,
        clock=clock or Clock(),
    )


# ---- the decision logic ------------------------------------------------

def test_fires_above_threshold():
    detection = gate().consider({"hey_jarvis": 0.9})
    assert detection is not None and detection.word == "hey_jarvis"


def test_silent_below_threshold():
    assert gate().consider({"hey_jarvis": 0.4}) is None


def test_threshold_is_inclusive():
    assert gate().consider({"hey_jarvis": 0.5}) is not None


def test_unknown_model_is_ignored():
    assert gate().consider({"alexa": 0.99}) is None


def test_refractory_suppresses_the_same_utterance_twice():
    clock = Clock()
    g = gate(clock)
    assert g.consider({"hey_jarvis": 0.9}) is not None
    clock.advance(0.08)
    assert g.consider({"hey_jarvis": 0.95}) is None


def test_fires_again_once_refractory_has_passed():
    clock = Clock()
    g = gate(clock)
    g.consider({"hey_jarvis": 0.9})
    clock.advance(2.1)
    assert g.consider({"hey_jarvis": 0.9}) is not None


def test_reset_clears_the_cooldown():
    clock = Clock()
    g = gate(clock)
    g.consider({"hey_jarvis": 0.9})
    g.reset()
    assert g.consider({"hey_jarvis": 0.9}) is not None


def test_highest_scorer_wins_when_overlapping_words_both_cross():
    g = gate(None, **{"hey_jarvis": 0.5, "hey_mycroft": 0.5})
    assert g.consider({"hey_jarvis": 0.80, "hey_mycroft": 0.93}).word == "hey_mycroft"


def test_each_word_keeps_its_own_threshold():
    g = gate(None, **{"hey_jarvis": 0.5, "alexa": 0.9})
    assert g.consider({"alexa": 0.6}) is None
    assert g.consider({"hey_jarvis": 0.6}) is not None


# ---- openWakeWord engine ------------------------------------------------

class FakeOWWModel:
    def __init__(self, scores):
        self.scores = scores
        self.frames = 0

    def predict(self, frame):
        self.frames += 1
        return self.scores


def test_oww_engine_reports_a_detection():
    engine = OpenWakeWordEngine(words=["hey_jarvis"], model=FakeOWWModel({"hey_jarvis": 0.9}))
    assert engine.process(np.zeros(1280, dtype=np.int16)).word == "hey_jarvis"


def test_oww_engine_stays_quiet_below_threshold():
    engine = OpenWakeWordEngine(words=["hey_jarvis"], model=FakeOWWModel({"hey_jarvis": 0.1}))
    assert engine.process(np.zeros(1280, dtype=np.int16)) is None


def test_oww_engine_applies_the_cooldown():
    engine = OpenWakeWordEngine(words=["hey_jarvis"], model=FakeOWWModel({"hey_jarvis": 0.9}))
    frame = np.zeros(1280, dtype=np.int16)
    assert engine.process(frame) is not None
    assert engine.process(frame) is None


def test_oww_frame_size_is_what_the_model_expects():
    engine = OpenWakeWordEngine(words=["hey_jarvis"], model=FakeOWWModel({}))
    assert engine.frame_samples == 1280


# ---- porcupine engine ---------------------------------------------------

class FakePorcupine:
    frame_length = 512

    def __init__(self, results):
        self.results = list(results)

    def process(self, frame):
        return self.results.pop(0) if self.results else -1


def test_porcupine_negative_index_means_nothing_heard():
    engine = PorcupineEngine(handle=FakePorcupine([-1]))
    assert engine.process(np.zeros(512, dtype=np.int16)) is None


def test_porcupine_reports_the_matched_keyword():
    engine = PorcupineEngine(handle=FakePorcupine([0]))
    engine._labels = ["hey_saathi", "jarvis"]
    assert engine.process(np.zeros(512, dtype=np.int16)).word == "hey_saathi"


def test_porcupine_frame_size_comes_from_the_handle():
    assert PorcupineEngine(handle=FakePorcupine([])).frame_samples == 512


# ---- engine selection ---------------------------------------------------

def test_unknown_engine_name_is_rejected():
    with pytest.raises(WakeWordError, match="openwakeword"):
        build_engine("whisper")


def test_porcupine_without_a_key_explains_the_free_alternative(monkeypatch):
    monkeypatch.setattr("saathi.wake.PORCUPINE_ACCESS_KEY", None)
    with pytest.raises(WakeWordError, match="openwakeword"):
        build_engine("porcupine")
