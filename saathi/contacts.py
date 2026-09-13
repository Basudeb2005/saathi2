"""
Contacts registry: name -> how to reach that person.

Schema, in contacts.json:

    {
      "daughter": {
        "transport": "sip",
        "address": "sip:priya@saathi.example.com",
        "label": "your daughter"
      },
      "doctor": {
        "transport": "pstn",
        "address": "+15551234567",
        "label": "Dr. Rao's clinic"
      }
    }

`transport` is the whole point of this file. Two kinds of contact, two
costs:

  - "sip"  -> Linphone (or any SIP softphone) registered against your own
              server. Rings their phone properly, costs nothing per
              minute. This is the default and what most contacts should
              be.
  - "pstn" -> a real phone number dialled out through a SIP trunk. Rings
              any phone on earth with nothing installed, and bills per
              minute. Use it for the people who won't install an app.

Both end up as a SIP participant in the same LiveKit room, so the rest of
the codebase does not branch on this -- only calling/sip.py picks a trunk
from it.

Deliberately a fixed, explicitly-configured list rather than "dial
whatever number was spoken aloud": an elderly user misspeaking a number,
or the model mishearing one, should never be able to dial an arbitrary
destination. The agent can only ever pass a *name* from this registry
through to the calling layer.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from saathi.config import CONTACTS_PATH
from saathi.logging_setup import get_logger

log = get_logger("contacts")

TRANSPORTS = ("sip", "pstn")

# E.164: optional leading +, 1-15 digits, first digit non-zero.
_E164_RE = re.compile(r"^\+?[1-9]\d{1,14}$")
# Loose on purpose. A SIP URI's user part allows far more than this, and
# rejecting a valid address someone pasted from Linphone is a worse
# failure than accepting an odd one and letting the trunk reject it.
_SIP_URI_RE = re.compile(r"^sips?:[^@\s]+@[^@\s]+$")


class ContactsError(Exception):
    """Base class for contacts-registry problems."""


class ContactNotFoundError(ContactsError):
    def __init__(self, name: str, known: List[str]):
        self.name = name
        self.known = known
        super().__init__(
            f"No contact named {name!r}. Known contacts: {', '.join(known) or '(none configured)'}"
        )


class InvalidContactError(ContactsError):
    """The address doesn't match its declared transport. Raised at add()
    time so a bad contact fails when it's configured, not at 2am when
    someone is trying to call their daughter."""


@dataclass
class Contact:
    transport: str                # "sip" | "pstn"
    address: str                  # sip:user@host  |  +15551234567
    label: Optional[str] = None   # spoken phrase, e.g. "your daughter"

    @property
    def is_free(self) -> bool:
        """True if reaching this contact costs nothing per minute."""
        return self.transport == "sip"

    def to_dict(self) -> dict:
        return {"transport": self.transport, "address": self.address, "label": self.label}

    @staticmethod
    def from_dict(d) -> "Contact":
        # Accept a bare string for hand-edited files: infer the transport
        # from its shape rather than making someone type it out.
        if isinstance(d, str):
            return Contact(transport="sip" if d.startswith("sip") else "pstn", address=d)
        return Contact(
            transport=d.get("transport", "sip"),
            address=d["address"],
            label=d.get("label"),
        )


def _validate(transport: str, address: str) -> None:
    if transport not in TRANSPORTS:
        raise InvalidContactError(
            f"transport must be one of {', '.join(TRANSPORTS)}, got {transport!r}"
        )
    if transport == "pstn" and not _E164_RE.match(address):
        raise InvalidContactError(
            f"{address!r} is not a valid E.164 number for a pstn contact, e.g. '+15551234567'"
        )
    if transport == "sip" and not _SIP_URI_RE.match(address):
        raise InvalidContactError(
            f"{address!r} is not a valid SIP URI for a sip contact, e.g. 'sip:priya@example.com'"
        )


class ContactsRegistry:
    def __init__(self, path: Path = CONTACTS_PATH):
        self.path = Path(path)
        self._data: Dict[str, Contact] = {}
        self.load()

    # ---- persistence ---------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            log.info("No contacts registry at %s yet; starting empty", self.path)
            self._data = {}
            return

        with open(self.path, "r") as f:
            raw = json.load(f)

        self._data = {name: Contact.from_dict(entry) for name, entry in raw.items()}
        log.info("Loaded %d contact(s) from %s", len(self._data), self.path)

    def save(self) -> None:
        serializable = {name: c.to_dict() for name, c in self._data.items()}

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, prefix=".contacts.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(serializable, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.path)
        except Exception:
            os.unlink(tmp_path)
            raise
        log.info("Saved %d contact(s) to %s", len(self._data), self.path)

    # ---- reads ---------------------------------------------------------

    def names(self) -> List[str]:
        return sorted(self._data.keys())

    def get(self, name: str) -> Contact:
        """Look a contact up, forgiving how speech recognition heard it.

        "Basudeb" comes back as Basudev, Vasudev or Basudeep depending on
        the room and the accent, and names are exactly what STT is worst
        at. Refusing those is a box that won't call your son because it
        misheard one consonant.

        Only close matches count, and only when one contact is clearly
        closest: two plausible candidates means it must ask rather than
        pick, since dialling the wrong person is worse than a question.
        """
        key = (name or "").strip()
        if key in self._data:
            return self._data[key]

        lowered = {n.lower(): n for n in self._data}
        if key.lower() in lowered:
            return self._data[lowered[key.lower()]]

        close = difflib.get_close_matches(key.lower(), list(lowered), n=2, cutoff=0.7)
        if len(close) == 1:
            matched = lowered[close[0]]
            log.info("Heard %r, matched contact %r", name, matched)
            return self._data[matched]

        raise ContactNotFoundError(name, self.names())

    def describe_for_prompt(self) -> str:
        """Compact listing for the agent's system prompt. Addresses are
        deliberately omitted -- the model never needs them and shouldn't
        be reciting a phone number aloud."""
        if not self._data:
            return "(no contacts configured yet)"
        lines = []
        for name in self.names():
            c = self._data[name]
            lines.append(f"- {name!r} ({c.label})" if c.label else f"- {name!r}")
        return "\n".join(lines)

    # ---- writes --------------------------------------------------------

    def add(self, name: str, transport: str, address: str, label: Optional[str] = None) -> Contact:
        _validate(transport, address)
        contact = Contact(transport=transport, address=address, label=label)
        self._data[name] = contact
        log.info("Registering contact name=%s transport=%s label=%r", name, transport, label)
        self.save()
        return contact

    def remove(self, name: str) -> None:
        try:
            del self._data[name]
        except KeyError:
            raise ContactNotFoundError(name, self.names()) from None
        log.info("Removed contact name=%s", name)
        self.save()
