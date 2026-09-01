"""Discord runtime.

The client owns an asyncio loop on its own thread; Gradio callbacks are
synchronous and hand work over with submit(). Everything that touches the
voice client runs on that loop.

Output only. There is no voice receive: Discord's DAVE end-to-end encryption
makes inbound Opus undecodable to third-party sinks, so real people join the
conversation by typing in a text channel (see DEADINTERNET.md).
"""
import asyncio
import base64
import binascii
import datetime as dt
import io
import logging
import random
import re
import threading
import time
from typing import List, Optional

import discord
import numpy as np

from .config import MAX_IMAGE_BYTES, Pin
from .pipeline import NULL_PIPELINE, PLAY

# How often the watchdog checks the voice connection still matches what the UI
# asked for. Discord terminates the call when the last person leaves (close
# code 4014/4022) and discord.py gives up permanently after one failed
# _potential_reconnect, so nothing rejoins without this.
VOICE_WATCH_INTERVAL = 3.0
# Rejoin backoff. Discord answers reconnect spam with 4021 (rate limited),
# which discord.py treats as fatal, so backing off matters.
REJOIN_BACKOFF = (2, 5, 10, 20, 30, 60)

# Persona mining. Each probe is one history page request; anchoring several of
# them at random points in the window is what stops the sample being nothing
# but last week's messages.
PROBE_LIMIT = 400
ANCHORS_PER_CHANNEL = 3
# Longer than this and a single message dominates the prompt.
MAX_SAMPLE_CHARS = 400
MIN_SAMPLE_CHARS = 4

# Discord's hard cap on how many options an autocomplete may offer. The roster
# is already larger than this, which is why /sayas autocompletes rather than
# enumerating: a static choice list cannot represent it at all.
MAX_CHOICES = 25

URL_RE = re.compile(r"https?://\S+")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
MENTION_RE = re.compile(r"<@[!&]?\d+>")


class _Mixer(discord.AudioSource):
    """Everything audible at once, summed into one outgoing stream.

    A voice client plays exactly one source. That single fact is why this
    class exists twice over:

      * an acknowledgement noise played the obvious way would stop whoever is
        mid-sentence, which is the opposite of what an acknowledgement is for;
      * and a second speaker cannot start without cutting off the first, so
        two people talking over each other is impossible by construction.

    So cues are summed into the frames already on their way out, and extra
    voices are added the same way -- as sources this mixer reads alongside the
    base and adds together. read() runs on discord.py's player thread while
    cue() and add_voice() are called from the event loop, hence the lock.

    Wrapping a base of None turns this into a plain cue player, for when
    nothing is talking.
    """

    # 20ms of 48kHz stereo 16-bit, which is one Opus frame.
    FRAME = 3840
    # Roughly two seconds of cues. A backlog longer than that is a stream of
    # notifications nobody can tell apart anyway.
    MAX_CUE = FRAME * 100
    # How many voices may pile on top of the base one. Past three or so it
    # stops being an argument and becomes noise nobody can pick a word out of.
    MAX_VOICES = 3
    # Overlaid voices come in a little under the one already talking, so the
    # original line stays followable and each addition reads as someone
    # cutting in rather than as the mix getting louder. Speech is normalised
    # to -20 dBFS RMS, so summing four at full scale would clip constantly.
    VOICE_GAIN = 0.78

    def __init__(self, base: Optional[discord.AudioSource], max_voices: int = MAX_VOICES):
        self._base = base
        self._lock = threading.Lock()
        self._cue = bytearray()
        self._voices: List[discord.AudioSource] = []
        self._max_voices = max(0, int(max_voices))

    def cue(self, pcm: bytes) -> bool:
        with self._lock:
            if len(self._cue) + len(pcm) > self.MAX_CUE:
                return False
            self._cue.extend(pcm)
            return True

    def add_voice(self, source: discord.AudioSource) -> bool:
        """Start reading `source` alongside whatever is already playing."""
        with self._lock:
            if len(self._voices) >= self._max_voices:
                return False
            self._voices.append(source)
            return True

    def voices(self) -> int:
        with self._lock:
            return len(self._voices)

    def is_opus(self) -> bool:
        return False

    @staticmethod
    def _pad(chunk: bytes) -> bytes:
        return chunk + b"\x00" * (_Mixer.FRAME - len(chunk))

    def read(self) -> bytes:
        data = self._base.read() if self._base else b""
        with self._lock:
            extra = list(self._voices)
            cue = bytes(self._cue[:self.FRAME])
            del self._cue[:self.FRAME]

        # Every overlaid voice is read every frame whether or not it has
        # anything left, so one that has finished is noticed and dropped
        # rather than being read forever.
        parts, spent = [], []
        for src in extra:
            chunk = src.read()
            if chunk:
                parts.append(chunk)
            else:
                spent.append(src)
        if spent:
            with self._lock:
                self._voices = [v for v in self._voices if v not in spent]
            for src in spent:
                try:
                    src.cleanup()
                except Exception:
                    pass

        if not cue and not parts:
            return data
        # Something is still going, so a short or empty base must be padded to
        # a full frame rather than returned as-is: a short read ends playback,
        # which would cut off whoever is still mid-word. Once everything is
        # empty this returns b"" and the player stops normally.
        mixed = np.frombuffer(self._pad(data) if len(data) < self.FRAME else data,
                              dtype="<i2").astype(np.int32)
        for chunk in parts:
            voice = np.frombuffer(self._pad(chunk) if len(chunk) < self.FRAME else chunk,
                                  dtype="<i2").astype(np.int32)
            mixed += (voice * self.VOICE_GAIN).astype(np.int32)
        if cue:
            mixed += np.frombuffer(self._pad(cue) if len(cue) < self.FRAME else cue,
                                   dtype="<i2").astype(np.int32)
        # A backstop, not the level control: VOICE_GAIN and MAX_VOICES are
        # what keep the sum in range, and clipping here should be rare.
        return np.clip(mixed, -32768, 32767).astype("<i2").tobytes()

    def cleanup(self):
        if self._base:
            self._base.cleanup()
        with self._lock:
            extra, self._voices = self._voices, []
        for src in extra:
            try:
                src.cleanup()
            except Exception:
                pass


