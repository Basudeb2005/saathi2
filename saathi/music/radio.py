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


# Words that carry no signal in a station search. "Play some old Hindi
# songs" is really a search for "hindi": everything else is politeness,
# and searching for "songs" returns noise.
_FILLER = {
    "play", "some", "put", "on", "the", "a", "an", "me", "my", "please",
    "song", "songs", "music", "station", "radio", "listen", "to", "of",
    "and", "for", "want", "like", "old", "new", "good", "nice", "lets",
    "let", "hear", "something", "anything", "channel",
}


def _keywords(query: str) -> List[str]:
    """The words worth searching on, longest first.

    Longest first because the specific word is usually the useful one:
    "hindi" beats "fm" when both are present.
    """
    words = [w.strip(".,!?'\"").lower() for w in query.split()]
    words = [w for w in words if w and w not in _FILLER and len(w) > 2]
    return sorted(dict.fromkeys(words), key=len, reverse=True)


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

        People don't speak in tags. "Play some old Hindi songs" has no
        station called that and no tag called that, but there are plenty
        of stations tagged "hindi" and plenty whose language is Hindi —
        so the query is widened progressively instead of failing on the
        literal phrase.

        Order matters: an exact name match is almost always what was
        meant ("play BBC Radio 4"), so it goes first. The word-by-word
        attempts come last, since they're the loosest.

        `hidebroken` is what keeps this usable at all: without it the
        directory happily returns stations whose stream died years ago.
        """
        if not query.strip():
            raise RadioError("A search term is required")

        common = {
            "limit": limit,
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }

        found: List[Station] = []
        seen = set()

        def collect(**params) -> None:
            if len(found) >= limit:
                return
            for row in self._get("/json/stations/search", **params, **common):
                station = Station.from_api(row)
                if station.url and station.url not in seen:
                    seen.add(station.url)
                    found.append(station)
                if len(found) >= limit:
                    return

        collect(name=query)
        collect(tag=query)

        # Widen: each meaningful word as a tag, and as a language. A
        # language hit is what turns "old hindi songs" into something
        # playable, and is usually a better match than a tag.
        for word in _keywords(query):
            if len(found) >= limit:
                break
            collect(language=word)
            collect(tag=word)

        log.info("Radio search %r -> %d playable station(s)", query, len(found))
        return found[:limit]

    def best(self, query: str) -> Optional[Station]:
        """The single station to play for `query`, or None."""
        results = self.search(query, limit=1)
        return results[0] if results else None
