"""
The music layer the agent actually talks to.

Sits on top of Mopidy (playback) and Radio Browser (discovery) and turns
a spoken request into something audible, returning a short sentence
suitable for reading back.

Two sources, deliberately different in character:

  - "station" -> Radio Browser. Free, no account, never breaks. Right
                 answer for "play some Hindi music" / "put the news on".
  - "song"    -> whatever on-demand backend Mopidy has configured
                 (Mopidy-YouTube). Right answer for a named track, and
                 the thing that will break first when YouTube changes.

`source="auto"` tries song, then falls back to station -- because a
failed song lookup should land on *something* playing rather than an
apology. That fallback is the single most important behaviour in this
file for an elderly user: silence reads as "it's broken".
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Dict, List, Optional

from saathi.config import (
    MUSIC_CACHE_PATH,
    MUSIC_CACHE_SIZE,
    MUSIC_DUCK_VOLUME,
    MUSIC_NORMAL_VOLUME,
    MUSIC_SONG_RESULTS,
    MUSIC_STOP_AFTER_SONG,
)
from saathi.logging_setup import get_logger
from saathi.music.mopidy import MopidyClient, MopidyError
from saathi.music.radio import RadioBrowser, RadioError

log = get_logger("music.player")

SOURCES = ("auto", "song", "station")


class SongCache:
    """query -> track URIs, kept on disk.

    A first YouTube lookup costs ten to twenty seconds on a Pi; the
    second time it should be instant. Caching is safe here because a
    youtube:video/<id> URI is an identifier, not a signed stream URL —
    Mopidy resolves it afresh at play time, so a cached entry cannot go
    stale in the way a cached URL would.

    Every failure is swallowed: a cache that can't be read or written is
    a slower lookup, never a failed one.
    """

    def __init__(self, path=MUSIC_CACHE_PATH, size: int = MUSIC_CACHE_SIZE):
        self.path = path
        self.size = size
        self._data: Dict[str, List[str]] = {}
        self._load()

    @staticmethod
    def _key(query: str) -> str:
        return " ".join(query.lower().split())

    def _load(self) -> None:
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict):
                    self._data = {k: v for k, v in loaded.items() if isinstance(v, list)}
        except Exception as e:
            log.info("Couldn't read the song cache (%s) — starting empty", e)
            self._data = {}

    def get(self, query: str) -> Optional[List[str]]:
        return self._data.get(self._key(query))

    def put(self, query: str, uris: List[str]) -> None:
        if not uris:
            return
        self._data[self._key(query)] = uris

        # Oldest-first eviction: dicts keep insertion order, and the
        # thing worth keeping is what was played recently.
        while len(self._data) > self.size:
            self._data.pop(next(iter(self._data)))

        try:
            self.path.write_text(json.dumps(self._data, indent=0))
        except Exception as e:
            log.info("Couldn't write the song cache: %s", e)


class MusicError(Exception):
    """Nothing could be played. The message is written to be spoken."""


class MusicPlayer:
    def __init__(
        self,
        mopidy: Optional[MopidyClient] = None,
        radio: Optional[RadioBrowser] = None,
        cache: Optional[SongCache] = None,
    ):
        self.mopidy = mopidy or MopidyClient()
        self.radio = radio or RadioBrowser()
        self.cache = cache if cache is not None else SongCache()

    # ---- playing -------------------------------------------------------

    def play(self, query: str, source: str = "auto") -> str:
        if source not in SOURCES:
            raise MusicError(f"Unknown music source {source!r}")
        if not query.strip():
            raise MusicError("I didn't catch what you'd like to hear.")

        if source == "station":
            return self._play_station(query)
        if source == "song":
            return self._play_song(query)

        try:
            return self._play_song(query)
        except MusicError as e:
            log.info("Song lookup failed for %r (%s); falling back to radio", query, e)
            return self._play_station(query)

    def _play_song(self, query: str) -> str:
        cached = self.cache.get(query)
        if cached:
            log.info("Song cache hit for %r", query)
            uris = cached
        else:
            try:
                uris = self.mopidy.search_tracks(
                    query, uri_scheme="youtube", limit=MUSIC_SONG_RESULTS
                )
            except MopidyError as e:
                raise MusicError(str(e)) from e

            if not uris:
                raise MusicError(f"I couldn't find a song called {query}.")
            self.cache.put(query, uris)

        try:
            # Set before playing: after, the track has already begun and
            # Mopidy may have decided what follows it.
            self.mopidy.set_single(MUSIC_STOP_AFTER_SONG)
            self.mopidy.play_uris(uris)
        except MopidyError as e:
            raise MusicError(str(e)) from e

        # Ask Mopidy what it actually loaded rather than echoing the
        # query back -- a search for "that Kishore Kumar song" should
        # confirm the real title, so a wrong match is obvious immediately.
        name = self.mopidy.current_track_name()
        return f"Playing {name}." if name else f"Playing {query}."

    def _play_station(self, query: str) -> str:
        try:
            station = self.radio.best(query)
        except RadioError as e:
            raise MusicError(str(e)) from e

        if station is None:
            raise MusicError(f"I couldn't find any station for {query}.")

        try:
            # A station is one endless stream — single mode would be
            # meaningless, and leaving it set from a previous song would
            # be confusing.
            self.mopidy.set_single(False)
            self.mopidy.play_uris([station.url])
        except MopidyError as e:
            raise MusicError(str(e)) from e

        return f"Playing {station.name}."

    # ---- transport -----------------------------------------------------

    def pause(self) -> str:
        try:
            self.mopidy.pause()
        except MopidyError as e:
            raise MusicError(str(e)) from e
        return "Paused."

    def resume(self) -> str:
        try:
            self.mopidy.resume()
        except MopidyError as e:
            raise MusicError(str(e)) from e
        return "Carrying on."

    def stop(self) -> str:
        try:
            self.mopidy.stop()
        except MopidyError as e:
            raise MusicError(str(e)) from e
        return "Stopped the music."

    def next_track(self) -> str:
        try:
            self.mopidy.next_track()
        except MopidyError as e:
            raise MusicError(str(e)) from e
        name = self.mopidy.current_track_name()
        return f"Playing {name}." if name else "Next one."

    def set_volume(self, volume: int) -> str:
        try:
            self.mopidy.set_volume(volume)
        except MopidyError as e:
            raise MusicError(str(e)) from e
        return "Done."

    # ---- ducking -------------------------------------------------------

    @contextmanager
    def ducked(self):
        """Drop the music under speech for the duration of the block.

        Music and the assistant share one speaker. Without this the mic
        hears the music, the wake word never lands, and the person ends
        up shouting at the box. Restores whatever the volume was before,
        not a constant, so a user who turned it down stays turned down.

        Every failure here is swallowed on purpose: not being able to
        duck must never stop Saathi from answering or taking a call.
        """
        previous = None
        try:
            previous = self.mopidy.get_volume()
            self.mopidy.set_volume(MUSIC_DUCK_VOLUME)
        except MopidyError as e:
            log.warning("Couldn't duck music: %s", e)

        try:
            yield
        finally:
            try:
                self.mopidy.set_volume(previous if previous is not None else MUSIC_NORMAL_VOLUME)
            except MopidyError as e:
                log.warning("Couldn't restore music volume: %s", e)
