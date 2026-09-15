"""
The web half of the console: a JSON API and the page that drives it.

Deliberately `http.server` and not Flask. This has to start on a Pi
before anything else is known to work, often before the venv has been
touched since an image was written, and the failure mode of "the rescue
console needs pip install" is the worst one available.

Threaded, because a wifi scan takes several seconds and a phone that gets
no answer to anything else in the meantime looks hung.

Authentication is one shared token on every API call. The page itself is
served without one — it is a shell with no data in it, and asking for a
key before showing the box you type the key into is a riddle.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from saathi.config import CONSOLE_HTTP_PORT
from saathi.console import auth, system
from saathi.console.commands import Console
from saathi.logging_setup import get_logger

log = get_logger("console.web")

STATIC = Path(__file__).parent / "static"
# Explicit, rather than serving a directory: a path that comes off the
# wire should never reach the filesystem, and a whitelist is the version
# of that with no traversal bug in it.
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


class Handler(BaseHTTPRequestHandler):
    console: Console = None      # type: ignore[assignment]
    token: str = ""

    server_version = "saathi-console"
    # Every request would otherwise try a reverse DNS lookup on the
    # phone's address, which on a Pi with no working DNS is a five-second
    # stall before the page starts loading.
    def address_string(self) -> str:     # noqa: D102
        return self.client_address[0]

    def log_message(self, fmt, *args):   # noqa: D102
        log.debug(fmt, *args)

    # ---- plumbing ------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                          # phone walked out of range

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _authorised(self) -> bool:
        supplied = self.headers.get("X-Saathi-Token", "")
        if not supplied:
            supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return auth.matches(supplied, self.token)

    # ---- routes --------------------------------------------------------

    def do_GET(self) -> None:             # noqa: N802
        path = urlparse(self.path).path
        if path in FILES:
            name, content_type = FILES[path]
            try:
                return self._send(200, (STATIC / name).read_bytes(), content_type)
            except FileNotFoundError:
                return self._send(404, b"missing", "text/plain")

        if path == "/api/ping":
            # Unauthenticated on purpose: this is how the phone finds the
            # Pi among several addresses, before it has offered a token,
            # and it says nothing a device on your wifi couldn't see.
            return self._json(200, {"saathi": True, "host": system.hostname()})

        if path.startswith("/api/"):
            if not self._authorised():
                return self._json(401, {"ok": False, "text": "Wrong or missing token."})
            return self._api(path[len("/api/"):])

        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:            # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        if not self._authorised():
            return self._json(401, {"ok": False, "text": "Wrong or missing token."})

        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            return self._json(413, {"ok": False, "text": "Too much."})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"ok": False, "text": "Not JSON."})
        if not isinstance(payload, dict):
            return self._json(400, {"ok": False, "text": "Not an object."})

        self._api(path[len("/api/"):], payload)

    def _api(self, route: str, payload: Optional[dict] = None) -> None:
        payload = payload or {}

        if route == "status":
            found = system.addresses(self.console.run)
            return self._json(200, {
                "ok": True,
                "host": system.hostname(),
                "network": system.current_network(self.console.run),
                "addresses": [{"interface": a.interface, "address": a.address} for a in found],
                "services": {
                    unit: system.service_state(unit, self.console.run)
                    for unit in self.console.units
                },
                "shell": self.console.allow_shell,
            })

        if route == "networks":
            found = system.networks(self.console.run)
            return self._json(200, {"ok": True, "networks": [
                {"ssid": n.ssid, "signal": n.signal, "open": n.open, "active": n.active}
                for n in found
            ]})

        if route == "run":
            # One door for everything else, so a verb added to the
            # Bluetooth side is on the web side the same minute. `token`
            # is the exception the command layer documents: the console
            # will not read its own key out to whoever asks.
            line = str(payload.get("command", "")).strip()
            if not line:
                return self._json(400, {"ok": False, "text": "Nothing to run."})
            # Both of these carry the token, and a console that reads
            # its own key out to anything on the wifi has no key.
            if line.split()[0].lower() in ("token", "hello"):
                return self._json(403, {"ok": False, "text": "Ask over Bluetooth for that."})
            reply = self.console.dispatch(line)
            return self._json(200, {"ok": reply.ok, "text": reply.text})

        self._json(404, {"ok": False, "text": f"No such route: {route}"})


def build_server(console: Console, token: str, port: int = CONSOLE_HTTP_PORT) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"console": console, "token": token})
    # 0.0.0.0 on purpose: on the hotspot the phone arrives on 10.42.0.1,
    # on the wifi on whatever DHCP gave us, and binding one of those means
    # the console is missing in exactly the situation it exists for.
    return ThreadingHTTPServer(("0.0.0.0", port), handler)


def serve_in_background(console: Console, token: str,
                        port: int = CONSOLE_HTTP_PORT) -> ThreadingHTTPServer:
    server = build_server(console, token, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Web console on http://0.0.0.0:%d", port)
    return server
