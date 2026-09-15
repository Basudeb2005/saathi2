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
    cli.py           guided calling setup: guide / trunk / contacts / test
  memory/
    base.py          what a memory backend must do — swap the vendor freely
    supermemory.py   the first implementation
  console/
    system.py        nmcli / systemctl / ip, and the parsing of what they say
    commands.py      the verbs, once, shared by both ways in
    bluetooth.py     RFCOMM, so it answers with no network at all
    web.py           the HTTP API and the page the phone installs
    netwatch.py      no network for 90s -> become one
  wake.py            on-device wake word (openWakeWord or Porcupine)
  button.py          a worn or table button, over evdev — the real answer
  keyboard.py        the spacebar as that button, over ssh, for testing
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

### Memory is what separates a companion from a gadget

Without it every conversation starts from nothing, which is fine for a
speaker and useless for a companion — "how did your grandson's exam go?"
is the whole difference.

`memory/base.py` defines three methods and nothing else imports a vendor
SDK, so the backend stays a decision you can revisit: Supermemory today,
Postgres or a local SQLite file later. That last option matters for a
device that sits in someone's home and hears private things.

Two rules it holds to:

- **Nothing is stored unless the agent asked for it.** Raw audio and raw
  transcripts never leave the Pi. The model calls `remember()` for facts
  worth keeping, and is told not to keep passing chatter.
- **Failing to remember never fails a conversation.** Every call swallows
  its errors and degrades to "no memory this turn". A box that goes
  silent because a memory API returned 503 is a far worse failure than
  one that forgets.

Recalled facts are shown to the model hedged — "may be out of date" —
because a stale fact asserted confidently at someone who believes you is
worse than not knowing.

### Languages

`AGENT_LANGUAGES` is not cosmetic. On a far-field mic, an unconstrained
model given a noisy signal doesn't return nothing — it returns confident
nonsense in a language it picked at random, and the reply comes back in
Urdu. Naming the languages removes the failure mode.

More than one language needs **Deepgram** (`STT_PROVIDER=deepgram`) —
nova-3's `multi` mode handles code-switching mid-sentence. OpenAI has no
equivalent, so with several configured it gets the first as a hint.

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

Calling is set up separately, whenever you're ready:

```bash
./venv/bin/python -m saathi.calling.cli guide
```

Also needed: **Mopidy** on the same Pi for music.

### Re-running setup

Nothing is one-shot. Every command below is safe to run again:

| | |
|---|---|
| `bash setup.sh` | the whole install; skips what's done |
| `python -m saathi.setup` | all keys, keeping current values |
| `python -m saathi.setup --list` | what sections exist |
| `python -m saathi.setup --only calling` | just one section |
| `python -m saathi.doctor` | what's broken, and the fix for each |
| `python -m saathi.calling.cli check` | what calling still needs |

## Tests

```bash
./venv/bin/pytest -q
```

141 tests, no API keys, no LiveKit, no Mopidy, no Pi — every network edge
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
- **Not built yet**: hardware echo cancellation, which is what actually
  fixes barge-in. Push-to-talk (`WAKE_MODE=button`, or `WAKE_MODE=space`
  to try it without hardware) sidesteps it rather than solving it.

### Reachable when it is broken

A headless Pi has a circular problem. To ssh in you need its address; to
learn its address you need to reach it; to reach it you need the network
that is often the thing that has gone wrong. Every answer to this that
works over the network has the same hole in it — mDNS needs both ends on
one LAN, ssh needs the address, a web console needs wifi to already work.

So the console answers over **Bluetooth**, which is a second radio that
doesn't care about any of it. Pair once, type `status`, get the address.
That path has no dependency on the network being right, or on the Pi
being on the same one as the phone, or on DNS.

Three things follow from that, and each was a decision:

- **The verbs live in one place.** `commands.py` returns sentences;
  Bluetooth prints them and HTTP wraps them in JSON. Write them twice and
  the Bluetooth path becomes the one nobody tested — which is the path
  you need on the bad day.
- **Apps get `hello`, people get `status`.** One is JSON and is a
  contract; the other is prose and can be improved. Without the split,
  rewording a status line silently breaks every installed copy of the
  app.
- **No network for ninety seconds and it becomes one.** With a ten-minute
  retry, because the failure mode of never coming back is a Pi that
  hotspotted once during a power cut and stayed there until someone
  noticed — which in an old age home is never.

The gate is a token generated on the Pi and handed out over Bluetooth:
pairing means being in the room, so physical presence is what gets you
the key. There is no default token and no way to disable it.

### Not sounding like a machine

The first version of the prompt was fifteen rules and no person, and it
produced exactly what you would expect from that: *"I have started
playback of your requested station."* Correct, useless. The things that
actually moved it:

- **A character rather than constraints.** `INSTRUCTIONS` in `agent.py`
  now describes someone — how they open, what they do when the person
  sounds low, what they never say — instead of listing prohibitions. A
  model told "be natural" isn't; a model told "never open with *Sure!*,
  never close with *anything else I can help with?*" is, because those
  are the actual tells.
- **Delivery, not just words.** `gpt-4o-mini-tts` takes a plain-English
  direction and acts on it, and `OPENAI_TTS_INSTRUCTIONS` is the single
  biggest lever on the robot impression — more than the model, more than
  the prompt. The default asks for pauses at full stops, falling pitch at
  the end of sentences, and clear final consonants, which is what an
  older listener in a room with a fan needs.
- **Temperature.** At 0.2 the same question gets the same sentence every
  time. Someone who talks to this box every day hears that repetition
  long before they hear anything else, so the default is 0.7.
- **The time of day.** The prompt carries the local time in words, so the
  greeting fits the hour. Without it the box says good morning at nine at
  night — which is why `doctor` now checks the Pi's timezone, since a
  fresh image is UTC.
- **`gpt-4.1` over `gpt-4o`.** Not for knowledge — for holding the
  character. 4o drifts back into assistant register after a few turns.

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
