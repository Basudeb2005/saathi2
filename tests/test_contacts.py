import json

import pytest

from saathi.contacts import (
    Contact,
    ContactNotFoundError,
    ContactsRegistry,
    InvalidContactError,
)


@pytest.fixture
def registry(tmp_path):
    return ContactsRegistry(path=tmp_path / "contacts.json")


def test_starts_empty_when_no_file(registry):
    assert registry.names() == []


def test_add_and_get_sip_contact(registry):
    registry.add("daughter", "sip", "sip:priya@example.com", label="your daughter")
    contact = registry.get("daughter")
    assert contact.transport == "sip"
    assert contact.is_free


def test_pstn_contact_is_not_free(registry):
    registry.add("doctor", "pstn", "+15551234567")
    assert registry.get("doctor").is_free is False


def test_rejects_bad_pstn_number(registry):
    with pytest.raises(InvalidContactError):
        registry.add("doctor", "pstn", "not-a-number")


def test_rejects_bad_sip_uri(registry):
    with pytest.raises(InvalidContactError):
        registry.add("daughter", "sip", "+15551234567")


def test_rejects_unknown_transport(registry):
    with pytest.raises(InvalidContactError):
        registry.add("daughter", "telepathy", "sip:x@y.com")


def test_unknown_name_lists_known_ones(registry):
    registry.add("daughter", "sip", "sip:priya@example.com")
    with pytest.raises(ContactNotFoundError) as excinfo:
        registry.get("son")
    assert "daughter" in str(excinfo.value)


def test_persists_across_instances(tmp_path):
    path = tmp_path / "contacts.json"
    ContactsRegistry(path=path).add("daughter", "sip", "sip:priya@example.com")
    assert ContactsRegistry(path=path).get("daughter").address == "sip:priya@example.com"


def test_remove(registry):
    registry.add("daughter", "sip", "sip:priya@example.com")
    registry.remove("daughter")
    assert registry.names() == []


def test_bare_string_entry_infers_transport(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps({"daughter": "sip:priya@example.com", "doctor": "+15551234567"}))
    registry = ContactsRegistry(path=path)
    assert registry.get("daughter").transport == "sip"
    assert registry.get("doctor").transport == "pstn"


def test_prompt_listing_omits_addresses(registry):
    registry.add("doctor", "pstn", "+15551234567", label="Dr. Rao")
    listing = registry.describe_for_prompt()
    assert "doctor" in listing and "Dr. Rao" in listing
    assert "+15551234567" not in listing
