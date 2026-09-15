"""Working out who is holding the microphone.

The bug this replaces: everything guessed the unit name (saathi@$USER)
and stopped that. On a Pi where the service was installed under another
name the stop quietly did nothing, and the next thing to open the device
got "Device or resource busy" — a guess that was wrong and then said
nothing about having guessed.
"""
import os

import pytest

from saathi.mic import CAPTURE, Holder, describe, free, holders, parse_cmdline, parse_unit


# ---- reading /proc ------------------------------------------------------

def test_unit_from_cgroup_v2():
    cgroup = "0::/system.slice/system-saathi.slice/saathi@pi.service\n"
    assert parse_unit(cgroup) == "saathi@pi.service"


def test_unit_from_cgroup_v1():
    cgroup = (
        "12:pids:/system.slice/saathi-agent@basudeb.service\n"
        "11:devices:/system.slice/saathi-agent@basudeb.service\n"
        "0::/\n"
    )
    assert parse_unit(cgroup) == "saathi-agent@basudeb.service"


def test_no_unit_for_a_plain_process():
    """Something started from a shell has no unit, so it has to be killed
    by pid rather than stopped by name."""
    assert parse_unit("0::/user.slice/user-1000.slice/session-3.scope\n") == ""


def test_no_unit_from_nothing():
    assert parse_unit("") == ""


def test_cmdline_is_nul_separated():
    assert parse_cmdline("arecord\x00-q\x00-D\x00plughw:3,0\x00") == "arecord -q -D plughw:3,0"


def test_cmdline_of_a_vanished_process():
    assert parse_cmdline("") == ""


# ---- capture vs playback ------------------------------------------------

def test_capture_devices_match():
    assert CAPTURE.search("/dev/snd/pcmC0D0c")
    assert CAPTURE.search("/dev/snd/pcmC3D0c")


def test_playback_devices_do_not():
    """Stopping a process for holding the speaker would be wrong, and the
    two device names differ by one letter."""
    assert not CAPTURE.search("/dev/snd/pcmC0D0p")
    assert not CAPTURE.search("/dev/snd/controlC0")
    assert not CAPTURE.search("/dev/snd/timer")


# ---- whose is it to stop ------------------------------------------------

def test_our_service_is_ours():
    assert Holder(1, "python -m saathi.device", "saathi@pi.service").ours is True


def test_our_agent_is_ours():
    assert Holder(1, "python -m saathi.agent start", "saathi-agent@pi.service").ours is True


def test_a_stray_arecord_is_ours():
    """Left behind by a run that crashed. No unit will clean it up."""
    assert Holder(1, "arecord -q -f S16_LE -D plughw:3,0", "").ours is True


def test_someone_elses_recorder_is_not():
    """This runs as root. "Kill whatever is in the way" is not a thing to
    do on someone's machine."""
    assert Holder(1, "/usr/bin/zoom", "zoom.service").ours is False
    assert Holder(1, "pipewire", "pipewire.service").ours is False


# ---- describing ---------------------------------------------------------

def test_describe_when_free():
    assert "Nothing" in describe([])


def test_describe_names_the_holder():
    text = describe([Holder(4321, "python -m saathi.device", "saathi@pi.service")])
    assert "4321" in text
    assert "saathi@pi.service" in text


def test_describe_says_when_it_is_not_ours():
    text = describe([Holder(99, "/usr/bin/zoom", "zoom.service")])
    assert "isn't mine to stop" in text


# ---- freeing ------------------------------------------------------------

class Ran:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))


def test_free_stops_units_by_name_not_pid():
    """A unit with Restart=always comes straight back if you kill it,
    which looks like the microphone freeing itself for two seconds and
    then not."""
    ran = Ran()
    did = free([Holder(7, "python -m saathi.device", "saathi@pi.service")], run=ran)
    assert ran.calls == [["systemctl", "stop", "saathi@pi.service"]]
    assert "saathi@pi.service" in did[0]


def test_free_stops_each_unit_once():
    ran = Ran()
    free([
        Holder(7, "python -m saathi.device", "saathi@pi.service"),
        Holder(8, "arecord", "saathi@pi.service"),
    ], run=ran)
    assert len(ran.calls) == 1


def test_free_leaves_other_peoples_processes_alone():
    ran = Ran()
    did = free([Holder(99, "/usr/bin/zoom", "zoom.service")], run=ran)
    assert ran.calls == []
    assert did == []


def test_free_when_nothing_holds_it():
    ran = Ran()
    assert free([], run=ran) == []
    assert ran.calls == []


# ---- against the real /proc --------------------------------------------

def test_holders_reads_the_real_proc_without_blowing_up():
    """It walks every process on the box, most of which it can't read and
    some of which vanish mid-walk."""
    assert isinstance(holders(), list)


def test_holders_skips_itself():
    assert all(h.pid != os.getpid() for h in holders())
