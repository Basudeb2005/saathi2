"""
Wake word detection — the only part of Saathi that runs on-device.

It has to be local: this is always listening, so shipping every second of
household audio to a cloud API is both a privacy problem and a bandwidth
one.

Neither engine here needs you to train anything.

  openWakeWord (default) ships pretrained models — alexa, hey mycroft,
  hey jarvis, hey rhasspy — and downloads them on first run. No account,
  no key, fully offline. The catch is that you get its words, not yours.

  Porcupine will make you a "Hey Saathi" in seconds: type the phrase into
  Picovoice's console and it hands back a .ppn. Still no training, but it
  wants a free account and an access key.

Audio comes from `arecord` rather than PyAudio — one less compiled
dependency on a Pi. Both engines want 16kHz mono 16-bit, so there is no
resampling in this path; they differ only in frame size, which each
engine reports and `listen()` reads from.
"""
from __future__ import annotations

import audioop
import os
import pathlib
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional

import numpy as np

from saathi.config import (
    OWW_THRESHOLD,
    WAKE_DUCK_HOLD_S,
    WAKE_DUCK_ON_SPEECH,
    WAKE_DUCK_RMS,
    WAKE_DUCK_VOLUME,
    WAKE_VERIFIER_PATH,
    WAKE_VERIFIER_THRESHOLD,
    OWW_WORDS,
    PORCUPINE_ACCESS_KEY,
    PORCUPINE_KEYWORD_PATHS,
    PORCUPINE_KEYWORDS,
    PORCUPINE_SENSITIVITY,
    WAKE_CAPTURE_DEVICE,
    WAKE_ENGINE,
    WAKE_REFRACTORY_S,
)
from saathi.logging_setup import get_logger

log = get_logger("wake")

SAMPLE_RATE = 16000


class WakeWordError(Exception):
    """The engine couldn't be built, or the mic couldn't be opened."""


@dataclass
class Detection:
    word: str
    score: float
    at: float = field(default_factory=time.monotonic)


class WakeGate:
    """The decision half, with no audio or model in it.

    Separated out because this is where the bugs actually live — a
    threshold comparison and a cooldown clock — and testing it shouldn't
    require a microphone or a model.

    The cooldown matters more than it looks: a single spoken "hey Jarvis"
    scores high across a run of consecutive frames, not one, so without a
    refractory period one greeting starts several sessions.
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

        When several models cross at once — similar-sounding words overlap
        by design — the highest score wins rather than whichever happens
        to come first in the dict.
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
        can say the wake word again immediately."""
        self._last_fire = None


