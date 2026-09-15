"""
Central configuration for Saathi v2.

Same rule as v1: import this instead of reading os.environ directly, so
tracking down a config problem means checking one file rather than
grepping every module.

Nothing here raises on a missing value. A missing key should fail at the
point of use with a message naming what to set, not at import time --
otherwise `pytest` and `--help` break on a machine that has no .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

CONTACTS_PATH = Path(os.getenv("CONTACTS_PATH", ROOT_DIR / "contacts.json"))
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---- LiveKit -----------------------------------------------------------
# The room is the meeting point: the agent, the person in the room, and
# any SIP participant (Linphone or PSTN) all join the same one.
LIVEKIT_URL = os.getenv("LIVEKIT_URL")            # wss://... (self-hosted or Cloud)
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
SAATHI_ROOM_NAME = os.getenv("SAATHI_ROOM_NAME", "saathi-home")

# Two SIP trunks, because there are two kinds of contact (see contacts.py):
#   - SIP trunk  -> Linphone on a family member's phone. Free.
#   - PSTN trunk -> a real phone number via Twilio/Telnyx. Costs per minute.
# Set whichever you actually use; calling a contact whose trunk is unset
# fails with a message saying which one to configure.
LIVEKIT_SIP_TRUNK_ID = os.getenv("LIVEKIT_SIP_TRUNK_ID")
LIVEKIT_PSTN_TRUNK_ID = os.getenv("LIVEKIT_PSTN_TRUNK_ID")

# ---- Models ------------------------------------------------------------
# All three run in the cloud. A local LLM on a Pi measures 5-8s per turn,
# which is fine for "what's the weather" and useless for conversation.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Providers are swappable so you can start with one key and upgrade the
# pieces that actually bother you. OpenAI does all three adequately;
# Deepgram is noticeably faster at STT and ElevenLabs noticeably better
# at multilingual TTS, which is the reason to add them — later.
STT_PROVIDER = os.getenv("STT_PROVIDER", "openai").lower()    # openai | deepgram
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai").lower()    # openai | elevenlabs

# gpt-4.1 rather than gpt-4o. On a companion the difference isn't
# knowledge, it's instruction-following: 4o drifts back to assistant
# register — "I have started playback of your requested station" — a
# few turns after being told to talk like a person, and 4.1 holds the
# character. It is also what the LiveKit plugin itself now defaults to.
# If your account doesn't have it, LLM_MODEL=gpt-4o still works.
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1")
# Warm rather than precise. At 0.2 the same question gets the same
# sentence every time, which is the single clearest tell that there is a
# machine on the other end — someone who talks to this box daily hears
# the repetition long before they hear anything else.
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
# gpt-4o-transcribe over whisper-1: markedly better on accented English
# and on Indian languages, which is most of what this box will hear.
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe")
DEEPGRAM_STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "nova-3")
# gpt-4o-mini-tts is noticeably quicker to first audio than tts-1, which
# is what the "slow audio generation" warnings are about. ElevenLabs is
# quicker still and much better in Hindi and Tamil — worth the extra key
# once English is working.
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
# coral rather than shimmer. shimmer is bright and young and reads every
# sentence like an announcement; coral sits lower and slower and sounds
# like someone in the room. Others worth trying: sage (calm), ballad
# (gentle), alloy (neutral), onyx (low, male).
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "coral")

# How the voice should be delivered — gpt-4o-mini-tts takes a plain
# English direction and acts on it, and this is the biggest single lever
# on "it sounds like a robot". Older tts-1 models ignore it, so it is
# only sent when the model supports it.
#
# Written for the listener, not the speaker: an eighty-year-old in a
# room with a fan running needs consonants and pauses far more than they
# need personality.
OPENAI_TTS_INSTRUCTIONS = os.getenv(
    "OPENAI_TTS_INSTRUCTIONS",
    "Speak like a warm, unhurried friend sitting in the same room as an older "
    "person. Calm and low, never bright or announcer-like. Leave a small pause "
    "at commas and a real one at full stops. Land consonants clearly at the "
    "ends of words. Let the pitch fall at the end of a sentence instead of "
    "rising. Never sound like you are reading something out.",
)
# Slightly under natural pace. Below about 0.9 it stops sounding careful
# and starts sounding slurred, which is worse than fast.
TTS_SPEED = float(os.getenv("TTS_SPEED", "0.95"))

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

# ---- Mopidy ------------------------------------------------------------
# Mopidy runs on this same Pi and owns audio output. We drive it over its
# JSON-RPC endpoint rather than importing it, so it can be restarted,
# upgraded, or swapped for another MPD-speaking server without touching
# the agent.
# 127.0.0.1, not "localhost". Mopidy binds to the IPv4 loopback, while
# "localhost" resolves to ::1 first on a dual-stack box — so the name
# connects to an IPv6 address nothing is listening on and the failure
# reads as "Mopidy isn't running" when it is.
MOPIDY_RPC_URL = os.getenv("MOPIDY_RPC_URL", "http://127.0.0.1:6680/mopidy/rpc")
MOPIDY_TIMEOUT_S = float(os.getenv("MOPIDY_TIMEOUT_S", "10"))
# Searching is a different order of magnitude from pausing. A YouTube
# lookup goes out through yt-dlp, over the network, on a Pi — well past
# ten seconds for the first one, and a timeout there reads to the user
# as "Mopidy is down" when it is simply still thinking.
MOPIDY_SEARCH_TIMEOUT_S = float(os.getenv("MOPIDY_SEARCH_TIMEOUT_S", "75"))
# How many song results to ask for. Small on purpose: yt-dlp resolves
# metadata for every result before returning any of them, so asking for
# twenty costs roughly four times the wait for a first track that is
# almost always the right one anyway.
MUSIC_SONG_RESULTS = int(os.getenv("MUSIC_SONG_RESULTS", "5"))
# Stop after the requested song rather than rolling into the next search
# result. "Play Lag Ja Gale" is a request for one song; wandering into
# whatever YouTube ranked fourth is not what was asked for, and someone
# who can't easily say "stop" is then stuck with it. The rest of the
# results stay queued, so "next" still works when they want it.
MUSIC_STOP_AFTER_SONG = os.getenv("MUSIC_STOP_AFTER_SONG", "true").lower() in ("1", "true", "yes")

# Remember what each request resolved to. Safe to cache because a
# youtube:video/<id> URI is stable — Mopidy resolves it to a stream at
# play time, so nothing here goes stale. Worth it because people replay
# the same handful of songs, and the second time should be instant.
MUSIC_CACHE_PATH = Path(os.getenv("MUSIC_CACHE_PATH", ROOT_DIR / "song_cache.json"))
MUSIC_CACHE_SIZE = int(os.getenv("MUSIC_CACHE_SIZE", "200"))

# Volume the music ducks to while Saathi is speaking or on a call, as a
# percentage of normal. Music and voice share one speaker; without this
# the mic hears the music and the wake word never lands.
MUSIC_DUCK_VOLUME = int(os.getenv("MUSIC_DUCK_VOLUME", "20"))
MUSIC_NORMAL_VOLUME = int(os.getenv("MUSIC_NORMAL_VOLUME", "70"))

# ---- Radio Browser -----------------------------------------------------
# Free, no account, no API key, ~45k stations. This is the default music
# source because it is the only one with no auth and no terms-of-service
# question hanging over it.
RADIO_BROWSER_URL = os.getenv("RADIO_BROWSER_URL", "https://de1.api.radio-browser.info")
# Volunteer-run mirrors; any one of them drops connections occasionally,
# so the client falls through the list rather than failing the request.
RADIO_BROWSER_MIRRORS = [
    h.strip() for h in os.getenv(
        "RADIO_BROWSER_MIRRORS",
        "https://de1.api.radio-browser.info,"
        "https://de2.api.radio-browser.info,"
        "https://nl1.api.radio-browser.info,"
        "https://at1.api.radio-browser.info",
    ).split(",") if h.strip()
]
RADIO_BROWSER_UA = os.getenv("RADIO_BROWSER_UA", "saathi/2.0")
RADIO_TIMEOUT_S = float(os.getenv("RADIO_TIMEOUT_S", "10"))

# ---- Wake word ---------------------------------------------------------
# The only model that runs on the Pi itself. Everything above is cloud;
# this can't be, because it is always listening.
#
# Two engines, because "no training" and "it should answer to Saathi"
# pull in opposite directions:
#
#   openwakeword — pretrained models, no account, no key, fully offline.
#                  Works the moment you pip install. You get its words,
#                  not yours. This is the default.
#   porcupine    — type "Hey Saathi" into Picovoice's console and it
#                  hands you a .ppn in seconds. Still no training, but it
#                  needs a free account and an access key.
WAKE_ENGINE = os.getenv("WAKE_ENGINE", "openwakeword").lower()

WAKE_REFRACTORY_S = float(os.getenv("WAKE_REFRACTORY_S", "2.0"))

# ALSA capture device, e.g. "plughw:3,0". Leave unset to use the ALSA
# default. Worth setting explicitly on a Pi: a USB mic's card number can
# move across reboots and replugs.
WAKE_CAPTURE_DEVICE = os.getenv("WAKE_CAPTURE_DEVICE")

# Duck the music the moment anyone starts talking, before knowing whether
# it was the wake word. Without echo cancellation the wake model is
# listening to a speaker playing music into the microphone and misses
# almost everything; a brief dip gives it a clean window. Costs a
# half-second dip whenever someone speaks near the box, which is a much
# smaller annoyance than a wake word that doesn't work during music.
WAKE_DUCK_ON_SPEECH = os.getenv("WAKE_DUCK_ON_SPEECH", "true").lower() in ("1", "true", "yes")
WAKE_DUCK_VOLUME = int(os.getenv("WAKE_DUCK_VOLUME", "25"))
WAKE_DUCK_HOLD_S = float(os.getenv("WAKE_DUCK_HOLD_S", "2.5"))
# RMS above which to bother ducking. Higher than SPEECH_RMS_THRESHOLD
# because the music itself is already in this signal.
WAKE_DUCK_RMS = int(os.getenv("WAKE_DUCK_RMS", "1200"))

# Speaker verification. A verifier model trained on one person's voice
# makes the wake word fire for them and not for the television — which
# is the other half of working while music plays. Train one with:
#   python -m saathi.wake enroll
WAKE_VERIFIER_PATH = os.getenv("WAKE_VERIFIER_PATH", str(ROOT_DIR / "wake_models" / "verifier.joblib"))
# 0-1. Higher means stricter about it being that person.
WAKE_VERIFIER_THRESHOLD = float(os.getenv("WAKE_VERIFIER_THRESHOLD", "0.1"))

# --- openwakeword -------------------------------------------------------
# Ships pretrained: alexa, hey mycroft, hey jarvis, hey rhasspy, plus two
# phrase models. openwakeword.utils.download_models() fetches them on
# first run, so there is nothing to train and nothing to sign up for.
# "hey jarvis" is the default because it's the most distinctive of the
# four — three syllables, uncommon phoneme run, and unlike "alexa" it
# won't fire every time the television says it.
OWW_WORDS = [w.strip() for w in os.getenv("OWW_WORDS", "hey_jarvis").split(",") if w.strip()]
OWW_THRESHOLD = float(os.getenv("OWW_THRESHOLD", "0.5"))

# --- porcupine ----------------------------------------------------------
# Free for personal use; the key comes from console.picovoice.ai.
PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY")
# Custom .ppn files (e.g. a "Hey Saathi" you generated). Comma-separated.
PORCUPINE_KEYWORD_PATHS = [p.strip() for p in os.getenv("PORCUPINE_KEYWORD_PATHS", "").split(",") if p.strip()]
# Built-in keywords, used when no custom .ppn is configured. "jarvis",
# "computer", "bumblebee" and friends need no file at all.
PORCUPINE_KEYWORDS = [k.strip() for k in os.getenv("PORCUPINE_KEYWORDS", "jarvis").split(",") if k.strip()]
# 0-1. Higher catches more and false-fires more.
PORCUPINE_SENSITIVITY = float(os.getenv("PORCUPINE_SENSITIVITY", "0.5"))

# ---- Device (the Pi as a room participant) -----------------------------
# Identity the Pi publishes under. The agent uses this to tell the person
# in the room apart from someone dialled in over SIP.
DEVICE_IDENTITY = os.getenv("DEVICE_IDENTITY", "saathi-device")

# Mic capture format. 16kHz mono matches what the wake word wants and
# what LiveKit publishes, so nothing resamples anywhere in this path.
DEVICE_SAMPLE_RATE = int(os.getenv("DEVICE_SAMPLE_RATE", "16000"))
DEVICE_FRAME_MS = int(os.getenv("DEVICE_FRAME_MS", "20"))

# A session ends this long after the last thing anyone said — the agent
# going quiet AND the room going quiet. Connecting only for the length of
# a conversation is what keeps this inside LiveKit's free tier; a box
# that stays connected all day burns the monthly allowance in a fortnight.
SESSION_IDLE_TIMEOUT_S = float(os.getenv("SESSION_IDLE_TIMEOUT_S", "12"))
# Hard cap, so a stuck session can't hold the line open forever.
SESSION_MAX_S = float(os.getenv("SESSION_MAX_S", "600"))
# Above this RMS (16-bit samples) someone is talking, so the idle timer
# should not be counting down.
SPEECH_RMS_THRESHOLD = int(os.getenv("SPEECH_RMS_THRESHOLD", "300"))

# Stay in the session while music is playing, so "stop" needs no wake
# word. Without this the session ends twelve seconds after you last
# spoke, the music carries on, and then the wake word has to compete
# with a speaker playing music into the microphone — which, with no echo
# cancellation, it loses. The cost is LiveKit minutes for as long as the
# music runs, so a long album will spend the free tier.
# Off by default: a session held open for the length of an album spends
# the free LiveKit tier, and an always-listening box is a different
# product from one you wake deliberately. The wake word stays the way in;
# WAKE_DUCK_ON_SPEECH is what gives it a chance over music.
MUSIC_HOLDS_SESSION = os.getenv("MUSIC_HOLDS_SESSION", "false").lower() in ("1", "true", "yes")
# How often to ask Mopidy whether it's still playing. Every frame would
# be 50 RPC calls a second for something that changes every few minutes.
MUSIC_CHECK_INTERVAL_S = float(os.getenv("MUSIC_CHECK_INTERVAL_S", "4"))
# Music is ducked for the whole session, not just while Saathi speaks:
# the microphone has to hear you over it, and at full volume it can't.
MUSIC_SESSION_VOLUME = int(os.getenv("MUSIC_SESSION_VOLUME", "35"))

# ALSA playback device, e.g. "plughw:0,0" for the Pi's headphone jack or
# "plughw:2,0" for a USB speaker. Unset uses the ALSA default — which, as
# with capture, is routinely not the device you actually plugged in.
AUDIO_OUTPUT_DEVICE = os.getenv("AUDIO_OUTPUT_DEVICE")

# How a conversation starts:
#   wake_word — say "hey jarvis" first. Cheapest, and immune to the
#               television. Costs you the delay of the wake phrase.
#   voice     — any speech starts a session. No delay, but anything in
#               the room can trigger it, including the TV.
#   always    — connected from boot. No delay at all, and burns LiveKit
#               minutes continuously — 1,000/month free is about 33
#               minutes a day, so this will exhaust it in a fortnight.
#   button    — a physical button, worn or on the table. No wake word to
#               miss, nothing to hear over the music, and no microphone
#               listening until someone asks for it — which is also the
#               answer when a care facility asks about privacy.
#   space     — hold the spacebar in the terminal you started it from.
#               The same push-to-talk behaviour as the button, over SSH,
#               with no hardware — so you can test the wearable's
#               ergonomics before the wearable exists.
WAKE_MODE = os.getenv("WAKE_MODE", "wake_word").lower()

# ---- Push-to-talk over the terminal (WAKE_MODE=space) -------------------
# A terminal has no key-up event: holding a key sends the character once,
# pauses for the system's repeat delay, then sends a fast stream of
# repeats, and sends nothing at all when you let go. So "held" has to
# mean "a keypress arrived within the last PTT_RELEASE_S", and that
# window has to be wider than the repeat delay or the mic closes in the
# gap before the repeats start. macOS defaults to about 0.5s; 1.0 covers
# it with room to spare. The cost is a tail of the same length after you
# actually release, which clips nothing — it only holds the mic open a
# moment longer.
PTT_RELEASE_S = float(os.getenv("PTT_RELEASE_S", "1.0"))
# "hold" needs key repeat switched on. If yours is off (macOS
# ApplePressAndHoldEnabled, or a terminal that swallows repeats), set
# "toggle": tap space to open the mic, tap again to close it.
PTT_STYLE = os.getenv("PTT_STYLE", "hold").lower()   # hold | toggle
# Ignore a second tap this soon after the first. Only used by "toggle" —
# without it key repeat flips the mic open and shut thirty times a second.
PTT_TOGGLE_DEBOUNCE_S = float(os.getenv("PTT_TOGGLE_DEBOUNCE_S", "0.5"))

# ---- Button ------------------------------------------------------------
# Any BLE or USB device that presents as a keyboard: a $5 shutter remote,
# or an ESP32 running the sketch in firmware/. Read through evdev, so the
# software doesn't care which.
#
# Matched by name substring rather than /dev/input/eventN, because that
# number changes when it reconnects.
BUTTON_NAME = os.getenv("BUTTON_NAME", "")
BUTTON_DEVICE = os.getenv("BUTTON_DEVICE", "")
# Which key counts. Empty means any — right for a single-button remote,
# where whatever it sends is the press.
BUTTON_KEYS = [k.strip().upper() for k in os.getenv("BUTTON_KEYS", "").split(",") if k.strip()]
# Ignore repeats inside this window. Buttons bounce, and BLE remotes
# often send a press twice.
BUTTON_DEBOUNCE_S = float(os.getenv("BUTTON_DEBOUNCE_S", "0.6"))
# A press during a conversation ends it — the same button that starts a
# session is how you stop the music, which is one thing to remember
# rather than two.
BUTTON_ENDS_SESSION = os.getenv("BUTTON_ENDS_SESSION", "true").lower() in ("1", "true", "yes")

# Hold to talk rather than press to start. evdev reports key-down and
# key-up exactly, so unlike the terminal version this needs no guessing —
# and it needs no terminal either, which is what makes a USB keyboard
# plugged into a headless Pi a working push-to-talk button under systemd.
#
# Set BUTTON_KEYS=KEY_SPACE with a real keyboard. The default of "any
# key" is right for a one-button remote and wrong for something with a
# hundred of them.
BUTTON_PUSH_TO_TALK = os.getenv("BUTTON_PUSH_TO_TALK", "false").lower() in ("1", "true", "yes")
# How long the mic stays open after the key comes up. People let go on
# the last syllable, and clipping it costs the word.
BUTTON_HANGOVER_S = float(os.getenv("BUTTON_HANGOVER_S", "0.35"))

# For WAKE_MODE=voice: how loud, and for how long, before it counts as
# someone talking rather than a door closing.
VOICE_TRIGGER_RMS = int(os.getenv("VOICE_TRIGGER_RMS", "700"))
VOICE_TRIGGER_MS = int(os.getenv("VOICE_TRIGGER_MS", "300"))

# Half-duplex. The speaker and the mic share a room with no echo
# cancellation, so an open mic hears the agent's own voice, transcribes
# it, and replies to itself — which is what "it's talking gibberish"
# actually is. Muting the mic while the agent speaks breaks that loop.
# The cost is barge-in: you can't interrupt it mid-sentence.
#
# Turn this OFF when you're using push-to-talk (WAKE_MODE=button or
# space). Push-to-talk solves the same problem better — the microphone is
# shut unless someone is deliberately holding the key, so there is no
# open mic for the speaker to leak into — and with both on you can't
# interrupt a reply even by holding the key down, which is the one thing
# push-to-talk was supposed to buy you.
HALF_DUPLEX = os.getenv("HALF_DUPLEX", "true").lower() in ("1", "true", "yes")
# Keep muted this long after the last sound from the far end, to cover
# the speaker's own decay and the room's reverb tail.
HALF_DUPLEX_HANGOVER_S = float(os.getenv("HALF_DUPLEX_HANGOVER_S", "0.4"))

# Spoken language — PIN THIS. "auto" is a trap on a far-field
# microphone: given a noisy or quiet signal, Whisper-family models don't
# return nothing, they return confident nonsense in a language they
# picked at random. A transcript comes back in Urdu, the model replies in
# Urdu, and the speaker reads it out. "en" or "hi" costs you nothing and
# removes the entire failure mode. Set "auto" only with a close mic.
AGENT_LANGUAGE = os.getenv("AGENT_LANGUAGE", "en")

# The languages this household actually speaks. Naming them is what stops
# the hallucination: an unconstrained model given a noisy signal invents
# a language, but one told to expect these will not answer English with
# Russian. Order matters only for the prompt's phrasing.
AGENT_LANGUAGES = [
    lang.strip() for lang in os.getenv("AGENT_LANGUAGES", "en,hi").split(",") if lang.strip()
]

# Human names, for the system prompt. A model reads "Tamil" more reliably
# than "ta", and this is also the list the doctor prints back at you.
LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "zh": "Chinese (Mandarin)",
    "ta": "Tamil", "ms": "Malay", "bn": "Bengali", "te": "Telugu",
    "mr": "Marathi", "es": "Spanish", "fr": "French", "de": "German",
}

# ---- Memory ------------------------------------------------------------
# What Saathi remembers between conversations. Without this every wake is
# a blank slate, which is fine for a speaker and useless for a companion:
# "how did your grandson's exam go?" is the whole difference.
#
# MEMORY_BACKEND: "supermemory" | "none"
MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "none").lower()
SUPERMEMORY_API_KEY = os.getenv("SUPERMEMORY_API_KEY")
SUPERMEMORY_BASE_URL = os.getenv("SUPERMEMORY_BASE_URL", "https://api.supermemory.ai")
# Scopes every memory to this household. One box, one tag — so a second
# device, or a shared account, never reads back somebody else's life.
MEMORY_CONTAINER_TAG = os.getenv("MEMORY_CONTAINER_TAG", "saathi-home")
# How many recalled facts to put in front of the model. More context is
# not better here: a long wall of half-relevant facts crowds out the
# actual question and slows every turn.
MEMORY_RECALL_LIMIT = int(os.getenv("MEMORY_RECALL_LIMIT", "6"))
MEMORY_TIMEOUT_S = float(os.getenv("MEMORY_TIMEOUT_S", "8"))


# ---- Headless console ---------------------------------------------------
# The answer to "I have no monitor and I don't know its address". A small
# service that answers over Bluetooth (no network needed at all) and over
# HTTP (once there is one), so a phone can find the Pi, put it on a wifi
# network, and start Saathi.
#
# It runs as root, because joining a network and starting a service both
# need to, and it listens on every interface, because a phone has to
# reach it. Everything over HTTP is gated on a token that is generated on
# first boot and handed out over Bluetooth — which you can only pair with
# from the same room.
CONSOLE_HTTP_PORT = int(os.getenv("CONSOLE_HTTP_PORT", "8765"))
# RFCOMM channel 1 is the conventional one for a serial profile, and what
# the installer registers in SDP so phones can see it.
CONSOLE_BT_CHANNEL = int(os.getenv("CONSOLE_BT_CHANNEL", "1"))
# Whether `sh` runs arbitrary commands. On, because a console you can't
# fix anything from is a status page — but this is a root shell reachable
# from the wifi, so it is one env var away from being off.
CONSOLE_SHELL = os.getenv("CONSOLE_SHELL", "true").lower() in ("1", "true", "yes")
# Generated, never chosen. Outside the repo so a `git add -A` can never
# publish it, and in /etc rather than a home directory because the
# console runs as root — joining a network and starting a service both
# need to — and a token that differs depending on who asked for it is a
# token nobody can find. Read it with: sudo cat /etc/saathi/console-token
CONSOLE_TOKEN_FILE = os.getenv("CONSOLE_TOKEN_FILE", "/etc/saathi/console-token")
# What the start/stop buttons act on, in start order — the agent first,
# so the device has something to talk to when it joins the room.
SAATHI_UNITS = [
    u.strip() for u in os.getenv("SAATHI_UNITS", "saathi-agent,saathi").split(",") if u.strip()
]

# When the Pi can't reach any known network it becomes one, so a phone
# can always get to the console. NetworkManager's shared mode puts it on
# 10.42.0.1 and runs DHCP and DNS itself.
HOTSPOT_SSID = os.getenv("HOTSPOT_SSID", "Saathi-Setup")
# WPA2 needs eight characters. Change it — this one is in a public repo.
HOTSPOT_PASSWORD = os.getenv("HOTSPOT_PASSWORD", "saathi123")
# How long with no network before giving up and becoming an access point.
HOTSPOT_AFTER_S = int(os.getenv("HOTSPOT_AFTER_S", "90"))
# And how long to stay one before trying the real networks again, so a
# router that was merely slow to boot doesn't strand the Pi in setup mode.
HOTSPOT_RETRY_AFTER_S = int(os.getenv("HOTSPOT_RETRY_AFTER_S", "600"))


# ---- Latency ------------------------------------------------------------
# Measure before changing any of this. `saathi talk` logs a line per turn:
#
#     turn  eou 0.51  stt 1.92  llm 0.88  tts 0.71  = 4.02s before it speaks
#
# and the biggest number is the thing to fix. It is almost always stt.

# How long after you stop talking before it decides you have. Pure
# waiting — nothing is computed during it.
#
# With push-to-talk this can be short, because releasing the key sends
# silence immediately and there is no ambiguity about whether you have
# finished. Without it, short means being cut off every time you pause
# for breath, which for an elderly speaker is often.
TURN_ENDPOINTING_S = float(os.getenv("TURN_ENDPOINTING_S", "0.2" if WAKE_MODE in ("button", "space") else "0.5"))

# Start the model on the transcript before the endpointing delay has
# finished running out, and throw the work away if the person turns out
# to still be talking. Costs a few wasted tokens, saves most of a second
# on every turn.
PREEMPTIVE_GENERATION = os.getenv("PREEMPTIVE_GENERATION", "true").lower() in ("1", "true", "yes")

# Log the per-turn breakdown. Cheap, and the only way "it's slow" turns
# into something you can act on.
LOG_LATENCY = os.getenv("LOG_LATENCY", "true").lower() in ("1", "true", "yes")
