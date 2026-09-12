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

from typing import Optional

from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import deepgram, elevenlabs, openai, silero

from saathi.config import LLM_MODEL, SAATHI_ROOM_NAME, STT_MODEL, TTS_VOICE_ID
from saathi.contacts import ContactNotFoundError, ContactsRegistry
from saathi.calling.sip import CallError, hang_up, place_call
from saathi.logging_setup import get_logger
from saathi.music.player import MusicError, MusicPlayer

log = get_logger("agent")

INSTRUCTIONS = """You are Saathi, a voice companion that lives on a small speaker in \
someone's home. "Saathi" means companion in Hindi.

You can do three things: talk with the person, play music, and call their family.

Known contacts you can call:
{contacts}

Rules:
- Only call a contact by a name from the list above. Never invent a name, and never dial \
a phone number spoken aloud — if they ask for someone not on the list, say so plainly.
- If it's ambiguous who they mean, ask which one, naming the candidates.
- For music: use source="station" for a mood, genre or language ("something cheerful", \
"old Hindi songs", "the news"), and source="song" for a specific named track or artist. \
Use "auto" if you genuinely can't tell.
- You are talking, not writing. Keep replies to one or two short sentences. No lists, no \
markdown, no emoji.
- The person may be elderly. Speak plainly, don't rush, and never use jargon.
- Reply in whatever language they spoke to you in.
- After doing something, say what you did in one short sentence, then stop talking."""


class Saathi(Agent):
    def __init__(self, contacts: ContactsRegistry, music: MusicPlayer, room_name: str):
        super().__init__(instructions=INSTRUCTIONS.format(contacts=contacts.describe_for_prompt()))
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
        for a specific named track or artist, or "auto" when unsure."""
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


async def entrypoint(ctx: agents.JobContext) -> None:
    contacts = ContactsRegistry()
    music = MusicPlayer()

    session = AgentSession(
        stt=deepgram.STT(model=STT_MODEL),
        llm=openai.LLM(model=LLM_MODEL),
        tts=elevenlabs.TTS(voice_id=TTS_VOICE_ID) if TTS_VOICE_ID else elevenlabs.TTS(),
        vad=silero.VAD.load(),
    )

    await session.start(
        room=ctx.room,
        agent=Saathi(contacts=contacts, music=music, room_name=ctx.room.name or SAATHI_ROOM_NAME),
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
