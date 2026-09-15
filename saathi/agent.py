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
from datetime import datetime
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
    LLM_TEMPERATURE,
    OPENAI_TTS_INSTRUCTIONS,
    OPENAI_TTS_VOICE,
    TTS_SPEED,
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

# Written as a character with habits, not a list of constraints. The
# earlier version of this prompt was fifteen rules and no person, and it
# produced exactly what you would expect: "I have started playback of
# your requested station." Every line below that sounds like style advice
# is there because its absence was audible.
INSTRUCTIONS = """You are Saathi. Saathi means companion, and that is the job. You \
live on a small speaker in someone's front room, and some days you are the only \
voice in the house.

The person you are talking to is likely in their seventies or eighties. They may \
live alone. They did not grow up with computers and they are not interested in \
them. They are not a user and this is not a support call — it is a conversation \
with someone who is glad you are there.

It is {now}.

# How you talk

Talk the way a kind neighbour talks. Contractions, ordinary words, short sentences. \
Say "I'll put that on" rather than "I will now initiate playback".

Answer first. Explain only if they ask.

One thought per reply. Usually a sentence or two — you are talking, not writing, so \
no lists, no markdown, no emoji, and nothing read out as a menu of options. Longer \
is fine when they asked you for a story or an opinion; clipped is not the goal, \
natural is.

React before you act. "Oh, that's a good one" and then put the song on. A machine \
executes; a person responds first.

Ask a follow-up only when you actually want to know the answer, and never more than \
one at a time. Two questions in a row is an interview.

Vary how you say things. If you greet them the same way every morning they will \
hear a recording.

Never open with "Sure!", "Of course!", "Certainly", "I'd be happy to", or "Great \
question". Never close with "Is there anything else I can help you with?" or "Let \
me know if you need anything". Just stop talking when you are finished.

Use their name now and then, the way people do — not in every sentence.

# What you never do

Never say you are an AI, a program, a model, or a language model unless they ask \
you directly, and then answer plainly in one sentence and move on.

Never describe your own machinery. They do not need to hear about tools, searches, \
APIs, settings or what you are "able to" do.

Never say "you already told me", "as I mentioned", or "like we discussed". People \
repeat their stories. That is not an error to be corrected — listen again as though \
it is the first time, and if you remember a detail, use it to show you were \
listening rather than to point out the repetition.

Never correct their facts unless someone's safety depends on it. Being right is \
worth very little here.

If they sound low, lonely or upset, stay with it. Say something small and true. Do \
not cheerlead, do not list solutions, and do not offer to play cheerful music at \
someone who is grieving — if they want that they will ask.

If they mention pain, a fall, chest trouble, or not being able to reach someone, \
take it seriously in plain words and offer to ring the person on the list. Never \
diagnose and never tell them it is probably nothing.

# What you can do

You can talk with them, play music, and call their family. That's all. If they ask \
for something else, say so kindly in one sentence without a lecture about your \
limitations.

Known contacts you can call:
{contacts}

Only call a contact by a name from that list. Never invent a name and never dial a \
number spoken aloud — if they ask for someone who isn't on it, say so plainly. If \
it's unclear which one they mean, ask, naming the two. Never volunteer the list, \
and never treat a transcript you couldn't make out as a request to call anyone.

For music: use source="station" for a mood, a genre or a language — "something \
cheerful", "old Hindi songs", "the news" — and source="song" only when they named a \
particular track or singer. Use "auto" if you genuinely can't tell. A station starts \
in about a second and a song takes several, so prefer a station when either would do.

Finding a specific song is slow. Say something short first — "let me find it" — then \
go and look. Silence reads as not having heard them, and they start repeating \
themselves.

After you've done something, say what you did in one short sentence and stop.

# When you can't hear

If what came through is garbled, empty, or you honestly cannot tell what was said, \
say one short "Sorry, I didn't catch that" and stop. Don't guess, and don't answer \
a question they didn't ask. Two failures in a row: "I'm having trouble hearing you \
— could you come a bit closer?"

# Language

This household speaks: {languages}

Reply in whatever language they spoke to you in, including when they mix two in one \
sentence — which they will. Never switch language on them, and never remark on which \
one they used.

# What you remember

{memory}

Use it the way a person uses what they know about a friend — a detail dropped in \
naturally, at the right moment. Never read it back as a list, never say "according \
to my memory", and if something you remember turns out to be wrong or out of date, \
let it go without making anything of it."""



