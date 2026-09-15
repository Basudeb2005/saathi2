"""The Bluetooth line protocol, against a fake socket.

The bug this file exists for: a Bluetooth read returns whatever happened
to arrive, which is not a line. Treating one recv as one command works
every time you test it by hand and fails the first time someone pastes.
"""
import pytest

from saathi.console import bluetooth, system
from saathi.console.commands import Console


class FakeSocket:
    """Hands over whatever the test queued, remembers what was sent."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""

    def recv(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data):
        self.sent += data


def talk(chunks, replies=None):
    def run(argv, timeout=None):
        joined = " ".join(argv)
        for needle, reply in (replies or {}).items():
            if needle in joined:
                return reply
        return system.Output(True, "")

    sock = FakeSocket(chunks)
    bluetooth.handle(sock, Console(runner=run, token="tok"), banner="hi\n")
    return sock.sent.decode()


def test_the_banner_comes_first():
    """A serial terminal that connects and shows nothing looks like a
    failed connection."""
    assert talk([b""]).startswith("hi\n")


def test_one_command():
    assert "tok" in talk([b"token\r\n"])


def test_a_command_split_across_two_reads():
    """Which is what actually happens over Bluetooth."""
    assert "tok" in talk([b"tok", b"en\r\n"])


def test_two_commands_in_one_read():
    """A paste, or a phone app that buffers."""
    out = talk([b"help\nhelp\n"])
    assert out.count("hotspot on|off") == 2


@pytest.mark.parametrize("terminator", [b"\r\n", b"\n", b"\r"])
def test_every_line_ending(terminator):
    """Which one arrives depends on the phone app, and there is no
    negotiating it."""
    assert "tok" in talk([b"token" + terminator])


def test_replies_use_crlf():
    """A serial terminal with no carriage return staircases every line
    down the right-hand side of the screen."""
    out = talk([b"help\n"])
    assert "\r\n" in out
    assert "\n" not in out.replace("\r\n", "").replace("hi\n", "")


def test_quit_closes_the_connection():
    out = talk([b"quit\r\n", b"token\r\n"])
    assert "Bye." in out
    assert "tok" not in out


def test_an_empty_read_ends_it():
    assert talk([b""]) == "hi\n" + bluetooth.PROMPT


def test_a_blank_line_just_reprompts():
    out = talk([b"\r\n"])
    assert out.count(bluetooth.PROMPT) == 2


def test_an_absurdly_long_line_is_refused_not_buffered():
    """This is reachable by anything that has paired, so an unbounded
    buffer is an unbounded buffer on the other end of a radio."""
    out = talk([b"x" * (bluetooth.MAX_LINE + 10)])
    assert "Line too long" in out


def test_it_recovers_after_a_long_line():
    out = talk([b"x" * (bluetooth.MAX_LINE + 10), b"token\r\n"])
    assert "Line too long" in out
    assert "tok" in out


def test_rubbish_bytes_do_not_crash_it():
    """Undecodable bytes arrive from a flaky link, not from a user."""
    assert "Don't know" in talk([b"\xff\xfe\x00abc\r\n"])


def test_a_failing_command_still_answers():
    out = talk([b"wifi Nowhere\r\n"],
               replies={"connect": system.Output(False, "Error: No network with SSID 'Nowhere' found.")})
    assert "scan" in out.lower()