class OpenWakeWordEngine:
    """Pretrained, offline, no account. The default.

    Downloads its models on first construction. `hey_jarvis` is the
    default word because it's the most distinctive of the four shipped:
    unlike "alexa" it won't fire every time the television says it.
    """

    frame_samples = 1280  # 80ms, what openWakeWord expects

    def __init__(self, words: Optional[List[str]] = None, threshold: float = OWW_THRESHOLD, model=None):
        self.words = words or OWW_WORDS
        self.gate = WakeGate(thresholds={w: threshold for w in self.words})

        if model is not None:
            self.model = model
            return

        try:
            # `openwakeword.utils` must be imported explicitly: some
            # versions don't pull the submodule in with the package, so
            # `openwakeword.utils.download_models()` after a plain
            # `import openwakeword` fails with "no attribute 'utils'"
            # on exactly the versions where it matters.
            from openwakeword import utils as oww_utils
            from openwakeword.model import Model
        except ImportError as e:
            raise WakeWordError("openwakeword isn't installed — pip install openwakeword") from e

        # Idempotent, and a no-op once they're cached. Not fatal: the
        # models may already be on disk from a previous run, and failing
        # the whole engine over a download that wasn't needed would be
        # worse than letting Model() report a genuinely missing file.
        # A version too old to have download_models is also too old to
        # accept `wakeword_models`, and would otherwise fail later with a
        # baffling error from deep inside AudioFeatures. Catch it here,
        # where we can name the actual cause and the one-line fix.
        if not hasattr(oww_utils, "download_models"):
            raise WakeWordError(
                "openwakeword is too old to use — pip resolved back to a pre-0.6 "
                "release because tflite-runtime has no wheel for this Python. "
                "Fix it with:  pip install --no-deps --force-reinstall openwakeword==0.6.0"
            )

        try:
            log.info("Ensuring pretrained wake models are downloaded")
            oww_utils.download_models(list(self.words))
        except Exception as e:
            log.warning("Couldn't download wake models (%s) — trying what's on disk", e)

        # A verifier trained on one person's voice is what makes the wake
        # word fire for them and not for the television — the other half
        # of working while music is playing. Optional: without one the
        # model is speaker-independent, which is fine in a quiet room.
        extra = {}
        if os.path.exists(WAKE_VERIFIER_PATH):
            extra = {
                "custom_verifier_models": {w: WAKE_VERIFIER_PATH for w in self.words},
                "custom_verifier_threshold": WAKE_VERIFIER_THRESHOLD,
            }
            log.info("Using voice verifier %s", WAKE_VERIFIER_PATH)

        try:
            self.model = Model(
                wakeword_models=list(self.words), inference_framework="onnx", **extra
            )
        except Exception as e:
            raise WakeWordError(
                f"Couldn't load wake models {self.words}. Pretrained names are: "
                f"alexa, hey_mycroft, hey_jarvis, hey_rhasspy. ({e})"
            ) from e

    def process(self, frame: np.ndarray) -> Optional[Detection]:
        return self.gate.consider(self.model.predict(frame))

    def reset(self) -> None:
        self.gate.reset()


class PorcupineEngine:
    """Picovoice Porcupine — the way to get a real "Hey Saathi".

    Built-in keywords ("jarvis", "computer", "bumblebee", ...) need no
    file. A custom phrase is generated in the console and dropped in as a
    .ppn; either way there is nothing for you to train.

    No WakeGate here: Porcupine already emits one detection per utterance
    rather than a run of scores, so a cooldown would be redundant.
    """

    def __init__(self, handle=None):
        if handle is not None:
            self._porcupine = handle
            self._labels = [f"keyword_{i}" for i in range(64)]
            return

        if not PORCUPINE_ACCESS_KEY:
            raise WakeWordError(
                "PORCUPINE_ACCESS_KEY isn't set. Get a free one at console.picovoice.ai, "
                "or switch to WAKE_ENGINE=openwakeword, which needs no account."
            )

        try:
            import pvporcupine
        except ImportError as e:
            raise WakeWordError(
                "WAKE_ENGINE=porcupine but pvporcupine isn't installed. Either "
                "`pip install pvporcupine`, or set WAKE_ENGINE=openwakeword in .env "
                "— that one is pretrained, needs no account, and is the default."
            ) from e

        if PORCUPINE_KEYWORD_PATHS:
            kwargs = {"keyword_paths": PORCUPINE_KEYWORD_PATHS}
            self._labels = [p.split("/")[-1].removesuffix(".ppn") for p in PORCUPINE_KEYWORD_PATHS]
        else:
            kwargs = {"keywords": PORCUPINE_KEYWORDS}
            self._labels = list(PORCUPINE_KEYWORDS)

        log.info("Loading Porcupine keywords: %s", ", ".join(self._labels))
        try:
            self._porcupine = pvporcupine.create(
                access_key=PORCUPINE_ACCESS_KEY,
                sensitivities=[PORCUPINE_SENSITIVITY] * len(self._labels),
                **kwargs,
            )
        except Exception as e:
            raise WakeWordError(f"Couldn't start Porcupine: {e}") from e

    @property
    def frame_samples(self) -> int:
        return self._porcupine.frame_length

    def process(self, frame: np.ndarray) -> Optional[Detection]:
        index = self._porcupine.process(frame)
        if index < 0:
            return None
        word = self._labels[index] if index < len(self._labels) else f"keyword_{index}"
        log.info("Wake word %r fired", word)
        # Porcupine reports a hit, not a confidence — 1.0 rather than a
        # fabricated number that would read as a real score.
        return Detection(word=word, score=1.0)

    def reset(self) -> None:
        pass


