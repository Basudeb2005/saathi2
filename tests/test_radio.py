import pytest

from saathi.music.radio import RadioBrowser, RadioError
from tests.fakes import FakeSession


def station(name, url, **extra):
    return {"name": name, "url": url, "url_resolved": extra.get("resolved", url), **extra}


def test_search_prefers_resolved_url():
    session = FakeSession([[station("BBC", "http://redirect", resolved="http://real")], []] + [[]] * 10)
    results = RadioBrowser(session=session).search("BBC")
    assert results[0].url == "http://real"


def test_search_falls_back_to_tag_when_name_is_thin():
    session = FakeSession([[], [station("Jazz FM", "http://jazz")]] + [[]] * 10)
    results = RadioBrowser(session=session).search("jazz")
    assert [s.name for s in results] == ["Jazz FM"]
    assert session.calls[0]["params"]["name"] == "jazz"
    assert session.calls[1]["params"]["tag"] == "jazz"


def test_tag_results_do_not_duplicate_name_results():
    same = station("Jazz FM", "http://jazz")
    session = FakeSession([[same], [same]] + [[]] * 10)
    assert len(RadioBrowser(session=session).search("jazz")) == 1


def test_hidebroken_is_always_requested():
    session = FakeSession([[station("A", "http://a")], []] + [[]] * 10)
    RadioBrowser(session=session).search("a")
    assert session.calls[0]["params"]["hidebroken"] == "true"


def test_stations_without_a_url_are_dropped():
    session = FakeSession([[station("Dead", ""), station("Live", "http://live")], []] + [[]] * 10)
    results = RadioBrowser(session=session).search("x")
    assert [s.name for s in results] == ["Live"]


def test_best_returns_none_when_nothing_found():
    session = FakeSession([[]] * 12)
    assert RadioBrowser(session=session).best("nothing") is None


def test_empty_query_rejected():
    with pytest.raises(RadioError):
        RadioBrowser(session=FakeSession()).search("   ")


def test_network_failure_becomes_radio_error():
    import requests

    from saathi.config import RADIO_BROWSER_MIRRORS

    # Every mirror down — one error per host it will try.
    session = FakeSession([requests.ConnectionError("down")] * (len(RADIO_BROWSER_MIRRORS) + 1))
    with pytest.raises(RadioError, match="No Radio Browser mirror responded"):
        RadioBrowser(session=session).search("jazz")


def test_spoken_phrases_are_reduced_to_keywords():
    """People don't speak in tags. "Play some old Hindi songs" has no
    station and no tag by that name, but "hindi" finds plenty."""
    from saathi.music.radio import _keywords

    assert _keywords("play some old hindi songs") == ["hindi"]


def test_keywords_keeps_the_specific_word_first():
    from saathi.music.radio import _keywords

    assert _keywords("bollywood fm radio")[0] == "bollywood"


def test_keywords_of_pure_filler_is_empty():
    from saathi.music.radio import _keywords

    assert _keywords("play me some music please") == []


def test_widens_to_language_when_the_phrase_finds_nothing():
    session = FakeSession([
        [],                                        # name="old hindi songs"
        [],                                        # tag="old hindi songs"
        [station("Vividh Bharati", "http://vb")],  # language="hindi"
    ] + [[]] * 10)
    results = RadioBrowser(session=session).search("old hindi songs")
    assert [s.name for s in results] == ["Vividh Bharati"]
    assert session.calls[2]["params"]["language"] == "hindi"


def test_falls_over_to_the_next_mirror():
    """One mirror dropping connections is routine, and must not read to
    the user as 'no music today'."""
    import requests

    session = FakeSession([
        requests.ConnectionError("de1 reset"),      # first mirror dies
        [station("Jazz FM", "http://jazz")],        # second answers
    ] + [[]] * 12)
    results = RadioBrowser(session=session).search("jazz")
    assert [s.name for s in results] == ["Jazz FM"]


def test_tries_the_configured_mirror_first():
    session = FakeSession([[station("A", "http://a")], []] + [[]] * 10)
    RadioBrowser(base_url="https://nl1.api.radio-browser.info", session=session).search("a")
    assert session.calls[0]["url"].startswith("https://nl1.api.radio-browser.info")


def test_user_agent_is_sent():
    session = FakeSession([[station("A", "http://a")], []] + [[]] * 10)
    RadioBrowser(session=session).search("a")
    assert "saathi" in session.calls[0]["headers"]["User-Agent"]
