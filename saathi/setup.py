"""
`python -m saathi.setup` — fill in .env by answering questions.

Editing a dotenv by hand on a headless Pi over SSH is where this project
loses people: a stray quote or a key pasted with a trailing newline fails
much later, as a confusing error from a library that never mentions your
config. So this asks, validates what it can validate cheaply, and writes
the file itself.

Safe to re-run. Existing values become the defaults, and anything already
in .env that this script doesn't know about is preserved rather than
dropped — hand-edits survive.

The parsing/rendering/validation half is pure and tested. Only `main()`
talks to a terminal.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from saathi.config import ROOT_DIR

ENV_PATH = ROOT_DIR / ".env"

BOLD, DIM, GREEN, YELLOW, RED, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
)


# ---- validation (pure) --------------------------------------------------

def require_prefix(prefix: str, human: str) -> Callable[[str], Optional[str]]:
    def check(value: str) -> Optional[str]:
        if value and not value.startswith(prefix):
            return f"that doesn't look like {human} — it should start with {prefix!r}"
        return None
    return check


def check_livekit_url(value: str) -> Optional[str]:
    if value and not value.startswith(("wss://", "ws://")):
        return "the LiveKit URL should start with 'wss://'"
    return None


def mask(value: str) -> str:
    """Show enough to recognise a key, never enough to use one."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


# ---- what we ask for ----------------------------------------------------

@dataclass
class Field:
    key: str
    label: str
    help: str = ""
    secret: bool = True
    validate: Optional[Callable[[str], Optional[str]]] = None


@dataclass
class Section:
    title: str
    blurb: str
    fields: List[Field]
    optional: bool = False
    # Set automatically when the section is filled in — so choosing to
    # configure Deepgram also flips STT_PROVIDER, rather than leaving the
    # user with a key that silently does nothing.
    implies: Dict[str, str] = field(default_factory=dict)


SECTIONS: List[Section] = [
    Section(
        title="OpenAI",
        blurb="Does the thinking, the listening and the speaking. One key covers all three.",
        fields=[
            Field(
                "OPENAI_API_KEY", "API key",
                help="platform.openai.com -> API keys. Put a few dollars of credit on it.",
                validate=require_prefix("sk-", "an OpenAI key"),
            ),
        ],
    ),
    Section(
        title="LiveKit",
        blurb="The room where you, Saathi and any caller meet. Free tier is plenty.",
        fields=[
            Field("LIVEKIT_URL", "URL", help="cloud.livekit.io -> your project. wss://...",
                  secret=False, validate=check_livekit_url),
            Field("LIVEKIT_API_KEY", "API key", help="Settings -> Keys",
                  validate=require_prefix("API", "a LiveKit API key")),
            Field("LIVEKIT_API_SECRET", "API secret"),
        ],
    ),
    Section(
        title="Calling",
        blurb="Trunk ids from `lk sip outbound create`. Skip until the speaker talks.",
        optional=True,
        fields=[
            Field("LIVEKIT_SIP_TRUNK_ID", "SIP trunk id (free, via Linphone)",
                  help="ST_...", secret=False),
            Field("LIVEKIT_PSTN_TRUNK_ID", "PSTN trunk id (paid, real numbers)",
                  help="ST_... — leave blank if you don't need it", secret=False),
        ],
    ),
    Section(
        title="Deepgram",
        blurb="Faster, more accurate speech-to-text than OpenAI's. Add it if transcription annoys you.",
        optional=True,
        implies={"STT_PROVIDER": "deepgram"},
        fields=[Field("DEEPGRAM_API_KEY", "API key", help="deepgram.com")],
    ),
    Section(
        title="ElevenLabs",
        blurb="Better voices, much better non-English. Add it if the voice annoys you.",
        optional=True,
        implies={"TTS_PROVIDER": "elevenlabs"},
        fields=[
            Field("ELEVENLABS_API_KEY", "API key", help="elevenlabs.io"),
            Field("ELEVENLABS_VOICE_ID", "Voice id", help="optional — blank uses the default",
                  secret=False),
        ],
    ),
    Section(
        title="Porcupine",
        blurb='Only needed to make it answer to "Hey Saathi". Default wake word needs nothing.',
        optional=True,
        implies={"WAKE_ENGINE": "porcupine"},
        fields=[
            Field("PORCUPINE_ACCESS_KEY", "Access key", help="console.picovoice.ai"),
            Field("PORCUPINE_KEYWORD_PATHS", "Path to your .ppn",
                  help="e.g. wake_models/hey_saathi.ppn", secret=False),
        ],
    ),
]


