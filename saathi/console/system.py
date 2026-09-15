r"""
Everything this needs from the operating system, in one place.

It is all shelling out — `nmcli`, `systemctl`, `ip`, `journalctl` — because
the alternative is D-Bus bindings and a GLib main loop for jobs that are
four words on a command line.

The part that is genuinely easy to get wrong is not running the commands,
it is reading what they say back. `nmcli -t` escapes colons inside values
with a backslash, so a network called "Bob: the router" arrives as
`Bob\: the router` and a naive `line.split(":")` silently invents a
field. So every command's output is parsed by a pure function that takes
a string and returns data, and those are what the tests exercise — no Pi,
no wifi, no root.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from saathi.logging_setup import get_logger

log = get_logger("console.system")

# Long enough for a wifi scan on a busy band, short enough that a wedged
# nmcli doesn't hang the phone waiting for a reply.
DEFAULT_TIMEOUT_S = 25.0


@dataclass
class Output:
    ok: bool
    text: str = ""

    def __bool__(self) -> bool:
        return self.ok


Runner = Callable[[Sequence[str]], Output]


def run(argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT_S) -> Output:
    """Run a command, never raise. Returns its output either way.

    Never raising is deliberate: every caller is answering a phone on the
    other end of a Bluetooth socket, and an exception there is a dropped
    connection where a sentence would do.
    """
    if not shutil.which(argv[0]):
        return Output(False, f"{argv[0]} isn't installed")
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return Output(False, f"{argv[0]} timed out after {timeout:.0f}s")
    except Exception as e:                       # pragma: no cover - defensive
        return Output(False, f"{argv[0]} failed: {e}")

    text = (done.stdout or "").strip() or (done.stderr or "").strip()
    return Output(done.returncode == 0, text)


# ---- parsing (pure, tested) --------------------------------------------

def split_terse(line: str) -> List[str]:
    """Split one line of `nmcli -t` output.

    Colons separate fields and `\\:` is a literal colon inside one. Getting
    this wrong doesn't error, it just quietly shifts every field after the
    first network whose name contains a colon — which is the kind of bug
    you find six months later.
    """
    fields: List[str] = []
    current: List[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


@dataclass
class Network:
    ssid: str
    signal: int = 0
    security: str = ""
    active: bool = False

    @property
    def open(self) -> bool:
        return self.security in ("", "--", "none")


def parse_networks(text: str) -> List[Network]:
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`.

    Deduplicated by name and sorted strongest first. A house with a mesh
    reports the same network once per access point, and a list with
    "BT-HUB-6" five times is unreadable on a phone.
    """
    best: dict = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = split_terse(line)
        if len(fields) < 4:
            continue
        in_use, ssid, signal, security = fields[0], fields[1].strip(), fields[2], fields[3]
        if not ssid:
            continue                     # hidden network, nothing to tap on
        try:
            strength = int(signal)
        except ValueError:
            # Not a data row. nmcli -t shouldn't emit headers, but a
            # localised build or a future version might, and a network
            # called "SSID" at zero bars is a confusing thing to offer.
            continue
        network = Network(
            ssid=ssid, signal=strength,
            security="" if security in ("--", "") else security,
            active=in_use.strip() == "*",
        )
        seen = best.get(ssid)
        if seen is None or network.signal > seen.signal or network.active:
            best[ssid] = network
    return sorted(best.values(), key=lambda n: (-int(n.active), -n.signal, n.ssid))


@dataclass
class Address:
    interface: str
    address: str


def parse_addresses(text: str) -> List[Address]:
    """`ip -4 -o addr show`. Loopback dropped — nobody ever wanted 127.0.0.1."""
    found: List[Address] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        interface = parts[1]
        if interface == "lo":
            continue
        found.append(Address(interface=interface, address=parts[3].split("/")[0]))
    return found


