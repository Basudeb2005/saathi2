"""Memory backends. `build_memory()` is the only thing agents import."""
from __future__ import annotations

from saathi.config import MEMORY_BACKEND
from saathi.logging_setup import get_logger
from saathi.memory.base import Fact, MemoryStore, NullMemory

log = get_logger("memory")

__all__ = ["Fact", "MemoryStore", "NullMemory", "build_memory"]


def build_memory(backend: str = None) -> MemoryStore:
    """The configured store, or NullMemory if it can't be built.

    Never raises. A misconfigured memory backend turns the feature off;
    it does not stop the speaker from working, because a box that goes
    silent is a worse failure than a box that forgets.
    """
    name = (backend or MEMORY_BACKEND).lower()

    if name in ("none", "", "null"):
        return NullMemory()

    if name == "supermemory":
        try:
            from saathi.memory.supermemory import SupermemoryStore

            return SupermemoryStore()
        except Exception as e:
            log.warning("Couldn't start supermemory (%s) — running without memory", e)
            return NullMemory()

    log.warning("Unknown MEMORY_BACKEND %r — running without memory", name)
    return NullMemory()
