"""
Placing a call = adding a SIP participant to the room Saathi is already in.

That framing is the whole reason this project is on LiveKit. There is no
separate "call mode", no audio bridge, no mu-law conversion, no webhook
round-trip. The person in the living room and the person on the other end
are both just participants in one room, and the media server handles
mixing, reconnection and echo cancellation.

Which trunk a call goes out on is decided entirely by the contact's
`transport` (see contacts.py):

  - sip  -> LIVEKIT_SIP_TRUNK_ID, terminating at Linphone on a family
            member's phone. Free.
  - pstn -> LIVEKIT_PSTN_TRUNK_ID, terminating at a real phone number
            through a paid trunk. Bills per minute.

Nothing else in the codebase branches on that distinction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from saathi.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_PSTN_TRUNK_ID,
    LIVEKIT_SIP_TRUNK_ID,
    LIVEKIT_URL,
)
from saathi.contacts import Contact
from saathi.logging_setup import get_logger

log = get_logger("calling.sip")


class CallError(Exception):
    """The call couldn't be placed — missing config, or the trunk
    rejected it. Distinct from ContactNotFoundError, which means the
    *name* didn't resolve. The message is written to be spoken."""


@dataclass
class CallResult:
    contact: str
    participant_identity: str
    free: bool


def trunk_for(contact: Contact) -> str:
    """Pick the trunk for this contact, or explain what's unconfigured.

    Failing here names the exact .env variable to set, because the
    alternative — a call that silently never rings — is the single most
    confusing failure this system can produce.
    """
    if contact.transport == "sip":
        if not LIVEKIT_SIP_TRUNK_ID:
            raise CallError(
                "LIVEKIT_SIP_TRUNK_ID isn't set, so I can't reach contacts on Linphone. "
                "Create an outbound trunk in LiveKit pointing at your SIP server and put "
                "its id in .env."
            )
        return LIVEKIT_SIP_TRUNK_ID

    if not LIVEKIT_PSTN_TRUNK_ID:
        raise CallError(
            "LIVEKIT_PSTN_TRUNK_ID isn't set, so I can't dial real phone numbers. "
            "Add a trunk from your telephony provider and put its id in .env."
        )
    return LIVEKIT_PSTN_TRUNK_ID


def sip_call_to(contact: Contact) -> str:
    """What LiveKit's sip_call_to field actually wants.

    Not a URI. The domain comes from the trunk's own address, so passing
    "sip:priya@sip.linphone.org" is rejected outright — it wants "priya".
    Contacts still store the full address because that is the thing a
    person can read, verify and paste from their phone; the narrowing
    happens here, at the boundary that cares.

    A PSTN number goes through untouched.
    """
    if contact.transport == "pstn":
        return contact.address

    address = contact.address
    for scheme in ("sips:", "sip:"):
        if address.startswith(scheme):
            address = address[len(scheme):]
            break
    return address.split("@", 1)[0]


def _identity_for(name: str) -> str:
    """Stable per-contact identity, so a second call to the same person
    replaces the first rather than putting two of them in the room."""
    return f"caller-{name}"


async def place_call(
    name: str,
    contact: Contact,
    room_name: str,
    api=None,
) -> CallResult:
    """Dial `contact` into `room_name`. `api` is injectable for tests.

    Returns as soon as the invite is accepted by the trunk — not when the
    person picks up. Ringing takes as long as it takes, and blocking the
    agent's turn on it would leave the user standing in silence wondering
    whether Saathi heard them.
    """
    trunk_id = trunk_for(contact)

    # Close only what we opened — an injected client belongs to the caller.
    ours = api is None

    if api is None:
        if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
            raise CallError(
                "LiveKit isn't configured — set LIVEKIT_URL, LIVEKIT_API_KEY and "
                "LIVEKIT_API_SECRET in .env."
            )
        from livekit import api as lk_api  # imported here so tests need no livekit install

        api = lk_api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    from livekit import api as lk_api  # noqa: F811  (same module; needed for the request type)

    identity = _identity_for(name)
    request = lk_api.CreateSIPParticipantRequest(
        sip_trunk_id=trunk_id,
        sip_call_to=sip_call_to(contact),
        room_name=room_name,
        participant_identity=identity,
        participant_name=contact.label or name,
    )

    log.info(
        "Placing call contact=%s to=%s transport=%s trunk=%s room=%s",
        name, request.sip_call_to, contact.transport, trunk_id, room_name,
    )
    try:
        await api.sip.create_sip_participant(request)
    except Exception as e:
        log.exception("Trunk rejected the call to contact=%s", name)
        raise CallError(
            f"I couldn't get through to {contact.label or name}. ({type(e).__name__}: {e})"
        ) from e
    finally:
        if ours:
            try:
                await api.aclose()
            except Exception:
                pass

    log.info("Call to contact=%s dialling as identity=%s", name, identity)
    return CallResult(contact=name, participant_identity=identity, free=contact.is_free)


async def hang_up(name: str, room_name: str, api=None) -> None:
    """Remove the contact's participant from the room, ending their leg
    of the call while leaving Saathi and the household untouched."""
    if api is None:
        from livekit import api as lk_api

        api = lk_api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    from livekit import api as lk_api  # noqa: F811

    log.info("Hanging up on contact=%s in room=%s", name, room_name)
    try:
        await api.room.remove_participant(
            lk_api.RoomParticipantIdentity(room=room_name, identity=_identity_for(name))
        )
    except Exception as e:
        # Already gone is the common case (they hung up first) and is not
        # worth surfacing to someone who just said "hang up".
        log.info("Couldn't remove participant for contact=%s: %s", name, e)