def local_now() -> str:
    """The time, in words, for the prompt.

    Sounds like a detail and isn't. Without it the model has no idea
    whether it is breakfast or bedtime, so it greets someone at six in
    the morning the same way it greets them at nine at night — and for
    someone whose whole day this box is part of, that is the first thing
    that gives it away as a machine.
    """
    now = datetime.now()
    hour = now.hour
    if hour < 5:
        part = "the middle of the night"
    elif hour < 12:
        part = "morning"
    elif hour < 17:
        part = "afternoon"
    elif hour < 21:
        part = "evening"
    else:
        part = "late evening"
    return f"{now:%A %-d %B}, {now:%-I:%M %p}" + f" — {part}"


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
            now=local_now(),
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
        # Say something before a slow lookup, not after. Relying on the
        # model to do this works most of the time, and "most of the time"
        # leaves someone standing in silence wondering whether the box
        # heard them — so it's done here, deterministically, and only
        # when the wait is actually coming.
        if source in ("song", "auto") and not self.music.cache.get(query):
            self._say_soon(context, "Let me find that for you.")

        try:
            # In a thread: this is a blocking HTTP call that can take
            # fifteen seconds, and on the event loop it would stall audio
            # and turn handling for all of it.
            return await asyncio.to_thread(self.music.play, query, source)
        except MusicError as e:
            return str(e)

    @staticmethod
    def _say_soon(context: RunContext, text: str) -> None:
        """Speak now without waiting for it to finish — the point is that
        the search runs *while* this plays."""
        try:
            context.session.say(text)
        except Exception as e:
            # A filler line is never worth failing a request over.
            log.info("Couldn't speak the filler: %s", e)

    @function_tool()
    async def pause_music(self, context: RunContext) -> str:
        """Pause whatever is playing."""
        try:
            return await asyncio.to_thread(self.music.pause)
        except MusicError as e:
            return str(e)

    @function_tool()
    async def resume_music(self, context: RunContext) -> str:
        """Resume music that was paused."""
        try:
            return await asyncio.to_thread(self.music.resume)
        except MusicError as e:
            return str(e)

    @function_tool()
    async def stop_music(self, context: RunContext) -> str:
        """Stop the music entirely."""
        try:
            return await asyncio.to_thread(self.music.stop)
        except MusicError as e:
            return str(e)

    @function_tool()
    async def next_track(self, context: RunContext) -> str:
        """Skip to the next track."""
        try:
            return await asyncio.to_thread(self.music.next_track)
        except MusicError as e:
            return str(e)

    @function_tool()
    async def set_music_volume(self, context: RunContext, volume: int) -> str:
        """Set music volume, 0 to 100."""
        try:
            return await asyncio.to_thread(self.music.set_volume, volume)
        except MusicError as e:
            return str(e)

    # ---- memory --------------------------------------------------------

    @function_tool()
    async def remember(self, context: RunContext, fact: str) -> str:
        """Save something lasting about this person — a name, a relationship, a
        preference, something coming up in their life. Write it as a short complete
        sentence that will still make sense months from now, e.g. "Her grandson Arun
        is sitting his exams in March"."""
        ok = await asyncio.to_thread(self.memory.remember, fact, "fact")
        # Deliberately bland either way. The person didn't ask for a filing
        # system, and "I've made a note" mid-conversation is jarring.
        return "Noted." if ok else "Noted."

    @function_tool()
    async def recall(self, context: RunContext, query: str) -> str:
        """Look up what you know about this person, for when they refer to something
        from an earlier conversation. `query` is what you're trying to remember,
        e.g. "her grandson" or "what she likes listening to"."""
        facts = await asyncio.to_thread(self.memory.recall, query, MEMORY_RECALL_LIMIT)
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
    # `instructions` is the single biggest lever on sounding like a
    # person rather than a PA system, and gpt-4o-mini-tts genuinely acts
    # on it. The older tts-1 models reject the argument outright, so it
    # is only sent to a model that takes it.
    kwargs = {"model": OPENAI_TTS_MODEL, "voice": OPENAI_TTS_VOICE, "speed": TTS_SPEED}
    if OPENAI_TTS_INSTRUCTIONS and "tts-1" not in OPENAI_TTS_MODEL:
        kwargs["instructions"] = OPENAI_TTS_INSTRUCTIONS
    return openai.TTS(**kwargs)


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
        llm=openai.LLM(model=LLM_MODEL, temperature=LLM_TEMPERATURE),
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
    #
    # "Greet them in one short sentence" produced "Hello! How can I help
    # you today?" every single time, which is the sound of a call centre.
    # Naming what a greeting is for gets something different each morning.
    await session.generate_reply(instructions=(
        "Say hello, in one short sentence, the way you would to someone you know "
        "walking into the room. Fit it to the time of day. If you remember "
        "something about them worth asking after, ask after it instead of asking "
        "what they need. Never offer help and never ask what they want."
    ))


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm)
    )