class _LogBridge(logging.Handler):
    """Forward discord.py's own voice logging into the app log.

    The close code that explains a dropped call is only ever reported on
    discord.py's logger; without this a call terminating is invisible.
    """

    def __init__(self, sink):
        super().__init__(level=logging.INFO)
        self.sink = sink

    def emit(self, record):
        try:
            self.sink(f"[discord.{record.name.split('.')[-1]}] {record.getMessage()}")
        except Exception:  # never let logging break the client
            pass


def _readable(text: str) -> str:
    """Make message text safe to read aloud.

    discord.py's clean_content already turns mention ids into names, but
    leaves the @ and # sigils on them, which the TTS voices as "at" and
    "hash". Custom emoji are dropped outright -- there is no way to say them.
    """
    text = CUSTOM_EMOJI_RE.sub(" ", text)
    # Backstop for anything clean_content did not get to. It resolves unknown
    # ids to "@deleted-user" rather than leaving digits, so this should never
    # fire -- but a raw id read aloud is bad enough to guard against twice.
    text = MENTION_RE.sub(" ", text)
    text = re.sub(r"[@#](?=\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_sample(text: str) -> str:
    """Strip the parts of a chat message that say nothing about how someone
    talks. A message that is only a link or only emoji has nothing left."""
    text = URL_RE.sub("", text)
    text = CUSTOM_EMOJI_RE.sub("", text)
    text = MENTION_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_SAMPLE_CHARS]


