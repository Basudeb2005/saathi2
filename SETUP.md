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

## 7. Push-to-talk on the spacebar

The wake word is the weakest part of the box: it has to be heard over a
speaker playing music a foot away, with no echo cancellation. A button
doesn't. Before committing to hardware, try the same interaction with
the keyboard you already have.

```bash
./venv/bin/python -m saathi.keyboard
```

Hold **space**. The bar should stay solid the whole time you hold it and
empty about a second after you let go. If it flickers, or only flashes
once no matter how long you hold, your key repeat is off — set
`PTT_STYLE=toggle` and tap to open, tap to close.

Then run the box itself in this mode:

```bash
WAKE_MODE=space ./venv/bin/python -m saathi.device
```

Hold space, talk, let go. Enter sends immediately without waiting out
the release window; `q` or escape hangs up; ctrl-c quits.

Two things to know:

- **It needs a terminal.** Under systemd stdin is `/dev/null` and there
  is nothing to read, so this mode is for running in the foreground. Stop
  the service first: `sudo systemctl stop saathi`.
- **It works over ssh**, which `WAKE_MODE=button` does not — evdev reads
  the Pi's own input devices, and your keyboard is attached to your
  laptop.

Turn **`HALF_DUPLEX=false`** while you're in this mode, in `.env`, and
restart the agent too. Half-duplex exists to stop the mic hearing the
speaker; push-to-talk already does that, better, by keeping the mic shut
unless someone is holding the key. With both on you can't interrupt a
reply even by holding space — which is the one thing push-to-talk was
supposed to buy you.

A terminal sends no key-up event, so "held" is inferred from key repeat:
a key counts as down for `PTT_RELEASE_S` (1 second) after the last
character arrived. That has to be wider than your system's repeat delay
— about half a second on macOS — or the mic closes in the gap before the
repeats start. The visible cost is a one-second tail after you let go,
which clips nothing.

---

## 8. Never carrying a monitor again

```bash
bash scripts/install-console.sh
```

One script. Afterwards the Pi is reachable three ways, and at least one
of them works in every situation that has caught you out so far.

### Over Bluetooth — works with no network at all

This is the one that solves `raspberrypi.local: Unknown host`. Bluetooth
is a second radio: it does not care what network the Pi is on, or whether
it is on one.

1. Phone → Bluetooth settings → pair with the Pi (it shows up under its
   hostname; pairing needs no PIN)
2. Install **Serial Bluetooth Terminal** (Kai Morich, free) — or the app
   in `android/`
3. Connect, and type:

| Type | Get |
|---|---|
| `status` | what's running, what network, what address |
| `ip` | just the addresses |
| `scan` | wifi networks in range |
| `wifi "My Network" mypassword` | join one, and remember it for next boot |
| `start` / `stop` / `restart` | Saathi itself |
| `logs 40` | the last 40 lines |
| `hotspot on` | become an access point |
| `token` | the key the web console wants |
| `sh CMD` | run a command |

### Over wifi — the web console

`http://<its address>:8765`, from any phone or laptop on the same
network. Same commands, with buttons: a big **Start Saathi**, a tappable
list of wifi networks, and a terminal.

It asks for the token once and remembers it. Get the token by typing
`token` over Bluetooth, or `sudo cat /etc/saathi/console-token`.

In Chrome, **⋮ → Add to Home screen** puts it on the home screen with an
icon and no browser chrome. That is the app.

### When it can't find a network — it becomes one

Boot it somewhere new and after 90 seconds it starts its own access
point. Join **Saathi-Setup** (password `saathi123` — change it), open
`http://10.42.0.1:8765`, and put it on the real network from there.

Ten minutes later it drops the hotspot and tries the saved networks
again, so a router that was merely slow to boot doesn't strand it.

### The security of all this

The console runs as root — joining a network and starting a service both
need to — and listens on the whole LAN, because a phone has to reach it.
So:

- Everything over HTTP needs a token that is **generated on the Pi**, 32
  random characters, never chosen by you and with no default.
- The token is handed out over **Bluetooth**, which you can only pair
  with from the same room.
- The web console will not read its own token out to anyone who asks,
  even with a valid token.
- `sh` is a root shell over HTTP. It is on because a console you cannot
  fix anything from is a status page, but it is one env var away from
  off: `CONSOLE_SHELL=false`, then restart the console.

Change `HOTSPOT_PASSWORD` in `.env`. The default is in a public repo.

---

## 9. A keyboard instead of a wake word, with no monitor

A USB keyboard plugged into the Pi is a push-to-talk button. Unlike the
spacebar-over-ssh mode in section 7, this needs no terminal at all, so it
runs under systemd at boot on a Pi with nothing attached but a keyboard.

In `.env`:

```
WAKE_MODE=button
BUTTON_PUSH_TO_TALK=true
BUTTON_KEYS=KEY_SPACE
HALF_DUPLEX=false
```

Then `sudo systemctl restart saathi@$USER` — or tap **Restart** in the
app.

`BUTTON_KEYS` matters here. The default is "any key", which is right for
a one-button remote and wrong for something with a hundred of them.
Escape or `q` hangs up.

To find out what your keyboard actually sends:

```bash
./venv/bin/python -m saathi.button
```

The same setting works for the wearable — a BLE remote or the ESP32 in
`firmware/` is just another evdev keyboard, which is the point.

---

## The one word

After any of the installers, `saathi` is on your PATH:

| Type | Get |
|---|---|
| `saathi` | starts it, then says what's running and where |
| `saathi talk` | talk to it now — hold SPACE, `q` to hang up |
| `saathi status` | what's running, what address |
| `saathi stop` / `restart` | the services |
| `saathi logs` | follow it (`logs agent`, `logs console`) |
| `saathi doctor` | check every moving part |
| `saathi update` | git pull, then restart |
| `saathi setup` | the API key wizard |
| `saathi console` | install the bluetooth + web console |
| `saathi token` | the key the web console wants |
| `saathi ip` | just the address |
| `saathi mic` | who is holding the microphone (`saathi mic free` takes it back) |
| `saathi keys` | what a plugged-in keyboard actually sends |
| `saathi calling` | guide / trunk / check / test |

If it isn't there yet:

```bash
cd ~/saathi2 && sudo bash scripts/saathi install
```

`saathi talk` is the one to reach for when testing. It stops the
background service first — it wants the same microphone, and ALSA will
not share — starts the agent if it isn't up, and turns half-duplex off so
you can interrupt a reply.

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
| `python -m saathi.doctor` | check every part, with fixes |
| `python -m saathi.keyboard` | check push-to-talk before wiring it to audio |
| `bash scripts/install-console.sh` | the bluetooth/web console again; safe to repeat |
| `sudo python -m saathi.console --run status` | ask the console without a phone |
| `sudo cat /etc/saathi/console-token` | the web console's key |
| `python -m saathi.calling.cli check` | what calling still needs |
| `python -m saathi.calling.cli guide` | walk calling again from the top |

### "Device or resource busy"

Only one process gets the capture device, and there are three that
legitimately want it: the background service, a conversation you started
by hand, and whatever you ran to test the microphone.

```bash
saathi mic          # who has it
saathi mic free     # take it back
```

`saathi talk` and `saathi doctor` both do this for you now. If `saathi
mic` shows something that isn't Saathi's — a browser, a conferencing app
— it will say so and leave it alone.

---

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
