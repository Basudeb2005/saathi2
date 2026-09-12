import pytest

from saathi.music.mopidy import MopidyError
from saathi.music.player import MusicError, MusicPlayer
from saathi.music.radio import RadioError, Station
from tests.fakes import FakeMopidy, FakeRadio


def player(mopidy=None, radio=None):
    return MusicPlayer(mopidy=mopidy or FakeMopidy(), radio=radio or FakeRadio())


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
