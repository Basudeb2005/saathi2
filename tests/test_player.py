import pytest

from saathi.music.mopidy import MopidyError
from saathi.music.player import MusicError, MusicPlayer
from saathi.music.radio import RadioError, Station
from tests.fakes import FakeMopidy, FakeRadio


def player(mopidy=None, radio=None, cache=None):
    # An isolated cache per player: tests must not read each other's
    # results, nor write into the developer's real cache file.
    import tempfile, pathlib as _p
    from saathi.music.player import SongCache

    cache = cache or SongCache(path=_p.Path(tempfile.mkdtemp()) / "cache.json")
    return MusicPlayer(mopidy=mopidy or FakeMopidy(), radio=radio or FakeRadio(), cache=cache)


def test_station_source_plays_the_stream_url():
    mopidy = FakeMopidy()
    radio = FakeRadio(station=Station(name="Jazz FM", url="http://jazz"))
    reply = player(mopidy, radio).play("jazz", source="station")
    assert mopidy.played == [["http://jazz"]]
    assert reply == "Playing Jazz FM."


def test_song_source_plays_search_results():
    mopidy = FakeMopidy(search_results=["youtube:video/a"], track_name="Lata - Lag Ja Gale")
    reply = player(mopidy).play("lag ja gale", source="song")
    assert mopidy.played == [["youtube:video/a"]]
    assert reply == "Playing Lata - Lag Ja Gale."


def test_song_confirmation_falls_back_to_query_when_track_unknown():
    mopidy = FakeMopidy(search_results=["youtube:video/a"], track_name=None)
    assert player(mopidy).play("something", source="song") == "Playing something."


def test_auto_falls_back_to_radio_when_no_song_found():
    mopidy = FakeMopidy(search_results=[])
    radio = FakeRadio(station=Station(name="Hindi Gold", url="http://hindi"))
    reply = player(mopidy, radio).play("old hindi songs", source="auto")
    assert reply == "Playing Hindi Gold."
    assert mopidy.played == [["http://hindi"]]


def test_song_source_does_not_fall_back():
    radio = FakeRadio(station=Station(name="Hindi Gold", url="http://hindi"))
    with pytest.raises(MusicError):
        player(FakeMopidy(search_results=[]), radio).play("nothing", source="song")
    assert radio.queries == []


def test_auto_surfaces_radio_failure_when_both_fail():
    mopidy = FakeMopidy(search_results=[])
    radio = FakeRadio(error=RadioError("radio browser is down"))
    with pytest.raises(MusicError, match="radio browser is down"):
        player(mopidy, radio).play("anything", source="auto")


def test_unknown_source_rejected():
    with pytest.raises(MusicError):
        player().play("jazz", source="vinyl")


def test_empty_query_rejected():
    with pytest.raises(MusicError):
        player().play("   ")


def test_transport_controls():
    mopidy = FakeMopidy()
    p = player(mopidy)
    p.pause()
    p.resume()
    p.stop()
    assert mopidy.actions == ["pause", "resume", "stop"]


def test_ducking_restores_previous_volume_not_a_constant():
    mopidy = FakeMopidy()
    mopidy.volume = 35
    with player(mopidy).ducked():
        assert mopidy.volume == 20
    assert mopidy.volume == 35


def test_ducking_restores_even_if_the_block_raises():
    mopidy = FakeMopidy()
    mopidy.volume = 55
    with pytest.raises(ValueError):
        with player(mopidy).ducked():
            raise ValueError("call failed")
    assert mopidy.volume == 55


def test_ducking_never_breaks_the_turn_when_mopidy_is_down():
    class Broken(FakeMopidy):
        def get_volume(self):
            raise MopidyError("down")

        def set_volume(self, volume):
            raise MopidyError("down")

    ran = False
    with player(Broken()).ducked():
        ran = True
    assert ran


