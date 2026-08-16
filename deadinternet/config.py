"""Shared state: speaker roster, mode, transcript.

Mutated from the Gradio thread, read from the Discord event loop, so every
mutation goes through the lock. Speakers persist to JSON; reference clips live
next to it in voices/ so the roster survives a tts-server restart (registered
voices are held in server memory only and are lost when it exits).
"""
import glob
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

from .events import SPEECH as EV_SPEECH, EventLog

# Above this the TTS server starts spilling and per-frame decode collapses --
# measured 4.2 ms/frame with headroom vs 34 ms/frame at 99% VRAM.
VRAM_WARN_FRACTION = 0.85

# Persona mining: how far back to read, how many of that person's messages to
# quote in the finished prompt, and the ceiling on how many messages we are
# willing to page through looking for them. The scan cap is what keeps a
# build from running for an hour on a busy server.
PERSONA_YEARS = 2.0
PERSONA_SAMPLES = 40
PERSONA_SCAN_CAP = 40000
# Stop early once this many of their messages are in hand; 40 are sampled out
# of the pool, and a wider pool past this point buys very little.
PERSONA_POOL_TARGET = 800

# Attachments larger than this are skipped rather than base64'd into a prompt.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def vram_info() -> Optional[Tuple[float, float]]:
    """(used_gb, total_gb) for the largest AMD card, or None if unreadable."""
    best = None
    for dev in glob.glob("/sys/class/drm/card*/device"):
        try:
            with open(os.path.join(dev, "mem_info_vram_used")) as f:
                used = int(f.read())
            with open(os.path.join(dev, "mem_info_vram_total")) as f:
                total = int(f.read())
        except (OSError, ValueError):
            continue
        if total > 0 and (best is None or total > best[1]):
            best = (used / 1e9, total / 1e9)
    return best


def gpu_busy_percent() -> Optional[int]:
    """GPU utilisation 0-100, or None if it cannot be read.

    Read straight from sysfs rather than shelling out. On AMD the amdgpu driver
    exposes gpu_busy_percent, which is what radeontop samples anyway -- reading
    the file is a few microseconds where spawning radeontop is tens of
    milliseconds, and this runs on every status tick. nvidia-smi is used as a
    fallback for NVIDIA cards, where there is no sysfs equivalent; it is only
    reached when no amdgpu node exists, so the subprocess cost is not paid on
    this machine.
    """
    for path in sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")):
        try:
            with open(path) as f:
                return max(0, min(100, int(f.read().strip())))
        except (OSError, ValueError):
            continue
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0)
        if out.returncode == 0 and out.stdout.strip():
            return max(0, min(100, int(out.stdout.strip().splitlines()[0])))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES_DIR = os.path.join(ROOT, "voices")
# Overridable so a test or a scratch script can never write over the real
# roster: any code that constructs State() picks this up, and a script that
# forgets to set it is the accident this exists to prevent.
CONFIG_PATH = os.environ.get(
    "DEADINTERNET_CONFIG", os.path.join(ROOT, "deadinternet.json"))
ENV_PATH = os.path.join(ROOT, ".env")


def load_env() -> bool:
    """Read .env into the environment. Real environment variables win, so
    an export still overrides the file. Returns whether the file existed."""
    if not os.path.exists(ENV_PATH):
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH, override=False)
    except ImportError:
        # Minimal fallback so a missing dependency does not break startup.
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip("\"'"))
    return True

MODE_PODCAST = "podcast"
MODE_INTERACTIVE = "interactive"
MODE_MANUAL = "manual"
MODES = [MODE_PODCAST, MODE_INTERACTIVE, MODE_MANUAL]

DEFAULT_PERSONA = "You are {name}. Keep replies to one or two short sentences, conversational and spoken aloud."

# Known-good conversation tuning, arrived at by listening to the output rather
# than by taste: 0 gap makes speakers cut each other off, 1.5 temperature
# wanders mid-sentence, and 60 tokens truncated lines often enough to matter.
# The Settings dataclass defaults below must match these.
RECOMMENDED = {
    "gap_seconds": 0.4,
    "temperature": 0.9,
    "num_predict": 80,
    "max_history": 12,
}

