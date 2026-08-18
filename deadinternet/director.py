"""Conversation engine.

Runs as a task on the Discord event loop. The efficiency trick is the
pipeline: the next speaker's line is generated (LLM + TTS) while the current
one is still playing, so the only audible gap is the configured one. The
current line is appended to the transcript *before* the pre-generation kicks
off, so the next speaker still sees it.

Everything blocking (HTTP to Ollama and tts-server) is pushed to an executor
-- the loop also drives audio playback and must never stall.
"""
import asyncio
import random
import re
import secrets
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .adbreak import AdBreak, segment_lines
from .audio import ack_pcm, normalize_wav, truncate_wav
from . import websearch
from .config import (MODE_INTERACTIVE, MODE_MANUAL, MODE_PODCAST, Pin, Turn,
                     render_template)


@dataclass
class PreparedTopic:
    """Everything a topic switch needs, assembled in the background.

    Fetching pins, downloading an image, describing it and synthesising the
    announcement all used to happen at the moment of the switch, which is the
    one moment the channel is waiting in silence. Doing it ahead of time makes
    the switch itself little more than a play_wav.
    """
    pin: object
    image: Optional[Tuple[str, str]] = None
    description: str = ""
    announcer: str = ""
    line: str = ""
    wav: Optional[bytes] = None
    built_at: float = field(default_factory=time.monotonic)

# The 12 Hz codec emits 12.5 audio frames per second (24000 / 1920-sample hop),
# which converts a seconds limit into the server's max_new_tokens frame count.
FRAMES_PER_SECOND = 12.5

# The receipt for a message being taken into the queue. Built once at
# import: it is 28 kB of PCM and it never changes.
ACK_PCM = ack_pcm()

# A hand-typed topic has no author to credit, so the pinned-by templates would
# render "new topic by : ...". These are used instead; not user-editable
# because there is no {author} to vary.
MANUAL_TOPIC_LINES = (
    "Okay, new topic: {topic}\n"
    "Right, moving on to this: {topic}\n"
    "New subject. {topic}"
)

# An empty channel for longer than this and the conversation restarts rather
# than resuming: whoever walks in would otherwise hear the tail of a
# discussion that stopped an hour ago.
RESUME_RESET_AFTER = 300.0


