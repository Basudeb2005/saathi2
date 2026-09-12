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

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
STT_MODEL = os.getenv("STT_MODEL", "nova-3")
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "")

# ---- Mopidy ------------------------------------------------------------
# Mopidy runs on this same Pi and owns audio output. We drive it over its
# JSON-RPC endpoint rather than importing it, so it can be restarted,
# upgraded, or swapped for another MPD-speaking server without touching
# the agent.
MOPIDY_RPC_URL = os.getenv("MOPIDY_RPC_URL", "http://localhost:6680/mopidy/rpc")
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
