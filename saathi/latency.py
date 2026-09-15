"""
Where the seconds go.

"It's slow" is not a bug report you can act on. A voice turn has five
stages that fail to be fast independently, and on a Pi they are wildly
uneven — one of them is usually most of the wait and the other four are
noise. Guessing which costs an evening; asking costs a log line.

    turn  eou 0.51  stt 1.92  llm 0.88  tts 0.71  =  4.02s before it speaks

LiveKit measures all of this already and throws it at a `metrics_collected`
event that nothing was listening to. This listens, keeps the pieces of one
turn together, and prints them on one line when the turn is done.

The numbers, in the order they happen:

  eou  how long after you stopped talking before it decided you had.
       Pure waiting — nothing is being computed. With push-to-talk it
       should be small, because releasing the key sends silence
       immediately; if it isn't, TURN_ENDPOINTING_S is the dial.
  stt  transcribing. The big one on OpenAI's STT, which is not a
       streaming model: it waits for the whole utterance, uploads it,
       and waits for the answer. Deepgram streams instead and returns
       the final transcript a couple of hundred milliseconds after you
       stop. This is usually the largest single number on the line.
  llm  time to the first token. Not the whole reply — the rest streams
       into the TTS while it plays.
  tts  time to the first byte of audio. After this it is talking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from saathi.logging_setup import get_logger

log = get_logger("latency")


@dataclass
class Turn:
    """One exchange's timings, gathered as they arrive.

    They arrive separately and slightly out of order, so this holds
    whatever has turned up and knows when it has enough to be worth
    printing.
    """

    eou: Optional[float] = None
    stt: Optional[float] = None
    llm: Optional[float] = None
    tts: Optional[float] = None

    @property
    def total(self) -> float:
        """Silence, from the end of your sentence to the start of its.

        The sum, not a wall clock: these stages are sequential, and the
        parts that overlap (the LLM still generating while the TTS
        speaks) are after the moment this is measuring.
        """
        return sum(v for v in (self.eou, self.stt, self.llm, self.tts) if v)

    @property
    def started(self) -> bool:
        return any(v is not None for v in (self.eou, self.stt, self.llm, self.tts))

    def line(self) -> str:
        def show(name: str, value: Optional[float]) -> str:
            return f"{name} {value:5.2f}" if value is not None else f"{name}     -"

        return (
            f"{show('eou', self.eou)}  {show('stt', self.stt)}  "
            f"{show('llm', self.llm)}  {show('tts', self.tts)}  "
            f"= {self.total:5.2f}s before it speaks"
        )

    def slowest(self) -> str:
        """Which stage to go and fix."""
        stages = [(n, v) for n, v in
                  (("waiting to decide you'd finished", self.eou),
                   ("transcribing", self.stt),
                   ("the model thinking", self.llm),
                   ("generating speech", self.tts)) if v]
        if not stages:
            return ""
        name, value = max(stages, key=lambda pair: pair[1])
        return f"{name} ({value:.2f}s)"


class Latency:
    """Collects metrics into turns and logs each one.

    Deliberately holds no reference to the session: metrics arrive as
    plain dataclasses, and taking only those keeps this testable with
    four fake objects instead of a LiveKit room.
    """

    # Worth saying something about, rather than leaving in a log file
    # nobody opens.
    SLOW_S = 3.0

    def __init__(self, on_turn: Optional[Callable[[Turn], None]] = None):
        self.turn = Turn()
        self.turns: List[Turn] = []
        self._on_turn = on_turn

    def collect(self, metrics) -> Optional[Turn]:
        """One metrics object from LiveKit. Returns a Turn when one closed.

        Matched on attributes rather than isinstance, because these
        classes have moved between modules across agent versions and an
        import that names the wrong path turns a diagnostic into a
        crash.
        """
        if hasattr(metrics, "end_of_utterance_delay"):
            # A new turn beginning. If the last one never spoke — it
            # ended in a tool call, or was interrupted — this is the
            # only chance to report it, and those are often the slowest
            # turns there are.
            flushed = self._close() if self.turn.started else None
            self.turn.eou = metrics.end_of_utterance_delay
            # transcription_delay is the part of the wait that was the
            # STT still working, which is the honest number for a
            # non-streaming model — its `duration` measures only the
            # request, not the waiting that preceded it.
            if getattr(metrics, "transcription_delay", None):
                self.turn.stt = metrics.transcription_delay
            return flushed

        if hasattr(metrics, "ttft"):
            self.turn.llm = metrics.ttft
            return None

        if hasattr(metrics, "ttfb"):
            # The end of the wait: after this it is making a noise.
            self.turn.tts = metrics.ttfb
            return self._close()

        if hasattr(metrics, "audio_duration") and self.turn.stt is None:
            self.turn.stt = getattr(metrics, "duration", None)

        return None

    def _close(self) -> Optional[Turn]:
        done, self.turn = self.turn, Turn()
        if not done.started:
            return None
        self.turns.append(done)

        log.info("turn  %s", done.line())
        if done.total >= self.SLOW_S:
            log.warning("slow turn — mostly %s", done.slowest())
        if self._on_turn:
            self._on_turn(done)
        return done

    def summary(self) -> str:
        if not self.turns:
            return "No complete turns measured yet."
        totals = sorted(t.total for t in self.turns)
        middle = totals[len(totals) // 2]
        worst = self.turns[max(range(len(self.turns)),
                               key=lambda i: self.turns[i].total)]
        return (
            f"{len(self.turns)} turns, median {middle:.2f}s, "
            f"worst {worst.total:.2f}s (mostly {worst.slowest()})"
        )
