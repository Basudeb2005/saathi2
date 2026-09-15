import json

import pytest

from saathi.console import netwatch, system
from saathi.console.commands import Console


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# ---- nmcli's terse format ----------------------------------------------

def test_split_terse_plain():
    assert system.split_terse("a:b:c") == ["a", "b", "c"]


def test_split_terse_keeps_escaped_colons():
    """A network called "Flat 3: wifi" arrives as Flat 3\\: wifi. Splitting
    naively invents a field and shifts every one after it."""
    assert system.split_terse(r"*:Flat 3\: wifi:78:WPA2") == ["*", "Flat 3: wifi", "78", "WPA2"]


def test_split_terse_keeps_escaped_backslash():
    assert system.split_terse(r"a\\b:c") == ["a\\b", "c"]


def test_split_terse_empty_fields():
    assert system.split_terse(":b:") == ["", "b", ""]


# ---- wifi list ----------------------------------------------------------

SCAN = "\n".join([
    r"*:Flat 3\: wifi:78:WPA2",
    ":Neighbour:41:WPA2",
    ":Neighbour:66:WPA2",          # same mesh, second access point
    ":Cafe:30:",
    "::19:WPA2",                   # hidden
])


def test_parse_networks_reads_all_four_fields():
    found = {n.ssid: n for n in system.parse_networks(SCAN)}
    assert found["Flat 3: wifi"].signal == 78
    assert found["Flat 3: wifi"].active is True
    assert found["Cafe"].open is True
    assert found["Neighbour"].open is False


def test_parse_networks_drops_hidden():
    assert all(n.ssid for n in system.parse_networks(SCAN))


def test_parse_networks_deduplicates_a_mesh():
    """One network per name, strongest wins — a list with BT-HUB five
    times is unreadable on a phone."""
    names = [n.ssid for n in system.parse_networks(SCAN)]
    assert names.count("Neighbour") == 1
    assert {n.ssid: n.signal for n in system.parse_networks(SCAN)}["Neighbour"] == 66


def test_parse_networks_puts_the_connected_one_first():
    assert system.parse_networks(SCAN)[0].ssid == "Flat 3: wifi"


def test_parse_networks_skips_a_header_row():
    text = "IN-USE:SSID:SIGNAL:SECURITY\n:Real:50:WPA2"
    assert [n.ssid for n in system.parse_networks(text)] == ["Real"]


def test_parse_networks_survives_nonsense():
    assert system.parse_networks("") == []
    assert system.parse_networks("garbage") == []


# ---- addresses ----------------------------------------------------------

ADDRS = (
    "1: lo    inet 127.0.0.1/8 scope host lo\n"
    "2: wlan0    inet 192.168.1.57/24 brd 192.168.1.255 scope global dynamic wlan0\n"
    "3: eth0    inet 10.0.0.4/24 brd 10.0.0.255 scope global eth0"
)


def test_parse_addresses_drops_loopback():
    found = system.parse_addresses(ADDRS)
    assert [a.interface for a in found] == ["wlan0", "eth0"]
    assert found[0].address == "192.168.1.57"


def test_parse_addresses_when_there_are_none():
    assert system.parse_addresses("") == []


def test_parse_active_prefers_wifi():
    text = "Wired connection 1:802-3-ethernet\nFlat 3:802-11-wireless"
    assert system.parse_active(text) == "Flat 3"


def test_parse_active_falls_back_to_ethernet():
    assert system.parse_active("Wired connection 1:802-3-ethernet") == "Wired connection 1"


def test_parse_active_when_offline():
    assert system.parse_active("") is None


# ---- the command layer --------------------------------------------------