class DiscordRuntime:
    # Which output this is, for the places that have to tell them apart --
    # the status line, and the messages that tell you what to connect.
    kind = "discord"

    def __init__(self, token: str, on_text=None, on_topic=None, log=print):
        self.token = token
        self.log = log
        self.on_text = on_text
        # Crowd-sourced topics, now via the /topics slash command.
        self.on_topic = on_topic
        # Set by the app once the director exists. /sayas needs both: the
        # roster to offer, and somewhere to send the line.
        self.on_say = None
        self.speaker_names = None

        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        self.client = discord.Client(intents=intents)
        # discord.Client has no tree of its own -- that lives on commands.Bot.
        # Attaching one here gets slash commands without migrating the whole
        # runtime to a different base class.
        self.tree = discord.app_commands.CommandTree(self.client)
        self.command_stats = {"synced": 0, "topics": 0, "sayas": 0,
                              "last": "", "last_error": None}

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.voice: Optional[discord.VoiceClient] = None
        self.ready = threading.Event()
        self.error: Optional[str] = None
        self._playback_done: Optional[asyncio.Event] = None
        # The live mixer, while something is playing. Cues are added to it.
        self._mixer: Optional[_Mixer] = None
        # The channel the UI asked for, kept so the watchdog can get back into
        # it after Discord terminates the call.
        self.target_channel_id: Optional[int] = None
        self.auto_rejoin = True
        self.pipeline = NULL_PIPELINE
        # How many voices may talk over the one already speaking. Pushed on by
        # app.sync_voice_settings(); bot.py never reads settings itself.
        self.overlap_max = _Mixer.MAX_VOICES
        self._watchdog: Optional[asyncio.Task] = None
        self._rejoin_fails = 0
        self._next_rejoin = 0.0
        self.voice_debug = {"state": "not joined", "humans": 0, "names": "",
                            "channel": "", "rejoins": 0, "last_drop": None,
                            "last_error": None}
        # Attachment objects for the current pin pool, keyed by message id.
        # Pin lives in config.py and must not import discord, so the handle
        # that can actually download the bytes is parked here instead.
        self._attachments = {}
        # Text is the only input path, so its health is worth showing.
        self.text_stats = {"seen": 0, "forwarded": 0, "last": "",
                           "last_channel": "", "last_error": None}
        self.topic_stats = {"accepted": 0, "rejected": 0, "last": ""}

        self._register_commands()

        @self.client.event
        async def on_ready():
            self.log(f"[discord] connected as {self.client.user}")
            # Per-guild, not global: a global sync can take an hour to show up
            # in clients, while a guild sync is effectively immediate. This bot
            # lives in one server at a time, so there is nothing to gain from
            # global registration and an hour to lose.
            for guild in self.client.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    self.command_stats["synced"] = len(synced)
                    self.log(f"[discord] {len(synced)} command(s) ready in {guild.name}")
                except Exception as e:
                    self.command_stats["last_error"] = f"sync failed: {e}"
                    self.log(f"[discord] command sync failed in {guild.name}: {e}")
            self.ready.set()

        @self.client.event
        async def on_message(message):
            if message.author.bot:
                return

            # Only the built-in text chat of the voice channel we are sitting
            # in. Accepting the whole guild meant any message in any of ~30
            # channels interrupted the conversation, including ones nobody in
            # the call could see.
            in_scope = bool(self.voice and self.voice.channel
                            and message.channel.id == self.voice.channel.id)
            if not in_scope:
                self.text_stats["out_of_scope"] = (
                    self.text_stats.get("out_of_scope", 0) + 1)
                return
            # Same treatment as pins: the router and the personas should see
            # "bran", not a mention id.
            body = _readable(getattr(message, "clean_content", None)
                             or message.content or "")
            self.text_stats["seen"] += 1
            self.text_stats["last"] = f"{message.author.display_name}: {body}"[:140]
            self.text_stats["last_channel"] = getattr(message.channel, "name", "?")
            if self.on_text and body:
                self.text_stats["forwarded"] += 1
                self.on_text(message.author.display_name, body)

    # ---- slash commands ---------------------------------------------------
    def _register_commands(self):
        """Declared once at construction; synced per-guild on ready."""
        tree = self.tree

        @tree.command(name="topics",
                      description="Add something for the hosts to talk about")
        @discord.app_commands.describe(text="What should they discuss?")
        async def topics(interaction: discord.Interaction, text: str):
            body = _readable(text or "")
            if not body:
                self.topic_stats["rejected"] += 1
                await self._reply(interaction, "That came through empty - say a bit more.")
                return
            who = interaction.user.display_name
            self.topic_stats["accepted"] += 1
            self.topic_stats["last"] = f"{who}: {body}"[:140]
            self.command_stats["topics"] += 1
            self.command_stats["last"] = f"/topics {who}: {body}"[:140]
            if self.on_topic:
                self.on_topic(who, body)
            self.log(f"[topic] submitted by {who}: {body[:80]}")
            await self._reply(interaction, f"Queued: **{body[:180]}**")

        @tree.command(name="sayas", description="Put a line in a host's mouth")
        @discord.app_commands.describe(speaker="Who says it", text="What they say")
        async def sayas(interaction: discord.Interaction, speaker: str, text: str):
            body = (text or "").strip()
            if not body:
                await self._reply(interaction, "Nothing to say.")
                return
            if not (self.voice and self.voice.is_connected()):
                # The line would generate and then go nowhere. Say so rather
                # than accepting it into a void.
                await self._reply(interaction,
                                  "I am not in a voice channel - nobody would hear it.")
                return
            if not self.on_say:
                await self._reply(interaction, "The director is not running.")
                return
            # Autocomplete suggests; it does not constrain. Discord submits
            # whatever was typed, and an unknown name reached _handle_manual,
            # which does `if not speaker: return` -- so the line disappeared
            # while the reply still said it had been queued.
            resolved = self._resolve_speaker(speaker)
            if resolved is None:
                await self._reply(
                    interaction,
                    f"I do not have a voice called **{speaker[:80]}**. "
                    "Pick one from the list the command offers.")
                return
            speaker = resolved
            try:
                # say_now hops to this loop and blocks on the result, so it
                # cannot run on the loop it is waiting for.
                # overlap=True: two people spamming this should talk over
                # each other, not queue up behind one another. The director
                # still has the final say via the setting.
                await asyncio.get_running_loop().run_in_executor(
                    None, self.on_say, speaker, body, True)
            except Exception as e:
                self.command_stats["last_error"] = str(e)
                await self._reply(interaction, f"Could not queue that - {e}")
                return
            self.command_stats["sayas"] += 1
            self.command_stats["last"] = f"/sayas {speaker}: {body}"[:140]
            self.log(f"[sayas] {interaction.user.display_name} -> {speaker}: {body[:60]}")
            await self._reply(interaction, f"**{speaker}** will say it.")

        @sayas.autocomplete("speaker")
        async def speaker_options(interaction: discord.Interaction, current: str):
            # Autocomplete, not choices: choices are static and capped at 25,
            # and the roster is both larger than that and edited while the bot
            # is running. This is re-evaluated on every keystroke instead.
            names = list(self.speaker_names() if self.speaker_names else [])
            q = (current or "").lower()
            hits = [n for n in names if q in n.lower()] if q else names
            return [discord.app_commands.Choice(name=n[:100], value=n)
                    for n in hits[:MAX_CHOICES]]

    def _resolve_speaker(self, name: str):
        """A typed name -> the roster's spelling of it, or None.

        Case-insensitive because the value comes from a text box: someone who
        types a name rather than picking it should not be punished for the
        capital letter.
        """
        names = list(self.speaker_names() if self.speaker_names else [])
        want = (name or "").strip()
        if not want:
            return None
        for n in names:
            if n == want:
                return n
        for n in names:
            if n.lower() == want.lower():
                return n
        return None

    async def _reply(self, interaction, message: str):
        """Answer only the person who ran the command."""
        try:
            await interaction.response.send_message(message, ephemeral=True)
        except Exception as e:
            self.log(f"[discord] could not reply to the command: {e}")

    async def _react(self, message, emoji):
        """Acknowledge a submission. Needs Add Reactions; not worth failing over."""
        try:
            await message.add_reaction(emoji)
        except Exception:
            pass

    # ---- lifecycle -----------------------------------------------------
    def start(self, timeout: float = 45.0) -> bool:
        if self.thread and self.thread.is_alive():
            return True

        # Surface the voice close codes; they are the only explanation of why
        # a call ended, and they only exist on discord.py's logger.
        bridge = _LogBridge(self.log)
        for name in ("discord.voice_state", "discord.gateway"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.INFO)
            if not any(isinstance(h, _LogBridge) for h in lg.handlers):
                lg.addHandler(bridge)

        def runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self.client.start(self.token))
            except Exception as e:
                self.error = str(e)
                self.log(f"[discord] {e}")
                self.ready.set()

        self.thread = threading.Thread(target=runner, daemon=True, name="discord")
        self.thread.start()
        self.ready.wait(timeout)
        return self.client.is_ready()

    def submit(self, coro, timeout: float = 120):
        if not self.loop:
            raise RuntimeError("discord runtime not started")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def submit_async(self, coro):
        """Schedule on the Discord loop and hand back the future without
        waiting. Long jobs (history mining) poll this so the UI can report
        progress instead of freezing for minutes."""
        if not self.loop:
            raise RuntimeError("discord runtime not started")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self):
        if self.loop and self.client.is_ready():
            asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)

    # ---- channels ------------------------------------------------------
    def voice_channels(self):
        out = []
        for guild in self.client.guilds:
            for ch in guild.voice_channels:
                out.append((f"{guild.name} / {ch.name}", ch.id))
        return out

    def text_channels(self):
        out = []
        for guild in self.client.guilds:
            for ch in guild.text_channels:
                out.append((f"{guild.name} / #{ch.name}", ch.id))
        return out

    # ---- pins ------------------------------------------------------------
    @staticmethod
    def _image_attachment(message):
        """First usable image on a message, or None.

        Discord reports the type it sniffed at upload; trusting the filename
        instead would try to base64 a 200 MB video into a prompt.
        """
        for att in getattr(message, "attachments", []):
            ctype = (att.content_type or "").lower()
            if not ctype.startswith("image/"):
                continue
            if ctype.startswith("image/svg"):
                continue  # not a raster image; no model will take it
            if att.size and att.size > MAX_IMAGE_BYTES:
                continue
            return att
        return None

    async def fetch_pins(self, channel_ids) -> List[Pin]:
        """Pinned messages pooled across every selected channel.

        Returns [] rather than raising: a deleted channel or a permissions
        change should not take down the conversation loop. Pins with neither
        text nor an image are dropped -- there would be nothing to talk about.
        """
        if isinstance(channel_ids, (str, int)):
            channel_ids = [channel_ids]
        out: List[Pin] = []
        seen = set()
        self._attachments = {}
        for raw in channel_ids or []:
            try:
                ch = self.client.get_channel(int(raw))
            except (TypeError, ValueError):
                continue
            if ch is None or not hasattr(ch, "pins"):
                continue
            try:
                pins = await ch.pins()
            except Exception as e:
                self.log(f"[topic] cannot read pins in {getattr(ch, 'name', raw)}: {e}")
                continue
            label = f"#{getattr(ch, 'name', raw)}"
            for m in reversed(pins):  # discord returns newest first
                if m.id in seen:
                    continue
                seen.add(m.id)
                att = self._image_attachment(m)
                # clean_content resolves mentions to names; raw content would
                # hand the TTS "<@!000000000000000000>" to read out as digits.
                body = _readable(getattr(m, "clean_content", None) or m.content or "")
                if not body and att is None:
                    continue
                if att is not None:
                    self._attachments[m.id] = att
                out.append(Pin(
                    author=getattr(m.author, "display_name", None) or str(m.author),
                    text=body,
                    channel=label,
                    message_id=m.id,
                    image_url=att.url if att else "",
                    image_type=(att.content_type or "image/png").split(";")[0] if att else "",
                ))
        return out

    async def load_image(self, pin: Pin) -> bool:
        """Download a pin's image and base64 it in place. Done only for the
        pin that was actually chosen."""
        if not pin.image_url or pin.image_b64:
            return bool(pin.image_b64)
        att = self._attachments.get(pin.message_id)
        if att is None:
            self.log("[topic] image attachment went stale; re-pin to refresh")
            return False
        try:
            data = await att.read()
        except Exception as e:
            self.log(f"[topic] image download failed: {e}")
            return False
        if len(data) > MAX_IMAGE_BYTES:
            self.log(f"[topic] image too large ({len(data)} bytes), skipping")
            return False
        try:
            pin.image_b64 = base64.b64encode(data).decode("ascii")
        except (binascii.Error, ValueError) as e:
            self.log(f"[topic] could not encode image: {e}")
            return False
        return True

    # ---- persona mining ----------------------------------------------------
    @staticmethod
    def _matches(author, handle: str) -> bool:
        wanted = handle.strip().lstrip("@").lower()
        if not wanted:
            return False
        if wanted.isdigit() and str(author.id) == wanted:
            return True
        candidates = {
            (getattr(author, "name", "") or "").lower(),
            (getattr(author, "global_name", "") or "").lower(),
            (getattr(author, "display_name", "") or "").lower(),
            str(author).lower(),
        }
        return wanted in {c for c in candidates if c}

    async def mine_messages(self, handle: str, years: float, scan_cap: int,
                            pool_target: int, rng=None, progress=None):
        """Collect messages written by `handle` from the last `years`.

        Reading each channel newest-first would sample only the last few weeks,
        so most probes are anchored at random points inside the window. Every
        request is one page; the scan cap bounds how many we are willing to
        make on a busy server.
        """
        rng = rng or random
        progress = progress if progress is not None else {}
        now = dt.datetime.now(dt.timezone.utc)
        start = now - dt.timedelta(days=365.25 * years)

        channels = []
        for guild in self.client.guilds:
            me = guild.me
            for ch in guild.text_channels:
                perms = ch.permissions_for(me) if me else None
                if perms is None or (perms.read_message_history and perms.view_channel):
                    channels.append(ch)

        probes = []
        for ch in channels:
            probes.append((ch, None))  # most recent window
            for _ in range(ANCHORS_PER_CHANNEL):
                frac = rng.random()
                probes.append((ch, start + (now - start) * frac))
        rng.shuffle(probes)

        progress.update({"state": "scanning", "scanned": 0, "found": 0,
                         "probes": len(probes), "probes_done": 0,
                         "channels": len(channels), "resolved": ""})

        samples, seen_text, scanned = [], set(), 0
        resolved = ""
        for ch, anchor in probes:
            if scanned >= scan_cap or len(samples) >= pool_target:
                break
            try:
                if anchor is None:
                    it = ch.history(limit=PROBE_LIMIT, after=start, oldest_first=False)
                else:
                    it = ch.history(limit=PROBE_LIMIT, after=anchor, oldest_first=True)
                async for m in it:
                    scanned += 1
                    if m.author.bot or not self._matches(m.author, handle):
                        continue
                    if not resolved:
                        resolved = (getattr(m.author, "display_name", None)
                                    or getattr(m.author, "name", "") or handle)
                        progress["resolved"] = resolved
                    text = _clean_sample(m.content or "")
                    key = text.lower()
                    if len(text) < MIN_SAMPLE_CHARS or key in seen_text:
                        continue
                    seen_text.add(key)
                    samples.append(text)
            except discord.Forbidden:
                continue
            except Exception as e:
                progress["last_error"] = f"{getattr(ch, 'name', '?')}: {e}"[:160]
                continue
            finally:
                progress["probes_done"] = progress.get("probes_done", 0) + 1
                progress["scanned"] = scanned
                progress["found"] = len(samples)

        progress["state"] = "scanned"
        return resolved or handle, samples, {"scanned": scanned,
                                             "channels": len(channels),
                                             "pool": len(samples)}

    # ---- voice -------------------------------------------------------------
    def occupants(self, channel_id=None):
        """(count, names) of real people in the target voice channel.

        Counted from the guild's voice states rather than the member list, so
        this works without the privileged members intent. Members are only
        looked up to skip other bots and to get a display name.
        """
        cid = channel_id if channel_id is not None else self.target_channel_id
        if cid is None:
            return 0, []
        try:
            ch = self.client.get_channel(int(cid))
        except (TypeError, ValueError):
            return 0, []
        if ch is None or not hasattr(ch, "voice_states"):
            return 0, []
        me = self.client.user.id if self.client.user else 0
        names = []
        for uid in ch.voice_states:
            if uid == me:
                continue
            member = ch.guild.get_member(uid) if ch.guild else None
            if member is not None and member.bot:
                continue
            names.append(member.display_name if member else str(uid))
        return len(names), names

    def humans_present(self) -> bool:
        return self.occupants()[0] > 0

    async def _check_voice(self):
        """One watchdog pass: keep the voice connection matching intent."""
        cid = self.target_channel_id
        if cid is None:
            self.voice_debug["state"] = "not joined"
            return
        humans, names = self.occupants(cid)
        self.voice_debug["humans"] = humans
        self.voice_debug["names"] = ", ".join(names[:6])

        if self.connected():
            self.voice_debug["state"] = ("connected" if humans
                                         else "connected, channel empty")
            self._rejoin_fails = 0
            return

        if self.voice_debug["state"] not in ("dropped", "waiting for someone to join"):
            self.voice_debug["last_drop"] = time.strftime("%H:%M:%S")
            self.log("[voice] connection lost - Discord ends the call when the "
                     "channel empties, and discord.py does not come back on its own")
        if not self.auto_rejoin:
            self.voice_debug["state"] = "dropped (auto-rejoin off)"
            return
        if humans == 0:
            # Rejoining an empty channel just gets the call terminated again,
            # and the retries would earn a 4021 rate limit.
            self.voice_debug["state"] = "waiting for someone to join"
            return

        self.voice_debug["state"] = "dropped"
        now = time.monotonic()
        if now < self._next_rejoin:
            return
        try:
            name = await self._connect_to(cid)
        except Exception as e:
            delay = REJOIN_BACKOFF[min(self._rejoin_fails, len(REJOIN_BACKOFF) - 1)]
            self._rejoin_fails += 1
            self._next_rejoin = now + delay
            self.voice_debug["last_error"] = f"rejoin failed: {e}"[:160]
            self.log(f"[voice] rejoin failed ({e}) - retrying in {delay}s")
            return
        self._rejoin_fails = 0
        self.voice_debug["rejoins"] += 1
        self.voice_debug["state"] = "connected"
        self.voice_debug["last_error"] = None
        self.log(f"[voice] rejoined '{name}' - {humans} listening")

    async def _watch_voice(self):
        while True:
            await asyncio.sleep(VOICE_WATCH_INTERVAL)
            try:
                await self._check_voice()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # a watchdog that dies is worse than useless
                self.voice_debug["last_error"] = str(e)[:160]
                self.log(f"[voice] watchdog error: {e}")

    async def _connect_to(self, channel_id: int):
        ch = self.client.get_channel(int(channel_id))
        if not isinstance(ch, discord.VoiceChannel):
            raise RuntimeError("not a voice channel")
        # A voice client left over from a terminated call would make connect()
        # raise "already connected"; force it down first.
        if self.voice and not self.voice.is_connected():
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass
            self.voice = None
        if self.voice and self.voice.is_connected():
            await self.voice.move_to(ch)
        else:
            self.voice = await ch.connect()
        self.voice_debug["channel"] = ch.name
        return ch.name

    async def _join(self, channel_id: int):
        name = await self._connect_to(channel_id)
        self.target_channel_id = int(channel_id)
        self._rejoin_fails = 0
        self._next_rejoin = 0.0
        if not self._watchdog or self._watchdog.done():
            self._watchdog = self.loop.create_task(self._watch_voice())
        return name

    def join(self, channel_id: int) -> str:
        return self.submit(self._join(channel_id))

    async def _leave(self):
        # Clear the target first, or the watchdog would treat a deliberate
        # departure as a drop and drag us straight back in.
        self.target_channel_id = None
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        self.voice_debug["state"] = "not joined"
        if self.voice:
            if self.voice.is_playing():
                self.voice.stop()
            await self.voice.disconnect()
            self.voice = None

    def leave(self):
        self.submit(self._leave())

    def connected(self) -> bool:
        return bool(self.voice and self.voice.is_connected())

    # ---- playback ------------------------------------------------------
    async def play_wav(self, wav: bytes, timeout: Optional[float] = None):
        """Play WAV bytes and wait for the end. ffmpeg handles the
        24 kHz mono -> 48 kHz stereo conversion Discord requires.

        `timeout` is a wedge guard, not a length limit. If the connection dies
        mid-clip, discord.py's player blocks in wait_until_connected for up to
        VoiceClient.timeout (60s by default) before giving up, and the turn
        loop would sit inside this await the whole time.
        """
        if not self.voice or not self.voice.is_connected():
            raise RuntimeError("not connected to a voice channel")
        if self.voice.is_playing():
            self.voice.stop()

        done = asyncio.Event()
        self._playback_done = done
        loop = asyncio.get_running_loop()

        source = discord.FFmpegPCMAudio(io.BytesIO(wav), pipe=True, options="-loglevel quiet")
        # Everything goes out through the mixer so a cue can be added to the
        # stream mid-sentence without stopping it.
        mixer = _Mixer(source, max_voices=self.overlap_max)
        self._mixer = mixer

        def after(err):
            if err:
                self.log(f"[discord] playback: {err}")
            if self._mixer is mixer:
                self._mixer = None
            loop.call_soon_threadsafe(done.set)

        self.voice.play(mixer, after=after)
        # Timed, because "playback" being the long pole is a completely
        # different diagnosis from the LLM being slow: it means the clip
        # really is that long, not that anything is stuck.
        with self.pipeline.track(PLAY):
            await self._await_playback(done, timeout)

    async def _await_playback(self, done, timeout):
        if not timeout:
            await done.wait()
            return
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except asyncio.TimeoutError:
            self.log(f"[voice] playback did not finish within {timeout:g}s - "
                     "cutting it and moving on")
            try:
                self.voice.stop()
            except Exception:
                pass

    def overlay(self, wav: bytes) -> str:
        """Add a voice on top of whatever is playing, without stopping it.

        -> "on" it is now mixed in, "full" too many already, "idle" nothing is
        playing to talk over, "offline" not in a channel.

        Must be called on the event loop. There is no await between looking at
        is_playing() and adding to the mixer, which is what makes "idle" a
        truthful answer the caller can act on rather than a race.
        """
        if not (self.voice and self.voice.is_connected()):
            return "offline"
        mixer = self._mixer
        if mixer is None or not self.voice.is_playing():
            return "idle"
        source = discord.FFmpegPCMAudio(io.BytesIO(wav), pipe=True,
                                        options="-loglevel quiet")
        if mixer.add_voice(source):
            return "on"
        # Cleaned up rather than dropped: FFmpegPCMAudio spawns the process in
        # its constructor, so one that is never read is a stray ffmpeg.
        try:
            source.cleanup()
        except Exception:
            pass
        return "full"

    def talking(self) -> int:
        """How many voices are audible right now, base included."""
        if not (self.voice and self.voice.is_playing()):
            return 0
        mixer = self._mixer
        return 1 + (mixer.voices() if mixer is not None else 0)

    def play_cue(self, pcm: bytes) -> bool:
        """Sound a short cue without interrupting speech. -> was it played.

        Called from the director's thread. If something is talking the cue is
        summed into the frames already on their way out; if not, it is played
        on its own.
        """
        if not (self.voice and self.voice.is_connected() and self.loop):
            return False
        mixer = self._mixer
        if mixer is not None and self.voice.is_playing():
            return mixer.cue(pcm)
        self.loop.call_soon_threadsafe(self._cue_alone, pcm)
        return True

    def _cue_alone(self, pcm: bytes):
        """Play a cue with nothing underneath it. On the event loop."""
        if not (self.voice and self.voice.is_connected()) or self.voice.is_playing():
            # Speech started in the gap since play_cue looked. It gets the
            # channel; a missed notification beats a truncated sentence.
            return
        lone = _Mixer(None)
        lone.cue(pcm)
        try:
            # No after= that touches _playback_done: this playback is not a
            # turn, and releasing play_wav()'s wait would end a turn early.
            self.voice.play(lone)
        except Exception as e:
            self.log(f"[voice] could not play the cue: {e}")

    def interrupt(self):
        """Cut off the current utterance -- used when a real user types.
        stop() fires the after-callback, which releases play_wav()."""
        if self.voice and self.voice.is_playing() and self.loop:
            self.loop.call_soon_threadsafe(self.voice.stop)
