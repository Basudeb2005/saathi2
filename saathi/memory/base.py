"""
What Saathi remembers, and the shape any memory backend must have.

The interface exists so the backend is a decision you can revisit. Today
it's Supermemory; tomorrow it could be Postgres with pgvector, or a
local SQLite file with no network at all — which matters for a device
that sits in someone's home and hears private things. Nothing outside
this package should import a vendor SDK.

Two deliberate rules:

**Failing to remember is never a reason to fail a conversation.** Every
method swallows its own errors and degrades to "no memory this turn".
An elderly person talking to a box that suddenly stops answering because
a memory API returned 503 is a far worse outcome than one that forgets.

**Nothing is stored unless the agent asked for it.** Transcribing every
word of a household into a cloud service is not a feature. The agent
calls `remember()` for facts worth keeping; raw audio and raw
transcripts never leave the device.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Fact:
    """One thing worth knowing about the person, with where it came from."""

    text: str
    source: Optional[str] = None
    score: Optional[float] = None

    def __str__(self) -> str:
        return self.text


class MemoryStore(ABC):
    """Implement these three and the agent works unchanged."""

    name = "memory"

    @abstractmethod
    def remember(self, text: str, kind: str = "fact") -> bool:
        """Store something. Returns whether it landed.

        `kind` separates a durable fact ("her grandson is called Arun")
        from a passing detail, so a backend that ages content out can
        treat them differently.
        """

    @abstractmethod
    def recall(self, query: str, limit: int) -> List[Fact]:
        """Facts relevant to `query`, best first. Never raises."""

    def profile(self) -> List[Fact]:
        """Standing facts worth knowing before anyone says anything —
        names, routines, preferences. Optional: the default is nothing,
        which is a perfectly good memory of someone you've just met."""
        return []

    def describe_for_prompt(self, facts: List[Fact]) -> str:
        """Render facts for the system prompt.

        Hedged on purpose. Presented as certainty, a stale or mistaken
        fact gets asserted confidently at someone who will believe it —
        and being wrong about their own family is worse than not knowing.
        """
        if not facts:
            return ""
        lines = "\n".join(f"- {f.text}" for f in facts)
        return (
            "Things you remember about this person from earlier conversations. "
            "They may be out of date, so treat them as reminders rather than "
            "facts to assert:\n" + lines
        )


class NullMemory(MemoryStore):
    """No memory at all — the default, and a real choice.

    Also what every backend falls back to when it's misconfigured, so
    "memory is broken" degrades to "memory is off" instead of taking the
    conversation down with it.
    """

    name = "none"

    def remember(self, text: str, kind: str = "fact") -> bool:
        return False

    def recall(self, query: str, limit: int) -> List[Fact]:
        return []
