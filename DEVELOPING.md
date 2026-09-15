# Working on this without the Pi

The interesting half of this project — the prompt, the turn shape, the
tools, the latency — has nothing to do with a Raspberry Pi. It was pinned
to one only because `arecord` and `aplay` had been typed directly into
four files. They aren't any more, so the conversation runs on your
laptop, with a normal edit-run loop and no network in the way.

You do not need a Linux VM or WSL on a Mac. macOS is already Unix and the
code is Python.

## Setup, once

```bash
git clone https://github.com/Basudeb2005/saathi2 && cd saathi2
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
brew install sox                 # macOS: the equivalent of alsa-utils
cp .env.example .env             # then put your keys in
./venv/bin/python -m saathi.doctor
```

`doctor` should say `rec and play (sox)` for Audio tools. Some checks
will fail — Mopidy, calling, the console. That is correct; they are about
running on a device in someone's front room.

## The loop

Two terminals:

```bash
./venv/bin/python -m saathi.agent dev            # the brain
WAKE_MODE=space HALF_DUPLEX=false ./venv/bin/python -m saathi.device
```

Hold **space**, talk, let go. `q` hangs up. Every turn prints where its
seconds went:

```
turn  eou 0.51  stt 1.92  llm 0.88  tts 0.71  = 4.02s before it speaks
```

Your laptop has a better microphone, a quieter room and a faster CPU than
the Pi, so treat those numbers as a floor rather than a prediction. What
they are good for is **relative**: change the STT provider, run it again,
see the number move. That comparison is the same on both machines, and it
is the only thing you actually need from a latency measurement.

macOS will ask for microphone permission the first time. If nothing is
recorded and there was no prompt: System Settings → Privacy & Security →
Microphone, and enable your terminal.

## What does not run off the Pi, and shouldn't

| | Why |
|---|---|
| `saathi.button` | evdev reads Linux input devices |
| `saathi.console` | RFCOMM sockets, nmcli, systemd |
| `saathi.mic` | reads `/proc` to find who holds the capture device |
| Mopidy / music | runs as a service on the Pi and owns its speaker |
| Calling | works anywhere, but you want it on the box that rings |

All of that is about being a device in someone's front room. There is
nothing to develop about it on a laptop, and the tests cover the parts
with logic in them — `pytest tests -q` passes on both machines.

## Then the Pi

```bash
saathi update
```

It is the same checkout and the same `.env` shape. The Pi-only pieces
were never running on your laptop, so nothing about them changed while
you weren't looking.

**Test on the Pi before believing anything about audio.** Three of the
worst bugs in this project's history — the echo loop, the double `aplay`
underrun, the wake word drowned out by the speaker a foot away — exist
only in a room with one microphone and one loudspeaker and no echo
cancellation. A laptop has headphones and a good AGC, and it will lie to
you about all three.
