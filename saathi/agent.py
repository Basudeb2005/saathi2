"""
The agent: one LiveKit session, a handful of tools, a short prompt.

Run it with:

    python -m saathi.agent dev       # local, verbose
    python -m saathi.agent start     # production

Compared with v1 this file is doing far less work, and that's the point.
There is no record-then-upload loop, no manual tool-call iteration, no
audio bridge: LiveKit's session owns streaming STT, turn detection,
barge-in and playback, so what's left here is the part that's actually
specific to Saathi — which tools exist, and how it should speak.

Everything a tool can fail with is caught and returned as a plain
sentence. A tool that raises leaves the user in silence; a tool that
returns "I couldn't find that station" gets read aloud, and they can try
again. That is the whole error-handling policy.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import openai, silero

from saathi.config import (
    AGENT_LANGUAGE,
    AGENT_LANGUAGES,
    LANGUAGE_NAMES,
    MEMORY_RECALL_LIMIT,
    HALF_DUPLEX,
    DEEPGRAM_STT_MODEL,
    OPENAI_STT_MODEL,
    OPENAI_TTS_MODEL,
    ELEVENLABS_VOICE_ID,
    LLM_MODEL,
    OPENAI_TTS_VOICE,
    SAATHI_ROOM_NAME,
    STT_PROVIDER,
    TTS_PROVIDER,
)
from saathi.contacts import ContactNotFoundError, ContactsRegistry
from saathi.calling.sip import CallError, hang_up, place_call
from saathi.logging_setup import get_logger
from saathi.memory import MemoryStore, build_memory
from saathi.music.player import MusicError, MusicPlayer

log = get_logger("agent")

INSTRUCTIONS = """You are Saathi, a voice companion that lives on a small speaker in \
someone's home. "Saathi" means companion in Hindi.

You can do three things: talk with the person, play music, and call their family.

Known contacts you can call:
{contacts}

{memory}

Languages this household speaks: {languages}

Rules:
- Only call a contact by a name from the list above. Never invent a name, and never dial \
a phone number spoken aloud — if they ask for someone not on the list, say so plainly.
- If it's ambiguous who they mean, ask which one, naming the candidates.
- Never volunteer the contact list. Only bring up calling when they clearly asked to call someone — a transcript you can't make sense of is not a request to call anyone.
- For music: use source="station" for a mood, genre or language ("something cheerful", \
"old Hindi songs", "the news"), and source="song" for a specific named track or artist. \
Use "auto" if you genuinely can't tell.
- If a message is garbled, empty, or you genuinely cannot tell what was said, say one short "Sorry, I didn't catch that" and stop. Do not guess at it, and do not answer a question the person didn't ask.
- Looking up a specific song takes several seconds. Say something short first — "let me find it" — then call the tool. Silence while you search reads as not having heard them, and they start repeating themselves.
- You are talking, not writing. Keep replies to one or two short sentences. No lists, no \
markdown, no emoji.
- The person may be elderly. Speak plainly, don't rush, and never use jargon.
- Reply in whatever language they spoke to you in — English, Hindi, Bengali, Tamil, or a mix of them in one sentence. Never switch languages on them, and never comment on which language they used.
- After doing something, say what you did in one short sentence, then stop talking."""


class Saathi(Agent):
    def __init__(
        self,
        contacts: ContactsRegistry,
        music: MusicPlayer,
        room_name: str,
        memory: Optional[MemoryStore] = None,
        remembered: str = "",
    ):
        self.memory = memory or build_memory()

        languages = ", ".join(LANGUAGE_NAMES.get(c, c) for c in AGENT_LANGUAGES)

        super().__init__(instructions=INSTRUCTIONS.format(
            contacts=contacts.describe_for_prompt(),
            memory=remembered or "(you haven't met this person before)",
            languages=languages,
        ))
        self.contacts = contacts
        self.music = music
        self.room_name = room_name
        # Who is currently dialled in, so "hang up" needs no argument —
        # nobody says "hang up on my daughter", they just say "hang up".
        self._active_call: Optional[str] = None

    # ---- music ---------------------------------------------------------

    @function_tool()
    async def play_music(self, context: RunContext, query: str, source: str = "auto") -> str:
        """Play music. `query` is what they asked for, e.g. "old Hindi songs" or
        "Lata Mangeshkar". `source` is "station" for a genre/mood/language, "song"
        for a specific named track or artist, or "auto" when unsure.

        Prefer "station" when either would do — a station starts in about a second,
        a song lookup takes several. Only use "song" when they named a specific
        track or artist and a station genuinely wouldn't satisfy them."""
        try:
            return self.music.play(query, source=source)
        except MusicError as e:
            return str(e)

    @function_tool()
    async def pause_music(self, context: RunContext) -> str:
        """Pause whatever is playing."""
        try:
            return self.music.pause()
        except MusicError as e:
            return str(e)

    @function_tool()
    async def resume_music(self, context: RunContext) -> str:
        """Resume music that was paused."""
        try:
            return self.music.resume()
        except MusicError as e:
            return str(e)

    @function_tool()
    async def stop_music(self, context: RunContext) -> str:
        """Stop the music entirely."""
        try:
            return self.music.stop()
        except MusicError as e:
            return str(e)

    @function_tool()
    async def next_track(self, context: RunContext) -> str:
        """Skip to the next track."""
        try:
            return self.music.next_track()
        except MusicError as e:
            return str(e)

    @function_tool()
    async def set_music_volume(self, context: RunContext, volume: int) -> str:
        """Set music volume, 0 to 100."""
        try:
            return self.music.set_volume(volume)
        except MusicError as e:
            return str(e)

    # ---- memory --------------------------------------------------------

    @function_tool()
    async def remember(self, context: RunContext, fact: str) -> str:
        """Save something lasting about this person — a name, a relationship, a
        preference, something coming up in their life. Write it as a short complete
        sentence that will still make sense months from now, e.g. "Her grandson Arun
        is sitting his exams in March"."""
        ok = self.memory.remember(fact, kind="fact")
        # Deliberately bland either way. The person didn't ask for a filing
        # system, and "I've made a note" mid-conversation is jarring.
        return "Noted." if ok else "Noted."

    @function_tool()
    async def recall(self, context: RunContext, query: str) -> str:
        """Look up what you know about this person, for when they refer to something
        from an earlier conversation. `query` is what you're trying to remember,
        e.g. "her grandson" or "what she likes listening to"."""
        facts = self.memory.recall(query, limit=MEMORY_RECALL_LIMIT)
        if not facts:
            return "Nothing remembered about that."
        return "\n".join(f"- {f.text}" for f in facts)

    # ---- calling -------------------------------------------------------

    @function_tool()
    async def call_contact(self, context: RunContext, name: str) -> str:
        """Call a family member or friend by name, so the person here can talk to
        them through the speaker. Only use a name from the contact list."""
        try:
            contact = self.contacts.get(name)
        except ContactNotFoundError as e:
            return str(e)

        # Duck rather than stop: if the call doesn't connect, the music
        # comes back up and nothing was lost.
        with self.music.ducked():
            try:
                await place_call(name, contact, self.room_name)
            except CallError as e:
                return str(e)

        self._active_call = name
        return f"Calling {contact.label or name} now."

    @function_tool()
    async def hang_up_call(self, context: RunContext) -> str:
        """End the phone call that's currently in progress."""
        if not self._active_call:
            return "There's no call to hang up."

        name = self._active_call
        await hang_up(name, self.room_name)
        self._active_call = None
        return "Hung up."


