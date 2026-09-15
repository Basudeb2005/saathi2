"""Recording and playing on a machine that isn't the Pi.

The point of this module is that the interesting half of this project —
the prompt, the turn shape, the tools, the latency — has nothing to do
with a Raspberry Pi, and was pinned to one only because `arecord` and
`aplay` had been typed directly into four files.

Both backends speak raw signed 16-bit PCM on a pipe. That is the whole
contract, and these tests are mostly about not breaking it.
"""
import pytest

from saathi.audio import (ALSA, FFMPEG, SOX, AudioError, capture_command,
                          detect, device_env, playback_command, require)


def installed(*names):
    """A fake shutil.which for a machine with exactly these tools."""
    return lambda name: name if name in names else None


# ---- picking a backend --------------------------------------------------

def test_linux_uses_alsa():
    assert detect("Linux", installed("arecord", "aplay")) is ALSA


def test_mac_uses_sox():
    assert detect("Darwin", installed("rec", "play")) is SOX


def test_mac_falls_back_to_ffmpeg():
    assert detect("Darwin", installed("ffmpeg")) is FFMPEG


def test_mac_prefers_sox_over_anything_else_present():
    """Preference is by platform, not by what happens to be installed —
    a Mac with an alsa-utils from somewhere should still use sox."""
    every = installed("rec", "play", "ffmpeg", "arecord", "aplay")
    assert detect("Darwin", every) is SOX


def test_linux_prefers_alsa():
    every = installed("rec", "play", "ffmpeg", "arecord", "aplay")
    assert detect("Linux", every) is ALSA


def test_half_a_backend_does_not_count():
    """arecord without aplay records into a machine that can't answer."""
    assert detect("Linux", installed("arecord")) is None


def test_nothing_installed():
    assert detect("Linux", installed()) is None


def test_require_explains_how_to_fix_it_per_platform():
    with pytest.raises(AudioError) as e:
        require("Darwin", installed())
    assert "brew install sox" in str(e.value)

    with pytest.raises(AudioError) as e:
        require("Linux", installed())
    assert "alsa-utils" in str(e.value)


# ---- the commands -------------------------------------------------------

@pytest.mark.parametrize("backend", [ALSA, SOX, FFMPEG])
def test_every_backend_records_16k_mono(backend):
    cmd = capture_command(None, 16000, backend)
    assert cmd[0] == backend.record
    assert "16000" in cmd


@pytest.mark.parametrize("backend", [ALSA, SOX, FFMPEG])
def test_every_backend_plays_at_the_rate_it_is_given(backend):
    """TTS and a phone call don't arrive at the same rate, and playing
    one at the other's rate is the chipmunk bug."""
    cmd = playback_command(None, 24000, 2, backend)
    assert cmd[0] == backend.play
    assert "24000" in cmd
    assert "2" in cmd


def test_alsa_takes_a_device_as_a_flag():
    assert "-D" in capture_command("plughw:3,0", 16000, ALSA)
    assert "plughw:3,0" in capture_command("plughw:3,0", 16000, ALSA)


def test_sox_takes_a_device_through_the_environment():
    """sox has no device flag — AUDIODEV is the only way in, which is
    the entire reason device_env exists rather than another argument."""
    assert "plughw:3,0" not in capture_command("plughw:3,0", 16000, SOX)
    assert device_env("plughw:3,0", SOX)["AUDIODEV"] == "plughw:3,0"


def test_no_device_means_no_environment_meddling():
    assert "AUDIODEV" not in device_env(None, SOX)
    assert "AUDIODEV" not in device_env("plughw:3,0", ALSA)


def test_sox_writes_to_stdout():
    """The trailing "-" is the output file. Without it rec waits for a
    filename and records nothing."""
    assert capture_command(None, 16000, SOX)[-1] == "-"


def test_sox_reads_from_stdin():
    assert playback_command(None, 16000, 1, SOX)[-1] == "-"


@pytest.mark.parametrize("backend", [ALSA, SOX, FFMPEG])
def test_nothing_is_quoted_or_shell_escaped(backend):
    """These are passed to Popen as a list. A quote in one would be a
    literal quote in the filename, not a shell nicety."""
    for piece in capture_command("a device", 16000, backend):
        assert '"' not in piece and "'" not in piece
