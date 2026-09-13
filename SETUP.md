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
curl -s -X POST http://127.0.0.1:6680/mopidy/rpc \
  -d '{"jsonrpc":"2.0","id":1,"method":"core.get_version"}'
```

A version number back means the music layer is done.

---

## 4. First conversation

**Do this before touching calling.** It proves the keys, the mic, the
speaker and the agent all work, and it's much easier to debug alone.

First, check every piece independently:

```bash
./venv/bin/python -m saathi.doctor
```

It tests config, ALSA, whether the mic **actually hears sound** (not just
whether it enumerates — a silent mic that looks configured is the most
demoralising failure here), your OpenAI key, LiveKit credentials, Mopidy,
Radio Browser and the wake engine. Each failure names its own fix, and
one failing never stops the rest, so you see everything wrong at once.

When it's green:

```bash
./venv/bin/python -m saathi.device
```

Say the wake word — **"hey jarvis"** by default — then talk. Ask it to
play some music. It hangs up after about 12 seconds of quiet and goes
back to listening.

`saathi.device` needs `saathi.agent` running too. Either open a second
terminal for `./venv/bin/python -m saathi.agent dev`, or install the
services (below) which handle both.

### Running on boot

`setup.sh` offers this at the end; to do it later:

```bash
bash systemd/install.sh
```

Two services, because they fail differently:

```bash
systemctl status saathi@$USER          # the box
systemctl status saathi-agent@$USER    # the brain
journalctl -fu saathi@$USER            # watch it live
```

Once that works, you have a working smart speaker. Everything below is
the calling feature.

---

## 5. Calling

One command walks the whole thing:

```bash
./venv/bin/python -m saathi.calling.cli guide
```

It asks as it goes, writes the files it can write, and prints the exact
commands for the ones it can't. Stop any time — re-running picks up where
you left off, and `check` tells you what's still missing.

The chain it builds:

```
   you ──► Saathi ──► LiveKit room ──► SIP trunk ──► SIP server ──► Linphone
                                                                    (their phone)
```

Saathi doesn't dial anyone in the telephone sense — it pulls them into
the room it's already in. That's why there's no audio bridge here.

### The five steps, if you'd rather do them by hand

**1. Everyone gets a free SIP address.** Each family member, **and Saathi
itself**, registers at
[subscribe.linphone.org](https://subscribe.linphone.org/register/email)
and gets `username@sip.linphone.org`. They install
[Linphone](https://linphone.org) and sign in. Make one extra account for
the speaker — the trunk needs its own identity to authenticate as.

> **iOS: test this before going further.** Lock the iPhone, leave it an
> hour, then call it. If it doesn't ring, Linphone was suspended in the
> background and you need VoIP push. Find this out now, not after you've
> built everything on top of it.

**2. Install LiveKit's CLI.**

```bash
curl -sSL https://get.livekit.io/cli | bash
lk cloud auth
```

**3. Create the trunk.**

```bash
./venv/bin/python -m saathi.calling.cli trunk    # writes trunk.json (mode 600)
lk sip outbound create trunk.json                 # prints ST_...
./venv/bin/python -m saathi.setup --only calling  # save the id
```

> **This is the step most likely to fail.** LiveKit authenticates per
> INVITE; some SIP servers want a full REGISTER first and reject with
> 401/403. If that happens, see 5d.
>
> Field names have also moved between LiveKit versions — check
> `lk sip outbound create --help` against `trunk.json` before assuming
> your password is wrong.

**4. Add contacts.** Only these names can ever be dialled.

```bash
./venv/bin/python -m saathi.calling.cli contacts add priya sip:priya@sip.linphone.org --label "your daughter"
./venv/bin/python -m saathi.calling.cli contacts add doctor +15551234567 --label "Dr. Rao"
./venv/bin/python -m saathi.calling.cli contacts list
```

A `+` address is inferred as PSTN (paid), a `sip:` one as free.

**5. Ring a real phone.**

```bash
./venv/bin/python -m saathi.calling.cli check      # everything in place?
./venv/bin/python -m saathi.calling.cli test priya # actually call
```

Then say **"call Priya"** to the speaker.

### 5d. If Linphone's free server won't accept the trunk

Run your own. A $5 VPS with Asterisk, family registers Linphone against
it instead of `sip.linphone.org`, and the trunk address becomes your
server. More work, but it definitely works — and nothing else changes,
only the `address` in `trunk.json` and the domain in your contacts.

### 5e. Calling real phone numbers (optional, costs money)

For relatives who won't install anything. Buy a trunk from Twilio or
Telnyx, add it as a second LiveKit outbound trunk, and save it:

```bash
./venv/bin/python -m saathi.setup --only calling   # LIVEKIT_PSTN_TRUNK_ID
```

Contacts with a `+` number then route over it automatically. **Check the
per-minute rate for your country first** — India is meaningfully pricier
than the US.

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

## Re-running anything

Nothing here is one-shot. If a step failed or you skipped it:

| Command | Does |
|---|---|
| `bash setup.sh` | the whole install again; skips what's done |
| `python -m saathi.setup` | all keys, keeping what's set |
| `python -m saathi.setup --list` | show the sections |
| `python -m saathi.setup --only calling` | just the trunk ids |
| `python -m saathi.setup --only openai` | just that key |
| `python -m saathi.doctor` | check all six parts, with fixes |
| `python -m saathi.calling.cli check` | what calling still needs |
| `python -m saathi.calling.cli guide` | walk calling again from the top |

## When something doesn't work

**Run `python -m saathi.doctor` first** — it checks all six moving parts
and names the fix for each. The table below is for what it can't catch.

| Symptom | Look at |
|---|---|
| It wakes but never replies | is `saathi-agent` running? `systemctl status saathi-agent@$USER` |
| It hangs up mid-sentence | raise `SESSION_IDLE_TIMEOUT_S`, or mic gain is too low to register speech |
| Nothing happens at all | `arecord -l` — no mic is the usual answer |
| Music never plays | `curl` Mopidy's RPC (step 3); check `journalctl -u mopidy` |
| Songs fail, radio works | Mopidy-YouTube broke again — expected, radio is the stable floor |
| Agent starts then exits | usually an OpenAI key with no credit on it |
| Call never rings | LiveKit SIP logs; 401/403 means the trunk auth problem in 5b |
| It hears itself / never wakes while music plays | no echo cancellation — you need a ReSpeaker HAT |

`logs/saathi.log` has every Mopidy, Radio Browser and SIP call with its
arguments and result.
