"""
One set of commands, two ways in.

The phone talks to this over Bluetooth as lines of text and over wifi as
JSON, and both are the same handful of verbs: where are you, what network
are you on, join this one, start, stop, what went wrong. Writing them
twice is how the two drift until the Bluetooth path is the one nobody
tested.

So the verbs live here, once, returning plain sentences. The web layer
wraps them in JSON; the Bluetooth layer prints them. Neither knows what
`nmcli` is.

The tone of the replies is deliberate. These are read on a phone, held by
someone standing next to a Pi that isn't working, and "Error: exit status
1" is not an answer. Every failure says what to try.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from saathi.config import (
    CONSOLE_HTTP_PORT,
    CONSOLE_SHELL,
    HOTSPOT_PASSWORD,
    HOTSPOT_SSID,
    SAATHI_UNITS,
)
from saathi.console import system
from saathi.logging_setup import get_logger

log = get_logger("console.commands")


@dataclass
class Reply:
    ok: bool
    text: str

    def __str__(self) -> str:
        return self.text


def _bars(signal: int) -> str:
    """Signal as something you can read at a glance on a phone."""
    filled = min(4, max(0, (signal + 24) // 25))
    return "|" * filled + "." * (4 - filled)


class Console:
    """The commands. `runner` is injected so all of this is testable
    without a wifi card, a root shell or a Pi."""

    def __init__(
        self,
        runner: system.Runner = system.run,
        token: str = "",
        allow_shell: bool = CONSOLE_SHELL,
        units: Optional[List[str]] = None,
    ):
        self.run = runner
        self.token = token
        self.allow_shell = allow_shell
        self.units = units if units is not None else list(SAATHI_UNITS)
        self._handlers: Dict[str, Callable[[List[str]], Reply]] = {
            "help": self.help,
            "status": self.status,
            "ip": self.ip,
            "token": self.show_token,
            "hello": self.hello,
            "scan": self.scan,
            "wifi": self.wifi,
            "forget": self.forget,
            "start": self.start,
            "stop": self.stop,
            "restart": self.restart,
            "logs": self.logs,
            "hotspot": self.hotspot,
            "reboot": self.reboot,
            "sh": self.shell,
        }

    # ---- dispatch ------------------------------------------------------

    def dispatch(self, line: str) -> Reply:
        """One line of text in, one reply out. This is the Bluetooth
        protocol in its entirety."""
        line = line.strip()
        if not line:
            return Reply(True, "")
        try:
            # shlex so a network called "Flat 3 wifi" can be given as one
            # quoted argument, and a password with a space in it survives.
            parts = shlex.split(line)
        except ValueError:
            return Reply(False, "Unbalanced quote — try: wifi \"My Network\" mypassword")
        if not parts:
            return Reply(True, "")

        name, args = parts[0].lower(), parts[1:]
        handler = self._handlers.get(name)
        if handler is None:
            return Reply(False, f"Don't know {name!r}. Type help.")
        try:
            return handler(args)
        except Exception as e:                   # pragma: no cover - defensive
            log.exception("Command %r failed", name)
            return Reply(False, f"{name} failed: {type(e).__name__}: {e}")

    # ---- the commands --------------------------------------------------

    def help(self, args: List[str]) -> Reply:
        lines = [
            "status            what's running, what network, what IP",
            "ip                just the addresses",
            "scan              wifi networks in range",
            'wifi "NAME" PASS  join one, and remember it',
            "forget NAME       remove a saved network",
            "start / stop      Saathi itself",
            "restart           both services",
            "logs [n]          the last n lines",
            "hotspot on|off    become an access point",
            "token             the key the web console wants",
            "hello             all of the above as one line of JSON",
            "reboot            restart the Pi",
        ]
        if self.allow_shell:
            lines.append("sh CMD            run a command")
        return Reply(True, "\n".join(lines))

    def ip(self, args: List[str]) -> Reply:
        found = system.addresses(self.run)
        if not found:
            return Reply(False, "No address on any interface — not on a network yet.")
        body = "\n".join(f"{a.interface:8} {a.address}" for a in found)
        first = found[0].address
        return Reply(True, f"{body}\n\nweb console: http://{first}:{CONSOLE_HTTP_PORT}")

    def status(self, args: List[str]) -> Reply:
        found = system.addresses(self.run)
        network = system.current_network(self.run)
        states = {unit: system.service_state(unit, self.run) for unit in self.units}

        # Padded to the longest label, unit names included — this is read
        # in a monospace terminal app on a phone, where a ragged left
        # column is most of what makes it hard to scan.
        width = max([7] + [len(u) for u in states])
        lines = [f"{'host':<{width}}  {system.hostname()}"]
        lines.append(f"{'network':<{width}}  {network or 'not connected'}")
        for address in found:
            lines.append(f"{'address':<{width}}  {address.address}  ({address.interface})")
        if not found:
            lines.append(f"{'address':<{width}}  none")
        for unit, state in states.items():
            mark = "running" if state == "active" else state
            lines.append(f"{unit:<{width}}  {mark}")
        if found:
            lines.append("")
            lines.append(f"web console: http://{found[0].address}:{CONSOLE_HTTP_PORT}")
        return Reply(True, "\n".join(lines))

    def show_token(self, args: List[str]) -> Reply:
        """Only ever answered over Bluetooth — the web layer doesn't route
        here, because a console that hands out its own key to anyone who
        asks is not a console with a key."""
        if not self.token:
            return Reply(False, "No token loaded.")
        return Reply(True, self.token)

    def hello(self, args: List[str]) -> Reply:
        """Everything an app needs, on one line of JSON.

        The alternative is an app scraping `status`, and the moment
        anyone improves the wording of that output every installed copy
        of the app stops finding the Pi. This is the contract; `status`
        is for people.

        Bluetooth only, like `token` — it contains the token.
        """
        found = system.addresses(self.run)
        return Reply(True, json.dumps({
            "host": system.hostname(),
            "ip": found[0].address if found else None,
            "addresses": [a.address for a in found],
            "port": CONSOLE_HTTP_PORT,
            "token": self.token,
            "network": system.current_network(self.run),
            "services": {u: system.service_state(u, self.run) for u in self.units},
        }, separators=(",", ":")))

    def scan(self, args: List[str]) -> Reply:
        found = system.networks(self.run)
        if not found:
            return Reply(False, "No networks found. Is the wifi radio on? Try: nmcli radio wifi on")
        lines = []
        for net in found[:25]:
            mark = "*" if net.active else " "
            lock = " " if net.open else "#"
            lines.append(f"{mark}{lock} {_bars(net.signal)}  {net.ssid}")
        return Reply(True, "\n".join(lines) + "\n\n* = connected, # = needs a password")

    def wifi(self, args: List[str]) -> Reply:
        if not args:
            return Reply(False, 'Which network? wifi "My Network" mypassword')
        ssid = args[0]
        password = args[1] if len(args) > 1 else ""
        result = system.join_network(ssid, password, self.run)
        if not result.ok:
            return Reply(False, self._why_wifi_failed(ssid, result.text))

        found = system.addresses(self.run)
        where = found[0].address if found else "?"
        return Reply(
            True,
            f"On {ssid}. Address {where}.\n"
            f"web console: http://{where}:{CONSOLE_HTTP_PORT}\n"
            f"It will rejoin this network by itself next time it boots.",
        )

    @staticmethod
    def _why_wifi_failed(ssid: str, text: str) -> str:
        """nmcli's own messages are decent; the two that aren't are the
        two you hit most."""
        lowered = text.lower()
        if "secrets were required" in lowered or "no secrets" in lowered:
            return f"{ssid} wanted a password and didn't get a working one. Check it and try again."
        if "no network with ssid" in lowered:
            return f"Can't see {ssid} from here. Run scan — the name has to match exactly, capitals included."
        return f"Couldn't join {ssid}: {text}"

    def forget(self, args: List[str]) -> Reply:
        if not args:
            return Reply(False, "Which one? forget NAME")
        result = system.forget_network(args[0], self.run)
        return Reply(result.ok, result.text or f"Forgot {args[0]}.")

    # ---- services ------------------------------------------------------

    def _units_from(self, args: List[str]) -> List[str]:
        if args and args[0] in self.units:
            return [args[0]]
        return list(self.units)

    def start(self, args: List[str]) -> Reply:
        return self._service("start", self._units_from(args))

    def stop(self, args: List[str]) -> Reply:
        # Reversed: stop the thing holding the microphone before the thing
        # it talks to, or the device spends its last seconds logging that
        # the agent has vanished.
        return self._service("stop", list(reversed(self._units_from(args))))

    def restart(self, args: List[str]) -> Reply:
        return self._service("restart", self._units_from(args))

    def _service(self, action: str, units: List[str]) -> Reply:
        lines, ok = [], True
        for unit in units:
            result = system.service_do(action, unit, self.run)
            if result.ok:
                lines.append(f"{unit}: {action}ed")
            else:
                ok = False
                lines.append(f"{unit}: {result.text or 'failed'}")
        return Reply(ok, "\n".join(lines))

    def logs(self, args: List[str]) -> Reply:
        unit = self.units[0] if self.units else "saathi"
        count = 40
        for arg in args:
            if arg.isdigit():
                count = int(arg)
            else:
                unit = arg
        text = system.journal(unit, count, self.run)
        return Reply(True, text or f"Nothing logged for {unit}.")

    def hotspot(self, args: List[str]) -> Reply:
        want = (args[0].lower() if args else "on")
        if want in ("off", "down", "stop"):
            result = system.hotspot_down(self.run)
            return Reply(result.ok, result.text or "Hotspot off.")
        result = system.hotspot_up(HOTSPOT_SSID, HOTSPOT_PASSWORD, self.run)
        if not result.ok:
            return Reply(False, result.text or "Couldn't start the hotspot.")
        return Reply(
            True,
            f"Hotspot up: {HOTSPOT_SSID} / {HOTSPOT_PASSWORD}\n"
            f"Join it from your phone, then open http://10.42.0.1:{CONSOLE_HTTP_PORT}",
        )

    def reboot(self, args: List[str]) -> Reply:
        result = self.run(["systemctl", "reboot"])
        return Reply(result.ok, result.text or "Rebooting. Back in about forty seconds.")

    def shell(self, args: List[str]) -> Reply:
        if not self.allow_shell:
            return Reply(False, "Shell access is off. Set CONSOLE_SHELL=true and restart the console.")
        if not args:
            return Reply(False, "sh WHAT?")
        # No shell=True: the arguments are already split, and passing them
        # back through a shell is how "sh echo hi; rm -rf /" becomes two
        # commands instead of one.
        result = self.run(args)
        return Reply(result.ok, result.text or "(no output)")
