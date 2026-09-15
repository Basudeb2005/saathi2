"""
Who is holding the microphone.

ALSA gives one process the capture device and tells the next one
"Device or resource busy" — four words, no name, no pid. On a box where
three different things legitimately want the microphone (the wake
listener, a conversation, and whatever you just started by hand to test
it) that message is nearly useless, and the usual response is to reboot.

So: ask the kernel. Every process's open files are in /proc, and a
process holding a capture device has an fd pointing at /dev/snd/pcmC0D0c.
That gives a pid; the pid gives a command line and, through its cgroup, a
systemd unit. Which turns "Device or resource busy" into "saathi@pi is
using it, here is how to stop it".

The unit name matters more than it looks. Everything here used to guess
it — saathi@$USER — and a Pi where the service was installed under
another name, or not templated at all, silently failed to stop and then
failed to record, which is exactly the loop this is for.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from saathi.logging_setup import get_logger

log = get_logger("mic")

PROC = Path("/proc")
# pcmC<card>D<device>c — the trailing "c" is capture. Playback is "p",
# and stopping a process for holding the speaker would be wrong.
CAPTURE = re.compile(r"/dev/snd/pcmC\d+D\d+c$")
# Things it is safe to stop to free the microphone: ours, and the
# recorder we started ourselves. Never anything else — this runs as root
# and "kill whatever is in the way" is not a thing to do on someone's
# machine.
OURS = ("saathi", "arecord")


@dataclass
class Holder:
    pid: int
    command: str = ""
    unit: str = ""

    @property
    def ours(self) -> bool:
        """Whether stopping this is our business.

        A unit called saathi-something, or a bare arecord — which on this
        box is only ever one of ours, since nothing else records. A
        browser or a conferencing app holding the mic is not ours to
        close, however inconvenient.
        """
        haystack = f"{self.unit} {self.command}".lower()
        return any(name in haystack for name in OURS)

    def __str__(self) -> str:
        what = self.unit or (self.command.split()[0] if self.command else "?")
        return f"pid {self.pid}  {what}"


# ---- parsing (pure, tested) --------------------------------------------

def parse_unit(cgroup: str) -> str:
    """The systemd unit from /proc/<pid>/cgroup, if it has one.

    cgroup v2 gives one line like
        0::/system.slice/system-saathi.slice/saathi@pi.service
    and v1 several, only some of which carry the unit. Either way the
    unit is the last path segment ending in .service.
    """
    for line in cgroup.splitlines():
        for segment in reversed(line.split("/")):
            if segment.endswith(".service"):
                return segment
    return ""


def parse_cmdline(raw: str) -> str:
    """/proc/<pid>/cmdline is NUL-separated and NUL-terminated."""
    return " ".join(part for part in raw.split("\0") if part)


# ---- looking ------------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return ""


def holders(proc: Path = PROC, me: Optional[int] = None) -> List[Holder]:
    """Every process with a capture device open.

    Reading another user's /proc/<pid>/fd needs root, so unprivileged
    this sees only your own processes. That is usually enough — the
    service runs as you — and when it isn't, the caller says "couldn't
    tell" rather than "nothing is holding it", which are very different
    answers.
    """
    me = os.getpid() if me is None else me
    found: List[Holder] = []

    for entry in sorted(proc.glob("[0-9]*")):
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if pid == me:
            continue

        fds = entry / "fd"
        try:
            descriptors = list(fds.iterdir())
        except OSError:
            continue                       # gone, or not ours to look at

        for fd in descriptors:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if CAPTURE.search(target):
                found.append(Holder(
                    pid=pid,
                    command=parse_cmdline(_read(entry / "cmdline")),
                    unit=parse_unit(_read(entry / "cgroup")),
                ))
                break

    return found


def describe(found: Optional[List[Holder]] = None) -> str:
    found = holders() if found is None else found
    if not found:
        return "Nothing is holding the microphone."
    lines = ["The microphone is in use by:"]
    lines += [f"  {holder}" for holder in found]
    if not any(h.ours for h in found):
        lines.append("")
        lines.append("None of that is Saathi's, so it isn't mine to stop.")
    return "\n".join(lines)


# ---- freeing ------------------------------------------------------------

def free(
    found: Optional[List[Holder]] = None,
    run: Callable[[List[str]], object] = subprocess.run,
) -> List[str]:
    """Stop our own holders. Returns what it did, in words.

    Units are stopped by name rather than killed by pid, because a unit
    with Restart=always comes straight back if you kill it — which looks
    like the microphone freeing itself for two seconds and then not.
    """
    found = holders() if found is None else found
    did: List[str] = []

    units = {h.unit for h in found if h.ours and h.unit}
    for unit in sorted(units):
        run(["systemctl", "stop", unit])
        did.append(f"stopped {unit}")

    # Anything left is a bare process — a recorder left behind by a run
    # that crashed, which no unit will clean up.
    for holder in found:
        if not holder.ours or holder.unit:
            continue
        try:
            os.kill(holder.pid, 15)
            did.append(f"stopped pid {holder.pid} ({holder.command.split()[0] if holder.command else '?'})")
        except (ProcessLookupError, PermissionError) as e:
            did.append(f"couldn't stop pid {holder.pid}: {e}")

    return did


def main(argv=None) -> int:
    """`python -m saathi.mic` — who has it. `--free` — take it back."""
    import argparse

    from saathi.logging_setup import quiet_console

    parser = argparse.ArgumentParser(prog="saathi.mic", description=__doc__)
    parser.add_argument("--free", action="store_true", help="stop Saathi's own holders")
    args = parser.parse_args(argv)

    quiet_console()
    found = holders()

    if not args.free:
        print(describe(found))
        if found and os.geteuid() != 0:
            print("\n(run with sudo to see processes that aren't yours)")
        return 0

    if not found:
        print("Microphone is already free.")
        return 0

    print(describe(found))
    did = free(found)
    if not did:
        print("\nNothing there was mine to stop.")
        return 1
    print()
    for line in did:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