# Help text shown under each control.
HELP = {
    "gap_seconds":
        "Silence left between speakers. 0 makes them cut each other off; 0.3-0.6 "
        "sounds like people talking. The next line is generated during playback, "
        "so this is pacing only - it does not add latency.",
    "temperature":
        "Randomness of the wording. Below ~0.7 personas get repetitive and bland; "
        "above ~1.1 they start losing the thread mid-sentence.",
    "num_predict":
        "Length cap per utterance, in tokens. Roughly 80 = two spoken sentences. "
        "Lines cut off by the cap are trimmed back to the last complete sentence, "
        "so a low value shortens replies rather than mangling them.",
    "max_history":
        "How many previous turns each speaker sees. Higher keeps the thread but "
        "grows every prompt, so turns take longer and the LLM costs more.",
}


@dataclass
class Speaker:
    name: str
    # Reference clip + transcript used to re-register the clone on the
    # tts-server at boot. ref_text empty => speaker-embedding-only clone.
    ref_wav: str = ""
    ref_text: str = ""
    persona: str = ""
    enabled: bool = True
    # Verbal tic: catchphrases this character blurts out. One per line (commas
    # also accepted), picked at random when the roll succeeds.
    stims: str = ""
    # Percentage of this speaker's turns that carry a stim.
    stim_chance: float = 0.0

    def system_prompt(self) -> str:
        return (self.persona or DEFAULT_PERSONA).replace("{name}", self.name)

    def stim_list(self):
        raw = (self.stims or "").replace(",", "\n")
        return [p.strip() for p in raw.split("\n") if p.strip()]


@dataclass
class Turn:
    speaker: str
    text: str
    # "bot" | "user" -- the router needs to know which lines came from a
    # real person so it can address them.
    kind: str = "bot"


# What {placeholders} each spoken template understands. Shown in the UI so the
# available variables are discoverable rather than guessed at.
TEMPLATE_VARS = {
    "opening_template": {
        "{topic}": "the current topic",
        "{speaker}": "who is saying the line",
    },
    "topic_template": {
        "{topic}": "the new topic text",
        "{author}": "who pinned it",
        "{channel}": "the channel it was pinned in",
        "{speaker}": "who is saying the line",
    },
    "topic_image_template": {
        "{description}": "what the image shows",
        "{author}": "who pinned it",
        "{channel}": "the channel it was pinned in",
        "{speaker}": "who is saying the line",
    },
    "topic_web_template": {
        "{topic}": "the search result",
        "{subject}": "the subject that was searched",
        "{speaker}": "who is saying the line",
    },
    "topic_crowd_template": {
        "{topic}": "what they submitted",
        "{author}": "who submitted it",
        "{speaker}": "who is saying the line",
    },
    "goodbye_template": {
        "{topic}": "the topic we finished on",
        "{speaker}": "who is saying the line",
    },
}