def test_a_song_stops_instead_of_rolling_into_the_next_result():
    """"Play Lag Ja Gale" asks for one song. Wandering into whatever
    YouTube ranked fourth isn't that, and someone who can't easily say
    "stop" is then stuck with it."""
    mopidy = FakeMopidy(search_results=["youtube:video/a", "youtube:video/b"])
    player(mopidy).play("lag ja gale", source="song")
    assert mopidy.single is True


def test_a_station_does_not_get_single_mode():
    mopidy = FakeMopidy()
    radio = FakeRadio(station=Station(name="Jazz FM", url="http://jazz"))
    player(mopidy, radio).play("jazz", source="station")
    assert mopidy.single is False


def test_the_other_results_stay_queued_for_next():
    mopidy = FakeMopidy(search_results=["youtube:video/a", "youtube:video/b"])
    player(mopidy).play("lag ja gale", source="song")
    assert mopidy.played == [["youtube:video/a", "youtube:video/b"]]


# ---- the song cache ----------------------------------------------------

def _cache(tmp_path):
    from saathi.music.player import SongCache

    return SongCache(path=tmp_path / "cache.json")


def test_a_repeat_request_skips_the_search(tmp_path):
    """A first YouTube lookup is ten to twenty seconds on a Pi. The
    second time should be instant."""
    cache = _cache(tmp_path)
    mopidy = FakeMopidy(search_results=["youtube:video/a"])
    player(mopidy, cache=cache).play("lag ja gale", source="song")

    again = FakeMopidy(search_results=[])          # would find nothing
    reply = player(again, cache=cache).play("lag ja gale", source="song")
    assert again.actions == []                      # never searched
    assert again.played == [["youtube:video/a"]]
    assert "Playing" in reply


def test_the_cache_ignores_case_and_spacing(tmp_path):
    cache = _cache(tmp_path)
    cache.put("Lag  Ja   Gale", ["youtube:video/a"])
    assert cache.get("lag ja gale") == ["youtube:video/a"]


def test_nothing_found_is_not_cached(tmp_path):
    cache = _cache(tmp_path)
    cache.put("nonsense", [])
    assert cache.get("nonsense") is None


def test_the_cache_survives_a_restart(tmp_path):
    _cache(tmp_path).put("lag ja gale", ["youtube:video/a"])
    assert _cache(tmp_path).get("lag ja gale") == ["youtube:video/a"]


def test_oldest_entries_are_evicted_first(tmp_path):
    from saathi.music.player import SongCache

    cache = SongCache(path=tmp_path / "cache.json", size=2)
    for name in ("first", "second", "third"):
        cache.put(name, [f"youtube:video/{name}"])
    assert cache.get("first") is None
    assert cache.get("third") is not None


def test_a_corrupt_cache_file_is_survivable(tmp_path):
    """A cache that can't be read is a slower lookup, never a failed one."""
    from saathi.music.player import SongCache

    path = tmp_path / "cache.json"
    path.write_text("{ this is not json")
    assert SongCache(path=path).get("anything") is None


# ---- yt-dlp staleness ---------------------------------------------------

def test_a_fresh_ytdlp_is_not_stale():
    import datetime

    from saathi.music.cli import _ytdlp_is_stale

    today = datetime.date.today()
    assert _ytdlp_is_stale(f"{today:%Y.%m.%d}") is False


def test_an_old_ytdlp_is_stale():
    """YouTube changes something every few weeks and every yt-dlp older
    than the change stops extracting — silently, as "no results" or a
    track that loads and plays nothing."""
    from saathi.music.cli import _ytdlp_is_stale

    assert _ytdlp_is_stale("2023.07.06") is True


def test_a_version_that_is_not_a_date_is_not_judged():
    """A distro build, or a future scheme. Guessing "stale" would send
    people to update something that was never the problem."""
    from saathi.music.cli import _ytdlp_is_stale

    for version in ("", "unknown", "2025.9", "nightly", "2024.13.45"):
        assert _ytdlp_is_stale(version) is False
