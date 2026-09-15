"""
`python -m saathi.music.cli` — test music without talking to anything.

When "play some music" does nothing, the failure could be the agent, the
tools, Mopidy, the network, or the speaker. This drives the music layer
directly so you can rule out the top half of that list in one command.

    python -m saathi.music.cli play "old hindi songs"
    python -m saathi.music.cli play "lata mangeshkar" --source song
    python -m saathi.music.cli stations "jazz"
    python -m saathi.music.cli stop
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import List, Optional

from saathi.logging_setup import get_logger, quiet_console
from saathi.music.mopidy import MopidyClient, MopidyError
from saathi.music.player import MusicError, MusicPlayer
from saathi.music.radio import RadioBrowser, RadioError

log = get_logger("music.cli")

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def cmd_play(args) -> int:
    try:
        print(f"  {MusicPlayer().play(args.query, source=args.source)}")
    except MusicError as e:
        print(f"  {RED}✗{RESET} {e}")
        return 1
    return 0


def cmd_stations(args) -> int:
    """Search without playing — separates 'can't find it' from 'can't play it'."""
    try:
        stations = RadioBrowser().search(args.query, limit=args.limit)
    except RadioError as e:
        print(f"  {RED}✗{RESET} {e}")
        return 1

    if not stations:
        print(f"  {DIM}nothing found for {args.query!r}{RESET}")
        return 1
    for s in stations:
        print(f"  {BOLD}{s.name}{RESET}  {DIM}{s.country or ''} {s.url}{RESET}")
    return 0


def cmd_status(args) -> int:
    client = MopidyClient()
    try:
        state = client.state()
        track = client.current_track_name()
        volume = client.get_volume()
    except MopidyError as e:
        print(f"  {RED}✗{RESET} {e}")
        return 1
    print(f"  state={state}  volume={volume}  track={track or '—'}")
    return 0


def cmd_backends(args) -> int:
    client = MopidyClient()
    try:
        schemes = client.uri_schemes()
    except MopidyError as e:
        print(f"  {RED}✗{RESET} {e}")
        return 1

    print(f"  loaded: {', '.join(schemes) or '(none)'}")
    if "youtube" in schemes:
        print(f"  {GREEN}✓{RESET} songs by name available")
        return 0

    print(f"  {RED}✗{RESET} no youtube backend — songs by name won't work, only radio")
    print(f"    {DIM}sudo pip3 install --break-system-packages Mopidy-YouTube yt-dlp{RESET}")
    print(f"    {DIM}then add [youtube]\\nenabled = true to /etc/mopidy/mopidy.conf{RESET}")
    print(f"    {DIM}check why it was skipped: journalctl -u mopidy -n 40 | grep -i youtube{RESET}")
    return 1


def _ytdlp() -> tuple:
    """(version, path) of the yt-dlp Mopidy will actually use.

    Mopidy runs from its own venv, so the yt-dlp on your PATH is
    routinely a different and healthier one than the one doing the work.
    """
    import subprocess

    for candidate in (
        os.path.expanduser(os.environ.get("MOPIDY_VENV", "~/mopidy-venv")) + "/bin/yt-dlp",
        "/opt/mopidy-venv/bin/yt-dlp",
        "yt-dlp",
    ):
        try:
            done = subprocess.run([candidate, "--version"], capture_output=True,
                                  text=True, timeout=20)
        except Exception:
            continue
        if done.returncode == 0:
            return done.stdout.strip(), candidate
    return "", ""


def _ytdlp_is_stale(version: str) -> bool:
    """yt-dlp versions are dates: 2025.09.05.

    YouTube changes something every few weeks and every yt-dlp older
    than the change stops extracting — silently, as "no results" or a
    track that loads and plays nothing. Two months is generous.
    """
    import datetime

    match = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})", version or "")
    if not match:
        return False
    try:
        released = datetime.date(*(int(g) for g in match.groups()))
    except ValueError:
        return False
    return (datetime.date.today() - released).days > 60


def cmd_check(args) -> int:
    """Walk the whole chain and name the link that's broken.

    "Music doesn't work" has six or seven possible causes that all
    present identically — silence — so each one is asked about
    separately and in the order they have to succeed.
    """
    failures = 0

    def ok(text):
        print(f"  {GREEN}✓{RESET} {text}")

    def fail(text, fix=""):
        nonlocal failures
        failures += 1
        print(f"  {RED}✗{RESET} {text}")
        if fix:
            print(f"    {DIM}{fix}{RESET}")

    client = MopidyClient()

    # 1. Is Mopidy there at all.
    print(f"\n{BOLD}Mopidy{RESET}")
    try:
        version = client.version()
        ok(f"reachable, version {version}")
        if version.startswith("3."):
            fail("version 3 is broken against GStreamer 1.26 — it reports "
                 "playing and makes no sound",
                 "bash scripts/install-mopidy.sh")
    except MopidyError as e:
        fail(str(e), "sudo systemctl status mopidy-venv")
        print(f"\n  {DIM}Nothing else can work until this does.{RESET}")
        return 1

    # 2. Which backends loaded.
    print(f"\n{BOLD}Backends{RESET}")
    try:
        schemes = client.uri_schemes()
        ok(f"loaded: {', '.join(schemes) or '(none)'}")
        if "youtube" not in schemes:
            fail("no youtube backend — named songs can't work, only radio",
                 "journalctl -u mopidy-venv -n 40 | grep -i youtube")
    except MopidyError as e:
        fail(str(e))
        schemes = []

    # 3. yt-dlp, which is what actually breaks.
    if "youtube" in schemes:
        print(f"\n{BOLD}yt-dlp{RESET}")
        version, where = _ytdlp()
        if not version:
            fail("not found in Mopidy's venv", "bash scripts/install-mopidy.sh")
        elif _ytdlp_is_stale(version):
            fail(f"{version} is old ({where})",
                 "YouTube breaks it every few weeks. Fix: saathi music update")
        else:
            ok(f"{version}")

    # 4. Radio, the half that doesn't depend on YouTube.
    print(f"\n{BOLD}Radio{RESET}")
    try:
        station = RadioBrowser().best(args.query)
        ok(f"found {station.name}" if station else "search works, no match for that query")
    except RadioError as e:
        fail(str(e), "this needs internet — check the Pi is online")

    # 5. Can it find a named song.
    if "youtube" in schemes:
        print(f"\n{BOLD}Searching for a song{RESET}")
        print(f"  {DIM}(first lookup is slow — yt-dlp resolves each hit){RESET}")
        try:
            uris = client.search_tracks(args.query, uri_scheme="youtube", limit=3)
            if uris:
                ok(f"{len(uris)} result(s): {uris[0]}")
            else:
                fail(f"no results for {args.query!r}",
                     "almost always a stale yt-dlp: saathi music update")
        except MopidyError as e:
            fail(str(e))

    # 6. The one that catches "says playing, makes no sound".
    if not args.no_play:
        print(f"\n{BOLD}Actually playing{RESET}")
        try:
            print(f"  {MusicPlayer().play(args.query)}")
            time.sleep(3)
            state = client.state()
            first = client.time_position()
            time.sleep(2)
            second = client.time_position()
            if state != "playing":
                fail(f"state is {state!r}, not playing")
            elif second <= first:
                # The exact shape of the Trixie/GStreamer failure: the
                # track loads, the state says playing, and the position
                # never moves.
                fail(f"state is playing but the position is stuck at {first}ms — "
                     "nothing is coming out of the speaker",
                     "usually Mopidy 3 on GStreamer 1.26: bash scripts/install-mopidy.sh")
            else:
                ok(f"playing, position moving ({first} -> {second}ms)")
                print(f"    {DIM}you should be able to hear this{RESET}")
        except MusicError as e:
            fail(str(e))

    print()
    if failures:
        print(f"  {RED}{failures} problem(s) above.{RESET}\n")
        return 1
    print(f"  {GREEN}Every part of the music chain works.{RESET}\n")
    return 0


def cmd_update(args) -> int:
    """Update yt-dlp inside Mopidy's venv, then restart it.

    The single most common fix. YouTube changes something, every yt-dlp
    older than the change stops extracting, and the symptom is "no
    results" or a track that loads and plays silence — neither of which
    points at yt-dlp.
    """
    import subprocess

    venv = os.path.expanduser(os.environ.get("MOPIDY_VENV", "~/mopidy-venv"))
    pip = f"{venv}/bin/pip"
    if not os.path.exists(pip):
        print(f"  {RED}✗{RESET} no Mopidy venv at {venv}")
        print(f"    {DIM}bash scripts/install-mopidy.sh{RESET}")
        return 1

    before, _ = _ytdlp()
    print(f"  updating yt-dlp in {venv} (was {before or 'unknown'})")
    done = subprocess.run([pip, "install", "--upgrade", "yt-dlp"],
                          capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        print(f"  {RED}✗{RESET} {(done.stderr or done.stdout).strip()[:400]}")
        return 1

    after, _ = _ytdlp()
    print(f"  now {after or 'unknown'}")
    # Mopidy imports yt-dlp at startup, so an upgraded package on disk
    # changes nothing until it restarts.
    print("  restarting Mopidy")
    subprocess.run(["sudo", "systemctl", "restart", "mopidy-venv"], timeout=120)
    print(f"  {GREEN}✓{RESET} done — try: saathi music check \"kishore kumar\"")
    return 0


def _simple(action: str):
    def run(args) -> int:
        try:
            print(f"  {getattr(MusicPlayer(), action)()}")
        except MusicError as e:
            print(f"  {RED}✗{RESET} {e}")
            return 1
        return 0
    return run


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m saathi.music.cli",
        description="Drive the music layer directly, without the agent.",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("play", help="play something")
    p.add_argument("query")
    p.add_argument("--source", choices=["auto", "song", "station"], default="auto")

    s = sub.add_parser("stations", help="search radio without playing")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)

    c = sub.add_parser("check", help="walk the whole chain, name what's broken")
    c.add_argument("query", nargs="?", default="kishore kumar")
    c.add_argument("--no-play", action="store_true", help="don't make a sound")
    c.set_defaults(func=cmd_check)

    u = sub.add_parser("update", help="update yt-dlp in Mopidy's venv (the usual fix)")
    u.set_defaults(func=cmd_update)

    sub.add_parser("status", help="what Mopidy is doing right now")
    sub.add_parser("backends", help="which Mopidy backends loaded")
    for name in ("pause", "resume", "stop", "next_track"):
        sub.add_parser(name.replace("_track", ""), help=name.replace("_", " "))

    args = parser.parse_args(argv)
    quiet_console()

    if args.command == "play":
        return cmd_play(args)
    if args.command == "stations":
        return cmd_stations(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "backends":
        return cmd_backends(args)
    if args.command in ("pause", "resume", "stop"):
        return _simple(args.command)(args)
    if args.command == "next":
        return _simple("next_track")(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