class Recorder:
    """A fake `run` that remembers what it was asked and replies to order."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for needle, reply in self.replies.items():
            if needle in joined:
                return reply
        return system.Output(True, "")


def console(replies=None, **kw):
    return Console(runner=Recorder(replies), token="tok", **kw)


def test_unknown_command_says_so():
    reply = console().dispatch("frobnicate")
    assert reply.ok is False
    assert "help" in reply.text


def test_blank_line_is_harmless():
    assert console().dispatch("   ").ok is True


def test_unbalanced_quote_is_explained_not_raised():
    reply = console().dispatch('wifi "Flat 3')
    assert reply.ok is False
    assert "quote" in reply.text.lower()


def test_wifi_passes_the_ssid_as_one_argument():
    """A network with spaces in its name is the normal case, not the edge."""
    c = console({"addr show": system.Output(True, ADDRS)})
    c.dispatch('wifi "Flat 3 wifi" hunter2')
    call = next(c for c in c.run.calls if "connect" in " ".join(c))
    assert call == ["nmcli", "device", "wifi", "connect", "Flat 3 wifi", "password", "hunter2"]


def test_wifi_without_a_password_is_allowed():
    c = console()
    c.dispatch("wifi Cafe")
    call = next(x for x in c.run.calls if "connect" in " ".join(x))
    assert "password" not in call


def test_wifi_explains_a_wrong_password():
    c = console({"connect": system.Output(False, "Error: Secrets were required, but not provided")})
    reply = c.dispatch("wifi Home wrongpass")
    assert reply.ok is False
    assert "password" in reply.text.lower()


def test_wifi_explains_a_network_it_cannot_see():
    c = console({"connect": system.Output(False, "Error: No network with SSID 'Hom' found.")})
    reply = c.dispatch("wifi Hom pass")
    assert "scan" in reply.text.lower()


def test_start_starts_every_unit_in_order():
    c = console()
    c.dispatch("start")
    started = [x[2] for x in c.run.calls if x[:2] == ["systemctl", "start"]]
    assert started == ["saathi-agent", "saathi"]


def test_stop_reverses_the_order():
    """Stop the thing holding the microphone before the thing it talks to."""
    c = console()
    c.dispatch("stop")
    stopped = [x[2] for x in c.run.calls if x[:2] == ["systemctl", "stop"]]
    assert stopped == ["saathi", "saathi-agent"]


def test_status_reports_every_unit():
    c = console({
        "addr show": system.Output(True, ADDRS),
        "is-active": system.Output(True, "active"),
    })
    text = c.dispatch("status").text
    assert "192.168.1.57" in text
    assert "saathi-agent" in text and "saathi" in text


def test_ip_when_there_is_no_network():
    reply = console({"addr show": system.Output(True, "")}).dispatch("ip")
    assert reply.ok is False
    assert "not on a network" in reply.text


def test_logs_takes_a_line_count():
    c = console()
    c.dispatch("logs 5")
    call = next(x for x in c.run.calls if x[0] == "journalctl")
    assert "5" in call


def test_logs_clamps_an_absurd_count():
    c = console()
    c.dispatch("logs 999999")
    call = next(x for x in c.run.calls if x[0] == "journalctl")
    assert "500" in call


def test_shell_is_not_run_through_a_shell():
    """`sh echo hi; rm -rf /` has to stay one command with a funny argument."""
    c = console(allow_shell=True)
    c.dispatch("sh echo hi")
    assert ["echo", "hi"] in c.run.calls


def test_shell_can_be_switched_off():
    reply = console(allow_shell=False).dispatch("sh echo hi")
    assert reply.ok is False
    assert "CONSOLE_SHELL" in reply.text


def test_help_hides_the_shell_when_it_is_off():
    assert "sh CMD" not in console(allow_shell=False).dispatch("help").text
    assert "sh CMD" in console(allow_shell=True).dispatch("help").text


def test_token_is_answered_here():
    """Over Bluetooth it is the only way to get the key onto a phone. The
    web layer is what refuses it."""
    assert console().dispatch("token").text == "tok"


# ---- the hotspot decision ----------------------------------------------

def decider(clock, offline_after_s=90, retry_after_s=600):
    return netwatch.Decider(offline_after_s, retry_after_s, clock=clock)


def test_online_never_starts_a_hotspot():
    clock = Clock()
    d = decider(clock)
    for _ in range(50):
        clock.advance(30)
        assert d.decide(online=True, hotspot=False) == netwatch.NOTHING


def test_hotspot_after_the_grace_period():
    clock = Clock()
    d = decider(clock)
    assert d.decide(online=False, hotspot=False) == netwatch.NOTHING
    clock.advance(60)
    assert d.decide(online=False, hotspot=False) == netwatch.NOTHING
    clock.advance(40)
    assert d.decide(online=False, hotspot=False) == netwatch.UP


def test_a_slow_router_does_not_strand_it():
    """The whole reason for the grace period: a router that takes a minute
    to boot shouldn't put the Pi into setup mode."""
    clock = Clock()
    d = decider(clock)
    d.decide(online=False, hotspot=False)
    clock.advance(70)
    assert d.decide(online=True, hotspot=False) == netwatch.NOTHING
    clock.advance(600)
    assert d.decide(online=True, hotspot=False) == netwatch.NOTHING


