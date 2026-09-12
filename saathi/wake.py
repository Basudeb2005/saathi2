"""
Wake word detection — the only part of Saathi that runs on-device.

It has to be local: this is always listening, so shipping every second of
household audio to a cloud API is both a privacy problem and a bandwidth
one. openWakeWord is small enough to run continuously on a Pi and only
wakes the expensive cloud pipeline once it hears its name.

Audio comes from `arecord` rather than PyAudio — one less compiled
dependency on a Pi, and it's the same tool the rest of the system already
assumes. openWakeWord wants 16kHz mono 16-bit frames of 1280 samples
(80ms), which is exactly what we ask ALSA for, so there's no resampling
anywhere in this path.

Two pieces of logic worth knowing about:

  - **Refractory period.** A single spoken "hey Saathi" produces a run of
    high scores across consecutive frames, not one. Without a cooldown
    after firing, one greeting starts several sessions.
  - **Per-model thresholds.** "hey saathi" and "hey boy" are not equally
    hard to hear (see MODELS below), so they don't share a number.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional

import numpy as np

from saathi.config import (
    WAKE_CAPTURE_DEVICE,
    WAKE_MODEL_DIR,
    WAKE_REFRACTORY_S,
    WAKE_THRESHOLDS,
    WAKE_WORDS,
)
from saathi.logging_setup import get_logger

log = get_logger("wake")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280           # 80ms — openWakeWord's expected frame size
FRAME_BYTES = FRAME_SAMPLES * 2


class WakeWordError(Exception):
    """Models missing or unloadable, or the mic couldn't be opened."""


@dataclass
class Detection:
    word: str
    score: float
    at: float = field(default_factory=time.monotonic)


class WakeGate:
    """The decision half, with no audio or model in it.

    Separated out because this is where the bugs actually live — a
    threshold comparison and a cooldown clock — and testing it shouldn't
    require a microphone or a trained model.
    """

    def __init__(
        self,
        thresholds: Dict[str, float],
        refractory_s: float = WAKE_REFRACTORY_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.thresholds = dict(thresholds)
        self.refractory_s = refractory_s
        self._clock = clock
        self._last_fire: Optional[float] = None

    def consider(self, scores: Dict[str, float]) -> Optional[Detection]:
        """Return a Detection if these frame scores should wake Saathi.

        When several models cross at once — "hey boy" and "hello boy"
        overlap heavily by design — the highest score wins rather than
        whichever happens to be first in the dict.
        """
        now = self._clock()

        if self._last_fire is not None and (now - self._last_fire) < self.refractory_s:
            return None

        best_word, best_score = None, 0.0
        for word, score in scores.items():
            threshold = self.thresholds.get(word)
            if threshold is None:
                continue
            if score >= threshold and score > best_score:
                best_word, best_score = word, score

        if best_word is None:
            return None

        self._last_fire = now
        log.info("Wake word %r fired (score=%.3f)", best_word, best_score)
        return Detection(word=best_word, score=best_score, at=now)

    def reset(self) -> None:
        """Clear the cooldown — call this when a session ends, so someone
        can say "hey Saathi" again immediately."""
        self._last_fire = None


def _model_path(word: str):
    return WAKE_MODEL_DIR / f"{word.replace(' ', '_')}.onnx"


def load_model(words: Optional[List[str]] = None):
    """Load openWakeWord with Saathi's custom models.

    These are not the models openWakeWord ships with. "hey saathi",
    "hey boy" and "hello boy" all have to be trained — openWakeWord's
    synthetic-data notebook does it in about an hour per word, with no
    recordings needed. Until those .onnx files exist in WAKE_MODEL_DIR
    this raises, rather than silently falling back to "hey jarvis".
    """
    words = words or WAKE_WORDS
    missing = [w for w in words if not _model_path(w).exists()]
    if missing:
        raise WakeWordError(
            f"No wake model for {', '.join(repr(w) for w in missing)} in {WAKE_MODEL_DIR}. "
            f"Train them with openWakeWord's synthetic-data notebook and save each as "
            f"<word_with_underscores>.onnx"
        )

    try:
        from openwakeword.model import Model
    except ImportError as e:
        raise WakeWordError("openwakeword isn't installed — pip install openwakeword") from e

    paths = [str(_model_path(w)) for w in words]
    log.info("Loading wake models: %s", ", ".join(paths))
    return Model(wakeword_models=paths, inference_framework="onnx")


def _spawn_arecord(device: Optional[str]) -> subprocess.Popen:
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"]
    if device:
        cmd += ["-D", device]
    log.info("Opening mic: %s", " ".join(cmd))
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE)
    except FileNotFoundError as e:
        raise WakeWordError("arecord not found — install alsa-utils") from e


def listen(model=None, gate: Optional[WakeGate] = None, device: Optional[str] = None) -> Iterator[Detection]:
    """Yield a Detection every time someone says one of the wake words.

    Runs until the caller stops consuming it. Each yield is one wake, with
    the cooldown already applied — a consumer can just loop over this and
    start a session per item.
    """
    model = model or load_model()
    gate = gate or WakeGate(thresholds=WAKE_THRESHOLDS)
    proc = _spawn_arecord(device if device is not None else WAKE_CAPTURE_DEVICE)

    try:
        while True:
            chunk = proc.stdout.read(FRAME_BYTES)
            if not chunk or len(chunk) < FRAME_BYTES:
                log.warning("Mic stream ended")
                return

            frame = np.frombuffer(chunk, dtype=np.int16)
            detection = gate.consider(model.predict(frame))
            if detection:
                yield detection
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def main() -> None:
    """`python -m saathi.wake` — print detections. The fastest way to find
    out whether a freshly trained model is any good in the actual room,
    before wiring it to anything."""
    print(f"Listening for: {', '.join(WAKE_WORDS)}   (ctrl-c to stop)")
    try:
        for detection in listen():
            print(f"  {detection.word}  {detection.score:.3f}")
    except WakeWordError as e:
        raise SystemExit(f"error: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
