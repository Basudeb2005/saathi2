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

# gpt-4o rather than gpt-4o-mini. Mini loses the thread on anything
# compound — "put on something cheerful and tell me what time my
# daughter called" — and for a companion, understanding beats the few
# cents a day the smaller model saves.
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
# gpt-4o-transcribe over whisper-1: markedly better on accented English
# and on Indian languages, which is most of what this box will hear.
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe")
DEEPGRAM_STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "nova-3")
# gpt-4o-mini-tts is noticeably quicker to first audio than tts-1, which
# is what the "slow audio generation" warnings are about. ElevenLabs is
# quicker still and much better in Hindi and Tamil — worth the extra key
# once English is working.
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "shimmer")
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
WAKE_MODE = os.getenv("WAKE_MODE", "wake_word").lower()

# For WAKE_MODE=voice: how loud, and for how long, before it counts as
# someone talking rather than a door closing.
VOICE_TRIGGER_RMS = int(os.getenv("VOICE_TRIGGER_RMS", "700"))
VOICE_TRIGGER_MS = int(os.getenv("VOICE_TRIGGER_MS", "300"))

# Half-duplex. The speaker and the mic share a room with no echo
# cancellation, so an open mic hears the agent's own voice, transcribes
# it, and replies to itself — which is what "it's talking gibberish"
# actually is. Muting the mic while the agent speaks breaks that loop.
# The cost is barge-in: you can't interrupt it mid-sentence.
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