def _build_stt():
    """Plugins are imported lazily so an unused provider's package never
    has to be installed — the whole point of starting on one key.

    Language is passed through only when it's pinned. "auto" is right for
    a household that switches between languages mid-sentence; naming one
    is more accurate when you know it won't change.
    """
    # One language pinned is the most accurate and the most robust against
    # the hallucinated-language failure. Several means code-switching, and
    # only Deepgram does that properly — OpenAI has no multi mode, so it
    # gets the first language as a hint rather than nothing at all, which
    # is what let it answer English with Russian.
    multilingual = len(AGENT_LANGUAGES) > 1

    if STT_PROVIDER == "deepgram":
        from livekit.plugins import deepgram

        return deepgram.STT(
            model=DEEPGRAM_STT_MODEL,
            language="multi" if multilingual else AGENT_LANGUAGES[0],
        )

    primary = AGENT_LANGUAGES[0] if AGENT_LANGUAGES else AGENT_LANGUAGE
    if primary == "auto":
        return openai.STT(model=OPENAI_STT_MODEL)
    return openai.STT(model=OPENAI_STT_MODEL, language=primary)


def _build_tts():
    if TTS_PROVIDER == "elevenlabs":
        from livekit.plugins import elevenlabs

        return elevenlabs.TTS(voice_id=ELEVENLABS_VOICE_ID) if ELEVENLABS_VOICE_ID else elevenlabs.TTS()
    return openai.TTS(model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE)


def prewarm(proc: agents.JobProcess) -> None:
    """Load everything slow before a job arrives.

    Without this the first wake pays for it: the worker logged 6.4s of
    "no warmed process available" plus a 1.4s stall importing the OpenAI
    client, all of it while someone stood there having already said the
    wake word.
    """
    proc.userdata["vad"] = silero.VAD.load()


def _profile_text(memory: MemoryStore) -> str:
    return memory.describe_for_prompt(memory.profile())


async def entrypoint(ctx: agents.JobContext) -> None:
    contacts = ContactsRegistry()
    music = MusicPlayer()
    memory = build_memory()
    log.info("Memory backend: %s", memory.name)

    # In a thread: the memory client is synchronous, and calling it
    # directly here blocked the agent's event loop for 2.2 seconds —
    # which delays audio and turn handling, not just this one call.
    remembered = await asyncio.to_thread(_profile_text, memory)

    session = AgentSession(
        stt=_build_stt(),
        llm=openai.LLM(model=LLM_MODEL),
        tts=_build_tts(),
        vad=ctx.proc.userdata.get("vad") or silero.VAD.load(),
        # With half-duplex the microphone is muted while the agent talks,
        # so anything that "interrupts" is the room, not the user — and
        # letting it cut the reply off mid-sentence every few seconds is
        # exactly what makes the box feel broken.
        allow_interruptions=not HALF_DUPLEX,
    )

    await session.start(
        room=ctx.room,
        agent=Saathi(
            contacts=contacts,
            music=music,
            room_name=ctx.room.name or SAATHI_ROOM_NAME,
            memory=memory,
            remembered=remembered,
        ),
    )

    # Say something immediately. A speaker that answers a wake word with
    # silence reads as broken, and the person starts talking over the
    # first reply — which without echo cancellation makes it worse.
    await session.generate_reply(instructions="Greet them in one short sentence.")


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm)
    )