class Director:
    def __init__(self, state, tts, llm, runtime, log=print):
        self.state = state
        self.tts = tts
        self.llm = llm
        self.runtime = runtime
        self.log = log

        # Dedicated RNG rather than the global `random`, so nothing another
        # library does to the module-level state can affect selection. Seed 0
        # in settings means "fresh entropy each start"; any other value makes
        # the ordering reproducible.
        configured = int(getattr(state.settings, "rng_seed", 0) or 0)
        self.rng_seed = configured or secrets.randbits(64)
        self._rng = random.Random(self.rng_seed)

        self.running = False
        self._task: Optional[asyncio.Task] = None
        # Created up front rather than inside _run(), so queuing a manual line
        # cannot race the loop starting up. asyncio.Queue does not bind to a
        # loop at construction on 3.10+.
        self._manual_q: asyncio.Queue = asyncio.Queue()
        self._last_speaker: Optional[str] = None
        self.status = "idle"
        self.last_norm = {}
        self.last_truncate = {}
        self.last_stim = ""
        self._topic_deadline = None
        # Where in the transcript the current segment starts, so the break can
        # analyse exactly the turns that belong to the topic that just ended.
        # An index rather than "the transcript is the topic": that only holds
        # while topic_clears_context is on, and it is a setting.
        self._topic_start = 0
        self.adbreak = AdBreak(
            self.state, llm, self._synth, self._speak, log,
            events=getattr(state, "events", None))
        # Shuffled bag of pin indices, refilled once exhausted. Sequential
        # order meant every restart began at the first pin again.
        self._topic_bag = []
        self._last_pin = None
        # (mime, base64) for the picture the group is currently looking at.
        # Normally cleared as soon as the announcer has described it out loud
        # -- the description carries into everyone else's context as text, so
        # only one vision call is ever made. It survives only when describing
        # failed, in which case the per-turn fallback attaches it directly.
        self.topic_image: Optional[Tuple[str, str]] = None
        self.topic_image_desc = ""
        # Cleared for the session the first time a model chokes on an image,
        # so we stop paying to upload one every turn.
        self._images_ok = True
        # Next topic, assembled in the background so the switch is instant.
        self._prepared: Optional[PreparedTopic] = None
        self._prepare_task: Optional[asyncio.Task] = None
        # Readable form of what is still in the bag, for the UI.
        self._queue_preview = []
        # Set when a switch should cut in immediately rather than waiting for
        # the current line to finish.
        self._switch_now = False
        # A topic typed by hand, used in place of the next pin exactly once.
        self._manual_next: Optional[str] = None
        # Web search round-robin: which subject is next, and the unused
        # results already fetched for each subject.
        self._web_subject_idx = 0
        self._web_results = {}
        # Which source each prepared topic came from, for the queue display.
        self._last_source = ""
        # Set by the UI; consumed by the turn loop at a turn boundary.
        self._force_topic = False
        self.topic_debug = {"last_switch": None, "pins_seen": 0, "current": "",
                            "next_in": None, "last_error": None, "image": "-",
                            "channels": 0}
        # Set when the context is cleared mid-turn, so a line pre-generated
        # against the old conversation gets thrown away instead of played.
        self._drop_pending = False
        self._opened = False
        # When the channel went quiet, so a short absence resumes but a long
        # one starts over. 0 means someone is here.
        self._empty_since = 0.0
        # Text chat is the only way in for a real person, so the debug panel
        # tracks it instead of a receive pipeline.
        self.text_debug = {"queued": 0, "answered": 0, "dropped": 0, "depth": 0,
                           "last": "",
                           "last_drop": None, "last_router": ""}

    # ---- speaker selection ---------------------------------------------
    def _pick_next(self, after: Optional[str] = None):
        roster = self.state.active()
        if not roster:
            return None
        if len(roster) == 1:
            return roster[0]
        prev = after or self._last_speaker
        options = [s for s in roster if s.name != prev] or roster
        return self._rng.choice(options)

    # ---- vocal stims -------------------------------------------------------
    def _roll_stim(self, speaker) -> Optional[str]:
        """Pick a catchphrase for this turn, or None. Uses the seeded RNG so a
        fixed seed reproduces the whole conversation, tics included."""
        phrases = speaker.stim_list()
        chance = float(getattr(speaker, "stim_chance", 0.0) or 0.0)
        if not phrases or chance <= 0:
            return None
        if self._rng.random() * 100.0 >= chance:
            return None
        return self._rng.choice(phrases)

    def _ensure_stim(self, text: str, stim: str, name: str) -> str:
        """Fall back to appending when the model ignored the tic, so a
        successful roll always actually fires."""
        if re.search(re.escape(stim), text, re.I):
            self.last_stim = f"{name}: '{stim}' (in-line)"
            return text
        joined = text.rstrip()
        if joined and joined[-1] not in ".!?":
            joined += "."
        self.last_stim = f"{name}: '{stim}' (appended)"
        return f"{joined} {stim}!".strip()

    # ---- production -----------------------------------------------------
    async def _produce(self, speaker, addressed_to=None) -> Optional[Tuple[str, str, bytes]]:
        """LLM line + synthesised audio for one speaker."""
        if speaker is None:
            return None
        loop = asyncio.get_running_loop()
        cfg = self.state.settings
        transcript = self.state.recent()
        stim = self._roll_stim(speaker)
        image = self.topic_image if (cfg.topic_images and self._images_ok) else None

        solo = len(self.state.active()) <= 1

        def generate(img):
            return self.llm.speak_as(
                speaker, transcript, cfg.topic, addressed_to,
                cfg.num_predict, cfg.temperature, stim, img, solo,
            )

        try:
            text = await loop.run_in_executor(None, lambda: generate(image))
        except Exception as e:
            if image is None:
                self.log(f"[director] llm failed for {speaker.name}: {e}")
                return None
            # Almost always a text-only model rejecting the image payload.
            # Drop it for the rest of the session rather than failing every
            # turn until the topic rotates away.
            self._images_ok = False
            self.topic_debug["image"] = f"disabled - model rejected it ({str(e)[:80]})"
            self.log(f"[topic] model rejected the image, falling back to text: {e}")
            try:
                text = await loop.run_in_executor(None, lambda: generate(None))
            except Exception as e2:
                self.log(f"[director] llm failed for {speaker.name}: {e2}")
                return None
        if not text:
            return None
        if stim:
            text = self._ensure_stim(text, stim, speaker.name)

        try:
            wav = await loop.run_in_executor(None, lambda: self._synth(text, speaker.name))
        except Exception as e:
            self.log(f"[director] tts failed for {speaker.name}: {e}")
            return None
        return speaker.name, text, wav

    def _synth_kwargs(self):
        """Cap generation in frames so the GPU never renders audio we would
        only throw away at playback."""
        s = self.state.settings
        if not s.speech_limit_enabled or s.max_speech_seconds <= 0:
            return {}
        return {"max_new_tokens": max(1, int(s.max_speech_seconds * FRAMES_PER_SECOND))}

    def _synth(self, text: str, voice: str) -> bytes:
        """Every synthesis in the director goes through here, so the limit
        cannot be bypassed by a call site that forgets it."""
        return self.tts.synth(text, voice, **self._synth_kwargs())

    async def _speak(self, name: str, text: str, wav: bytes, kind: str = "bot"):
        self._last_speaker = name
        s = self.state.settings
        # Truncate before normalising, so the level is measured over what is
        # actually played.
        if s.speech_limit_enabled:
            wav, tinfo = truncate_wav(wav, s.max_speech_seconds)
            self.last_truncate = tinfo
            if tinfo.get("truncated"):
                self.log(f"[speech] capped {name}: "
                         f"{tinfo['original']}s -> {tinfo['duration']}s")
        if self.state.settings.normalize_audio:
            wav, info = normalize_wav(wav, self.state.settings.target_dbfs)
            self.last_norm = info
        try:
            await self.runtime.play_wav(wav, timeout=self._play_timeout())
        except Exception as e:
            self.log(f"[director] playback failed: {e}")

    def _play_timeout(self) -> float:
        """Generous upper bound on one clip, so a connection that dies during
        playback cannot park the turn loop inside play_wav."""
        s = self.state.settings
        if s.speech_limit_enabled and s.max_speech_seconds > 0:
            return s.max_speech_seconds * 2 + 30
        return 600.0

    # ---- user input ------------------------------------------------------
    def push_user_text(self, user: str, text: str):
        """Called from the Discord thread for text-channel messages. This is
        the only way a real person gets into the conversation.

        Everything accepted is answered, in the order it was said. The cue is
        the receipt: it sounds the moment a message is taken, over the top of
        whoever is talking, so someone typing during a line knows it landed
        without the bot having to stop to tell them.
        """
        text = (text or "").strip()
        if not text:
            return
        s = self.state.settings
        if s.mode != MODE_INTERACTIVE:
            self.text_debug["last_drop"] = (
                f"mode is {s.mode}, not interactive")
            return

        with self.state.lock:
            q = self.state.pending_users
            if len(q) >= max(1, s.user_queue_max):
                # Refused, not silently replaced. The cue stays quiet, so
                # silence after a message means it was not taken -- which is
                # the only honest thing an acknowledgement can do here.
                self.text_debug["dropped"] += 1
                self.text_debug["last_drop"] = (
                    f"{len(q)} already waiting - queue is full")
                self.log(f"[text] queue full ({len(q)}), refused {user}: {text[:60]}")
                return
            first = not q
            q.append(Turn(speaker=user, text=text, kind="user"))
            depth = len(q)

        self.text_debug["queued"] += 1
        self.text_debug["depth"] = depth
        self.text_debug["last"] = f"{user}: {text}"[:140]
        self.text_debug["last_drop"] = None

        if s.ack_sound:
            self.runtime.play_cue(ACK_PCM)

        if s.barge_in and first:
            # Cut whoever is mid-sentence and bin the pre-generated line; it
            # was written for a conversation that has just changed. Only for
            # the message that finds the queue empty: interrupting again for
            # each one of a rapid handful would stop the bot ever finishing a
            # sentence, and the cue has already said the later ones landed.
            self._drop_pending = True
            self.status = f"{user} typed - answering"
            self.runtime.interrupt()
        elif not first:
            self.status = f"{user} queued - {depth} waiting"

    # ---- topic rotation ----------------------------------------------------
    def _next_pin(self, pins):
        """Random pin, without repeating one until every pin has been used.

        A plain shuffle-each-time would sometimes play the same pin twice in a
        row, and sequential order always restarted at the first pin after a
        restart. The bag gives random order with full coverage.
        """
        by_id = {p.message_id: p for p in pins}

        # The bag holds message ids, not positions. Positions were wrong: the
        # pin list is re-fetched before every build, so a pin being added,
        # removed or edited anywhere shifted every index after it and the
        # remaining bag silently pointed at different pins than it was
        # shuffled to hold. That is what made the order look repetitive --
        # the bag was not being honoured.
        self._topic_bag = [mid for mid in self._topic_bag if mid in by_id]

        if not self._topic_bag:
            bag = list(by_id)
            self._rng.shuffle(bag)
            # Don't open a fresh bag on the pin we just played. Swap with a
            # *random* later slot, not rotate to the end -- rotating is
            # deterministic and made consecutive cycles look alike.
            if len(bag) > 1 and self._last_pin is not None and bag[0] == self._last_pin:
                j = self._rng.randrange(1, len(bag))
                bag[0], bag[j] = bag[j], bag[0]
            self._topic_bag = bag

        mid = self._topic_bag.pop(0)
        chosen = by_id[mid]
        self._last_pin = mid
        # Remember the labels so the queue stays readable after the pins
        # themselves have gone out of scope.
        self._queue_preview = [
            f"{by_id[m].label()[:60]}  ({by_id[m].channel})"
            for m in self._topic_bag if m in by_id
        ]
        self.topic_debug["bag"] = self._queue_preview[:12]
        return chosen

    def topic_queue(self):
        """What is lined up, across every source.

        The pin bag has a real order; the other sources are pools that get
        drawn from by weight, so they are shown as pools rather than pretending
        to a sequence they do not have.
        """
        s = self.state.settings
        out = []
        prep = self._prepared
        if prep is not None:
            out.append(f"**next up** - {prep.pin.label()[:70]}"
                       + (f"  _({prep.pin.channel})_" if prep.pin.channel else ""))
        if self._manual_next:
            out.append(f"**queued by you** - {self._manual_next[:70]}")
        live = dict(self._source_weights())
        total = sum(live.values()) or 1.0
        for name in ("pins", "web", "crowd"):
            if name not in live:
                continue
            pct = 100.0 * live[name] / total
            if name == "pins":
                items = list(getattr(self, "_queue_preview", []))[:6]
                out.append(f"**pins** ({pct:.0f}%) - {len(self._topic_bag)} left in the bag")
                out += [f"  - {x}" for x in items]
            elif name == "web":
                subs = [x.strip() for x in (s.web_subjects or "").splitlines() if x.strip()]
                nxt = subs[self._web_subject_idx % len(subs)] if subs else "-"
                cached = sum(len(v) for v in self._web_results.values())
                out.append(f"**web** ({pct:.0f}%) - {len(subs)} subjects, next "
                           f"_{nxt}_, {cached} results cached")
            else:
                pending = list(s.crowd_topics)
                out.append(f"**crowd** ({pct:.0f}%) - {len(pending)} waiting")
                for raw in pending[:6]:
                    who, _, text = raw.partition("\x1f")
                    out.append(f"  - {text[:60] or who} _({who})_" if text
                               else f"  - {who[:60]}")
        return out

    def _source_weights(self):
        """Enabled sources and their weights, as [(name, weight), ...]."""
        s = self.state.settings
        pairs = [
            ("pins", float(s.source_pins_weight or 0)),
            ("web", float(s.source_web_weight or 0)),
            ("crowd", float(s.source_crowd_weight or 0)),
        ]
        # A source with nothing in it must not win the roll, or the switch
        # silently does nothing and the timer resets for another full cycle.
        avail = {
            "pins": bool(s.topic_channel_ids),
            "web": bool([x for x in (s.web_subjects or "").splitlines() if x.strip()]),
            "crowd": bool(s.crowd_topics),
        }
        return [(n, w) for n, w in pairs if w > 0 and avail[n]]

    def _pick_source(self) -> Optional[str]:
        live = self._source_weights()
        if not live:
            return None
        total = sum(w for _, w in live)
        roll = self._rng.random() * total
        for name, w in live:
            roll -= w
            if roll <= 0:
                return name
        return live[-1][0]

    async def _next_web_topic(self) -> Optional[Pin]:
        """Round-robin the subjects, and round-robin the results within each."""
        s = self.state.settings
        subjects = [x.strip() for x in (s.web_subjects or "").splitlines() if x.strip()]
        if not subjects:
            return None
        loop = asyncio.get_running_loop()
        # Try each subject once before giving up, so one dead search does not
        # stall the whole source.
        for _ in range(len(subjects)):
            subject = subjects[self._web_subject_idx % len(subjects)]
            self._web_subject_idx += 1
            queue = self._web_results.get(subject) or []
            if not queue:
                found = await loop.run_in_executor(
                    None,
                    lambda sub=subject: websearch.search(
                        sub, int(s.web_results_per_search or 8), self.log))
                queue = [r.as_topic() for r in found]
                self._rng.shuffle(queue)
                self._web_results[subject] = queue
            if queue:
                text = queue.pop(0)
                return Pin(author="", text=text, channel=f"web:{subject}")
        self.topic_debug["last_error"] = "web search returned nothing"
        return None

    def _next_crowd_topic(self) -> Optional[Pin]:
        """Oldest submission first -- whoever asked first gets heard first."""
        with self.state.lock:
            if not self.state.settings.crowd_topics:
                return None
            raw = self.state.settings.crowd_topics.pop(0)
        self.state.save()
        who, _, text = raw.partition("\x1f")
        if not text:
            who, text = "", raw
        return Pin(author=who, text=text, channel="crowd")

    async def _build_topic(self) -> Optional[PreparedTopic]:
        """Assemble the next topic off the critical path.

        Everything expensive lives here: reading pins from every channel,
        downloading the image, the single vision call that describes it, and
        synthesising the announcement.
        """
        s = self.state.settings
        loop = asyncio.get_running_loop()

        # A hand-typed topic wins over the pin bag, exactly once. Consumed
        # here rather than at switch time so its announcement is pre-rendered
        # like any other -- the switch stays instant.
        manual, self._manual_next = self._manual_next, None
        if manual:
            prep = PreparedTopic(pin=Pin(author="", text=manual, channel="typed"))
            self.topic_debug["last_error"] = None
            self.topic_debug["image"] = "-"
            await self._prepare_announcement(prep, loop)
            return prep

        source = self._pick_source()
        if source is None:
            self.topic_debug["last_error"] = (
                "no topic source is enabled and stocked - give a source some "
                "weight, and make sure it has something in it")
            return None
        self._last_source = source

        if source == "web":
            pin = await self._next_web_topic()
            if pin is None:
                return None
            prep = PreparedTopic(pin=pin)
            self.topic_debug["image"] = "-"
            self.topic_debug["last_error"] = None
            await self._prepare_announcement(prep, loop)
            return prep

        if source == "crowd":
            pin = self._next_crowd_topic()
            if pin is None:
                return None
            prep = PreparedTopic(pin=pin)
            self.topic_debug["image"] = "-"
            self.topic_debug["last_error"] = None
            await self._prepare_announcement(prep, loop)
            return prep

        pins = await self.runtime.fetch_pins(s.topic_channel_ids)
        self.topic_debug["pins_seen"] = len(pins)
        self.topic_debug["channels"] = len(s.topic_channel_ids)
        if not pins:
            self.topic_debug["last_error"] = (
                "no pinned messages with text or an image in the selected channels")
            return None
        self.topic_debug["last_error"] = None

        prep = PreparedTopic(pin=self._next_pin(pins))
        pin = prep.pin

        # Only the chosen pin's image is downloaded -- fetching every
        # attachment on every rotation would be wasted bandwidth.
        if pin.has_image and s.topic_images:
            if await self.runtime.load_image(pin):
                prep.image = (pin.image_type, pin.image_b64)
                kb = len(pin.image_b64) * 3 // 4096
                self.topic_debug["image"] = f"{pin.image_type}, ~{kb} KB"
                try:
                    prep.description = await loop.run_in_executor(
                        None,
                        lambda: self.llm.describe_image(
                            pin.image_type, pin.image_b64, pin.text),
                    )
                except Exception as e:
                    self.log(f"[topic] could not describe the image: {e}")
                    self.topic_debug["image"] += " (describe failed)"
                if prep.description:
                    self.topic_debug["image"] += " - described"
            else:
                self.topic_debug["image"] = "download failed - text only"
        else:
            self.topic_debug["image"] = "-" if not pin.has_image else "off"

        await self._prepare_announcement(prep, loop)
        return prep

    async def _prepare_announcement(self, prep, loop):
        """Pick who reads the handover and synthesise it ahead of time."""
        s = self.state.settings
        pin = prep.pin
        if s.topic_announce:
            speaker = self._pick_next()
            if speaker is not None:
                prep.announcer = speaker.name
                prep.line = self._announcement(
                    pin, pin.has_image, prep.description, speaker.name)
                try:
                    prep.wav = await loop.run_in_executor(
                        None, lambda: self._synth(prep.line, speaker.name))
                except Exception as e:
                    # Not fatal: the switch re-synthesises inline.
                    self.log(f"[topic] could not pre-synthesise the announcement: {e}")
        return prep

    def _start_prepare(self):
        """Kick off building the next topic, if one is not already in hand."""
        if self._prepared or (self._prepare_task and not self._prepare_task.done()):
            return
        if not self._manual_next and not self._source_weights():
            return

        async def run():
            try:
                self._prepared = await self._build_topic()
            except Exception as e:
                self.log(f"[topic] prepare failed: {e}")
                self._prepared = None

        self._prepare_task = asyncio.create_task(run())

    def _discard_prepared(self):
        """Throw away a prepared topic -- the pin order it was built against no
        longer applies.

        Callable from the Gradio thread (Reshuffle), so the cancel has to go
        through the loop: Task.cancel() is not safe from another thread.
        """
        self._prepared = None
        task, self._prepare_task = self._prepare_task, None
        if not task or task.done():
            return
        loop = getattr(self.runtime, "loop", None)
        if loop and loop.is_running():
            loop.call_soon_threadsafe(task.cancel)
        else:
            task.cancel()

    async def _maybe_rotate_topic(self, force=False):
        """Swap in the next pinned message as the topic when the timer is up.

        Pins are pooled across every selected channel and drawn from a
        shuffled bag, so a long session works through all of them in a
        different order each time.
        """
        s = self.state.settings
        if not force and not s.topic_rotation:
            return False

        # Paused means paused. The pause-when-empty guard lives further down
        # the turn loop than this call does, so rotation used to carry on into
        # an empty channel -- announcing topics, and (once the ad break landed
        # here) reading a full sponsor spot every few minutes to nobody. The ad
        # then put its own line in the transcript, which satisfied the "did a
        # segment happen" check for the next one, so it sustained itself
        # indefinitely. Left overnight that is all it does.
        #
        # force=True still goes through: that is someone pressing Switch topic
        # now in the web UI, and they can see the channel is empty.
        if (not force and self.running and s.pause_when_empty
                and not self.runtime.humans_present()):
            return False
        # A hand-typed topic does not need a pin pool to switch to.
        if not self._source_weights() and not self._manual_next and not self._prepared:
            self.topic_debug["last_error"] = "no pin channels selected"
            return False

        now = time.monotonic()
        if self._topic_deadline is None:
            self._topic_deadline = now + s.topic_interval_minutes * 60
            if not force:
                # Start building the first one now, so the first switch is as
                # quick as the rest.
                self._start_prepare()
                return False
        self.topic_debug["next_in"] = max(0, int(self._topic_deadline - now))
        if not force and now < self._topic_deadline:
            self._start_prepare()
            return False

        self._topic_deadline = now + s.topic_interval_minutes * 60

        prep, self._prepared = self._prepared, None
        if prep is None:
            # Nothing ready -- either the timer beat the prefetch or this is a
            # forced switch. Build it inline; the channel waits, but only this
            # once.
            if self._prepare_task and not self._prepare_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self._prepare_task), 20)
                    prep, self._prepared = self._prepared, None
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            if prep is None:
                prep = await self._build_topic()
        if prep is None:
            return False

        # ---- the break -------------------------------------------------
        # Ordered so the ad covers the rewriting. Both need the segment that
        # is about to be destroyed: clear_transcript() and the s.topic write
        # are both below, so capture first.
        outgoing_topic = s.topic
        with self.state.lock:
            turns = segment_lines(self.state.transcript, self._topic_start)
        roster = self.state.active()
        evolving = None

        # Only close out a segment that happened. Two reasons, both from
        # pressing "Switch topic now":
        #
        #  - With nothing said yet there is nothing to advertise and nothing to
        #    learn from, and the ad would be about a discussion that did not
        #    occur.
        #  - While the director is stopped, rotate_topic_now() runs this inline
        #    and blocks the web request on fut.result(60). The evolution wait
        #    alone defaults to 60s, so the break could outlast the budget and
        #    report a failure for a switch that was actually fine.
        if not self.running or not any(
                getattr(t, "kind", "bot") == "bot" for t in turns):
            # NB "bot" excludes kind="ad" and kind="user": neither an advert
            # nor someone typing counts as the hosts having had a conversation.
            self.log("[adbreak] nothing to close out - going straight to the topic")
            turns = []

        try:
            if s.evolve_enabled and turns:
                # Started, not awaited: the whole point is that it runs behind
                # the ad rather than in front of the next topic.
                evolving = asyncio.create_task(
                    self.adbreak.evolve(outgoing_topic, turns))
            if s.adbreak_enabled and turns:
                self.status = "ad break"
                await self.adbreak.play(outgoing_topic, turns, roster)
            if evolving is not None:
                self.status = "rewriting the cast"
                n = await asyncio.wait_for(
                    asyncio.shield(evolving), s.evolve_timeout_seconds)
                if n:
                    self.log(f"[evolve] {n} character(s) rewritten")
        except asyncio.TimeoutError:
            # Let it finish in the background. system_prompt() is read fresh
            # every turn, so a late result still lands -- just a topic later.
            # Holding the show open for it would be dead air, which is worse.
            self.log(f"[evolve] still running after {s.evolve_timeout_seconds:g}s "
                     "- carrying on, it will apply when it lands")
        except asyncio.CancelledError:
            if evolving is not None:
                evolving.cancel()
            raise
        except Exception as e:
            # A broken break must never stop the show rotating.
            self.log(f"[adbreak] break failed - {e}")

        pin = prep.pin
        self.topic_image = prep.image
        self.topic_image_desc = prep.description
        if prep.description:
            # The announcer says this out loud and it lands in the transcript,
            # so everyone else works from the text. No further vision calls.
            self.topic_image = None
        elif prep.image:
            # Describing failed; fall back to attaching the image per turn.
            self._images_ok = True

        topic = pin.label()
        if prep.description:
            topic = (f"{pin.text} (the image shows: {prep.description})"
                     if pin.text else f"an image: {prep.description}")
        with self.state.lock:
            s.topic = topic
            s.topic_author = f"{pin.author} in {pin.channel}" if pin.author else ""
        self.topic_debug["current"] = topic[:160]
        self.topic_debug["author"] = pin.author
        self.topic_debug["channel"] = pin.channel
        self.topic_debug["last_switch"] = time.strftime("%H:%M:%S")
        self.topic_debug["prepared"] = prep.wav is not None
        self.log(f"[topic] switched to {pin.channel} ({pin.author}): {topic[:80]}"
                 + (" [+image]" if prep.image else ""))

        if s.topic_clears_context:
            self.state.clear_transcript()
            self._last_speaker = None
        self._drop_pending = True
        # Whatever the transcript is now is where this segment begins --
        # correct whether it was just cleared or left alone.
        with self.state.lock:
            self._topic_start = len(self.state.transcript)

        if s.topic_announce:
            # Keyed off the pin, not off whether we managed to load the image:
            # the people in the channel can see the pin either way, so an
            # image that failed to download still needs calling out.
            await self._announce_topic(prep)

        # Line up the one after this while the conversation carries on.
        self._start_prepare()
        return True

    async def _announce_opening(self):
        """Spoken once at the top of a conversation: what the topic is, and
        that people can type in the channel to be answered."""
        speaker = self._pick_next()
        if speaker is None:
            return
        s = self.state.settings
        line = render_template(
            s.opening_template, self._rng,
            topic=s.topic or "whatever comes up", speaker=speaker.name)
        if not line.strip():
            return
        loop = asyncio.get_running_loop()
        try:
            wav = await loop.run_in_executor(None, lambda: self._synth(line, speaker.name))
        except Exception as e:
            self.log(f"[director] could not speak the opening: {e}")
            return
        self.state.add_turn(Turn(speaker=speaker.name, text=line))
        self.status = f"{speaker.name} opening the show"
        self.log(f"[opening] {speaker.name}: {line[:80]}")
        await self._speak(speaker.name, line, wav)

    def _announcement(self, pin, has_image: bool, description: str = "",
                      speaker: str = "") -> str:
        """What the handover sounds like.

        Built from the user-editable templates. An image topic gets a second
        sentence appended: the listeners can see the pin in the channel, but
        nothing in the spoken line would otherwise explain why the subject
        changed to something with no words in it. When the image was
        described, the announcer reads that out -- which is also how every
        other speaker comes to know what is in it, since the line lands in
        the transcript.
        """
        s = self.state.settings
        # Each source has its own wording: the pinned-by templates talk about
        # {author}/{channel}, which web results and typed topics do not have.
        chan = pin.channel or ""
        if chan.startswith("web:"):
            return render_template(
                s.topic_web_template, self._rng,
                topic=pin.text, subject=chan[4:], speaker=speaker)
        if chan == "crowd" and pin.author:
            return render_template(
                s.topic_crowd_template, self._rng,
                topic=pin.text, author=pin.author, speaker=speaker)
        # A typed topic has nobody to credit, so the pinned-by wording would
        # read "new topic by : ...".
        tpl = s.topic_template if pin.author else MANUAL_TOPIC_LINES
        # An image with no caption has no topic text worth reading out; the
        # image sentence carries the whole announcement instead.
        head = render_template(
            tpl, self._rng,
            topic=pin.text, author=pin.author, channel=pin.channel,
            speaker=speaker) if (pin.text or not has_image) else ""
        if not has_image:
            return head
        tail = render_template(
            s.topic_image_template, self._rng,
            description=description, author=pin.author, channel=pin.channel,
            speaker=speaker)
        if not head:
            return tail
        # Reads as its own sentence after the caption.
        return f"{head.rstrip('.!?')}. {tail}".strip()

    async def _announce_topic(self, prep):
        """Have one of the speakers read the new topic out, so the channel
        hears the handover instead of the subject just changing."""
        speaker = self.state.get(prep.announcer) if prep.announcer else None
        if speaker is None:
            # The roster changed since this was prepared.
            speaker = self._pick_next()
            prep.wav = None
        if speaker is None:
            return
        line = prep.line or self._announcement(
            prep.pin, prep.pin.has_image, prep.description, speaker.name)
        wav = prep.wav
        if wav is None:
            loop = asyncio.get_running_loop()
            try:
                wav = await loop.run_in_executor(
                    None, lambda: self._synth(line, speaker.name))
            except Exception as e:
                self.log(f"[topic] could not announce: {e}")
                return
        # Recorded as a turn so the others know the subject changed, who set
        # it, and what is in the image -- even when context was just cleared.
        self.state.add_turn(Turn(speaker=speaker.name, text=line))
        self.status = f"{speaker.name} announcing new topic"
        await self._speak(speaker.name, line, wav)

    def reseed(self, seed=None):
        """Fresh RNG and empty the bag, so the next cycle is a new order."""
        self.rng_seed = int(seed) if seed else secrets.randbits(64)
        self._rng = random.Random(self.rng_seed)
        self._topic_bag = []
        self._last_pin = None
        self.topic_debug["bag"] = []
        # Whatever was queued came out of the old bag.
        self._discard_prepared()
        return self.rng_seed

    def rotate_topic_now(self):
        """Request the next pinned topic, from the UI thread.

        Returns (switched, message). While the director is running this only
        raises a flag: the turn loop performs the switch between turns. Doing
        it inline would run concurrently with the loop, and the announcement's
        play_wav would stop() whoever was mid-sentence.
        """
        if not self.runtime.loop:
            raise RuntimeError("Discord runtime not started")
        if not self.running:
            # Nothing is speaking, so there is nobody to interrupt.
            fut = asyncio.run_coroutine_threadsafe(
                self._maybe_rotate_topic(force=True), self.runtime.loop)
            return fut.result(60), ""

        self._force_topic = True
        if not self.state.settings.topic_switch_instant:
            return False, "queued - switching once the current line finishes"

        # Instant: cut the speaker off mid-sentence, bin the line that was
        # already generated for the next turn, and let the loop pick the
        # switch up on its very next pass instead of after the current
        # utterance finishes playing.
        self._switch_now = True
        self._drop_pending = True
        self._cancel_inflight()
        self.runtime.interrupt()
        return False, "switching now - cutting the current line off"

    def _cancel_inflight(self):
        """Abandon work that belongs to the topic we are leaving.

        The pre-generated turn is an in-flight LLM + TTS round trip; without
        cancelling it the switch still has to wait for it to land before the
        loop comes back around.
        """
        task = getattr(self, "_pre_task", None)
        if task and not task.done():
            loop = getattr(self.runtime, "loop", None)
            if loop and loop.is_running():
                loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()

    def queue_topic(self, text: str) -> str:
        """Make `text` the next topic instead of the next pin.

        The prepared topic is thrown away and rebuilt around this one, so the
        switch stays instant when it does fire.
        """
        text = (text or "").strip()
        if not text:
            return ""
        self._manual_next = text
        self._discard_prepared()
        loop = getattr(self.runtime, "loop", None)
        if loop and loop.is_running():
            # _start_prepare creates a task, so it has to run on the loop.
            loop.call_soon_threadsafe(self._start_prepare)
        return text

    def switch_to_topic(self, text: str):
        """Queue `text` and switch to it straight away."""
        self.queue_topic(text)
        return self.rotate_topic_now()

    def clear_context(self):
        """Wipe the conversation the LLM sees. Also drops the pre-generated
        turn, which would otherwise carry the old context into the new one."""
        self.state.clear_transcript()
        self._drop_pending = True
        self._last_speaker = None
        return "LLM context cleared."

    def _take_pending_user(self) -> Optional[Turn]:
        with self.state.lock:
            q = self.state.pending_users
            turn = q.popleft() if q else None
            self.text_debug["depth"] = len(q)
        return turn

    # ---- manual mode ------------------------------------------------------
    def say_now(self, speaker_name: str, text: str):
        """Queue an explicit line. Works in any mode; in manual mode it is
        the only thing that speaks."""
        if not self.runtime.loop:
            raise RuntimeError("Discord runtime not started - press Connect bot")
        asyncio.run_coroutine_threadsafe(
            self._manual_q.put((speaker_name, text)), self.runtime.loop
        ).result(10)

    async def _handle_manual(self, name: str, text: str):
        speaker = self.state.get(name)
        if not speaker:
            return
        loop = asyncio.get_running_loop()
        try:
            wav = await loop.run_in_executor(None, lambda: self._synth(text, speaker.name))
        except Exception as e:
            self.log(f"[director] manual tts failed: {e}")
            return
        self.state.add_turn(Turn(speaker=name, text=text))
        await self._speak(name, text, wav)

    # ---- main loop --------------------------------------------------------
    async def _run(self):
        pending = None  # pre-generated next turn

        while self.running:
            try:
                mode = self.state.settings.mode

                # Between turns, so nobody is cut off mid-sentence -- unless
                # an instant switch was asked for, which deliberately does
                # cut them off. Checked before the drop below, since a switch
                # sets that flag.
                if mode != MODE_MANUAL:
                    forced, self._force_topic = self._force_topic, False
                    self._switch_now = False
                    await self._maybe_rotate_topic(force=forced)

                # A context clear, barge-in, or topic switch invalidates
                # anything already queued.
                if self._drop_pending:
                    self._drop_pending = False
                    pending = None

                # Manual lines always win, whatever the mode.
                if not self._manual_q.empty():
                    name, text = self._manual_q.get_nowait()
                    pending = None
                    await self._handle_manual(name, text)
                    continue

                if mode == MODE_MANUAL:
                    # Drop any turn pre-generated while we were still in an
                    # automatic mode, so switching to manual really does go
                    # quiet instead of playing one more line.
                    pending = None
                    self.status = "manual - waiting"
                    try:
                        name, text = await asyncio.wait_for(self._manual_q.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    await self._handle_manual(name, text)
                    continue

                if not self.runtime.connected():
                    # The runtime's watchdog owns getting back in; the loop
                    # just waits it out. Drop the queued line so a rejoin does
                    # not open with something written before the gap.
                    self.status = self.runtime.voice_debug.get(
                        "state", "waiting for voice channel")
                    pending = None
                    self._empty_since = self._empty_since or time.monotonic()
                    await asyncio.sleep(0.5)
                    continue

                # Nobody in the channel: stop burning the GPU on speech no one
                # can hear, and pick straight back up when someone arrives.
                if (self.state.settings.pause_when_empty
                        and not self.runtime.humans_present()):
                    if not self._empty_since:
                        self._empty_since = time.monotonic()
                        self.log("[director] channel is empty - pausing")
                    self.status = "channel empty - paused"
                    pending = None
                    await asyncio.sleep(1.0)
                    continue

                if self._empty_since:
                    away = time.monotonic() - self._empty_since
                    self._empty_since = 0.0
                    self.log(f"[director] someone is back after {away:.0f}s - resuming")
                    # A long gap makes the old transcript read as a non
                    # sequitur to whoever just walked in.
                    if away >= RESUME_RESET_AFTER:
                        self.state.clear_transcript()
                        self._last_speaker = None
                        self._opened = False

                roster = self.state.active()
                if not roster:
                    self.status = "no speakers configured"
                    await asyncio.sleep(0.5)
                    continue

                # Open the show before anyone starts riffing.
                if not self._opened:
                    self._opened = True
                    if self.state.settings.opening_enabled:
                        await self._announce_opening()
                    continue

                # A real user talking pre-empts the pre-generated turn.
                user_turn = self._take_pending_user() if mode == MODE_INTERACTIVE else None

                if user_turn:
                    pending = None
                    self.state.add_turn(user_turn)
                    loop = asyncio.get_running_loop()
                    responder_name = await loop.run_in_executor(
                        None,
                        lambda: self.llm.route(
                            user_turn.speaker, user_turn.text, roster, self.state.recent()
                        ),
                    )
                    self.status = f"{responder_name} answering {user_turn.speaker}"
                    self.text_debug["answered"] += 1
                    self.text_debug["last_router"] = (
                        f"{user_turn.speaker} -> {responder_name}")
                    self.log(f"[router] {user_turn.speaker} -> {responder_name}")
                    produced = await self._produce(
                        self.state.get(responder_name), addressed_to=user_turn.speaker
                    )
                    if produced:
                        name, text, wav = produced
                        self.state.add_turn(Turn(speaker=name, text=text))
                        await self._speak(name, text, wav)
                    continue

                # Normal podcast turn.
                if pending is None:
                    produced = await self._produce(self._pick_next())
                else:
                    produced, pending = pending, None
                if not produced:
                    await asyncio.sleep(0.5)
                    continue

                name, text, wav = produced
                self.state.add_turn(Turn(speaker=name, text=text))
                self.status = f"{name} speaking"

                # Pre-generate the following turn against a transcript that
                # already contains the line about to be played.
                nxt = self._pick_next(after=name)
                pre_task = asyncio.create_task(self._produce(nxt))
                # Exposed so an instant topic switch can cancel it from the
                # UI thread rather than waiting for it to land.
                self._pre_task = pre_task

                await self._speak(name, text, wav)

                interrupted = (self.state.settings.mode == MODE_INTERACTIVE
                               and self._peek_user())
                if interrupted or self._switch_now:
                    pre_task.cancel()
                    pending = None
                else:
                    try:
                        pending = await pre_task
                    except asyncio.CancelledError:
                        pending = None
                self._pre_task = None

                if self._switch_now:
                    # Skip the inter-turn gap and go straight back round, so
                    # the switch lands immediately rather than after a pause.
                    continue

                await asyncio.sleep(max(0.0, self.state.settings.gap_seconds))

            except asyncio.CancelledError:
                raise
            except Exception:
                self.log("[director] " + traceback.format_exc(limit=3))
                await asyncio.sleep(1.0)

        self.status = "stopped"

    def _peek_user(self) -> bool:
        with self.state.lock:
            return bool(self.state.pending_users)

    # ---- lifecycle ---------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self._opened = False
        self._task = asyncio.run_coroutine_threadsafe(
            self._wrap(), self.runtime.loop
        )
        self.status = "running"

    async def _wrap(self):
        try:
            await self._run()
        except asyncio.CancelledError:
            pass
        else:
            # Only on a clean stop. A cancelled task means the loop is being
            # torn down, not that the show is ending politely.
            try:
                await self._say_goodbye()
            except Exception as e:
                self.log(f"[director] goodbye failed: {e}")

    async def _say_goodbye(self):
        """Sign off before going quiet. Runs after the turn loop has exited,
        so it cannot be talked over by a line that was already in flight."""
        s = self.state.settings
        if not s.goodbye_enabled or not self.runtime.connected():
            return
        speaker = self._pick_next()
        if speaker is None:
            return
        line = render_template(s.goodbye_template, self._rng,
                               topic=s.topic or "", speaker=speaker.name)
        if not line:
            return
        loop = asyncio.get_running_loop()
        try:
            wav = await loop.run_in_executor(
                None, lambda: self._synth(line, speaker.name))
        except Exception as e:
            self.log(f"[director] could not speak the goodbye: {e}")
            return
        self.status = f"{speaker.name} signing off"
        self.log(f"[goodbye] {speaker.name}: {line[:80]}")
        # Recorded like any other spoken line, so the transcript is a complete
        # record of what the channel actually heard. It is added before
        # playback for the same reason every other turn is: if playback is cut
        # short, the line still went out and belongs in the log.
        self.state.add_turn(Turn(speaker=speaker.name, text=line))
        await self._speak(speaker.name, line, wav)
        self.status = "stopped"

    def stop(self):
        self.running = False
        self.status = "stopping"
        # Cancel the half-finished next turn, or the loop would sit waiting
        # for an LLM round trip before it could exit and sign off.
        self._cancel_inflight()
        # Keep the prepared topic: restarting should not have to rebuild it.
        self.runtime.interrupt()
