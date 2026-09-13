# Saathi

A conversational smart speaker for a home. Talk to it, ask it for music,
ask it to call your family. "Saathi" is Hindi for *companion*.

Three things, all by voice:

- **Wake word** — on-device, with nothing to train.
- **Conversation** — streaming, interruptible. You can talk over it.
- **Music** — free internet radio by default, specific songs on demand.
- **Calling** — it rings the family member's actual phone. Free when they
  have Linphone; paid per minute only for the people who won't install
  anything.

## Why it looks like this

The predecessor to this project recorded a whole clip, uploaded it to
Whisper, waited for a chat completion, waited for an MP3, then played it.
Three blocking network calls per turn, no wake word, no barge-in. That
architecture can answer a question but it can't hold a conversation, and
no amount of tuning fixes it.

So the conversation layer is [LiveKit Agents](https://github.com/livekit/agents):
streaming STT, turn detection, barge-in and playback are its problem now,
not ours. What's left in `saathi/agent.py` is the part that's actually
specific to this project — which tools exist, and how it should speak.

Choosing LiveKit also collapsed the calling layer to almost nothing.
**A call is just another participant in the room.** The person at home,
the agent, and whoever was dialled are all in one place; the media server
does mixing, reconnection and echo cancellation. There's no bridge, no
mu-law conversion, no webhook round-trip.

```
                    ┌─────────────────────────────┐
   you ────mic────► │                             │
                    │   LiveKit room              │ ◄──SIP──► Linphone (free)
   you ◄──speaker── │   (agent + humans)          │ ◄──SIP──► PSTN (paid)
                    └──────────────┬──────────────┘
                                   │ tools
                    ┌──────────────┴──────────────┐
                    │  Mopidy ──► speaker         │
                    │  Radio Browser (discovery)  │
                    └─────────────────────────────┘
```

## Layout

```
saathi/
  config.py          one place for every environment variable
  contacts.py        name -> transport + address; the only dialable list
  agent.py           LiveKit session, prompt, and the tools
  music/
    mopidy.py        JSON-RPC client (playback, search, volume)
    radio.py         Radio Browser search — free, no account, no key
    player.py        "play me something" -> something audible
  calling/
    sip.py           add a SIP participant to the room
  wake.py            on-device wake word (openWakeWord or Porcupine)
  device.py          the Pi in its own room: mic in, speaker out
  doctor.py          check all six moving parts before blaming the code
  setup.py           the key prompt
```

### Two processes, on purpose

`saathi.agent` is a LiveKit worker — the brain, waiting to be dispatched
into a room. `saathi.device` is the box — wake word, microphone, speaker.
They're separate services because they fail differently: the agent dying
is a cloud problem, the device dying is an audio one, and restarting one
shouldn't disturb the other.

The device **connects only while you're talking to it**. LiveKit's free
tier is 1,000 agent minutes a month; a box holding the line open all day
spends that in a fortnight, while one that joins per conversation spends
a few minutes a day. That's what the idle timeout in `device.py` is for.

Only one process can hold the microphone, so the wake listener is torn
down for the duration of a session and rebuilt afterwards — serial by
construction rather than by luck.

### Contacts decide what a call costs

`contacts.json` gives every person a `transport`:

| transport | reaches | cost |
|---|---|---|
| `sip` | Linphone on their phone, via your SIP server | **free** |
| `pstn` | any phone number, via a paid trunk | per minute |

Both become a SIP participant in the same room, so nothing else in the
codebase branches on it — only `calling/sip.py`, to pick a trunk.

The list is fixed and configured by hand on purpose. An elderly user
misspeaking a number, or the model mishearing one, must never be able to
dial an arbitrary destination. The agent can only pass a *name*.

### Music: radio first

Radio Browser is the default source because it's the only one with no
key, no account, no quota and no terms-of-service question — ~45,000
stations that just keep working. Mopidy-YouTube handles specific named
songs and is the thing that will break first when YouTube changes
something. `source="auto"` tries the song, then falls back to a station,
because for someone elderly silence reads as "it's broken".

### Wake word is the only thing running locally

Everything else is a cloud API — a local LLM on a Pi measures 5–8 seconds
per turn, which is fine for "what's the weather" and useless for
conversation. But wake detection is always listening, so shipping every
second of household audio to a cloud service is both a privacy problem
and a bandwidth one.

**Nothing here needs training.** Two engines, because "no training" and
"it should answer to Saathi" pull in opposite directions:

| | [openWakeWord](https://github.com/dscripka/openWakeWord) (default) | [Porcupine](https://github.com/Picovoice/porcupine) |
|---|---|---|
| Setup | `pip install`, done | free account + key |
| Words | `alexa`, `hey_mycroft`, `hey_jarvis`, `hey_rhasspy` | built-ins, **or your own phrase** |
| "Hey Saathi"? | no | yes — type it in the console, get a `.ppn` in seconds |
| Offline | fully | after init |

Start on openWakeWord with **`hey_jarvis`**. It's the most distinctive of
the four — three syllables and an uncommon phoneme run — and unlike
`alexa` it won't fire every time the television says it. Models download
themselves on first run.

When the name starts to matter, generate a "Hey Saathi" at
[console.picovoice.ai](https://console.picovoice.ai), drop the `.ppn` in
`wake_models/`, and switch `WAKE_ENGINE=porcupine`. Still no training —
you type the phrase and it hands you the model.

Either way, test it in the real room, at the real distance, with the
television on, before wiring it up:

```bash
python -m saathi.wake     # prints every detection and its score
```

A note on picking a phrase, if you generate your own: short common words
("hey boy", "hello boy") fire constantly on ordinary conversation and on
television. Length and uncommon sounds are what make a wake word
reliable, which is why every shipped one is three syllables.

## Setup

**[SETUP.md](SETUP.md)** walks through it end to end — keys, the Pi,
music, the first conversation, then calling.

You need **one API key** to start (OpenAI does LLM, STT and TTS) plus a
free LiveKit Cloud project. Deepgram, ElevenLabs and Picovoice are
upgrades you add later, if and when something bothers you.

One command on a fresh Pi — installs everything, then asks for your keys
in the terminal so you never open a dotenv by hand:

```bash
git clone https://github.com/Basudeb2005/saathi2.git && bash saathi2/setup.sh
```

Re-run it any time to add a key or repair the install. To change keys
later without the rest:

```bash
./venv/bin/python -m saathi.setup
```

Then:

```bash
./venv/bin/python -m saathi.doctor     # check all six moving parts
./venv/bin/python -m saathi.device     # then talk to it
```

Also needed, separately: **Mopidy** on the same Pi for music, and — for
calling — a free [Linphone](https://linphone.org) SIP account per family
member plus a LiveKit outbound trunk. SETUP.md §5 covers the whole chain,
including the step most likely to fail.

## Tests

```bash
./venv/bin/pytest -q
```

100 tests, no API keys, no LiveKit, no Mopidy, no Pi — every network edge
is faked.

## What is and isn't proven

Being straight about this, because the gap matters:

- **Tested and passing**: contacts and transport validation, the Mopidy
  JSON-RPC client, Radio Browser search and its fallbacks, the
  song→station fallback, volume ducking and restore, trunk selection.
- **Written but not yet run against a live service**: `agent.py` and the
  `create_sip_participant` call in `calling/sip.py`. They're written
  against LiveKit Agents 1.x — check the field names against the version
  pip actually resolves before assuming a typo is a bug in your config.
- **Not built yet**: wake word. The agent currently responds to anyone in
  the room. openWakeWord is the intended piece here.

### Three things that will bite

1. **Echo cancellation.** This is worse with a wake word than without:
   the mic is listening *while music plays*, so without AEC the box hears
   its own speaker and either never wakes or wakes constantly. Music and the mic share one speaker. Ducking
   (`MusicPlayer.ducked()`) is a floor, not a solution — get a ReSpeaker
   HAT or a USB mic with hardware AEC.
2. **iOS will suspend Linphone** unless VoIP push is configured. Test an
   incoming call on a real iPhone that's been locked for an hour before
   building anything on top of this.
3. **Mopidy-YouTube is a moving target.** Radio is the stable floor;
   treat on-demand song lookup as best-effort.
