"""
`python -m saathi.calling.cli` — get calling working, step by step.

Calling is the one feature you can't just switch on: it needs SIP
accounts that exist outside this project, a trunk created through
LiveKit's CLI, and contacts that match both. Four things in three
different places, and getting any of them subtly wrong produces the same
symptom — the phone never rings.

So this walks through it, writes the files it can write, prints the exact
commands for the ones it can't, and can place a real test call at the end.

    python -m saathi.calling.cli guide          # start here
    python -m saathi.calling.cli trunk          # write trunk.json
    python -m saathi.calling.cli contacts add   # add someone
    python -m saathi.calling.cli check          # what's missing?
    python -m saathi.calling.cli test priya     # actually ring their phone
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from saathi.config import (
    LIVEKIT_PSTN_TRUNK_ID,
    LIVEKIT_SIP_TRUNK_ID,
    ROOT_DIR,
    SAATHI_ROOM_NAME,
)
from saathi.contacts import (
    ContactNotFoundError,
    ContactsRegistry,
    InvalidContactError,
)
from saathi.logging_setup import get_logger, quiet_console

log = get_logger("calling.cli")

TRUNK_PATH = ROOT_DIR / "trunk.json"

BOLD, DIM, GREEN, YELLOW, RED, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
)


def _step(n: int, total: int, title: str) -> None:
    print(f"\n{BOLD}Step {n}/{total} — {title}{RESET}")


def _cmd(command: str) -> None:
    print(f"\n    {BOLD}{command}{RESET}\n")


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _confirm(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {prompt} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer in ("y", "yes")


# ---- pure helpers (tested) ---------------------------------------------

def build_trunk(name: str, address: str, username: str, password: str) -> dict:
    """The JSON `lk sip outbound create` expects.

    `numbers` is the identity calls appear to come from. For a SIP trunk
    that's the account's own username; for PSTN it's the purchased number.
    """
    return {
        "trunk": {
            "name": name,
            "address": address,
            "numbers": [username],
            "auth_username": username,
            "auth_password": password,
        }
    }


def calling_status() -> List[tuple]:
    """(label, ok, detail) for each thing calling needs."""
    contacts = ContactsRegistry()
    names = contacts.names()
    sip_contacts = [n for n in names if contacts.get(n).transport == "sip"]
    pstn_contacts = [n for n in names if contacts.get(n).transport == "pstn"]

    return [
        ("lk CLI installed", shutil.which("lk") is not None, "" if shutil.which("lk") else "not on PATH"),
        ("SIP trunk (free)", bool(LIVEKIT_SIP_TRUNK_ID), LIVEKIT_SIP_TRUNK_ID or "LIVEKIT_SIP_TRUNK_ID unset"),
        ("PSTN trunk (paid)", bool(LIVEKIT_PSTN_TRUNK_ID), LIVEKIT_PSTN_TRUNK_ID or "unset — optional"),
        ("Contacts", bool(names), f"{len(sip_contacts)} sip, {len(pstn_contacts)} pstn" if names else "contacts.json is empty"),
    ]


# ---- commands -----------------------------------------------------------

def cmd_guide(args) -> int:
    total = 5
    print(f"\n{BOLD}Setting up calling{RESET}")
    print(f"{DIM}Saathi doesn't dial anyone — it pulls them into the room it's already{RESET}")
    print(f"{DIM}in. These five steps build the path from that room to their phone.{RESET}")

    _step(1, total, "Everyone gets a free SIP address")
    print("  Each family member, and one for Saathi itself, registers at:")
    print(f"\n    {BOLD}https://subscribe.linphone.org/register/email{RESET}\n")
    print(f"  You get addresses like {BOLD}priya@sip.linphone.org{RESET}.")
    print("  They install Linphone on their phone and sign in.")
    print(f"\n  {YELLOW}Test this before going further:{RESET} lock an iPhone, leave it an")
    print("  hour, then call it from another Linphone. If it doesn't ring, iOS")
    print("  suspended the app and you need VoIP push. Find out now, not later.")
    if not _confirm("\n  Done that?", default=False):
        print(f"\n  {DIM}Come back when you have. Re-run: python -m saathi.calling.cli guide{RESET}\n")
        return 0

    _step(2, total, "Install LiveKit's CLI")
    if shutil.which("lk"):
        print(f"  {GREEN}✓{RESET} already installed")
    else:
        _cmd("curl -sSL https://get.livekit.io/cli | bash && lk cloud auth")
        if not _confirm("Installed and authenticated?", default=False):
            return 0

    _step(3, total, "Create the trunk")
    print("  This is Saathi's own SIP account — the identity calls go out as.")
    if not cmd_trunk(args):
        return 1

    _step(4, total, "Add contacts")
    print("  Only these names can ever be dialled. Nothing else, ever.")
    while _confirm("\n  Add a contact?", default=True):
        _add_contact_interactive()

    _step(5, total, "Try it")
    names = ContactsRegistry().names()
    if names:
        print(f"  Place a real call with:")
        _cmd(f"python -m saathi.calling.cli test {names[0]}")
        print(f"  Or just say {BOLD}\"call {names[0]}\"{RESET} to the speaker.")
    print(f"{DIM}  Check everything with: python -m saathi.calling.cli check{RESET}\n")
    return 0


def cmd_trunk(args) -> bool:
    print(f"\n  {DIM}Saathi's own SIP account — the one you made for the speaker.{RESET}")
    address = _ask("SIP server", "sip.linphone.org")
    username = _ask("Saathi's username (no @domain)")
    if not username:
        print(f"  {RED}A username is required.{RESET}")
        return False
    password = _ask("its password")

    trunk = build_trunk("saathi-sip", address, username, password)
    TRUNK_PATH.write_text(json.dumps(trunk, indent=2) + "\n")
    TRUNK_PATH.chmod(0o600)
    print(f"\n  {GREEN}✓{RESET} wrote {TRUNK_PATH} {DIM}(mode 600 — it has a password in it){RESET}")

    print("\n  Now create it in LiveKit:")
    _cmd(f"lk sip outbound create {TRUNK_PATH}")
    print(f"  That prints a trunk id like {BOLD}ST_7fK2mQx9pLnV{RESET}. Save it with:")
    _cmd("python -m saathi.setup --only calling")
    print(f"  {YELLOW}If it fails with 401 or 403{RESET}, the SIP server wants a REGISTER")
    print("  before it will accept a call. That's the known failure — see")
    print("  SETUP.md §5d for the self-hosted Asterisk fallback.\n")
    return True


def _add_contact_interactive() -> None:
    registry = ContactsRegistry()
    name = _ask("name Saathi will hear (e.g. priya, daughter)")
    if not name:
        return

    free = _confirm("  do they have Linphone? (no = dial a real number, costs money)")
    transport = "sip" if free else "pstn"
    example = "sip:priya@sip.linphone.org" if free else "+15551234567"
    address = _ask(f"their address ({example})")
    label = _ask("how Saathi should refer to them (e.g. your daughter)")

    try:
        registry.add(name, transport, address, label or None)
    except InvalidContactError as e:
        print(f"  {RED}✗{RESET} {e}")
        return
    cost = f"{GREEN}free{RESET}" if free else f"{YELLOW}paid per minute{RESET}"
    print(f"  {GREEN}✓{RESET} added {name} ({cost})")


def cmd_contacts(args) -> int:
    registry = ContactsRegistry()

    if args.action == "list":
        names = registry.names()
        if not names:
            print(f"\n  {DIM}No contacts yet. Add one: python -m saathi.calling.cli contacts add{RESET}\n")
            return 0
        print()
        for name in names:
            c = registry.get(name)
            cost = f"{GREEN}free{RESET}" if c.is_free else f"{YELLOW}paid{RESET}"
            print(f"  {BOLD}{name}{RESET}  {c.address}  [{cost}]  {DIM}{c.label or ''}{RESET}")
        print()
        return 0

    if args.action == "add":
        if args.name and args.address:
            transport = "pstn" if args.address.startswith("+") else "sip"
            try:
                registry.add(args.name, transport, args.address, args.label)
            except InvalidContactError as e:
                print(f"  {RED}✗{RESET} {e}")
                return 1
            print(f"  {GREEN}✓{RESET} added {args.name}")
            return 0
        _add_contact_interactive()
        return 0

    if args.action == "remove":
        if not args.name:
            print("  name required: contacts remove <name>")
            return 1
        try:
            registry.remove(args.name)
        except ContactNotFoundError as e:
            print(f"  {RED}✗{RESET} {e}")
            return 1
        print(f"  {GREEN}✓{RESET} removed {args.name}")
        return 0

    return 1


def cmd_check(args) -> int:
    print(f"\n{BOLD}Calling status{RESET}\n")
    rows = calling_status()
    for label, ok, detail in rows:
        mark = f"{GREEN}✓{RESET}" if ok else f"{YELLOW}!{RESET}"
        print(f"  {mark} {label:<22} {DIM}{detail}{RESET}")

    blocking = [label for label, ok, _ in rows if not ok and "PSTN" not in label]
    print()
    if blocking:
        print(f"{YELLOW}Not ready:{RESET} {', '.join(blocking)}")
        print(f"{DIM}Walk through it: python -m saathi.calling.cli guide{RESET}\n")
        return 1
    print(f"{GREEN}Calling is configured.{RESET}")
    print(f"{DIM}Try it: python -m saathi.calling.cli test <name>{RESET}\n")
    return 0


def cmd_test(args) -> int:
    registry = ContactsRegistry()
    try:
        contact = registry.get(args.name)
    except ContactNotFoundError as e:
        print(f"  {RED}✗{RESET} {e}")
        return 1

    from saathi.calling.sip import CallError, place_call

    cost = "free" if contact.is_free else "PAID — this will cost money"
    print(f"\n  Calling {BOLD}{contact.label or args.name}{RESET} at {contact.address}")
    print(f"  {DIM}room={SAATHI_ROOM_NAME}  transport={contact.transport}  ({cost}){RESET}\n")

    try:
        asyncio.run(place_call(args.name, contact, SAATHI_ROOM_NAME))
    except CallError as e:
        print(f"  {RED}✗{RESET} {e}")
        print(f"\n  {DIM}Check logs/saathi.log for the SIP response.{RESET}")
        print(f"  {DIM}401/403 means the trunk auth problem — SETUP.md §5d.{RESET}\n")
        return 1

    print(f"  {GREEN}✓{RESET} invite accepted — their phone should be ringing.")
    print(f"  {DIM}Accepted by the trunk isn't the same as answered.{RESET}")
    print()
    print(f"  {YELLOW}!{RESET} Nothing else is in the room. This command only proves the")
    print(f"    trunk works — answering it gets you silence and a call that")
    print(f"    drops, because there's no one on this end.")
    print(f"    For a real call, run {BOLD}saathi.device{RESET} and say \"call {args.name}\".\n")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m saathi.calling.cli",
        description="Set up and test calling, step by step.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("guide", help="walk through the whole setup (start here)")
    sub.add_parser("trunk", help="write trunk.json and print the lk command")
    sub.add_parser("check", help="what's configured and what's missing")

    c = sub.add_parser("contacts", help="list, add or remove contacts")
    c.add_argument("action", choices=["list", "add", "remove"], nargs="?", default="list")
    c.add_argument("name", nargs="?")
    c.add_argument("address", nargs="?")
    c.add_argument("--label")

    t = sub.add_parser("test", help="place a real call to a contact")
    t.add_argument("name")

    args = parser.parse_args(argv)
    quiet_console()

    if args.command in (None, "guide"):
        return cmd_guide(args)
    if args.command == "trunk":
        return 0 if cmd_trunk(args) else 1
    if args.command == "contacts":
        return cmd_contacts(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "test":
        return cmd_test(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
