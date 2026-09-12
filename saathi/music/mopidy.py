"""
Mopidy JSON-RPC client.

Mopidy runs as a separate service on the same Pi and owns the speaker.
We talk to it over HTTP JSON-RPC rather than importing it, so it can be
restarted, upgraded, or swapped for anything else speaking the same API
without touching the agent.

Only the handful of methods the agent actually needs are wrapped. Adding
another is one line -- `self._call("core.playback.seek", time_position=...)`
-- so there's no value in mirroring Mopidy's whole surface here.

Every method raises MopidyError on transport failure or a JSON-RPC error
object, so the tool layer above has exactly one exception type to turn
into a spoken apology.
"""
from __future__ import annotations

import itertools
from typing import Any, List, Optional

import requests

from saathi.config import MOPIDY_RPC_URL, MOPIDY_TIMEOUT_S
from saathi.logging_setup import get_logger

log = get_logger("music.mopidy")


class MopidyError(Exception):
    """Mopidy was unreachable, or returned a JSON-RPC error."""


class MopidyClient:
    def __init__(self, url: str = MOPIDY_RPC_URL, timeout: float = MOPIDY_TIMEOUT_S, session=None):
        self.url = url
        self.timeout = timeout
        # Injectable for tests, and a real Session keeps the TCP
        # connection warm across the many small calls a single "play
        # something" turn makes.
        self._session = session or requests.Session()
        self._ids = itertools.count(1)

    def _call(self, method: str, **params) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params or {},
        }
        log.debug("Mopidy call %s params=%s", method, params)
        try:
            response = self._session.post(self.url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as e:
            raise MopidyError(
                f"Couldn't reach Mopidy at {self.url} ({e}). Is the mopidy service running?"
            ) from e
        except ValueError as e:
            raise MopidyError(f"Mopidy returned a non-JSON response to {method}") from e

        if "error" in body:
            err = body["error"]
            raise MopidyError(f"Mopidy rejected {method}: {err.get('message', err)}")

        return body.get("result")

    # ---- playback ------------------------------------------------------

    def play_uris(self, uris: List[str]) -> None:
        """Replace the queue with `uris` and start playing. Clearing
        first is deliberate: "play something else" should mean exactly
        that, not append to a queue nobody can see."""
        if not uris:
            raise MopidyError("No tracks to play")
        self._call("core.tracklist.clear")
        self._call("core.tracklist.add", uris=uris)
        self._call("core.playback.play")
        log.info("Playing %d uri(s), first=%s", len(uris), uris[0])

    def pause(self) -> None:
        self._call("core.playback.pause")

    def resume(self) -> None:
        self._call("core.playback.resume")

    def stop(self) -> None:
        self._call("core.playback.stop")

    def next_track(self) -> None:
        self._call("core.playback.next")

    def state(self) -> str:
        """One of "playing", "paused", "stopped"."""
        return self._call("core.playback.get_state")

    def current_track_name(self) -> Optional[str]:
        """Best-effort "Artist - Title" for confirmations. Returns None
        rather than raising when nothing is loaded -- not knowing the
        track name is never a reason to fail the turn."""
        track = self._call("core.playback.get_current_track")
        if not track:
            return None
        title = track.get("name")
        artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
        if title and artists:
            return f"{artists} - {title}"
        return title or artists or None

    # ---- volume --------------------------------------------------------

    def set_volume(self, volume: int) -> None:
        """0-100. Used for ducking under speech, not just user requests."""
        self._call("core.mixer.set_volume", volume=max(0, min(100, int(volume))))

    def get_volume(self) -> Optional[int]:
        return self._call("core.mixer.get_volume")

    # ---- search --------------------------------------------------------

    def search_tracks(self, query: str, uri_scheme: Optional[str] = None, limit: int = 20) -> List[str]:
        """Search Mopidy's backends and return track URIs, best first.

        `uri_scheme` restricts to one backend (e.g. "youtube"). Mopidy
        returns one result set per backend, so without it a query can
        come back led by whichever backend answered first rather than
        whichever is most relevant."""
        kwargs = {"query": {"any": [query]}}
        if uri_scheme:
            kwargs["uris"] = [f"{uri_scheme}:"]

        results = self._call("core.library.search", **kwargs) or []
        uris: List[str] = []
        for result in results:
            for track in result.get("tracks", []) or []:
                uri = track.get("uri")
                if uri:
                    uris.append(uri)
                if len(uris) >= limit:
                    return uris
        return uris
