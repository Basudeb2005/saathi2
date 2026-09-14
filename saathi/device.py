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
from collections import deque
from typing import Callable, Deque, Dict, Optional

from saathi.config import (
    AUDIO_OUTPUT_DEVICE,
    HALF_DUPLEX,
    HALF_DUPLEX_HANGOVER_S,
    MUSIC_CHECK_INTERVAL_S,
    MUSIC_HOLDS_SESSION,
    MUSIC_SESSION_VOLUME,
    BUTTON_ENDS_SESSION,
    VOICE_TRIGGER_MS,
    VOICE_TRIGGER_RMS,
    WAKE_MODE,
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

# About a second of slack per stream. Enough to ride out a scheduling
# hiccup, short enough that a stalled stream is dropped rather than
# played back a second late.
_SPEAKER_QUEUE_FRAMES = 50


async def _rest(iterator):
    """Re-wrap an async iterator we already took one item from."""
    while True:
        try:
            yield await iterator.__anext__()
        except StopAsyncIteration:
            return


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


class FarEnd:
    """Tracks when the other side last made a sound.

    Used for two different things at once: keeping the session alive, and
    muting our microphone while the agent talks. Without the mute the mic
    hears the speaker, the agent transcribes its own voice and answers
    itself — which is what the gibberish is.
    """

    def __init__(self, hangover_s: float = HALF_DUPLEX_HANGOVER_S,
                 clock: Callable[[], float] = time.monotonic):
        self.hangover_s = hangover_s
        self._clock = clock
        self._last_sound: Optional[float] = None

    def heard(self) -> None:
        self._last_sound = self._clock()

    @property
    def speaking(self) -> bool:
        """True while the agent's voice is still in the room — including
        a tail after the last frame, for the speaker's decay and the
        room's reverb."""
        if self._last_sound is None:
            return False
        return (self._clock() - self._last_sound) < self.hangover_s


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

    def extend(self) -> None:
        """Reset the hard cap too, not just the idle countdown.

        The cap exists to stop a stuck session holding the line open. A
        session held open deliberately — music playing, so "stop" needs
        no wake word — is not stuck, and an album is longer than ten
        minutes.
        """
        now = self._clock()
        self._started = now
        self._last_activity = now

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
    if AUDIO_OUTPUT_DEVICE:
        cmd += ["-D", AUDIO_OUTPUT_DEVICE]
    log.info("Playback: %s", " ".join(cmd))
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
    far_end = FarEnd()
    # Built on the first track, from that track's own format — TTS and a
    # phone call don't arrive at the same rate.
    speaker: Dict[str, Speaker] = {}
    mixer: Dict[str, asyncio.Task] = {}

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        log.info("Hearing %s", participant.identity)

        async def start():
            stream = rtc.AudioStream(track)
            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return

            if "s" not in speaker:
                speaker["s"] = Speaker(first.frame.sample_rate, first.frame.num_channels)
                mixer["m"] = asyncio.create_task(speaker["s"].run())

            speaker["s"].submit(participant.identity, bytes(first.frame.data))
            await _play(_rest(iterator), timer, speaker["s"], far_end, participant.identity)

        asyncio.create_task(start())

    log.info("Joining room=%s as %s at %s", room_name, DEVICE_IDENTITY, LIVEKIT_URL)
    try:
        # Bounded: an unreachable or wrong URL otherwise hangs here with
        # no output at all, which is indistinguishable from a working
        # session sitting quietly waiting for someone to speak.
        await asyncio.wait_for(room.connect(LIVEKIT_URL, _access_token()), timeout=20)
    except asyncio.TimeoutError as e:
        raise DeviceError(
            f"Timed out connecting to {LIVEKIT_URL}. Check the URL is right and the Pi "
            f"can reach it — `python -m saathi.doctor` tests this."
        ) from e
    except Exception as e:
        raise DeviceError(f"Couldn't join the room: {e}") from e
    log.info("Connected to room=%s", room_name)

    @room.on("participant_connected")
    def _on_join(participant):
        log.info("%s joined the room", participant.identity)

    source = rtc.AudioSource(DEVICE_SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    log.info("Published the microphone")

    others = [p.identity for p in room.remote_participants.values()]
    if others:
        log.info("Already in the room: %s", ", ".join(others))
    else:
        log.warning(
            "Nobody else in the room yet — if the agent never joins, check that "
            "`python -m saathi.agent dev` is running and connected to the same project"
        )

    # Ducked for the whole session, not only while Saathi speaks: at full
    # volume the microphone cannot hear you over the speaker, and with no
    # echo cancellation there is nothing else to fall back on.
    previous_volume = await asyncio.to_thread(_duck_music)

    stop_pressed = asyncio.Event()
    watcher = None
    if WAKE_MODE == "button" and BUTTON_ENDS_SESSION:
        # The same button that starts a conversation is how you stop the
        # music. One thing to remember rather than two, which matters
        # more than it sounds for the person this is for.
        watcher = asyncio.create_task(_watch_for_stop(stop_pressed))

    mic = _spawn_arecord(WAKE_CAPTURE_DEVICE)
    log.info("Listening — say something. Session ends after %.0fs of quiet.", SESSION_IDLE_TIMEOUT_S)
    try:
        await _pump_mic(mic, source, timer, rtc, far_end, stop_pressed)
    finally:
        if watcher:
            watcher.cancel()
        await asyncio.to_thread(_restore_music, previous_volume)
        _stop(mic)
        for task in mixer.values():
            task.cancel()
        if "s" in speaker:
            speaker["s"].close()
        await room.disconnect()
        log.info("Left room=%s", room_name)


def _duck_music() -> Optional[int]:
    """Turn music down for the session. Returns the previous volume, or
    None if there was nothing to turn down."""
    try:
        from saathi.music.mopidy import MopidyClient

        client = MopidyClient()
        if client.state() != "playing":
            return None
        previous = client.get_volume()
        client.set_volume(MUSIC_SESSION_VOLUME)
        log.info("Ducked music %s -> %s for the session", previous, MUSIC_SESSION_VOLUME)
        return previous
    except Exception as e:
        log.info("Couldn't duck music: %s", e)
        return None


def _restore_music(previous: Optional[int]) -> None:
    """Put it back where the user had it, not at a constant — someone who
    turned it down stays turned down."""
    if previous is None:
        return
    try:
        from saathi.music.mopidy import MopidyClient

        MopidyClient().set_volume(previous)
        log.info("Restored music volume to %s", previous)
    except Exception as e:
        log.info("Couldn't restore music volume: %s", e)


def _music_is_playing() -> bool:
    """Best-effort. Never let a music check end a conversation: if Mopidy
    can't be reached, the honest answer is 'probably not playing', not a
    crash mid-sentence."""
    try:
        from saathi.music.mopidy import MopidyClient

        return MopidyClient().state() == "playing"
    except Exception:
        return False


async def _watch_for_stop(pressed: asyncio.Event) -> None:
    """Set the flag on the next press, from a thread — evdev's read_loop
    blocks, and blocking here would stall the audio it's meant to end."""
    from saathi.button import wait_for_press

    try:
        if await asyncio.to_thread(wait_for_press):
            log.info("Button pressed — ending the session")
            pressed.set()
    except Exception as e:
        log.info("Stop watcher ended: %s", e)


async def _pump_mic(
    mic, source, timer: IdleTimer, rtc, far_end: "FarEnd",
    stop_pressed: Optional[asyncio.Event] = None,
) -> None:
    """Mic -> LiveKit, until the room goes quiet.

    The blocking read runs in a thread so it never stalls the event loop —
    otherwise inbound audio stutters while we wait on ALSA.
    """
    loop = asyncio.get_running_loop()

    # Long enough that a healthy mic never trips it (a frame is 20ms),
    # short enough that a wedged one doesn't hold the session open.
    read_timeout = max(2.0, SESSION_IDLE_TIMEOUT_S / 4)
    next_music_check = 0.0
    music_playing = False

    while True:
        if stop_pressed is not None and stop_pressed.is_set():
            log.info("Ending the session on a button press")
            return

        if timer.expired:
            # Music keeps the session open so "stop" works without the
            # wake word — which it wouldn't, over a speaker playing music
            # into the microphone.
            now = time.monotonic()
            if MUSIC_HOLDS_SESSION and now >= next_music_check:
                music_playing = await asyncio.to_thread(_music_is_playing)
                next_music_check = now + MUSIC_CHECK_INTERVAL_S
            if not (MUSIC_HOLDS_SESSION and music_playing):
                return
            timer.extend()

        try:
            pcm = await asyncio.wait_for(
                loop.run_in_executor(None, mic.stdout.read, FRAME_BYTES),
                timeout=read_timeout,
            )
        except asyncio.TimeoutError:
            # arecord is alive but producing nothing — usually the device
            # is held by something else. Without this the read blocks
            # forever, timer.expired is never re-checked, and the session
            # hangs with no output rather than ending and cleaning up.
            log.warning("Mic produced nothing for %.0fs — giving up on this session", read_timeout)
            return

        if not pcm or len(pcm) < FRAME_BYTES:
            log.warning("Mic stream ended")
            return

        if HALF_DUPLEX and far_end.speaking:
            # Publish silence rather than stopping: the track staying live
            # keeps the far end's turn detection from treating a dropped
            # stream as us hanging up.
            pcm = b"\x00" * FRAME_BYTES
        elif is_speech(pcm):
            timer.poke()

        await source.capture_frame(
            rtc.AudioFrame(
                data=pcm,
                sample_rate=DEVICE_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=FRAME_SAMPLES,
            )
        )


class Speaker:
    """One aplay for the whole room, with the streams mixed into it.

    A player per track looked fine until a call: the agent and the person
    on the phone each got their own aplay, both opened the same ALSA
    device, and the result was a 163-second underrun. ALSA does not mix
    for you unless dmix happens to be configured, which on a Pi it
    usually isn't.

    So there is exactly one process, and audio is summed here in int16
    with clipping — which is also what makes a three-way call sound like
    a conversation rather than whoever spoke last.
    """

    def __init__(self, sample_rate: int, channels: int):
        self.sample_rate = sample_rate
        self.channels = channels
        self._streams: Dict[str, Deque[bytes]] = {}
        self._proc = _spawn_aplay(sample_rate, channels)
        log.info("Speaker open at %dHz %dch", sample_rate, channels)

    def submit(self, who: str, pcm: bytes) -> None:
        self._streams.setdefault(who, deque(maxlen=_SPEAKER_QUEUE_FRAMES)).append(pcm)

    def _mix(self) -> Optional[bytes]:
        """One frame from everyone who has audio waiting, summed."""
        frames = [q.popleft() for q in self._streams.values() if q]
        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]

        # Pad to the longest, then sum pairwise. audioop clips at int16
        # rather than wrapping, so two loud speakers distort instead of
        # turning into noise.
        longest = max(len(f) for f in frames)
        mixed = frames[0].ljust(longest, b"\x00")
        for frame in frames[1:]:
            mixed = audioop.add(mixed, frame.ljust(longest, b"\x00"), 2)
        return mixed

    async def run(self) -> None:
        """Drain the queues at real time until cancelled."""
        frame_s = DEVICE_FRAME_MS / 1000
        try:
            while True:
                pcm = self._mix()
                if pcm is None:
                    await asyncio.sleep(frame_s)
                    continue
                try:
                    self._proc.stdin.write(pcm)
                    self._proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    log.warning("Speaker closed underneath us")
                    return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    def close(self) -> None:
        if self._proc.stdin:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        _stop(self._proc)


async def _play(stream, timer: IdleTimer, speaker: "Speaker", far_end: "FarEnd", who: str) -> None:
    """One remote track -> the shared speaker."""
    try:
        async for event in stream:
            pcm = bytes(event.frame.data)

            # Only sound counts. LiveKit delivers frames continuously
            # while the track is subscribed, silence included — so poking
            # on every frame kept the far end "speaking" forever, which
            # left our mic muted forever and meant it never heard the
            # second thing anyone said.
            if is_speech(pcm):
                timer.poke()
                # Only the agent's own voice should mute our microphone.
                # Muting for the person on the phone would stop them ever
                # hearing us, which is the entire point of the call.
                if who.startswith("agent"):
                    far_end.heard()

            speaker.submit(who, pcm)
    except Exception as e:
        log.info("Playback stream for %s ended: %s", who, e)


# ---- the forever loop ---------------------------------------------------

def wait_for_voice() -> bool:
    """Block until somebody in the room talks. Returns False if the mic died.

    The no-wake-word path. Cheaper on latency than a wake phrase and much
    cheaper on LiveKit minutes than staying connected — but it will also
    fire on the television, which is the trade you are making.
    """
    needed = max(1, VOICE_TRIGGER_MS // DEVICE_FRAME_MS)
    loud = 0
    mic = _spawn_arecord(WAKE_CAPTURE_DEVICE)
    try:
        while True:
            pcm = mic.stdout.read(FRAME_BYTES)
            if not pcm or len(pcm) < FRAME_BYTES:
                log.warning("Mic stream ended while waiting for a voice")
                return False

            if is_speech(pcm, VOICE_TRIGGER_RMS):
                loud += 1
                if loud >= needed:
                    log.info("Heard someone talking")
                    return True
            else:
                # Consecutive, not cumulative: a door closing is one loud
                # frame, speech is a run of them.
                loud = 0
    finally:
        _stop(mic)


def _wait_for_trigger() -> bool:
    """Whatever starts a conversation in this mode. False = give up."""
    if WAKE_MODE == "always":
        return True

    if WAKE_MODE == "button":
        from saathi.button import ButtonError, wait_for_press

        try:
            return wait_for_press()
        except ButtonError as e:
            raise DeviceError(str(e)) from e

    if WAKE_MODE == "voice":
        return wait_for_voice()

    from saathi.wake import build_engine, listen

    engine = _wake_engine()
    # close() explicitly rather than relying on the loop variable going
    # out of scope: the generator's finally is what kills arecord, and
    # leaving that to the garbage collector means the old process can
    # still hold the device when the next one opens it.
    stream = listen(engine)
    try:
        detection = next(stream, None)
    finally:
        stream.close()

    if detection is None:
        return False
    log.info("Woken by %r", detection.word)
    engine.reset()
    return True


_ENGINE = None


def _wake_engine():
    """Built once and reused — loading the models per wake would add
    seconds to every conversation."""
    global _ENGINE
    if _ENGINE is None:
        from saathi.wake import build_engine

        _ENGINE = build_engine()
    return _ENGINE


def run_forever() -> None:
    """Trigger -> conversation -> trigger, for as long as it's on."""
    if WAKE_MODE not in ("wake_word", "voice", "always", "button"):
        raise DeviceError(
            f"WAKE_MODE must be wake_word, voice, button or always — got {WAKE_MODE!r}"
        )

    if WAKE_MODE == "wake_word":
        _wake_engine()  # fail now, loudly, rather than on the first wake
        log.info("Saathi is listening for its wake word")
        print("Saathi is up. Say the wake word.", flush=True)
    elif WAKE_MODE == "button":
        from saathi.button import ButtonError, find_device
        from saathi.config import BUTTON_DEVICE, BUTTON_NAME

        # Fail now, with the device list, rather than on the first press.
        try:
            device = find_device(BUTTON_NAME, BUTTON_DEVICE)
        except ButtonError as e:
            raise DeviceError(str(e)) from e
        log.info("Saathi starts on a button press")
        print(f"Saathi is up. Press the button. ({device.name})", flush=True)

    elif WAKE_MODE == "voice":
        log.info("Saathi starts when it hears someone talking")
        print("Saathi is up. Just talk.", flush=True)
    else:
        log.info("Saathi stays connected")
        print("Saathi is up and connected.", flush=True)

    while True:
        if not _wait_for_trigger():
            log.warning("Trigger stopped; retrying in 2s")
            time.sleep(2)
            continue

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
