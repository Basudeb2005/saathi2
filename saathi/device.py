"""
The Pi as a participant in its own room.

This is the piece that makes Saathi a speaker rather than a demo. The
agent (`saathi.agent`) is a worker waiting to be dispatched; without
something publishing a microphone and playing what comes back, the only
way to talk to it is a browser. This module is that something.

The loop:

    wait for the wake word  ->  join the room, publish the mic, play what
    comes back  ->  go quiet  ->  leave  ->  wait for the wake word

Two details that are load-bearing rather than incidental:

**One mic at a time.** The wake listener and the session both want
`arecord` on the same ALSA device, and ALSA will not reliably give it to
two processes at once. So the wake listener is stopped for the duration
of a session and restarted afterwards. Serial by construction, not by
luck.

**Connect only while talking.** LiveKit's free tier is 1,000 agent
minutes a month. A box that holds the connection open all day spends that
in a fortnight; one that connects per conversation spends a few minutes a
day. Hence the idle timeout rather than a permanent link.
"""
from __future__ import annotations

import asyncio
import audioop
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

from saathi.config import (
    DEVICE_FRAME_MS,
    DEVICE_IDENTITY,
    DEVICE_SAMPLE_RATE,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    SAATHI_ROOM_NAME,
    SESSION_IDLE_TIMEOUT_S,
    SESSION_MAX_S,
    SPEECH_RMS_THRESHOLD,
    WAKE_CAPTURE_DEVICE,
)
from saathi.logging_setup import get_logger
from saathi.wake import _stop

log = get_logger("device")

FRAME_SAMPLES = DEVICE_SAMPLE_RATE * DEVICE_FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


class DeviceError(Exception):
    """The Pi couldn't join, or couldn't open its own audio."""


# ---- pure helpers (tested) ---------------------------------------------

def is_speech(pcm: bytes, threshold: int = SPEECH_RMS_THRESHOLD) -> bool:
    """True if this 16-bit PCM frame sounds like someone talking.

    Only used to hold the idle timer open — a false positive costs a few
    seconds of connection, a false negative hangs up on someone
    mid-sentence, so it is deliberately generous.
    """
    if not pcm:
        return False
    return audioop.rms(pcm, 2) > threshold


class IdleTimer:
    """Counts down from the last thing anyone said.

    Both directions reset it: the agent talking, and the person talking.
    Watching only one side hangs up on whoever is quieter.
    """

    def __init__(
        self,
        timeout_s: float = SESSION_IDLE_TIMEOUT_S,
        max_s: float = SESSION_MAX_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.timeout_s = timeout_s
        self.max_s = max_s
        self._clock = clock
        self._started = clock()
        self._last_activity = self._started

    def poke(self) -> None:
        self._last_activity = self._clock()

    @property
    def expired(self) -> bool:
        now = self._clock()
        if now - self._started >= self.max_s:
            log.info("Session hit the hard cap of %.0fs", self.max_s)
            return True
        return (now - self._last_activity) >= self.timeout_s


# ---- audio i/o ----------------------------------------------------------

def _spawn_arecord(device: Optional[str]) -> subprocess.Popen:
    cmd = [
        "arecord", "-q", "-f", "S16_LE",
        "-r", str(DEVICE_SAMPLE_RATE), "-c", "1", "-t", "raw",
    ]
    if device:
        cmd += ["-D", device]
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE)
    except FileNotFoundError as e:
        raise DeviceError("arecord not found — install alsa-utils") from e


def _spawn_aplay(sample_rate: int, channels: int) -> subprocess.Popen:
    cmd = ["aplay", "-q", "-f", "S16_LE", "-r", str(sample_rate), "-c", str(channels)]
    try:
        return subprocess.Popen(cmd, stdin=subprocess.PIPE)
    except FileNotFoundError as e:
        raise DeviceError("aplay not found — install alsa-utils") from e


def _access_token() -> str:
    """A join token for our own room, minted locally.

    The Pi holds the API secret anyway, so there is no auth server in this
    design — one less thing to run, and one less thing to be down at 3am.
    """
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise DeviceError(
            "LiveKit isn't configured — run `python -m saathi.setup`."
        )

    from livekit import api

    return (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(DEVICE_IDENTITY)
        .with_name("Saathi speaker")
        .with_grants(api.VideoGrants(room_join=True, room=SAATHI_ROOM_NAME))
        .to_jwt()
    )


