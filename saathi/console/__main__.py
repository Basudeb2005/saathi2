"""
`python -m saathi.console` — the thing that starts at boot.

One process, three jobs, because three systemd units to install is three
that can be half-installed:

  * Bluetooth, so the Pi can be asked its address with no network at all
  * HTTP, so a phone can drive it once there is one
  * the network watch, so there is one even when there isn't

Any of them failing leaves the others up. A Pi with no Bluetooth adapter
still gets a web console; a Pi with no network still answers over
Bluetooth. The mistake would be refusing to start because one radio is
missing, in the service whose entire job is being reachable when things
are broken.
"""
from __future__ import annotations

import argparse
import sys
import time

from saathi.config import CONSOLE_BT_CHANNEL, CONSOLE_HTTP_PORT, CONSOLE_SHELL
from saathi.console import auth, netwatch, system
from saathi.console.bluetooth import serve_in_background as serve_bluetooth
from saathi.console.commands import Console
from saathi.console.web import serve_in_background as serve_web
from saathi.logging_setup import get_logger

log = get_logger("console")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="saathi.console", description=__doc__)
    parser.add_argument("--token", action="store_true", help="print the console token and exit")
    parser.add_argument("--run", metavar="COMMAND", help="run one command and exit")
    parser.add_argument("--port", type=int, default=CONSOLE_HTTP_PORT)
    parser.add_argument("--no-hotspot", action="store_true",
                        help="don't fall back to an access point")
    args = parser.parse_args(argv)

    token = auth.load_or_create()

    if args.token:
        print(token)
        return 0

    console = Console(token=token)

    if args.run:
        reply = console.dispatch(args.run)
        print(reply.text)
        return 0 if reply.ok else 1

    bluetooth = serve_bluetooth(console, CONSOLE_BT_CHANNEL)
    serve_web(console, token, args.port)
    if not args.no_hotspot:
        netwatch.watch_in_background()

    where = system.addresses()
    log.info(
        "Console up. bluetooth=%s http=:%d shell=%s address=%s",
        "yes" if bluetooth else "no", args.port, CONSOLE_SHELL,
        where[0].address if where else "none",
    )
    if where:
        log.info("Open http://%s:%d on your phone", where[0].address, args.port)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
