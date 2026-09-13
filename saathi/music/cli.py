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
import sys
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

    sub.add_parser("status", help="what Mopidy is doing right now")
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
    if args.command in ("pause", "resume", "stop"):
        return _simple(args.command)(args)
    if args.command == "next":
        return _simple("next_track")(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