# ---- one conversation ---------------------------------------------------

async def run_session(room_name: str = SAATHI_ROOM_NAME) -> None:
    """Join, talk, leave. Returns when the room has been quiet a while."""
    from livekit import rtc

    room = rtc.Room()
    timer = IdleTimer()
    players: list[subprocess.Popen] = []

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            log.info("Hearing %s", participant.identity)
            asyncio.create_task(_play(rtc.AudioStream(track), timer, players))

    log.info("Joining room=%s as %s", room_name, DEVICE_IDENTITY)
    try:
        await room.connect(LIVEKIT_URL, _access_token())
    except Exception as e:
        raise DeviceError(f"Couldn't join the room: {e}") from e

    source = rtc.AudioSource(DEVICE_SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    mic = _spawn_arecord(WAKE_CAPTURE_DEVICE)
    try:
        await _pump_mic(mic, source, timer, rtc)
    finally:
        _stop(mic)
        for p in players:
            _stop(p)
        await room.disconnect()
        log.info("Left room=%s", room_name)


async def _pump_mic(mic, source, timer: IdleTimer, rtc) -> None:
    """Mic -> LiveKit, until the room goes quiet.

    The blocking read runs in a thread so it never stalls the event loop —
    otherwise inbound audio stutters while we wait on ALSA.
    """
    loop = asyncio.get_running_loop()

    while not timer.expired:
        pcm = await loop.run_in_executor(None, mic.stdout.read, FRAME_BYTES)
        if not pcm or len(pcm) < FRAME_BYTES:
            log.warning("Mic stream ended")
            return

        if is_speech(pcm):
            timer.poke()

        await source.capture_frame(
            rtc.AudioFrame(
                data=pcm,
                sample_rate=DEVICE_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=FRAME_SAMPLES,
            )
        )


async def _play(stream, timer: IdleTimer, players: list) -> None:
    """LiveKit -> speaker. aplay is started from the first frame's own
    format rather than an assumed one, since TTS and a phone call arrive
    at different rates."""
    player = None
    try:
        async for event in stream:
            frame = event.frame
            if player is None:
                player = _spawn_aplay(frame.sample_rate, frame.num_channels)
                players.append(player)
                log.info("Playing %dHz %dch", frame.sample_rate, frame.num_channels)

            timer.poke()
            player.stdin.write(bytes(frame.data))
            player.stdin.flush()
    except Exception as e:
        log.info("Playback stream ended: %s", e)
    finally:
        if player and player.stdin:
            try:
                player.stdin.close()
            except Exception:
                pass


# ---- the forever loop ---------------------------------------------------

def run_forever() -> None:
    """Wake word -> conversation -> wake word, for as long as it's on."""
    from saathi.wake import build_engine, listen

    engine = build_engine()
    log.info("Saathi is listening for its wake word")
    print("Saathi is up. Say the wake word.", flush=True)

    while True:
        # `listen` owns the mic, so it is torn down before a session
        # starts and rebuilt afterwards — see the module docstring.
        #
        # close() explicitly rather than relying on the loop variable
        # going out of scope: the generator's finally is what kills
        # arecord, and leaving that to the garbage collector means the
        # old process can still hold the device when the next one opens
        # it, which fails as "Device or resource busy" forever after.
        stream = listen(engine)
        try:
            detection = next(stream, None)
        finally:
            stream.close()

        if detection is None:
            log.warning("Wake listener stopped; retrying in 2s")
            time.sleep(2)
            continue

        log.info("Woken by %r", detection.word)
        engine.reset()

        try:
            asyncio.run(run_session())
        except DeviceError as e:
            log.error("Session failed: %s", e)
            time.sleep(2)
        except Exception:
            log.exception("Session crashed; carrying on")
            time.sleep(2)


def main() -> int:
    try:
        run_forever()
    except KeyboardInterrupt:
        print()
        return 0
    except DeviceError as e:
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
