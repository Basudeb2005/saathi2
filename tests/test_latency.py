"""Turning "it's slow" into a number.

The point of this file is the turn boundary. Metrics arrive as four
separate events, slightly out of order, and a turn that ends in a tool
call never produces the one that would normally close it — so the
slowest turns are exactly the ones a naive implementation drops.
"""
from types import SimpleNamespace as Metric

import pytest

from saathi.latency import Latency, Turn


def eou(delay=0.5, transcription=None):
    return Metric(end_of_utterance_delay=delay, transcription_delay=transcription)


def llm(ttft=0.8):
    return Metric(ttft=ttft)


def tts(ttfb=0.7):
    return Metric(ttfb=ttfb)


def stt(duration=1.5, audio_duration=2.0):
    return Metric(duration=duration, audio_duration=audio_duration)


# ---- one turn -----------------------------------------------------------

def test_a_turn_closes_when_it_starts_speaking():
    """Not when the LLM finishes — the wait ends at the first sound."""
    meter = Latency()
    assert meter.collect(eou(0.5, 1.9)) is None
    assert meter.collect(llm(0.8)) is None
    turn = meter.collect(tts(0.7))
    assert turn is not None
    assert turn.total == pytest.approx(3.9)


def test_the_line_names_every_stage():
    meter = Latency()
    meter.collect(eou(0.51, 1.92))
    meter.collect(llm(0.88))
    turn = meter.collect(tts(0.71))
    line = turn.line()
    for piece in ("eou", "stt", "llm", "tts", "0.51", "1.92", "0.88", "0.71"):
        assert piece in line


def test_slowest_names_the_thing_to_fix():
    meter = Latency()
    meter.collect(eou(0.2, 2.4))
    meter.collect(llm(0.5))
    turn = meter.collect(tts(0.4))
    assert "transcrib" in turn.slowest()


def test_slowest_when_the_model_is_the_problem():
    meter = Latency()
    meter.collect(eou(0.2, 0.3))
    meter.collect(llm(2.9))
    turn = meter.collect(tts(0.4))
    assert "model" in turn.slowest()


# ---- turns that never speak --------------------------------------------

def test_a_turn_that_ended_in_a_tool_call_is_still_reported():
    """It never produces a tts metric. Dropping it would hide the
    slowest turns there are — a song lookup is ten seconds."""
    meter = Latency()
    meter.collect(eou(0.5, 1.9))
    meter.collect(llm(0.8))
    flushed = meter.collect(eou(0.4, 1.2))     # the next turn beginning
    assert flushed is not None
    assert flushed.tts is None
    assert flushed.total == pytest.approx(3.2)


def test_the_flushed_turn_does_not_leak_into_the_next():
    meter = Latency()
    meter.collect(eou(0.5, 1.9))
    meter.collect(llm(0.8))
    meter.collect(eou(0.4, 1.2))
    meter.collect(llm(0.6))
    turn = meter.collect(tts(0.5))
    assert turn.eou == pytest.approx(0.4)
    assert turn.llm == pytest.approx(0.6)
    assert len(meter.turns) == 2


def test_the_first_eou_flushes_nothing():
    assert Latency().collect(eou()) is None


# ---- robustness ---------------------------------------------------------

def test_an_stt_metric_fills_in_when_there_is_no_transcription_delay():
    """Streaming STT reports its own duration rather than a delay the
    turn spent waiting."""
    meter = Latency()
    meter.collect(eou(0.3, None))
    meter.collect(stt(duration=0.25))
    meter.collect(llm(0.4))
    turn = meter.collect(tts(0.3))
    assert turn.stt == pytest.approx(0.25)


def test_transcription_delay_wins_over_a_later_stt_duration():
    """For a non-streaming model, `duration` is the request alone and
    misses the waiting that preceded it."""
    meter = Latency()
    meter.collect(eou(0.3, 2.0))
    meter.collect(stt(duration=0.1))
    meter.collect(llm(0.4))
    turn = meter.collect(tts(0.3))
    assert turn.stt == pytest.approx(2.0)


def test_unknown_metrics_are_ignored():
    """VAD metrics and anything a future version adds come through the
    same event."""
    meter = Latency()
    assert meter.collect(Metric(idle_time=4.0, inference_count=90)) is None
    assert meter.collect(Metric()) is None


def test_a_missing_stage_still_prints():
    turn = Turn(eou=0.4, llm=0.9)
    assert "-" in turn.line()
    assert turn.total == pytest.approx(1.3)


# ---- summary ------------------------------------------------------------

def test_summary_before_anything_happened():
    assert "No complete turns" in Latency().summary()


def test_summary_reports_the_median_and_the_worst():
    meter = Latency()
    for seconds in (1.0, 5.0, 2.0):
        meter.collect(eou(0.0, seconds))
        meter.collect(llm(0.0))
        meter.collect(tts(0.0))
    text = meter.summary()
    assert "3 turns" in text
    assert "2.00" in text      # median
    assert "5.00" in text      # worst


def test_the_callback_fires_once_per_turn():
    seen = []
    meter = Latency(on_turn=seen.append)
    meter.collect(eou(0.3, 1.0))
    meter.collect(llm(0.4))
    meter.collect(tts(0.2))
    assert len(seen) == 1
