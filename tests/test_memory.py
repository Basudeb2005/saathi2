import pytest

from saathi.memory import build_memory
from saathi.memory.base import Fact, MemoryStore, NullMemory
from saathi.memory.supermemory import SupermemoryStore
from tests.fakes import FakeSession


def store(responses):
    return SupermemoryStore(api_key="sm_test", session=FakeSession(responses))


# ---- the null backend ---------------------------------------------------

def test_null_remembers_nothing():
    assert NullMemory().remember("x") is False


def test_null_recalls_nothing():
    assert NullMemory().recall("x", limit=5) == []


def test_unknown_backend_degrades_instead_of_raising():
    assert build_memory("nonsense").name == "none"


def test_supermemory_without_a_key_degrades(monkeypatch):
    """A misconfigured memory backend must turn the feature off, not take
    the whole conversation down with it."""
    monkeypatch.setattr("saathi.config.SUPERMEMORY_API_KEY", None)
    monkeypatch.setattr("saathi.memory.supermemory.SUPERMEMORY_API_KEY", None)
    assert build_memory("supermemory").name == "none"


# ---- writes -------------------------------------------------------------

def test_remember_posts_the_content_and_tag():
    session = FakeSession([{"id": "m1"}])
    SupermemoryStore(api_key="sm_test", container_tag="home-1", session=session).remember("Arun is her grandson")
    sent = session.calls[0]["json"]
    assert sent["content"] == "Arun is her grandson"
    assert sent["containerTag"] == "home-1"


def test_remember_sends_the_bearer_token():
    session = FakeSession([{"id": "m1"}])
    SupermemoryStore(api_key="sm_abc", session=session).remember("x")
    assert session.calls[0]["headers"]["Authorization"] == "Bearer sm_abc"


def test_remember_ignores_blank_text():
    session = FakeSession([])
    assert SupermemoryStore(api_key="sm_test", session=session).remember("   ") is False
    assert session.calls == []


def test_remember_survives_a_network_failure():
    import requests
    session = FakeSession([requests.ConnectionError("down")])
    assert SupermemoryStore(api_key="sm_test", session=session).remember("x") is False


# ---- reads --------------------------------------------------------------

def test_recall_reads_the_results_array():
    facts = store([{"results": [{"memory": "Arun is her grandson"}]}]).recall("grandson", limit=5)
    assert [f.text for f in facts] == ["Arun is her grandson"]


def test_recall_accepts_the_other_field_names():
    """The response shape has moved between API versions; a renamed field
    should cost a recall, not crash a conversation."""
    for key in ("memory", "content", "summary", "text"):
        facts = store([{"results": [{key: "a fact"}]}]).recall("x", limit=5)
        assert [f.text for f in facts] == ["a fact"], key


def test_recall_reads_documents_and_memories_keys():
    for key in ("memories", "documents"):
        facts = store([{key: [{"content": "a fact"}]}]).recall("x", limit=5)
        assert [f.text for f in facts] == ["a fact"], key


def test_recall_digs_into_chunks():
    body = {"results": [{"chunks": [{"content": "chunked fact"}]}]}
    assert [f.text for f in store([body]).recall("x", limit=5)] == ["chunked fact"]


def test_recall_respects_the_limit():
    body = {"results": [{"memory": f"fact {i}"} for i in range(20)]}
    assert len(store([body]).recall("x", limit=3)) == 3


def test_recall_drops_empty_rows():
    body = {"results": [{"memory": "  "}, {"memory": "real"}]}
    assert [f.text for f in store([body]).recall("x", limit=5)] == ["real"]


def test_recall_survives_a_network_failure():
    import requests
    session = FakeSession([requests.ConnectionError("down")])
    assert SupermemoryStore(api_key="sm_test", session=session).recall("x", limit=5) == []


def test_recall_survives_an_unexpected_shape():
    assert store([{"unexpected": True}]).recall("x", limit=5) == []


def test_blank_query_never_hits_the_network():
    session = FakeSession([])
    assert SupermemoryStore(api_key="sm_test", session=session).recall("", limit=5) == []
    assert session.calls == []


# ---- prompt rendering ---------------------------------------------------

def test_prompt_is_empty_when_nothing_is_remembered():
    assert NullMemory().describe_for_prompt([]) == ""


def test_prompt_hedges_rather_than_asserting():
    """A stale fact stated as certainty gets asserted confidently at
    someone who will believe it."""
    text = NullMemory().describe_for_prompt([Fact(text="Arun is her grandson")])
    assert "Arun is her grandson" in text
    assert "out of date" in text


def test_every_store_satisfies_the_interface():
    for cls in (NullMemory, SupermemoryStore):
        assert issubclass(cls, MemoryStore)
