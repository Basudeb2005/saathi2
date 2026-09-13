"""
Fakes for everything that would otherwise touch the network.

The whole suite runs with no API keys, no LiveKit, no Mopidy and no
Raspberry Pi — same rule v1 held itself to.
"""
from __future__ import annotations

from typing import Any, List, Optional


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Stands in for a requests.Session. Queue up responses; every call
    is recorded so tests can assert on what was actually sent."""

    def __init__(self, responses: Optional[List[Any]] = None):
        self.responses = list(responses or [])
        self.calls: List[dict] = []

    def _next(self, kind: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"kind": kind, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"FakeSession ran out of responses on {kind} {url}")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt if isinstance(nxt, FakeResponse) else FakeResponse(nxt)

    def post(self, url, json=None, timeout=None, headers=None):
        return self._next("post", url, json=json, timeout=timeout, headers=headers)

    def get(self, url, params=None, timeout=None, headers=None):
        return self._next("get", url, params=params, timeout=timeout, headers=headers)


def rpc_ok(result: Any = None) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def rpc_error(message: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": message}}


class FakeMopidy:
    """Records what the player asked for, without any JSON-RPC."""

    def __init__(self, search_results: Optional[List[str]] = None, track_name: Optional[str] = None):
        self.search_results = search_results if search_results is not None else []
        self.track_name = track_name
        self.played: List[List[str]] = []
        self.volumes: List[int] = []
        self.actions: List[str] = []
        self.volume: Optional[int] = 70
        self.single: Optional[bool] = None

    def search_tracks(self, query, uri_scheme=None, limit=20):
        self.actions.append(f"search:{query}")
        return list(self.search_results)

    def play_uris(self, uris):
        self.played.append(list(uris))

    def current_track_name(self):
        return self.track_name

    def set_single(self, single):
        self.single = single

    def pause(self): self.actions.append("pause")
    def resume(self): self.actions.append("resume")
    def stop(self): self.actions.append("stop")
    def next_track(self): self.actions.append("next")

    def get_volume(self):
        return self.volume

    def set_volume(self, volume):
        self.volume = volume
        self.volumes.append(volume)


class FakeRadio:
    def __init__(self, station=None, error: Optional[Exception] = None):
        self.station = station
        self.error = error
        self.queries: List[str] = []

    def best(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.station