def build_engine(name: Optional[str] = None):
    """Pick an engine by name. Defaults to WAKE_ENGINE from the config."""
    name = (name or WAKE_ENGINE).lower()
    if name == "openwakeword":
        return OpenWakeWordEngine()
    if name == "porcupine":
        return PorcupineEngine()
    raise WakeWordError(f"Unknown WAKE_ENGINE {name!r} — use 'openwakeword' or 'porcupine'")


def _stop(proc: subprocess.Popen) -> None:
    """Make sure arecord is actually gone.

    terminate() alone is not enough: if it ignores SIGTERM or is stuck in
    a blocking read, it keeps the ALSA device open, and the next attempt
    fails with "Device or resource busy" — the process fighting itself
    for a microphone it already holds.
    """
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        log.warning("arecord ignored SIGTERM; killing it")
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            log.error("arecord would not die — the mic may stay busy")


def _spawn_arecord(device: Optional[str]) -> subprocess.Popen:
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"]
    if device:
        cmd += ["-D", device]
    log.info("Opening mic: %s", " ".join(cmd))
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE)
    except FileNotFoundError as e:
        raise WakeWordError("arecord not found — install alsa-utils") from e


class _MusicDucker:
    """Dips the music the moment anyone speaks, before we know what they
    said.

    Without echo cancellation the wake model is listening to a speaker
    playing music into the microphone, and misses almost everything. A
    brief dip gives it a clean window. The cost is a half-second dip
    whenever someone talks near the box — a far smaller annoyance than a
    wake word that simply doesn't work while music is on.
    """

    def __init__(self):
        self._ducked_until = 0.0
        self._previous: Optional[int] = None

    def consider(self, pcm: bytes) -> None:
        if not WAKE_DUCK_ON_SPEECH:
            return

        now = time.monotonic()
        loud = audioop.rms(pcm, 2) > WAKE_DUCK_RMS

        if loud:
            if self._previous is None:
                self._duck()
            self._ducked_until = now + WAKE_DUCK_HOLD_S
        elif self._previous is not None and now >= self._ducked_until:
            self.restore()

    def _duck(self) -> None:
        try:
            from saathi.music.mopidy import MopidyClient

            client = MopidyClient()
            if client.state() != "playing":
                return
            self._previous = client.get_volume()
            client.set_volume(WAKE_DUCK_VOLUME)
        except Exception:
            # Never let a volume call cost us a wake word.
            self._previous = None

    def restore(self) -> None:
        if self._previous is None:
            return
        try:
            from saathi.music.mopidy import MopidyClient

            MopidyClient().set_volume(self._previous)
        except Exception:
            pass
        self._previous = None


def listen(engine=None, device: Optional[str] = None) -> Iterator[Detection]:
    """Yield a Detection every time someone says the wake word.

    Runs until the caller stops consuming it. Each yield is one wake, with
    any cooldown already applied — a consumer can just loop over this and
    start a session per item.
    """
    engine = engine or build_engine()
    frame_bytes = engine.frame_samples * 2
    proc = _spawn_arecord(device if device is not None else WAKE_CAPTURE_DEVICE)
    ducker = _MusicDucker()

    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if not chunk or len(chunk) < frame_bytes:
                log.warning("Mic stream ended")
                return

            ducker.consider(chunk)

            detection = engine.process(np.frombuffer(chunk, dtype=np.int16))
            if detection:
                yield detection
    finally:
        ducker.restore()
        _stop(proc)


