import pytest

from saathi.calling import sip
from saathi.contacts import Contact


def test_sip_contact_uses_the_free_trunk(monkeypatch):
    monkeypatch.setattr(sip, "LIVEKIT_SIP_TRUNK_ID", "ST_free")
    contact = Contact(transport="sip", address="sip:priya@example.com")
    assert sip.trunk_for(contact) == "ST_free"


def test_pstn_contact_uses_the_paid_trunk(monkeypatch):
    monkeypatch.setattr(sip, "LIVEKIT_PSTN_TRUNK_ID", "ST_paid")
    contact = Contact(transport="pstn", address="+15551234567")
    assert sip.trunk_for(contact) == "ST_paid"


def test_missing_sip_trunk_names_the_env_var(monkeypatch):
    monkeypatch.setattr(sip, "LIVEKIT_SIP_TRUNK_ID", None)
    contact = Contact(transport="sip", address="sip:priya@example.com")
    with pytest.raises(sip.CallError, match="LIVEKIT_SIP_TRUNK_ID"):
        sip.trunk_for(contact)


def test_missing_pstn_trunk_names_the_env_var(monkeypatch):
    monkeypatch.setattr(sip, "LIVEKIT_PSTN_TRUNK_ID", "")
    contact = Contact(transport="pstn", address="+15551234567")
    with pytest.raises(sip.CallError, match="LIVEKIT_PSTN_TRUNK_ID"):
        sip.trunk_for(contact)


def test_identity_is_stable_per_contact():
    assert sip._identity_for("daughter") == sip._identity_for("daughter")
    assert sip._identity_for("daughter") != sip._identity_for("son")