def test_hotspot_comes_back_down_to_retry():
    """Otherwise a Pi that hotspotted once during a power cut stays there
    forever, which for a device in an old age home means until someone
    notices — so, never."""
    clock = Clock()
    d = decider(clock)
    assert d.decide(online=False, hotspot=True) == netwatch.NOTHING
    clock.advance(599)
    assert d.decide(online=False, hotspot=True) == netwatch.NOTHING
    clock.advance(2)
    assert d.decide(online=False, hotspot=True) == netwatch.DOWN


def test_being_a_hotspot_does_not_count_as_being_online():
    """An access point always has an address of its own, so the naive
    check says "connected" and it never retries."""
    clock = Clock()
    d = decider(clock)
    d.decide(online=True, hotspot=True)
    clock.advance(601)
    assert d.decide(online=True, hotspot=True) == netwatch.DOWN


# ---- service state ------------------------------------------------------

def test_service_state_reads_a_real_state():
    c = console({"is-active": system.Output(True, "active")})
    assert system.service_state("saathi", c.run) == "active"


def test_service_state_of_a_stopped_unit():
    """is-active exits non-zero for anything but active, so the return
    code is not the answer."""
    c = console({"is-active": system.Output(False, "inactive")})
    assert system.service_state("saathi", c.run) == "inactive"


def test_service_state_when_there_is_no_systemd():
    """It prints a paragraph about itself, which is not a state — and
    reporting that paragraph as one made the doctor claim a unit was
    called "System has not been booted with systemd"."""
    c = console({"is-active": system.Output(
        False, "System has not been booted with systemd as init system (PID 1). Can't operate.")})
    assert system.service_state("saathi", c.run) == "unknown"


def test_service_state_of_a_unit_that_does_not_exist():
    c = console({"is-active": system.Output(False, "")})
    assert system.service_state("nope", c.run) == "unknown"


def test_service_do_refuses_a_verb_it_does_not_know():
    c = console()
    assert system.service_do("obliterate", "saathi", c.run).ok is False
    assert c.run.calls == []


# ---- hello, the app's contract -----------------------------------------

def test_hello_is_one_line_of_json():
    c = console({
        "addr show": system.Output(True, ADDRS),
        "is-active": system.Output(True, "active"),
    })
    reply = c.dispatch("hello")
    assert "\n" not in reply.text
    payload = json.loads(reply.text)
    assert payload["ip"] == "192.168.1.57"
    assert payload["token"] == "tok"
    assert payload["port"]


def test_hello_says_so_when_there_is_no_address():
    """The app needs to tell "I found it but it has no network" apart
    from "I didn't find it", and they look identical otherwise."""
    payload = json.loads(console({"addr show": system.Output(True, "")}).dispatch("hello").text)
    assert payload["ip"] is None
    assert payload["token"] == "tok"
