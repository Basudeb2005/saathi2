"""The HTTP half, against a real server on a real socket.

Auth and route-shape bugs don't show up in unit tests of the handler —
they show up in what a phone actually gets back — so this starts the
thing and talks to it.
"""
import json
import threading
import urllib.error
import urllib.request

import pytest

from saathi.console import system, web
from saathi.console.commands import Console

TOKEN = "a-real-looking-token"


def fake_run(argv, timeout=None):
    joined = " ".join(argv)
    if "addr show" in joined:
        return system.Output(True, "2: wlan0    inet 192.168.1.57/24 scope global wlan0")
    if "wifi list" in joined:
        return system.Output(True, "*:Home:80:WPA2\n:Cafe:30:")
    if "connection show --active" in joined:
        return system.Output(True, "Home:802-11-wireless")
    if "is-active" in joined:
        return system.Output(True, "active")
    return system.Output(True, "ok")


@pytest.fixture(scope="module")
def server():
    """One server for the whole module. shutdown() waits out serve_forever's
    half-second poll, and paying that per test turns a fast file into a
    twelve-second one."""
    srv = web.build_server(Console(runner=fake_run, token=TOKEN), TOKEN, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(base, path, token=TOKEN, body=None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("X-Saathi-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---- the page -----------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/index.html", "/manifest.webmanifest",
                                  "/sw.js", "/icon-192.png", "/icon-512.png"])
def test_every_file_the_manifest_promises_exists(server, path):
    """An installable web app whose icon 404s installs with no icon."""
    status, body = call(server, path, token=None)
    assert status == 200
    assert body


def test_the_page_is_served_without_a_token(server):
    """Asking for the key before showing the box you type it into is a riddle."""
    status, body = call(server, "/", token=None)
    assert status == 200
    assert b"<title>Saathi" in body


def test_manifest_icons_all_resolve(server):
    _, body = call(server, "/manifest.webmanifest", token=None)
    for icon in json.loads(body)["icons"]:
        assert call(server, icon["src"], token=None)[0] == 200


# ---- auth ---------------------------------------------------------------

@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN[:-1]])
def test_the_api_refuses_a_bad_token(server, token):
    assert call(server, "/api/status", token=token)[0] == 401


def test_a_token_in_the_query_string_works(server):
    """So a link can be handed over, which is how the Android app opens it."""
    assert call(server, f"/api/status?token={TOKEN}", token=None)[0] == 200


def test_ping_needs_no_token(server):
    """It's how the app finds the Pi among several addresses, before it
    has a token to offer."""
    status, body = call(server, "/api/ping", token=None)
    assert status == 200
    assert json.loads(body)["saathi"] is True


# ---- routes -------------------------------------------------------------

def test_status(server):
    status, body = call(server, "/api/status")
    payload = json.loads(body)
    assert status == 200
    assert payload["addresses"][0]["address"] == "192.168.1.57"
    assert payload["network"] == "Home"
    assert payload["services"]


def test_networks(server):
    payload = json.loads(call(server, "/api/networks")[1])
    assert [n["ssid"] for n in payload["networks"]] == ["Home", "Cafe"]
    assert payload["networks"][1]["open"] is True


def test_run(server):
    status, body = call(server, "/api/run", body={"command": "start"})
    assert status == 200
    assert json.loads(body)["ok"] is True


def test_run_needs_a_command(server):
    assert call(server, "/api/run", body={"command": "  "})[0] == 400


def test_the_console_will_not_read_out_its_own_key(server):
    """Bluetooth answers `token` because pairing means being in the room.
    HTTP is reachable by everything on the wifi, so it does not."""
    status, body = call(server, "/api/run", body={"command": "token"})
    assert status == 403
    assert TOKEN not in body.decode()


def test_bad_json_is_a_400_not_a_crash(server):
    request = urllib.request.Request(server + "/api/run", data=b"{oh dear",
                                     headers={"X-Saathi-Token": TOKEN})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(request, timeout=5)
    assert e.value.code == 400


def test_a_json_array_is_rejected(server):
    request = urllib.request.Request(server + "/api/run", data=b"[1,2,3]",
                                     headers={"X-Saathi-Token": TOKEN})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(request, timeout=5)
    assert e.value.code == 400


def test_unknown_routes_are_404(server):
    assert call(server, "/api/nope")[0] == 404
    assert call(server, "/nope", token=None)[0] == 404


def test_no_path_traversal(server):
    """The static files are a whitelist, so there is no filesystem to walk."""
    for path in ["/../config.py", "/..%2f..%2fetc%2fpasswd", "/static/../../../etc/passwd"]:
        assert call(server, path, token=None)[0] == 404
