"""
Radio Browser search — the free, no-account music source.

https://api.radio-browser.info is a community directory of ~45,000
internet radio stations with a public REST API: no key, no signup, no
quota, no terms-of-service question. That combination is why radio is
Saathi's default music source rather than a fallback. A YouTube backend
gives you any specific song on demand, but it is a moving target that
breaks whenever YouTube changes something; radio just keeps working.

The API asks clients to send a descriptive User-Agent so they can
attribute traffic, so we do (RADIO_BROWSER_UA).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import requests

from saathi.config import (
    RADIO_BROWSER_MIRRORS,
    RADIO_BROWSER_UA,
    RADIO_BROWSER_URL,
    RADIO_TIMEOUT_S,
)
from saathi.logging_setup import get_logger

log = get_logger("music.radio")


class RadioError(Exception):
    """Radio Browser was unreachable or returned something unusable."""


@dataclass
class Station:
    name: str
    url: str
    country: Optional[str] = None
    tags: Optional[str] = None

    @staticmethod
    def from_api(d: dict) -> "Station":
        # url_resolved has redirects already followed; plain `url` can be
        # a redirector that some players won't chase. Prefer the former.
        return Station(
            name=(d.get("name") or "").strip() or "Unknown station",
            url=d.get("url_resolved") or d.get("url") or "",
            country=d.get("country") or None,
            tags=d.get("tags") or None,
        )


class RadioBrowser:
    def __init__(
        self,
        base_url: str = RADIO_BROWSER_URL,
        timeout: float = RADIO_TIMEOUT_S,
        session=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()

    def _hosts(self) -> List[str]:
        """The configured mirror first, then the others.

        Radio Browser is a handful of volunteer-run mirrors, and any one
        of them drops connections from time to time. Falling over to the
        next is the difference between "no music today" and a pause
        nobody notices.
        """
        hosts = [self.base_url]
        hosts += [h for h in RADIO_BROWSER_MIRRORS if h != self.base_url]
        return hosts

    def _get(self, path: str, **params) -> list:
        last_error = None

        for host in self._hosts():
            url = f"{host.rstrip('/')}{path}"
            try:
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": RADIO_BROWSER_UA},
                )
                response.raise_for_status()
                body = response.json()
            except requests.RequestException as e:
                log.info("Radio mirror %s failed (%s) — trying the next", host, e)
                last_error = e
                continue
            except ValueError as e:
                last_error = e
                continue

            if not isinstance(body, list):
                last_error = RadioError("unexpected response shape")
                continue
            return body

        raise RadioError(f"No Radio Browser mirror responded (last error: {last_error})")

    def search(self, query: str, limit: int = 10) -> List[Station]:
        """Find working stations matching `query`, most-listened first.

        Searched by name first, then by tag. "Play some jazz" is a tag
        match; "play BBC Radio 4" is a name match, and a user won't tell
        you which kind they meant -- so try both and take names first,
        since an exact name match is almost always what was intended.

        `hidebroken` is what keeps this usable: without it the directory
        happily returns stations whose stream died years ago.
        """
        if not query.strip():
            raise RadioError("A search term is required")

        common = {
            "limit": limit,
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }

        stations = [Station.from_api(d) for d in self._get("/json/stations/search", name=query, **common)]
        if len(stations) < limit:
            seen = {s.url for s in stations}
            by_tag = [Station.from_api(d) for d in self._get("/json/stations/search", tag=query, **common)]
            stations.extend(s for s in by_tag if s.url and s.url not in seen)

        playable = [s for s in stations if s.url][:limit]
        log.info("Radio search %r -> %d playable station(s)", query, len(playable))
        return playable

    def best(self, query: str) -> Optional[Station]:
        """The single station to play for `query`, or None."""
        results = self.search(query, limit=1)
        return results[0] if results else None
