"""
`python -m saathi.doctor` — check everything before blaming the code.

This project has six moving parts that fail independently: keys, the
microphone, the speaker, Mopidy, Radio Browser and LiveKit. When it
"doesn't work", the symptom is always the same — silence — and the cause
is almost never where you'd look first. So each piece is checked on its
own and each failure names its own fix.

Every check is independent: one failing never stops the rest from
running, because knowing all four things that are wrong beats finding out
one at a time.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from saathi.logging_setup import quiet_console
from saathi.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    MOPIDY_RPC_URL,
    OPENAI_API_KEY,
    ROOT_DIR,
    WAKE_CAPTURE_DEVICE,
)

GREEN, YELLOW, RED, DIM, BOLD, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
)

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Result:
    status: str
    detail: str = ""
    fix: str = ""


@dataclass
class Check:
    name: str
    run: Callable[[], Result]
    # A warning rather than a failure: things you don't need until later.
    optional: bool = False


# ---- the checks ---------------------------------------------------------

def check_env() -> Result:
    if not (ROOT_DIR / ".env").exists():
        return Result(FAIL, "no .env file", "run: python -m saathi.setup")

    missing = [
        name for name, value in (
            ("OPENAI_API_KEY", OPENAI_API_KEY),
            ("LIVEKIT_URL", LIVEKIT_URL),
            ("LIVEKIT_API_KEY", LIVEKIT_API_KEY),
            ("LIVEKIT_API_SECRET", LIVEKIT_API_SECRET),
        ) if not value
    ]
    if missing:
        return Result(FAIL, f"missing {', '.join(missing)}", "run: python -m saathi.setup")
    return Result(OK, "all required keys present")


def check_tools() -> Result:
    missing = [t for t in ("arecord", "aplay") if not shutil.which(t)]
    if missing:
        return Result(FAIL, f"missing {', '.join(missing)}", "sudo apt install alsa-utils")
    return Result(OK, "arecord and aplay present")


def check_mic_exists() -> Result:
    if not shutil.which("arecord"):
        return Result(FAIL, "arecord not installed", "sudo apt install alsa-utils")
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return Result(FAIL, f"couldn't list capture devices ({e})")

    cards = [line for line in out.splitlines() if line.startswith("card ")]
    if not cards:
        return Result(
            FAIL, "no capture device",
            "plug in a USB mic or a HAT — the Pi's onboard audio is playback-only",
        )
    return Result(OK, f"{len(cards)} capture device(s)")


def _record(device: Optional[str], seconds: int = 1) -> bytes:
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
           "-d", str(seconds), "-t", "raw"]
    if device:
        cmd += ["-D", device]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=15 + seconds).stdout
    except Exception:
        return b""


def _capture_cards() -> List[int]:
    """Card numbers from `arecord -l`, e.g. 'card 3: Device [USB...]'."""
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    cards = []
    for line in out.splitlines():
        if line.startswith("card "):
            try:
                cards.append(int(line.split()[1].rstrip(":")))
            except (IndexError, ValueError):
                continue
    return cards


def check_mic_hears() -> Result:
    """Record a second and look at the level.

    A mic that enumerates but returns silence is the single most
    demoralising failure in this project — everything looks configured and
    nothing works — so it gets its own check rather than being assumed.

    When the default device gives nothing, try each card in turn instead
    of telling someone to go read `arecord -l` themselves: ALSA's
    "default" is routinely not the USB mic, and the fix is a device
    string we can work out here and hand over ready to paste.
    """
    import audioop

    pcm = _record(WAKE_CAPTURE_DEVICE)

    if not pcm or audioop.rms(pcm, 2) < 20:
        for card in _capture_cards():
            candidate = f"plughw:{card},0"
            if candidate == WAKE_CAPTURE_DEVICE:
                continue
            probe = _record(candidate)
            if probe and audioop.rms(probe, 2) >= 20:
                return Result(
                    FAIL,
                    f"default device is silent, but {candidate} works",
                    f"python -m saathi.setup, or add WAKE_CAPTURE_DEVICE={candidate} to .env",
                )

    if not pcm:
        return Result(
            FAIL, "recorded nothing on any device",
            "is the mic plugged in? check `arecord -l`, and `alsamixer` F4 -> raise Capture",
        )

    level = audioop.rms(pcm, 2)
    if level < 20:
        return Result(
            FAIL, f"mic is silent (rms {level})",
            "check it's not muted: alsamixer -> F4 -> raise Capture",
        )
    if level < 100:
        return Result(WARN, f"mic is very quiet (rms {level})", "raise the gain in alsamixer")
    return Result(OK, f"mic hears sound (rms {level})")


def check_speaker() -> Result:
    """Play a short tone and make sure aplay accepts it.

    This can't verify you actually *heard* anything — no loopback — but it
    does catch the common case of playback pointing at a device that
    isn't there, which otherwise presents as the agent talking to itself.
    """
    from saathi.config import AUDIO_OUTPUT_DEVICE

    cmd = ["speaker-test", "-t", "sine", "-f", "440", "-l", "1", "-c", "2"]
    if AUDIO_OUTPUT_DEVICE:
        cmd += ["-D", AUDIO_OUTPUT_DEVICE]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=20)
    except FileNotFoundError:
        return Result(WARN, "speaker-test not installed", "sudo apt install alsa-utils")
    except Exception as e:
        return Result(WARN, f"couldn't test playback ({e})")

    where = AUDIO_OUTPUT_DEVICE or "the ALSA default"
    if done.returncode != 0:
        return Result(
            FAIL, f"playback failed on {where}",
            "list outputs with `aplay -l`, then set AUDIO_OUTPUT_DEVICE=plughw:N,0 in .env",
        )
    return Result(OK, f"played a tone on {where}")


def check_mopidy() -> Result:
    import requests

    try:
        response = requests.post(
            MOPIDY_RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "core.get_version"},
            timeout=5,
        )
        response.raise_for_status()
        version = response.json().get("result")
    except Exception as e:
        return Result(
            FAIL, f"unreachable at {MOPIDY_RPC_URL} ({type(e).__name__})",
                "bash scripts/install-mopidy.sh  (Debian's 3.4.2 is broken on Trixie)",
        )
    # Reporting "playing" is not the same as being audible: Mopidy has
    # its own GStreamer output, and an unconfigured one goes to HDMI
    # while the speaker sits silent.
    sink = ""
    try:
        conf = subprocess.run(
            ["mopidyctl", "config"], capture_output=True, text=True, timeout=20
        ).stdout
        for line in conf.splitlines():
            if line.strip().startswith("output ="):
                sink = line.split("=", 1)[1].strip()
                break
    except Exception:
        pass

    if version and str(version).startswith("3"):
        return Result(
            WARN, f"Mopidy {version} — the broken Debian build",
            "bash scripts/install-mopidy.sh — 3.x can't play on GStreamer 1.26",
        )

    if sink and "autoaudiosink" in sink:
        return Result(
            WARN, f"Mopidy {version}, output={sink}",
            "autoaudiosink usually picks HDMI — set [audio] output = alsasink in /etc/mopidy/mopidy.conf",
        )
    return Result(OK, f"Mopidy {version}" + (f", output={sink}" if sink else ""))


def check_radio() -> Result:
    from saathi.music.radio import RadioBrowser, RadioError

    try:
        station = RadioBrowser().best("news")
    except RadioError as e:
        return Result(FAIL, str(e), "check the Pi's internet connection")
    if station is None:
        return Result(WARN, "reachable but returned no stations")
    return Result(OK, f"found {station.name!r}")


def check_openai() -> Result:
    if not OPENAI_API_KEY:
        return Result(FAIL, "no key", "run: python -m saathi.setup")

    import requests

    try:
        response = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            timeout=15,
        )
    except Exception as e:
        return Result(FAIL, f"couldn't reach OpenAI ({type(e).__name__})")

    if response.status_code == 401:
        return Result(FAIL, "key rejected", "the key is wrong or revoked — make a new one")
    if response.status_code == 429:
        return Result(FAIL, "rate limited or out of credit", "add credit at platform.openai.com")
    if not response.ok:
        return Result(FAIL, f"HTTP {response.status_code}")
    return Result(OK, "key accepted")


def check_livekit() -> Result:
    """Actually talk to the server.

    Minting a token only proves three strings are present — it never
    leaves the machine, so a typo'd URL or a blocked network still
    reported "credentials valid" and then hung at connect time with no
    output. Listing rooms is a real round trip.
    """
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        return Result(FAIL, "not configured", "run: python -m saathi.setup")

    import asyncio

    async def probe():
        from livekit import api as lk_api

        client = lk_api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        try:
            return await asyncio.wait_for(
                client.room.list_rooms(lk_api.ListRoomsRequest()), timeout=20
            )
        finally:
            await client.aclose()

    try:
        rooms = asyncio.run(probe())
    except asyncio.TimeoutError:
        return Result(FAIL, f"timed out reaching {LIVEKIT_URL}", "is the URL right, and the Pi online?")
    except Exception as e:
        return Result(FAIL, f"{type(e).__name__}: {str(e)[:100]}", "check LIVEKIT_URL / key / secret")

    count = len(getattr(rooms, "rooms", []) or [])
    return Result(OK, f"reachable, {count} room(s) live")


def check_wake_models() -> Result:
    from saathi.config import WAKE_ENGINE

    try:
        from saathi.wake import build_engine

        build_engine()
    except Exception as e:
        # The engine's own message already names the fix (install this,
        # or switch to the free one), so don't paper over it with a
        # generic "check internet".
        return Result(FAIL, f"[{WAKE_ENGINE}] {str(e)[:140]}")
    return Result(OK, f"{WAKE_ENGINE} loads")


def check_clock() -> Result:
    """The Pi's timezone, because the agent greets people by it.

    A fresh Raspberry Pi OS image is UTC. In Singapore that is eight
    hours out, so the box wishes someone good morning at six in the
    evening — which is a small thing that instantly gives away that
    nobody is really there.
    """
    from datetime import datetime

    now = datetime.now()
    zone = now.astimezone().tzname() or "?"
    offset = now.astimezone().utcoffset()
    stamp = f"{now:%a %-d %b %-I:%M %p} {zone}"

    if offset is None or offset.total_seconds() == 0:
        return Result(
            WARN, f"{stamp} — looks like UTC, so greetings will be hours out",
            "sudo timedatectl set-timezone Asia/Singapore   (or your own)",
        )
    return Result(OK, stamp)


def check_console() -> Result:
    """The way back in when everything else is broken.

    Checked last but worth checking: the whole point of the console is to
    be running before you need it, and "it wasn't started" is only ever
    discovered at the moment it would have helped.
    """
    from saathi.config import CONSOLE_HTTP_PORT
    from saathi.console import system as console_system

    # Installed as a template (saathi-console@pi), like the other units,
    # so the instance name has to be guessed before it can be asked about.
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    candidates = [f"saathi-console@{user}"] if user else []
    candidates.append("saathi-console")

    unit = unit_state = None
    for candidate in candidates:
        state = console_system.service_state(candidate)
        if state != "unknown":
            unit, unit_state = candidate, state
            break

    if unit is None:
        return Result(
            WARN, "not installed — you'll need a monitor if the network changes",
            "bash scripts/install-console.sh",
        )
    if unit_state != "active":
        return Result(FAIL, f"{unit} is {unit_state}", f"sudo systemctl restart {unit}")

    where = console_system.addresses()
    if not where:
        return Result(WARN, "running, but this Pi has no address — reachable over Bluetooth only")
    return Result(OK, f"http://{where[0].address}:{CONSOLE_HTTP_PORT}")


def check_memory() -> Result:
    from saathi.config import AGENT_LANGUAGES, LANGUAGE_NAMES, MEMORY_BACKEND
    from saathi.memory import build_memory

    langs = ", ".join(LANGUAGE_NAMES.get(c, c) for c in AGENT_LANGUAGES)

    store = build_memory()
    if store.name == "none":
        if MEMORY_BACKEND not in ("none", "", "null"):
            return Result(
                FAIL, f"{MEMORY_BACKEND} configured but wouldn't start",
                "python -m saathi.setup --only memory",
            )
        return Result(WARN, f"off — speaks {langs}", "optional: python -m saathi.setup --only memory")

    # A real round trip. A key that's present but rejected otherwise only
    # shows up as an assistant that quietly never remembers anything.
    facts = store.recall("test", limit=1)
    return Result(OK, f"{store.name} reachable ({len(facts)} hit) — speaks {langs}")


def check_calling() -> Result:
    from saathi.config import LIVEKIT_PSTN_TRUNK_ID, LIVEKIT_SIP_TRUNK_ID

    if not (LIVEKIT_SIP_TRUNK_ID or LIVEKIT_PSTN_TRUNK_ID):
        return Result(WARN, "no trunk configured", "optional — see SETUP.md §5")
    from saathi.contacts import ContactsRegistry

    names = ContactsRegistry().names()
    if not names:
        return Result(WARN, "trunk set but no contacts", "edit contacts.json")
    return Result(OK, f"{len(names)} contact(s): {', '.join(names)}")


CHECKS: List[Check] = [
    Check("Configuration", check_env),
    Check("Audio tools", check_tools),
    Check("Microphone present", check_mic_exists),
    Check("Microphone hears", check_mic_hears),
    Check("Speaker", check_speaker),
    Check("OpenAI", check_openai),
    Check("LiveKit", check_livekit),
    Check("Mopidy", check_mopidy),
    Check("Radio Browser", check_radio),
    Check("Wake word", check_wake_models),
    Check("Clock", check_clock),
    Check("Console", check_console, optional=True),
    Check("Memory & language", check_memory, optional=True),
    Check("Calling", check_calling, optional=True),
]


# ---- runner -------------------------------------------------------------

def run_check(check: Check) -> Result:
    """Never let a check's own crash look like the thing it was checking."""
    try:
        result = check.run()
    except Exception as e:
        return Result(FAIL, f"check itself errored: {type(e).__name__}: {e}")
    if check.optional and result.status == FAIL:
        return Result(WARN, result.detail, result.fix)
    return result