def parse_active(text: str) -> Optional[str]:
    """`nmcli -t -f NAME,TYPE connection show --active` -> the wifi one.

    Wired and the hotspot both show up here too; the question this is
    answering is always "which network am I on", so ethernet is a fine
    answer and a loopback connection is not.
    """
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) < 2:
            continue
        name, kind = fields[0], fields[1]
        if kind in ("802-11-wireless", "wifi"):
            return name
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) >= 2 and fields[1] in ("802-3-ethernet", "ethernet"):
            return fields[0]
    return None


# ---- the actual operations ---------------------------------------------

def addresses(runner: Runner = run) -> List[Address]:
    result = runner(["ip", "-4", "-o", "addr", "show"])
    return parse_addresses(result.text) if result.ok else []


def networks(runner: Runner = run, rescan: bool = True) -> List[Network]:
    argv = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    if rescan:
        # Without this you get whatever was cached when the interface came
        # up, which on a Pi that booted in another room is nothing at all.
        argv += ["--rescan", "yes"]
    result = runner(argv)
    return parse_networks(result.text) if result.ok else []


def current_network(runner: Runner = run) -> Optional[str]:
    result = runner(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
    return parse_active(result.text) if result.ok else None


def join_network(ssid: str, password: str = "", runner: Runner = run) -> Output:
    """Join a wifi network, and remember it for next boot.

    nmcli creates a saved connection as a side effect, which is what makes
    this survive a reboot — the whole point of doing it from a phone once.
    """
    argv = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        argv += ["password", password]
    # Longer than the default: association, DHCP and the DNS check together
    # regularly take twenty seconds on a busy band.
    return runner(argv)


def forget_network(ssid: str, runner: Runner = run) -> Output:
    return runner(["nmcli", "connection", "delete", ssid])


def hotspot_up(ssid: str, password: str, runner: Runner = run) -> Output:
    """Become an access point, so a phone can reach us with no network at all.

    NetworkManager's shared mode puts us on 10.42.0.1 and runs its own
    DHCP and DNS, which is the whole of what a setup portal needs.
    """
    return runner([
        "nmcli", "device", "wifi", "hotspot",
        "con-name", "saathi-setup", "ssid", ssid, "password", password,
    ])


def hotspot_down(runner: Runner = run) -> Output:
    return runner(["nmcli", "connection", "down", "saathi-setup"])


def hotspot_active(runner: Runner = run) -> bool:
    result = runner(["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"])
    if not result.ok:
        return False
    return any(split_terse(line)[0] == "saathi-setup" for line in result.text.splitlines() if line)


# What `systemctl is-active` actually says. Anything else — "Failed to
# connect to bus", "System has not been booted with systemd" — is systemd
# talking about itself, not about the unit.
STATES = frozenset({
    "active", "inactive", "failed", "activating",
    "deactivating", "reloading", "unknown",
})


def service_state(unit: str, runner: Runner = run) -> str:
    """active | inactive | failed | ... | unknown.

    `systemctl is-active` exits non-zero for anything but active, so the
    return code is not the answer — and on a box with no systemd at all
    it prints a paragraph, which is not a state.
    """
    result = runner(["systemctl", "is-active", unit])
    lines = result.text.strip().splitlines()
    state = lines[0].strip() if lines else ""
    return state if state in STATES else "unknown"


def service_do(action: str, unit: str, runner: Runner = run) -> Output:
    if action not in ("start", "stop", "restart", "enable", "disable"):
        return Output(False, f"can't {action} anything")
    return runner(["systemctl", action, unit])


def journal(unit: str, lines: int = 40, runner: Runner = run) -> str:
    result = runner([
        "journalctl", "-u", unit, "-n", str(max(1, min(lines, 500))),
        "--no-pager", "-o", "short-iso",
    ])
    return result.text


def hostname() -> str:
    try:
        return os.uname().nodename
    except Exception:                            # pragma: no cover - defensive
        return "raspberrypi"
