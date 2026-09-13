"""
Supermemory backend.

Talks to the REST API directly rather than through the SDK: it's three
endpoints, and a vendor SDK in the dependency tree of a device that has
to keep working for years is a liability out of proportion to what it
saves. Swapping this file for a different service, or for a local store,
touches nothing else.

https://api.supermemory.ai — POST /v3/add, POST /v3/search.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from saathi.config import (
    MEMORY_CONTAINER_TAG,
    MEMORY_TIMEOUT_S,
    SUPERMEMORY_API_KEY,
    SUPERMEMORY_BASE_URL,
)
from saathi.logging_setup import get_logger
from saathi.memory.base import Fact, MemoryStore

log = get_logger("memory.supermemory")


class SupermemoryStore(MemoryStore):
    name = "supermemory"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = SUPERMEMORY_BASE_URL,
        container_tag: str = MEMORY_CONTAINER_TAG,
        session=None,
    ):
        self.api_key = api_key or SUPERMEMORY_API_KEY
        if not self.api_key:
            raise ValueError(
                "SUPERMEMORY_API_KEY isn't set — run `python -m saathi.setup --only memory`"
            )
        self.base_url = base_url.rstrip("/")
        self.container_tag = container_tag
        self._session = session or requests.Session()

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[dict]:
        """Returns None on any failure. See the note in base.py: a memory
        service having a bad day must never take the conversation with
        it."""
        try:
            response = self._session.post(
                f"{self.base_url}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=MEMORY_TIMEOUT_S,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            log.warning("Supermemory %s failed: %s", path, e)
            return None
        except ValueError:
            log.warning("Supermemory %s returned non-JSON", path)
            return None

    # ---- writes --------------------------------------------------------

    def remember(self, text: str, kind: str = "fact") -> bool:
        text = (text or "").strip()
        if not text:
            return False

        result = self._post("/v3/add", {
            "content": text,
            "containerTag": self.container_tag,
            "metadata": {"kind": kind, "source": "saathi"},
        })
        if result is None:
            return False
        log.info("Remembered (%s): %s", kind, text[:80])
        return True

    # ---- reads ---------------------------------------------------------

    @staticmethod
    def _facts_from(body: dict, limit: int) -> List[Fact]:
        """Tolerant on purpose — the response shape has moved between API
        versions, and a renamed field should cost us a recall, not crash
        someone's conversation."""
        if not isinstance(body, dict):
            return []

        rows = body.get("results") or body.get("memories") or body.get("documents") or []
        facts: List[Fact] = []
        for row in rows:
            if isinstance(row, str):
                facts.append(Fact(text=row))
                continue
            if not isinstance(row, dict):
                continue
            text = (
                row.get("memory")
                or row.get("content")
                or row.get("summary")
                or row.get("text")
                or ""
            )
            # Chunked documents put the text one level down.
            if not text and isinstance(row.get("chunks"), list) and row["chunks"]:
                first = row["chunks"][0]
                text = first.get("content", "") if isinstance(first, dict) else str(first)
            text = text.strip()
            if text:
                facts.append(Fact(text=text, source=row.get("id"), score=row.get("score")))
            if len(facts) >= limit:
                break
        return facts

    def recall(self, query: str, limit: int) -> List[Fact]:
        query = (query or "").strip()
        if not query:
            return []

        body = self._post("/v3/search", {
            "q": query,
            "containerTag": self.container_tag,
            "limit": limit,
        })
        if body is None:
            return []

        facts = self._facts_from(body, limit)
        log.info("Recalled %d fact(s) for %r", len(facts), query[:60])
        return facts

    def profile(self) -> List[Fact]:
        """Standing facts, fetched with a deliberately broad query.

        There's no "give me everything" endpoint, and there shouldn't be
        — the point is the few things that matter, not a transcript.
        """
        return self.recall("who is this person, their family, routines and preferences", limit=5)
