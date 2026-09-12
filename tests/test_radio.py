import pytest

from saathi.music.radio import RadioBrowser, RadioError
from tests.fakes import FakeSession


def station(name, url, **extra):
    return {"name": name, "url": url, "url_resolved": extra.get("resolved", url), **extra}


def test_search_prefers_resolved_url():
    session = FakeSession([[station("BBC", "http://redirect", resolved="http://real")], []])
    results = RadioBrowser(session=session).search("BBC")
    assert results[0].url == "http://real"


def test_search_falls_back_to_tag_when_name_is_thin():
    session = FakeSession([[], [station("Jazz FM", "http://jazz")]])
    results = RadioBrowser(session=session).search("jazz")
    assert [s.name for s in results] == ["Jazz FM"]
    assert session.calls[0]["params"]["name"] == "jazz"
    assert session.calls[1]["params"]["tag"] == "jazz"


def test_tag_results_do_not_duplicate_name_results():
    same = station("Jazz FM", "http://jazz")
    session = FakeSession([[same], [same]])
    assert len(RadioBrowser(session=session).search("jazz")) == 1


def test_hidebroken_is_always_requested():
    session = FakeSession([[station("A", "http://a")], []])
    RadioBrowser(session=session).search("a")
    assert session.calls[0]["params"]["hidebroken"] == "true"


def test_stations_without_a_url_are_dropped():
    session = FakeSession([[station("Dead", ""), station("Live", "http://live")], []])
    results = RadioBrowser(session=session).search("x")
    assert [s.name for s in results] == ["Live"]


def test_best_returns_none_when_nothing_found():
    session = FakeSession([[], []])
    assert RadioBrowser(session=session).best("nothing") is None


def test_empty_query_rejected():
    with pytest.raises(RadioError):
        RadioBrowser(session=FakeSession()).search("   ")


def test_network_failure_becomes_radio_error():
    import requests

    session = FakeSession([requests.ConnectionError("down")])
    with pytest.raises(RadioError, match="Couldn't reach"):
        RadioBrowser(session=session).search("jazz")


def test_user_agent_is_sent():
    session = FakeSession([[station("A", "http://a")], []])
    RadioBrowser(session=session).search("a")
    assert "saathi" in session.calls[0]["headers"]["User-Agent"]
