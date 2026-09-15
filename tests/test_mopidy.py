import pytest

from saathi.music.mopidy import MopidyClient, MopidyError
from tests.fakes import FakeSession, rpc_error, rpc_ok


def client(responses):
    return MopidyClient(url="http://localhost:6680/mopidy/rpc", session=FakeSession(responses))


def test_play_uris_clears_then_adds_then_plays():
    session = FakeSession([rpc_ok(), rpc_ok(), rpc_ok()])
    MopidyClient(session=session).play_uris(["youtube:video/abc"])
    methods = [c["json"]["method"] for c in session.calls]
    assert methods == ["core.tracklist.clear", "core.tracklist.add", "core.playback.play"]


def test_play_uris_rejects_empty_list():
    with pytest.raises(MopidyError):
        client([]).play_uris([])


def test_jsonrpc_error_becomes_mopidy_error():
    with pytest.raises(MopidyError, match="rejected"):
        client([rpc_error("no such method")]).stop()


def test_unreachable_server_names_the_url():
    import requests

    session = FakeSession([requests.ConnectionError("refused")])
    with pytest.raises(MopidyError, match="mopidy service"):
        MopidyClient(session=session).stop()


def test_search_returns_uris_in_order():
    payload = rpc_ok([
        {"tracks": [{"uri": "youtube:video/a"}, {"uri": "youtube:video/b"}]},
    ])
    assert client([payload]).search_tracks("lata") == ["youtube:video/a", "youtube:video/b"]


def test_search_respects_limit_across_backends():
    payload = rpc_ok([
        {"tracks": [{"uri": f"youtube:video/{i}"} for i in range(10)]},
    ])
    assert len(client([payload]).search_tracks("lata", limit=3)) == 3


def test_search_with_no_results_is_empty_not_an_error():
    assert client([rpc_ok([])]).search_tracks("nonsense") == []


def test_current_track_name_combines_artist_and_title():
    payload = rpc_ok({"name": "Lag Ja Gale", "artists": [{"name": "Lata Mangeshkar"}]})
    assert client([payload]).current_track_name() == "Lata Mangeshkar - Lag Ja Gale"


def test_current_track_name_is_none_when_nothing_loaded():
    assert client([rpc_ok(None)]).current_track_name() is None


def test_volume_is_clamped():
    session = FakeSession([rpc_ok(), rpc_ok()])
    c = MopidyClient(session=session)
    c.set_volume(500)
    c.set_volume(-20)
    assert [call["json"]["params"]["volume"] for call in session.calls] == [100, 0]


def test_uri_schemes_lists_loaded_backends():
    """Installing Mopidy-YouTube isn't the same as Mopidy loading it, and
    the only symptom of a skipped extension is empty song searches."""
    assert client([rpc_ok(["http", "youtube"])]).uri_schemes() == ["http", "youtube"]


def test_uri_schemes_is_empty_not_none_when_unset():
    assert client([rpc_ok(None)]).uri_schemes() == []


def test_search_gets_the_longer_timeout():
    """A YouTube lookup goes through yt-dlp and out to the network; the
    control-command timeout is far too short for it."""
    from saathi.config import MOPIDY_SEARCH_TIMEOUT_S

    session = FakeSession([rpc_ok([])])
    MopidyClient(session=session, timeout=10).search_tracks("lata")
    assert session.calls[0]["timeout"] == MOPIDY_SEARCH_TIMEOUT_S


def test_control_commands_keep_the_short_timeout():
    session = FakeSession([rpc_ok()])
    MopidyClient(session=session, timeout=10).stop()
    assert session.calls[0]["timeout"] == 10


def test_a_timeout_does_not_claim_mopidy_is_down():
    import requests

    session = FakeSession([requests.Timeout("slow")])
    with pytest.raises(MopidyError, match="in time"):
        MopidyClient(session=session).search_tracks("lata")


# ---- position and version ----------------------------------------------

def test_time_position_is_the_only_honest_playback_test():
    """Mopidy 3 on GStreamer 1.26 says "playing" for a track whose
    position never moves. Nothing else can tell that apart from sound
    actually coming out of the speaker."""
    assert client([rpc_ok(4200)]).time_position() == 4200


def test_time_position_when_nothing_is_loaded():
    """Mopidy returns null, and comparing two of those is a TypeError in
    the caller sampling it twice."""
    assert client([rpc_ok(None)]).time_position() == 0


def test_version():
    assert client([rpc_ok("4.0.4")]).version() == "4.0.4"


def test_version_when_mopidy_does_not_say():
    assert client([rpc_ok(None)]).version() == "unknown"
