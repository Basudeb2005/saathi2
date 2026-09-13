import json

import pytest

from saathi.calling.cli import build_trunk, main
from saathi.contacts import ContactsRegistry


# ---- trunk json ---------------------------------------------------------

def test_trunk_has_the_shape_lk_expects():
    trunk = build_trunk("saathi-sip", "sip.linphone.org", "saathi-home", "pw")["trunk"]
    assert trunk["address"] == "sip.linphone.org"
    assert trunk["auth_username"] == "saathi-home"
    assert trunk["auth_password"] == "pw"


def test_trunk_identity_is_the_username():
    trunk = build_trunk("t", "sip.example.com", "saathi-home", "pw")["trunk"]
    assert trunk["numbers"] == ["saathi-home"]


def test_trunk_is_json_serialisable():
    json.dumps(build_trunk("t", "a", "u", "p"))


# ---- contacts subcommand ------------------------------------------------

@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "contacts.json"
    monkeypatch.setattr("saathi.contacts.CONTACTS_PATH", path)
    monkeypatch.setattr("saathi.calling.cli.ContactsRegistry", lambda: ContactsRegistry(path=path))
    return path


def test_add_infers_pstn_from_a_leading_plus(registry, capsys):
    assert main(["contacts", "add", "doctor", "+15551234567"]) == 0
    assert ContactsRegistry(path=registry).get("doctor").transport == "pstn"


def test_add_infers_sip_from_a_uri(registry):
    assert main(["contacts", "add", "priya", "sip:priya@sip.linphone.org"]) == 0
    assert ContactsRegistry(path=registry).get("priya").is_free


def test_add_rejects_a_mismatched_address(registry, capsys):
    assert main(["contacts", "add", "priya", "not-an-address"]) == 1


def test_list_is_fine_when_empty(registry):
    assert main(["contacts", "list"]) == 0


def test_remove_unknown_contact_fails_cleanly(registry):
    assert main(["contacts", "remove", "nobody"]) == 1


def test_add_then_remove_round_trips(registry):
    main(["contacts", "add", "priya", "sip:priya@sip.linphone.org"])
    assert main(["contacts", "remove", "priya"]) == 0
    assert ContactsRegistry(path=registry).names() == []


def test_label_is_stored(registry):
    main(["contacts", "add", "priya", "sip:priya@x.com", "--label", "your daughter"])
    assert ContactsRegistry(path=registry).get("priya").label == "your daughter"


# ---- check --------------------------------------------------------------

def test_check_reports_not_ready_when_nothing_is_configured(registry, monkeypatch):
    monkeypatch.setattr("saathi.calling.cli.LIVEKIT_SIP_TRUNK_ID", "")
    assert main(["check"]) == 1


def test_test_command_rejects_an_unknown_name(registry):
    assert main(["test", "nobody"]) == 1