def _record_clip(path: str, seconds: float, device: Optional[str]) -> bool:
    """One 16kHz mono WAV, which is what the trainer expects."""
    cmd = [
        "arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
        "-d", str(seconds), path,
    ]
    if device:
        cmd += ["-D", device]
    try:
        return subprocess.run(cmd, timeout=seconds + 10).returncode == 0
    except Exception as e:
        log.error("Recording failed: %s", e)
        return False


def enroll(
    positives: int = 8,
    negatives: int = 8,
    seconds: float = 2.0,
    device: Optional[str] = None,
) -> int:
    """Train a verifier on one person's voice.

    Two things this buys, and they're the same mechanism: the wake word
    stops firing for the television, and it keeps working when the room
    is noisy — because "is this the wake word" becomes "is this the wake
    word, from the person who lives here".

    The negatives matter as much as the positives. Without examples of
    the same voice saying other things, the model learns "this person"
    rather than "this person saying this phrase", and then fires on any
    sentence they utter.
    """
    import tempfile

    word = OWW_WORDS[0]
    spoken = word.replace("_", " ")
    device = device if device is not None else WAKE_CAPTURE_DEVICE

    print(f"\nTeaching Saathi your voice — about two minutes.\n")
    print(f"  It will record {positives} clips of you saying {spoken!r},")
    print(f"  then {negatives} clips of you saying anything else.\n")
    print("  Sit where you normally would, and speak normally.")
    input("  Press Enter when you're ready. ")

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="saathi-enroll-"))
    pos_dir, neg_dir = workdir / "positive", workdir / "negative"
    pos_dir.mkdir()
    neg_dir.mkdir()

    for i in range(positives):
        input(f"\n  [{i + 1}/{positives}] Press Enter, then say {spoken!r}: ")
        if not _record_clip(str(pos_dir / f"{i}.wav"), seconds, device):
            print("  recording failed — is the mic device right?")
            return 1

    print(f"\n  Now {negatives} clips of ordinary talking — anything at all,")
    print(f"  just NOT {spoken!r}. Count to five, read something out.")
    for i in range(negatives):
        input(f"\n  [{i + 1}/{negatives}] Press Enter, then talk: ")
        if not _record_clip(str(neg_dir / f"{i}.wav"), seconds, device):
            print("  recording failed")
            return 1

    print("\n  Training… (a minute or so on a Pi)")
    try:
        import openwakeword

        pathlib.Path(WAKE_VERIFIER_PATH).parent.mkdir(parents=True, exist_ok=True)
        openwakeword.train_custom_verifier(
            positive_reference_clips=str(pos_dir),
            negative_reference_clips=str(neg_dir),
            output_path=WAKE_VERIFIER_PATH,
            model_name=word,
        )
    except Exception as e:
        print(f"  training failed: {e}")
        return 1

    print(f"\n  Done — saved to {WAKE_VERIFIER_PATH}")
    print("  It's picked up automatically on the next start.")
    print(f"  Too strict? Lower WAKE_VERIFIER_THRESHOLD (now {WAKE_VERIFIER_THRESHOLD}).")
    print(f"  Fires for other people? Raise it.\n")
    return 0


def main() -> None:
    """`python -m saathi.wake` — print detections, so you can find out
    whether the wake word works in the real room, at the real distance,
    with the television on.

    `python -m saathi.wake enroll` trains it on one person's voice."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m saathi.wake")
    sub = parser.add_subparsers(dest="command")
    e = sub.add_parser("enroll", help="teach it one person's voice")
    e.add_argument("--positives", type=int, default=8)
    e.add_argument("--negatives", type=int, default=8)
    e.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    if args.command == "enroll":
        raise SystemExit(enroll(args.positives, args.negatives, args.seconds))

    try:
        engine = build_engine()
    except WakeWordError as e:
        raise SystemExit(f"error: {e}")

    print(f"Listening via {WAKE_ENGINE}   (ctrl-c to stop)")
    try:
        for detection in listen(engine):
            print(f"  {detection.word}  {detection.score:.3f}")
    except WakeWordError as e:
        raise SystemExit(f"error: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
