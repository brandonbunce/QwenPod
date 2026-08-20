"""The break between segments: a sponsor read, and characters being rewritten.

These are one feature rather than two. Rewriting a character with a reasoning
model takes tens of seconds, and the gap between topics is the only place that
can happen -- but a gap that long is dead air, which is the thing this whole
app is most careful about. So the ad plays *over* the rewriting. The break is
the loading screen.

Nothing here is allowed to stop the show. Every entry point returns rather than
raises: no music, no LLM, no tts-server, a model that returns nonsense -- each
of those skips a piece of the break and the next topic still starts.
"""
import asyncio
import random
import time

from .audio import bed_under
from .config import Turn, music_tracks, render_template
from .events import RUN, VOICE


def segment_lines(transcript, start):
    """The turns belonging to the segment that just ended.

    Sliced from a recorded index rather than assuming the transcript *is* the
    current topic. That assumption holds only while topic_clears_context is on,
    and it is a setting.
    """
    return list(transcript[start:]) if start < len(transcript) else []


def lines_by_speaker(turns):
    """{name: [what they said]} for bot turns only.

    Real people's lines are excluded deliberately: this drives what a character
    becomes, and a character should be shaped by their own behaviour, not by
    someone in the channel talking at them.
    """
    out = {}
    for t in turns:
        if getattr(t, "kind", "bot") != "bot":
            continue
        text = (t.text or "").strip()
        if text:
            out.setdefault(t.speaker, []).append(text)
    return out


def pick_for_evolution(state, said, limit):
    """Which speakers to rewrite this break, longest-unevolved first.

    Only speakers who actually spoke are candidates -- there is nothing to
    analyse otherwise. Ordering by how long ago each was last rewritten means a
    quiet character still comes round instead of being permanently starved by
    whoever talks most.
    """
    stamps = state.evolve_stamps
    names = [n for n in said if state.get(n)]
    names.sort(key=lambda n: (stamps.get(n, 0.0), n))
    return names[:max(0, int(limit))]


