# Setting up Saathi

Read this in order and **test at each step**. Setting all of it up and
then debugging is how you end up with a silent box and no idea which of
six services is wrong.

Rough time: an hour for a talking speaker, another hour for calling.

---

## 0. What it costs

| | |
|---|---|
| OpenAI | pay-as-you-go, a few dollars a month for one household |
| LiveKit Cloud | **free tier**: 5,000 WebRTC min/mo, 1,000 agent min/mo, hard cap (no surprise bills) |
| Radio Browser | free, no account |
| Linphone SIP accounts | free |
| Calling family | **free** over SIP |
| Calling a normal phone | per minute — optional, skip it at first |

1,000 agent minutes is about 33 minutes of talking per day. Start on the
free tier; self-host LiveKit only when you outgrow it.

---

## 1. Keys

> Getting these is the only manual part. `setup.sh` (step 2) will ask for
> them, so you can collect them now and paste them in when prompted —
> there's no file to edit.

**You only need one API key to start.** OpenAI does the LLM, speech-to-text
and text-to-speech adequately. Add the specialists later, once you know
what actually bothers you.

### OpenAI — required

1. [platform.openai.com](https://platform.openai.com) → API keys → create
2. Add a few dollars of credit (a new key with no credit fails in a way
   that looks like a bug in your config)
3. `OPENAI_API_KEY=sk-...`

### LiveKit Cloud — required

1. [cloud.livekit.io](https://cloud.livekit.io) → new project
2. Settings → Keys → create
3. Copy all three into `.env`:

```
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=API...
LIVEKIT_API_SECRET=...
```

### Later, when something bothers you

| Symptom | Fix | Key |
|---|---|---|
| Transcription is slow or mishears | `STT_PROVIDER=deepgram` | [deepgram.com](https://deepgram.com) |
| The voice sounds robotic, or Hindi sounds wrong | `TTS_PROVIDER=elevenlabs` | [elevenlabs.io](https://elevenlabs.io) |
| You want it to answer to "Hey Saathi" | `WAKE_ENGINE=porcupine` | [console.picovoice.ai](https://console.picovoice.ai) |

---

## 2. The Pi — one command

```bash
git clone https://github.com/Basudeb2005/saathi2.git && bash saathi2/setup.sh
```

That installs the system packages (`mopidy`, `alsa-utils`, `espeak-ng`),
creates the venv, installs the Python dependencies, checks for a
microphone, and then asks for your keys in the terminal. Nothing to edit
by hand.

It's safe to re-run — every step is skipped if it's already done, so it
doubles as "repair my install". To change keys later without the rest:

```bash
./venv/bin/python -m saathi.setup
```

The prompt keeps whatever is already set (press Enter to keep it),
validates obvious mistakes, skips the optional sections unless you ask
for them, and writes `.env` mode 600 so it isn't world-readable. Keys you
added by hand are preserved.

### Check the microphone

If the script warned about no capture device, stop here — it's the single
most common reason everything else looks broken later:

```bash
arecord -l                    # must list a card
arecord -d 3 test.wav && aplay test.wav
```

The Pi's onboard audio is playback-only. You need a USB mic or a HAT.

## 3. Music

Enable Mopidy's HTTP API in `/etc/mopidy/mopidy.conf`:

```ini
[http]
enabled = true
hostname = 127.0.0.1
port = 6680

[youtube]
enabled = true
```

```bash
sudo systemctl enable --now mopidy
curl -s -X POST http://localhost:6680/mopidy/rpc \
  -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}'
```

A version number back means the music layer is done.

---

## 4. First conversation

**Do this before touching calling.** It proves the keys, the mic, the
speaker and the agent all work, and it's much easier to debug alone.

```bash
./venv/bin/python -m saathi.agent dev
```

Then open your LiveKit project's **Playground** in a browser and join the
room. Talk to it. Ask it to play some music.

Once that works, you have a working smart speaker. Everything below is
the calling feature.

---

## 5. Calling

The chain, end to end:

```
   you ──► Saathi ──► LiveKit room ──► SIP trunk ──► SIP server ──► Linphone
                                                                    (their phone)
```

Saathi doesn't "call" anyone in the telephone sense — it pulls them into
the room it's already in. That's why there's no audio bridge to build.

### 5a. Everyone gets a free SIP address

Each family member, **and Saathi itself**:

1. Register at [subscribe.linphone.org](https://subscribe.linphone.org/register/email)
2. You get `username@sip.linphone.org`
3. They install [Linphone](https://linphone.org) and sign in

Make one extra account for the speaker — say `saathi-home` — because the
trunk needs its own identity to authenticate as.

> **iOS: test this before going further.** Lock the iPhone, leave it an
> hour, then call it. If it doesn't ring, Linphone has been suspended in
> the background and you need VoIP push configured. Find this out now,
> not after you've built everything on top of it.

### 5b. Point LiveKit at it

Install the CLI, then create an outbound trunk:

```bash
curl -sSL https://get.livekit.io/cli | bash
lk cloud auth
```

`trunk.json`:

```json
{
  "trunk": {
    "name": "linphone",
    "address": "sip.linphone.org",
    "numbers": ["saathi-home"],
    "auth_username": "saathi-home",
    "auth_password": "the password you set"
  }
}
```

```bash
lk sip outbound create trunk.json     # prints a trunk id: ST_...
```

Put it in `.env`:

```
LIVEKIT_SIP_TRUNK_ID=ST_xxxxxxxx
```

> **This is the step most likely to fail.** LiveKit authenticates per
> INVITE; some SIP servers, possibly including Linphone's free one, want
> a full REGISTER first and will reject the call. If you get 401/403 in
> the SIP logs, that's this — see 5d.
>
> Field names have also moved between LiveKit versions. Check
> `lk sip outbound create --help` against the JSON above before assuming
> your credentials are wrong.

### 5c. Contacts

`contacts.json` — names Saathi will accept, and nothing else:

```json
{
  "priya": {
    "transport": "sip",
    "address": "sip:priya@sip.linphone.org",
    "label": "your daughter"
  }
}
```

Restart the agent and say *"call Priya"*. Their phone should ring.

### 5d. If Linphone's free server won't accept the trunk

Run your own. A $5 VPS with Asterisk, family registers Linphone against
it instead of `sip.linphone.org`, and the trunk address becomes your
server. More work, but it definitely works and nothing else changes —
only the `address` in `trunk.json` and the domain in `contacts.json`.

### 5e. Calling real phone numbers (optional, costs money)

For the relatives who won't install anything. Buy a trunk from Twilio or
Telnyx, add it as a second LiveKit outbound trunk, and set:

```
LIVEKIT_PSTN_TRUNK_ID=ST_yyyyyyyy
```

Then contacts with `"transport": "pstn"` and an E.164 number route over
it automatically. **Check the per-minute rate for your country first** —
India is meaningfully pricier than the US.

---

## 6. Wake word

```bash
./venv/bin/python -m saathi.wake
```

First run downloads the pretrained models. Say **"hey jarvis"** — you
should see a line with a score.

Test it in the real room, at the real distance, **with the television
on**. A wake word that works six inches from the mic in a quiet kitchen
tells you nothing.

To make it answer to "Hey Saathi": generate the phrase at
[console.picovoice.ai](https://console.picovoice.ai) (seconds, no
training), save the `.ppn` in `wake_models/`, then:

```
WAKE_ENGINE=porcupine
PORCUPINE_ACCESS_KEY=...
PORCUPINE_KEYWORD_PATHS=wake_models/hey_saathi.ppn
```

Note that `wake.py` currently *prints* detections — wiring one to "start
a session" is still to do.

---

## When something doesn't work

| Symptom | Look at |
|---|---|
| Nothing happens at all | `arecord -l` — no mic is the usual answer |
| Music never plays | `curl` Mopidy's RPC (step 3); check `journalctl -u mopidy` |
| Songs fail, radio works | Mopidy-YouTube broke again — expected, radio is the stable floor |
| Agent starts then exits | usually an OpenAI key with no credit on it |
| Call never rings | LiveKit SIP logs; 401/403 means the trunk auth problem in 5b |
| It hears itself / never wakes while music plays | no echo cancellation — you need a ReSpeaker HAT |

`logs/saathi.log` has every Mopidy, Radio Browser and SIP call with its
arguments and result.