# ---- file i/o (pure-ish) ------------------------------------------------

def read_env(path: Path) -> Dict[str, str]:
    """Parse an existing .env. Tolerant on purpose — a malformed line
    should not cost someone the keys they already entered."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def render_env(values: Dict[str, str]) -> str:
    """Group the known keys under their section headings and append
    anything else at the end, so a hand-added variable is never lost."""
    known = {f.key for s in SECTIONS for f in s.fields}
    known |= {k for s in SECTIONS for k in s.implies}

    lines = ["# Written by `python -m saathi.setup`. Safe to edit by hand.", ""]

    for section in SECTIONS:
        keys = [f.key for f in section.fields]
        keys += [k for k in section.implies if k in values]
        if not any(values.get(k) for k in keys):
            continue
        lines.append(f"# ---- {section.title} " + "-" * max(0, 60 - len(section.title)))
        for key in keys:
            if values.get(key):
                lines.append(f"{key}={values[key]}")
        lines.append("")

    extras = {k: v for k, v in values.items() if k not in known and v}
    if extras:
        lines.append("# ---- other " + "-" * 60)
        for key in sorted(extras):
            lines.append(f"{key}={extras[key]}")
        lines.append("")

    return "\n".join(lines)


def write_env(path: Path, values: Dict[str, str]) -> None:
    """Atomic, and mode 600 — this file is a pile of API keys and has no
    business being world-readable on a shared machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(render_env(values))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def missing_required(values: Dict[str, str]) -> List[str]:
    return [
        f.key
        for s in SECTIONS if not s.optional
        for f in s.fields
        if not values.get(f.key)
    ]


# ---- the interactive part ----------------------------------------------

def _ask(field: Field, current: str) -> str:
    if field.help:
        print(f"  {DIM}{field.help}{RESET}")

    shown = mask(current) if field.secret else current
    suffix = f" {DIM}[{shown}]{RESET}" if current else ""

    while True:
        try:
            answer = input(f"  {field.label}{suffix}: ").strip()
        except EOFError:
            print()
            return current

        if not answer:
            return current

        error = field.validate(answer) if field.validate else None
        if error:
            # A warning, not a wall: our prefix guesses can go stale when
            # a provider changes its key format, and being wrong about
            # that must not block someone from finishing setup.
            print(f"  {YELLOW}!{RESET} {error}")
            if input(f"  use it anyway? [y/N] ").strip().lower() not in ("y", "yes"):
                continue
        return answer


def _confirm(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {hint} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def main() -> int:
    if not sys.stdin.isatty():
        print("saathi.setup needs an interactive terminal.", file=sys.stderr)
        return 1

    values = read_env(ENV_PATH)

    print(f"\n{BOLD}Saathi setup{RESET}")
    print(f"{DIM}Writes {ENV_PATH}. Enter keeps what's already there. Ctrl-C to bail.{RESET}")

    try:
        for section in SECTIONS:
            has_values = any(values.get(f.key) for f in section.fields)

            print(f"\n{BOLD}{section.title}{RESET}")
            print(f"{DIM}{section.blurb}{RESET}")

            if section.optional and not has_values:
                if not _confirm(f"  set up {section.title} now?"):
                    print(f"  {DIM}skipped{RESET}")
                    continue

            for f in section.fields:
                values[f.key] = _ask(f, values.get(f.key, ""))

            if any(values.get(f.key) for f in section.fields):
                values.update(section.implies)
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Cancelled — nothing written.{RESET}")
        return 130

    write_env(ENV_PATH, values)
    print(f"\n{GREEN}✓{RESET} wrote {ENV_PATH} {DIM}(mode 600){RESET}")

    still_missing = missing_required(values)
    if still_missing:
        print(f"{YELLOW}!{RESET} still needed before it can talk: {', '.join(still_missing)}")
        print(f"  {DIM}re-run `python -m saathi.setup` when you have them{RESET}")
        return 1

    print(f"\nNext:  {BOLD}python -m saathi.agent dev{RESET}")
    print(f"{DIM}then join the room from your LiveKit project's Playground.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
