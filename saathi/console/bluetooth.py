"""
A serial port over Bluetooth, so the Pi can be asked things with no
network at all.

This is the piece that means never carrying a monitor again. Every other
way of finding out a headless Pi's address needs the thing you are trying
to establish: ssh needs the address, mDNS needs the Pi and the phone on
one network, a web console needs wifi to already work. Bluetooth needs
none of it — it is a second radio, independent of the first, and it works
while the Pi is sitting on a network it can't reach or none at all.

No third-party library. Python's socket module speaks RFCOMM natively on
Linux:

    socket(AF_BLUETOOTH, SOCK_STREAM, BTPROTO_RFCOMM)

which is the whole of what pybluez wraps, and pybluez has no wheel for
Python 3.13. What Python cannot do is advertise the service in SDP so a
phone's app can see it, and that is one `sdptool` call in the installer
rather than a dependency here.

The protocol is lines of text, because the thing on the other end is a
free serial terminal app from the Play Store and you may well be typing
into it by hand while standing over the Pi wondering why it is dark.
"""
from __future__ import annotations

import socket
import threading
from typing import Optional

from saathi.config import CONSOLE_BT_CHANNEL
from saathi.console.commands import Console
from saathi.logging_setup import get_logger

log = get_logger("console.bluetooth")

BANNER = (
    "Saathi console. Type help.\n"
    "Useful first: status\n"
)
PROMPT = "\r\n> "
# Lines from a phone keyboard, so generous but not unbounded — this is
# reachable by anything that has paired.
MAX_LINE = 4096


class BluetoothError(Exception):
    """No Bluetooth adapter, or the channel is taken."""


def listen_socket(channel: int = CONSOLE_BT_CHANNEL):
    """An RFCOMM server socket bound to every adapter."""
    if not hasattr(socket, "BTPROTO_RFCOMM"):    # pragma: no cover - non-Linux
        raise BluetoothError("This Python has no Bluetooth socket support (Linux only)")
    try:
        server = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("", channel))
        server.listen(1)
    except OSError as e:
        raise BluetoothError(
            f"Couldn't listen on RFCOMM channel {channel}: {e}. "
            "Is bluetooth running (systemctl status bluetooth), and is another "
            "copy of the console already up?"
        ) from e
    return server


def handle(conn, console: Console, banner: str = BANNER) -> None:
    """One phone, until it goes away.

    Reads whole lines out of a byte stream, because a Bluetooth read
    returns whatever happened to arrive — half a command, or two of them.
    Treating one read as one command works every time you test it by hand
    and fails the first time someone pastes.
    """
    buffer = b""
    conn.sendall(banner.encode() + PROMPT.encode())
    while True:
        try:
            chunk = conn.recv(1024)
        except OSError:
            return
        if not chunk:
            return

        buffer += chunk
        if len(buffer) > MAX_LINE:
            conn.sendall(b"Line too long.\r\n")
            buffer = b""
            continue

        # \r\n, \n and bare \r all arrive depending on the app.
        while True:
            for terminator in (b"\r\n", b"\n", b"\r"):
                index = buffer.find(terminator)
                if index >= 0:
                    line, buffer = buffer[:index], buffer[index + len(terminator):]
                    break
            else:
                break

            text = line.decode("utf-8", "replace").strip()
            if text.lower() in ("quit", "exit", "bye"):
                conn.sendall(b"Bye.\r\n")
                return
            reply = console.dispatch(text)
            body = reply.text.replace("\n", "\r\n")
            if body:
                conn.sendall(body.encode() + b"\r\n")
            conn.sendall(PROMPT.encode())


def serve(console: Console, channel: int = CONSOLE_BT_CHANNEL,
          stop: Optional[threading.Event] = None) -> None:
    """Accept phones forever. One at a time, which is what RFCOMM
    channel 1 is for and how many phones there will ever be."""
    server = listen_socket(channel)
    log.info("Bluetooth console listening on RFCOMM channel %d", channel)
    try:
        while not (stop and stop.is_set()):
            try:
                conn, address = server.accept()
            except OSError as e:
                if stop and stop.is_set():
                    return
                log.warning("Bluetooth accept failed: %s", e)
                continue
            log.info("Phone connected: %s", address[0] if address else "?")
            try:
                handle(conn, console)
            except Exception as e:
                log.warning("Bluetooth session ended: %s", e)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                log.info("Phone disconnected")
    finally:
        server.close()


def serve_in_background(console: Console, channel: int = CONSOLE_BT_CHANNEL) -> Optional[threading.Thread]:
    """Start the Bluetooth side, and carry on without it if it won't start.

    A Pi with no Bluetooth, or with the adapter blocked by rfkill, should
    still bring up the web console — that is the half that works once you
    are on a network, and refusing to start either because one radio is
    missing helps nobody.
    """
    try:
        listen_socket(channel).close()           # fail fast, with the reason
    except BluetoothError as e:
        log.warning("No Bluetooth console: %s", e)
        return None

    thread = threading.Thread(target=serve, args=(console, channel), daemon=True)
    thread.start()
    return thread