def main() -> int:
    quiet_console()
    print(f"\n{BOLD}Saathi doctor{RESET}\n")

    results = []
    for check in CHECKS:
        print(f"  {DIM}...{RESET} {check.name}", end="\r", flush=True)
        result = run_check(check)
        results.append((check, result))

        mark = {OK: f"{GREEN}✓{RESET}", WARN: f"{YELLOW}!{RESET}", FAIL: f"{RED}✗{RESET}"}[result.status]
        print(f"  {mark} {check.name:<22} {DIM}{result.detail}{RESET}")
        if result.fix and result.status != OK:
            print(f"    {DIM}→ {result.fix}{RESET}")

    failed = [c.name for c, r in results if r.status == FAIL]
    warned = [c.name for c, r in results if r.status == WARN]

    print()
    if failed:
        print(f"{RED}{len(failed)} blocking problem(s){RESET}: {', '.join(failed)}")
        return 1

    if warned:
        print(f"{YELLOW}Ready, with {len(warned)} warning(s){RESET}: {', '.join(warned)}")
    else:
        print(f"{GREEN}Everything checks out.{RESET}")

    # Templated units, and there are two — the brain and the box. Naming
    # the wrong one sends people to "Unit saathi.service not found", which
    # reads as a broken install rather than a typo in this message.
    import getpass

    user = getpass.getuser()
    installed = os.path.exists("/etc/systemd/system/saathi@.service")

    print(f"\nRun it in the foreground first, where you can see it work:")
    print(f"  {BOLD}./venv/bin/python -m saathi.agent dev{RESET}   {DIM}(terminal 1){RESET}")
    print(f"  {BOLD}./venv/bin/python -m saathi.device{RESET}      {DIM}(terminal 2){RESET}")

    if installed:
        print(f"\nOr as services:  {BOLD}sudo systemctl start saathi-agent@{user} saathi@{user}{RESET}")
        print(f"{DIM}  watch it:      journalctl -fu saathi@{user}{RESET}\n")
    else:
        print(f"\nTo run on boot:  {BOLD}bash systemd/install.sh{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