def render_template(template: str, rng=None, **values) -> str:
    """Pick one line from a multi-line template and fill in {placeholders}.

    One sentence per line, chosen at random, so a bot that switches topic
    twenty times in a session does not say the exact same words every time.
    Unknown placeholders are left alone rather than raising -- a typo in a
    template should not take the conversation down mid-sentence.
    """
    lines = [ln.strip() for ln in (template or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    line = (rng.choice(lines) if rng is not None else lines[0])
    for key, val in values.items():
        line = line.replace("{%s}" % key, str(val if val is not None else ""))
    # Tidy up the gap left by a placeholder that resolved to nothing.
    return re.sub(r"\s{2,}", " ", line).strip()


@dataclass
class Pin:
    """A pinned message eligible to become the topic.

    Pins are pooled across several channels, so `channel` is carried along to
    make the debug panel legible. `image` is populated only for the pin that
    actually gets picked -- downloading every attachment on every rotation
    would waste bandwidth on pins we never use.
    """
    author: str
    text: str
    channel: str = ""
    message_id: int = 0
    # Set when the pin carries an image attachment we could hand to a
    # multimodal model. The bytes are fetched lazily; see Director._load_image.
    image_url: str = ""
    image_type: str = ""
    image_b64: str = ""

    @property
    def has_image(self) -> bool:
        return bool(self.image_url)

    def label(self) -> str:
        """What goes in the topic field."""
        if self.text:
            return self.text
        return "an image" if self.has_image else ""


@dataclass
class Settings:
    # "ollama" (local, shares the GPU with TTS) or "openai" (remote, leaves
    # the whole card free for speech).
    provider: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:e4b"
    openai_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-luna"
    tts_url: str = "http://127.0.0.1:8080"
    # Used to relaunch tts-server from the UI when it is down. Paths are
    # relative to the repo root.
    tts_binary: str = "build/tts-server"
    tts_model: str = "models/qwen-talker-1.7b-base-Q8_0.gguf"
    tts_codec: str = "models/qwen-tokenizer-12hz-F32.gguf"
    tts_lang: str = "English"
    # Microphone input on the Run tab. Built by ./setup-whisper.sh; absent by
    # default, in which case the mic just reports it is not set up. CPU only --
    # see the note at the top of deadinternet/transcribe.py.
    whisper_binary: str = "whisper.cpp/build/bin/whisper-cli"
    whisper_model: str = "whisper.cpp/models/ggml-small.en.bin"
    # whisper.cpp language code, not the TTS language name.
    whisper_lang: str = "en"
    mode: str = MODE_PODCAST
    topic: str = "whatever comes to mind"
    # Who pinned the current topic, and where. Empty when the topic was typed
    # in by hand -- there is no sender to credit.
    topic_author: str = ""
    # Turn pacing. gap_seconds is dead air deliberately left between
    # utterances; generation for the next turn overlaps playback anyway.
    gap_seconds: float = 0.4
    max_history: int = 12
    num_predict: int = 80
    temperature: float = 0.9
    # Cut the bots off the moment a real person posts a message, instead of
    # letting the current line finish first. Text is the only input path --
    # there is no speech recognition (see DEADINTERNET.md).
    barge_in: bool = True
    # Discord ends the call when the last person leaves the voice channel and
    # discord.py does not reconnect, so a watchdog gets back in once somebody
    # returns. Turn off only if you want to control joining by hand.
    auto_rejoin: bool = True
    # Stay silent while nobody is in the channel, instead of talking to an
    # empty room and holding the GPU.
    pause_when_empty: bool = True
    # Level all speakers to a common loudness before playback.
    normalize_audio: bool = True
    target_dbfs: float = -20.0
    # Hard ceiling on how long any single utterance may occupy the channel.
    # Enforced twice: the generation request is capped in frames so the GPU
    # never renders audio we would throw away, and the clip is truncated
    # before playback in case the server ignores the cap.
    speech_limit_enabled: bool = True
    max_speech_seconds: float = 25.0
    # Periodically pull a new topic from the pinned messages of a Discord text
    # channel. Time-based rather than token-based: turn lengths vary wildly, so
    # a clock gives predictable segment lengths.
    topic_rotation: bool = False
    # Several channels can feed the pool, so a short pin list in one channel
    # does not force the same few topics round again.
    topic_channel_ids: List[str] = field(default_factory=list)
    topic_interval_minutes: float = 10.0
    # Hand image attachments to the LLM instead of describing them. Needs a
    # multimodal model; falls back to text automatically when one rejects it.
    topic_images: bool = True
    # A new topic on top of the old transcript tends to drag the conversation
    # backwards, so by default the context is wiped at each switch.
    topic_clears_context: bool = True
    # Have a speaker read the handover out loud on each switch.
    topic_announce: bool = True
    # Cut the current speaker off and switch immediately, instead of letting
    # the line finish first. Also cancels the pre-generated next turn.
    topic_switch_instant: bool = True
    # Spoken once when the conversation starts: states the topic and tells
    # people how to join in.
    opening_enabled: bool = True
    opening_template: str = (
        "Alright, we're live. Today's topic: {topic}.\n"
        "If you want to jump in, type a message in the voice channel chat "
        "and one of us will answer you."
    )
    # ---- topic sources ----------------------------------------------------
    # Relative weights for where the next topic comes from. A source with
    # weight 0 is never drawn. They do not have to add up to 100 -- each is
    # taken as a share of the total, so 50/50/0 and 1/1/0 behave the same.
    source_pins_weight: float = 100.0
    source_web_weight: float = 0.0
    source_crowd_weight: float = 0.0
    # Web search: one subject per line, searched round-robin. Results are
    # walked round-robin too, so a subject is not exhausted before moving on.
    web_subjects: str = ""
    web_results_per_search: int = 8
    # Topics people submitted with "/topic ..." in Discord, oldest first.
    crowd_topics: List[str] = field(default_factory=list)
    crowd_max: int = 100

    # Read out on every topic switch. One line per sentence; a random line is
    # picked each time so repeated switches don't sound identical.
    topic_template: str = (
        "Okay, new topic by {author}: {topic}\n"
        "Right, {author} wants us on this: {topic}\n"
        "Moving on. {author} pinned this one: {topic}"
    )
    # Appended to the topic announcement when the pin is an image, so the
    # channel understands why the subject changed to something wordless.
    topic_image_template: str = (
        "We're looking at an image. {description}\n"
        "There's a picture up. {description}"
    )
    # Announcements for the non-pin sources, which have no {author}/{channel}.
    topic_web_template: str = (
        "Here's something from the web about {subject}: {topic}\n"
        "Found this on {subject}: {topic}\n"
        "New one, searched up on {subject}: {topic}"
    )
    topic_crowd_template: str = (
        "Topic from {author}: {topic}\n"
        "{author} put this one in: {topic}\n"
        "Someone asked for this: {topic}"
    )
    # Spoken when Stop is pressed, before the director shuts down.
    goodbye_enabled: bool = True
    goodbye_template: str = (
        "That's all from us. Thanks for listening.\n"
        "Alright, we're done here. See you next time.\n"
        "That's a wrap. Catch you later."
    )
    # Seeds the RNG that picks pin order and next speaker. 0 = fresh entropy
    # on every start; any other value makes the sequence reproducible.
    rng_seed: int = 0


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.speakers: List[Speaker] = []
        self.settings = Settings()
        self.transcript: List[Turn] = []
        # Set when a real user speaks in interactive mode; the director
        # drains this instead of continuing the podcast.
        self.pending_user: Optional[Turn] = None
        # The Diagnostics event log. Owned by the app, attached here so
        # add_turn can record spoken lines without every caller knowing about
        # it. None is a working state -- a State built by a test or a script
        # keeps its transcript and simply logs nothing.
        self.events: Optional[EventLog] = None
        self.load()

    # ---- persistence -------------------------------------------------
    def load(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH) as f:
                blob = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        with self.lock:
            self.speakers = [Speaker(**s) for s in blob.get("speakers", [])]
            saved = dict(blob.get("settings", {}))
            # Pins used to come from a single channel. Carry an old config
            # forward rather than silently losing the channel it was using.
            legacy = saved.pop("topic_channel_id", "")
            if legacy and not saved.get("topic_channel_ids"):
                saved["topic_channel_ids"] = [str(legacy)]
            saved["topic_channel_ids"] = [
                str(c) for c in (saved.get("topic_channel_ids") or []) if str(c).strip()
            ]
            known = {f for f in Settings.__dataclass_fields__}
            self.settings = Settings(**{k: v for k, v in saved.items() if k in known})

    def save(self):
        with self.lock:
            blob = {
                "speakers": [asdict(s) for s in self.speakers],
                "settings": asdict(self.settings),
            }
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(blob, f, indent=2)
        os.replace(tmp, CONFIG_PATH)

    # ---- roster ------------------------------------------------------
    def get(self, name: str) -> Optional[Speaker]:
        with self.lock:
            return next((s for s in self.speakers if s.name == name), None)

    def upsert(self, sp: Speaker):
        with self.lock:
            for i, existing in enumerate(self.speakers):
                if existing.name == sp.name:
                    self.speakers[i] = sp
                    break
            else:
                self.speakers.append(sp)
        self.save()

    def remove(self, name: str):
        with self.lock:
            self.speakers = [s for s in self.speakers if s.name != name]
        self.save()

    def active(self) -> List[Speaker]:
        """Speakers eligible to take turns in a conversation."""
        with self.lock:
            return [s for s in self.speakers if s.enabled and s.ref_wav]

    def restorable(self) -> List[Speaker]:
        """Everything with a clip on disk, including voices cloned purely for
        the Generate tab. These all get re-registered at boot -- the server
        keeps registrations in memory only."""
        with self.lock:
            return [s for s in self.speakers if s.ref_wav]

    def names(self) -> List[str]:
        with self.lock:
            return [s.name for s in self.speakers]

    # ---- transcript --------------------------------------------------
    def add_turn(self, turn: Turn):
        with self.lock:
            self.transcript.append(turn)
            # Unbounded growth would slowly inflate every prompt.
            limit = self.settings.max_history * 4
            if len(self.transcript) > limit:
                self.transcript = self.transcript[-limit:]
        # Outside the lock: the log takes its own, and holding both in a fixed
        # order here would be one more deadlock to reason about for no gain.
        # Every spoken line goes through this method, so one hook catches the
        # lot without touching the director's six call sites.
        if self.events is not None:
            who = "you" if turn.kind == "user" else turn.speaker
            self.events.add(EV_SPEECH, f"{who}: {turn.text}")

    def recent(self, n: Optional[int] = None) -> List[Turn]:
        with self.lock:
            n = n or self.settings.max_history
            return list(self.transcript[-n:])

    def clear_transcript(self):
        with self.lock:
            self.transcript = []

    def last_bot_speaker(self) -> Optional[str]:
        with self.lock:
            for t in reversed(self.transcript):
                if t.kind == "bot":
                    return t.speaker
        return None