class AdBreak:
    """Owns the interstitial. Constructed once by the director."""

    def __init__(self, state, llm, synth, speak, log, events=None):
        self.state = state
        self.llm = llm
        # Both come from the director: synth(text, speaker) -> wav bytes, and
        # speak(name, text, wav) which handles truncation, loudness and
        # playback. Passing them in keeps this module free of both tts and
        # discord.
        self.synth = synth
        self.speak = speak
        self.log = log
        self.events = events
        self.debug = {"last": "", "last_error": None, "breaks": 0,
                      "evolved": 0, "last_track": "", "last_evolved": "",
                      "last_intro": ""}

    # ---- the sponsor read -------------------------------------------------
    async def play(self, topic, turns, roster):
        """Write, synthesise and play one ad read. -> did it play."""
        s = self.state.settings
        if not s.adbreak_enabled:
            return False
        if not roster:
            self.debug["last_error"] = "no enabled speakers to read it"
            return False

        loop = asyncio.get_running_loop()
        reader = random.choice(roster)
        heard = [f"{t.speaker}: {t.text}" for t in turns][-12:]

        # Started before the hand-off is spoken, not after. Writing the ad is
        # the slow part, and covering exactly that latency is what the hand-off
        # is for -- awaiting it first would put the silence back and leave the
        # line playing into a break that was already ready.
        #
        # The reader's own prompt, so an evolved character reads the spot as
        # who they have become. Read here rather than captured earlier because
        # evolve() may be rewriting it concurrently -- attribute assignment is
        # atomic, so this gets the old or the new one, never a half-written one.
        writing = loop.run_in_executor(
            None, lambda: self.llm.write_ad(topic, heard, reader.name,
                                            s.adbreak_prompt,
                                            reader.system_prompt()))
        try:
            await self._hand_off(reader, topic)
        except Exception as e:
            # Belt and braces around an already-guarded method: an exception
            # escaping here would abandon `writing` half-run, and the ad is
            # worth playing whether or not anyone introduced it.
            self.log(f"[adbreak] hand-off failed - {e}")

        try:
            text = await writing
        except Exception as e:
            self.debug["last_error"] = f"could not write the ad: {e}"
            self.log(f"[adbreak] skipped - {e}")
            return False
        if not text or not text.strip():
            self.debug["last_error"] = "the model returned an empty ad"
            return False
        text = text.strip()

        try:
            wav = await loop.run_in_executor(
                None, lambda: self.synth(text, reader.name))
        except Exception as e:
            self.debug["last_error"] = f"could not synthesise the ad: {e}"
            self.log(f"[adbreak] skipped - {e}")
            return False

        track = random.choice(music_tracks() or [""]) or ""
        wav, info = await loop.run_in_executor(
            None, lambda: bed_under(wav, track, s.adbreak_music_gain))
        if not info.get("bed"):
            # Dry is a perfectly good ad break; say why once and carry on.
            self.log(f"[adbreak] no bed - {info.get('reason')}")
        self.debug["last_track"] = info.get("track", "")
        self.debug["last"] = f"{reader.name}: {text}"[:160]
        self.debug["last_error"] = None
        self.debug["breaks"] += 1
        self.log(f"[adbreak] {reader.name}: {text[:80]}")

        # A spoken line, recorded the way every other spoken line is. add_turn
        # is what puts words in the Run tab's transcript *and* in the event
        # log's speech lines; an ad that skipped it was audible and then
        # invisible everywhere afterwards.
        #
        # Safe for context: the segment was captured before this, and
        # _topic_start is reset after the switch below, so this turn falls
        # between the two and is analysed as part of neither segment.
        # kind="ad", not "bot". It is a real spoken line -- it belongs in the
        # transcript and the log -- but it is not the show. Marking it keeps it
        # out of lines_by_speaker (so a character never learns from reading an
        # advert) and out of the "did a segment actually happen" check, which
        # an ad would otherwise satisfy for the next ad, forever.
        self.state.add_turn(Turn(speaker=reader.name, text=text, kind="ad"))
        if self.events:
            # Marks the break itself. The words are already in the log via
            # add_turn, so this carries what that cannot: that it was an ad,
            # and what it played over.
            self.events.add(RUN, f"ad break read by {reader.name}"
                                 + (f" over {info['track']}" if info.get("bed") else " (no music)"))

        await self.speak(reader.name, text, wav)
        return True

    async def _hand_off(self, reader, topic):
        """The line that hands over to the break, in the reader's own voice.

        Spoken over the top of the ad being written, so the break opens with
        someone talking instead of with however many seconds the model takes.

        Its own turn in the transcript, kind="ad" for the same reasons the read
        is: it is genuinely spoken, but it is not the show, and a character
        must never learn from it or have it count as a segment having happened.

        Returns whether anything was said. Every failure is a skipped line and
        a log entry -- the break still runs.
        """
        s = self.state.settings
        if not s.adbreak_intro_enabled:
            return False
        line = render_template(s.adbreak_intro_template, rng=random,
                               name=reader.name, topic=topic)
        if not line:
            return False
        try:
            wav = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self.synth(line, reader.name))
        except Exception as e:
            # Not fatal, and deliberately not an early return from play(): the
            # ad is already being written and is worth playing on its own.
            self.log(f"[adbreak] hand-off skipped - {e}")
            return False
        self.debug["last_intro"] = f"{reader.name}: {line}"[:160]
        self.log(f"[adbreak] hand-off - {reader.name}: {line}")
        self.state.add_turn(Turn(speaker=reader.name, text=line, kind="ad"))
        await self.speak(reader.name, line, wav)
        return True

    # ---- rewriting the cast ------------------------------------------------
    async def evolve(self, topic, turns):
        """Rewrite the characters who spoke. -> how many changed."""
        s = self.state.settings
        if not s.evolve_enabled:
            return 0
        said = lines_by_speaker(turns)
        if not said:
            return 0

        loop = asyncio.get_running_loop()
        picked = pick_for_evolution(self.state, said, s.evolve_max_per_break)
        changed = 0
        for name in picked:
            sp = self.state.get(name)
            if sp is None:
                continue
            try:
                new = await loop.run_in_executor(
                    None, lambda sp=sp, name=name: self.llm.evolve_persona(
                        name, sp.persona, sp.dynamic_persona, said[name], topic))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"[evolve] {name} failed - {e}")
                self.debug["last_error"] = f"{name}: {e}"
                continue
            if not new or new == (sp.dynamic_persona or "").strip():
                continue
            with self.state.lock:
                # Re-fetch under the lock: the roster can be edited from the
                # web UI while this is running, and writing to the object we
                # captured before the call could resurrect a deleted speaker.
                live = self.state.get(name)
                if live is None:
                    continue
                live.dynamic_persona = new
                self.state.evolve_stamps[name] = time.time()
            changed += 1
            self.log(f"[evolve] {name}: {new[:80]}")
            if self.events:
                self.events.add(VOICE, f"{name} evolved: {new}")

        if changed:
            self.state.save()
            self.debug["evolved"] += changed
            self.debug["last_evolved"] = ", ".join(picked[:6])
        return changed
