"""
When there is no network, become one.

A Pi that boots somewhere new — a different house, a new router, a
changed wifi password — has no way to be told about it. No screen, no
keyboard, and ssh needs the network that is the problem. The standard
answer, and what every commercial device does, is to give up after a
while and start an access point of its own, so a phone can always reach
the console.

The whole of the tricky part is the giving up and the coming back:

- Too eager and a router that is merely slow to boot strands the Pi in
  setup mode while its own wifi comes up thirty seconds later.
- Never coming back and a Pi that went to a hotspot once during a power
  cut stays there until someone notices, which for a device in an old
  age home is never.

So: ninety seconds of no address before becoming an access point, and ten
minutes as one before dropping it and letting NetworkManager try the
saved networks again. If they are still gone, it is an access point again
ninety seconds later, and that cycle is fine to leave running for weeks.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from saathi.config import (
    HOTSPOT_AFTER_S,
    HOTSPOT_PASSWORD,
    HOTSPOT_RETRY_AFTER_S,
    HOTSPOT_SSID,
)
from saathi.console import system
from saathi.logging_setup import get_logger

log = get_logger("console.netwatch")

UP, DOWN, NOTHING = "up", "down", ""


class Decider:
    """Should the hotspot go up, come down, or be left alone?

    Pure and clock-injected. Every bug this could have is a timing bug,
    and waiting ninety real seconds to find out is not a test.
    """

    def __init__(
        self,
        offline_after_s: float = HOTSPOT_AFTER_S,
        retry_after_s: float = HOTSPOT_RETRY_AFTER_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.offline_after_s = offline_after_s
        self.retry_after_s = retry_after_s
        self._clock = clock
        self._offline_since: Optional[float] = None
        self._hotspot_since: Optional[float] = None

    def decide(self, online: bool, hotspot: bool) -> str:
        now = self._clock()

        if hotspot:
            # An access point always has an address of its own, so being
            # "online" means nothing here — only elapsed time does.
            self._offline_since = None
            if self._hotspot_since is None:
                self._hotspot_since = now
            elif now - self._hotspot_since >= self.retry_after_s:
                self._hotspot_since = None
                return DOWN
            return NOTHING

        self._hotspot_since = None

        if online:
            self._offline_since = None
            return NOTHING

        if self._offline_since is None:
            self._offline_since = now
            return NOTHING

        if now - self._offline_since >= self.offline_after_s:
            self._offline_since = None
            return UP
        return NOTHING


def online(runner: system.Runner = system.run) -> bool:
    """Any address on any real interface. Deliberately not a ping.

    A house whose broadband is down still has a working wifi network, and
    dropping that for a hotspot would take the Pi off the only network
    the phone is on — making a bad afternoon worse.
    """
    return bool(system.addresses(runner))


def watch(runner: system.Runner = system.run, interval_s: float = 15.0,
          stop: Optional[threading.Event] = None) -> None:
    decider = Decider()
    log.info(
        "Watching the network: hotspot %r after %ds offline, retry after %ds",
        HOTSPOT_SSID, HOTSPOT_AFTER_S, HOTSPOT_RETRY_AFTER_S,
    )
    while not (stop and stop.is_set()):
        try:
            action = decider.decide(online(runner), system.hotspot_active(runner))
            if action == UP:
                log.warning("No network for %ds — starting hotspot %r", HOTSPOT_AFTER_S, HOTSPOT_SSID)
                result = system.hotspot_up(HOTSPOT_SSID, HOTSPOT_PASSWORD, runner)
                if not result.ok:
                    log.error("Hotspot failed: %s", result.text)
            elif action == DOWN:
                log.info("Dropping the hotspot to try the saved networks again")
                system.hotspot_down(runner)
        except Exception:                        # pragma: no cover - defensive
            log.exception("Network watch stumbled; carrying on")

        if stop:
            stop.wait(interval_s)
        else:
            time.sleep(interval_s)


def watch_in_background(runner: system.Runner = system.run) -> threading.Thread:
    thread = threading.Thread(target=watch, args=(runner,), daemon=True)
    thread.start()
    return thread
