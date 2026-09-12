# Saathi

A conversational smart speaker for a home. Talk to it, ask it for music,
ask it to call your family. "Saathi" is Hindi for *companion*.

Three things, all by voice:

- **Wake word** — "hey Saathi", "hey boy", or "hello boy".
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
  wake.py            on-device wake word: "hey saathi" / "hey boy" / "hello boy"
```

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
and a bandwidth one. [openWakeWord](https://github.com/dscripka/openWakeWord)
is small enough to run continuously on a Pi and only wakes the expensive
pipeline once it hears its name.

**You have to train the models.** None of these three are words
openWakeWord ships. Its synthetic-data notebook trains one in about an
hour with no recordings needed; save each as
`wake_models/<word_with_underscores>.onnx`. Until then `saathi.wake`
raises with that instruction rather than quietly falling back to
"hey jarvis".

Test one in the actual room before wiring it up:

```bash
python -m saathi.wake     # prints every detection and its score
```

**"hey boy" and "hello boy" will misfire.** They're short, common English
words, so they'll trigger on ordinary conversation and on television.
They ship at a threshold of 0.75 against "hey saathi"'s 0.5 for exactly
that reason — and if they still interrupt people, raise them rather than
living with it. "Hey Saathi" is three syllables with an uncommon phoneme
run, which is what makes a wake word reliable.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env              # fill in LiveKit + model keys
cp contacts.json.example contacts.json

./venv/bin/python -m saathi.agent dev
```

You also need, separately:

- **Mopidy** on the same Pi (`mopidy`, `mopidy-youtube`), with its HTTP
  frontend on `:6680`.
- **A LiveKit server** — self-hosted is free and Apache-2.0, but it needs
  to be somewhere your family can reach, so a small VPS rather than the
  Pi itself.
- **A SIP server** (Asterisk, Kamailio) with an account per family member
  for them to register Linphone against, plus a LiveKit outbound trunk
  pointing at it.

## Tests

```bash
./venv/bin/pytest -q
```

57 tests, no API keys, no LiveKit, no Mopidy, no Pi — every network edge
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
