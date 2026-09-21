"""qwentts.cpp web app: voice cloning + Dead Internet Mode.

    export DISCORD_TOKEN=...        # only needed for Dead Internet Mode
    .venv-app/bin/python app.py

Talks to a running tts-server (see --tts) and Ollama (see --ollama). The
Discord client is optional: without a token the cloning tabs work normally.
"""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from deadinternet.config import (ENV_PATH, LOG_MAX_BYTES, LOG_PATH,
                                 OUTPUT_DISCORD, OUTPUT_LOCAL,
                                 TTS_CPU, TTS_DEVICES, TTS_GPU,
                                 PERSONA_POOL_TARGET, PERSONA_SAMPLES,
                                 PERSONA_SCAN_CAP, PERSONA_YEARS, ROOT,
                                 VRAM_WARN_FRACTION, State, gpu_busy_percent,
                                 load_env, plain_md, vram_info)
from deadinternet import comedy
from deadinternet.events import EventLog
from deadinternet.director import Director
from deadinternet.llm import (PROVIDER_OLLAMA, PROVIDER_OPENAI, OllamaClient,
                              OpenAIClient, sampling_options)
from deadinternet.local import LocalRuntime, available as local_available
from deadinternet.local import sinks as local_sinks
from deadinternet.pipeline import Pipeline
from deadinternet.rawfeed import NULL_TAP, RawFeed
from deadinternet.transcribe import Whisper
from deadinternet.tts import TTSClient
from deadinternet.ui import APP_CSS, build

# How long to wait for a freshly launched tts-server to answer.
TTS_BOOT_TIMEOUT = 120


def _is_local(url: str) -> bool:
    """Is this tts-server on this machine? Used only to decide whether the URL
    is worth showing -- localhost is the default and says nothing."""
    return bool(re.search(r"//(127\.0\.0\.1|localhost|\[::1\])\b", url or ""))


class DeadInternetApp:
    def __init__(self, args):
        # Before anything reads DISCORD_TOKEN / OPENAI_API_KEY.
        self.env_file_found = load_env()
        self.state = State()
        # Attached before anything can speak, so no line is missed. State keeps
        # the reference so add_turn can log without its callers knowing.
        self.events = EventLog()
        self.state.events = self.events
        s = self.state.settings
        if args.tts:
            s.tts_url = args.tts
        if args.ollama:
            s.ollama_url = args.ollama
        if args.model:
            s.ollama_model = args.model

        # One tracker, shared by every client that can be the reason
        # nothing is coming out. Built before them, because they take it.
        self.pipeline = Pipeline()
        self.tts = TTSClient(s.tts_url)
        self.tts.pipeline = self.pipeline
        self.whisper = Whisper(ROOT, s.whisper_binary, s.whisper_model, s.whisper_lang)
        self.whisper.pipeline = self.pipeline
        self._whisper_proc = None
        # Kept alongside the active client so the UI can list Ollama models
        # even while OpenAI is selected.
        self.ollama = OllamaClient(s.ollama_url, s.ollama_model)
        # Built before the first client, because make_client attaches it.
        self.raw = RawFeed()
        self.llm = self.make_client(s.provider)
        self.runtime = None
        self.director = None
        self._channels = []
        self._text_channels = []
        self._log_tail = []
        # log() is called from the director thread, the discord thread and
        # gradio's workers.
        self._log_lock = threading.Lock()
        self._tts_proc = None
        # Serialises launching the server and re-uploading the roster, so the
        # background boot and a Start press can never launch two servers or
        # register the same voice twice. Re-entrant because try_start does
        # both steps and boot_tts holds it across the pair.
        self._tts_lock = threading.RLock()
        self._tts_boot = None

    # ---- llm backends ------------------------------------------------------
    def make_client(self, provider):
        s = self.state.settings
        if provider == PROVIDER_OPENAI:
            client = OpenAIClient(s.openai_url, s.openai_model)
        else:
            client = self.ollama
            client.model = s.ollama_model
            # Read here rather than at construction: the Ollama client is
            # shared and long-lived, so rebuild_llm() is what makes a settings
            # change take effect without restarting the app.
            client.think = s.thinking
            client.sampling = sampling_options(s)
        # Attaching the tap is the whole of what turns streaming on; the null
        # one leaves both providers on their original buffered request.
        client.tap = self.raw if s.raw_feed else NULL_TAP
        client.pipeline = self.pipeline
        if not s.raw_feed:
            # Emptied rather than left as it was: nothing will write to it
            # again, and a box still showing the last generation an hour later
            # reads as a live feed that has hung.
            self.raw.clear()
        return client

    def rebuild_llm(self):
        """Swap the active backend. The director reads self.llm through its
        own reference, so update that too if one is running."""
        self.llm = self.make_client(self.state.settings.provider)
        if self.director:
            self.director.llm = self.llm
        return self.llm

    # ---- tts server ----------------------------------------------------------
    def tts_alive(self, timeout=2.0) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self.state.settings.tts_url}/v1/models", timeout=timeout
            ) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def start_tts_server(self):
        """Launch tts-server if it is not answering. Returns (ok, message).

        Starting it while VRAM is free matters: if the driver evicts its
        Vulkan buffers later they never migrate back, and speech stays ~5x
        slow until the process is restarted.
        """
        with self._tts_lock:
            return self._start_tts_server()

    def _start_tts_server(self):
        if self.tts_alive():
            return True, "tts-server already running."

        s = self.state.settings
        binary = os.path.join(ROOT, s.tts_binary)
        model = os.path.join(ROOT, s.tts_model)
        codec = os.path.join(ROOT, s.tts_codec)
        for label, path in (("binary", binary), ("model", model), ("codec", codec)):
            if not os.path.exists(path):
                return False, f"cannot start tts-server - {label} not found at {path}"

        port = s.tts_url.rsplit(":", 1)[-1]
        cmd = [binary, "--model", model, "--codec", codec,
               "--host", "127.0.0.1", "--port", port, "--lang", s.tts_lang]
        env = dict(os.environ)
        if s.tts_device == TTS_CPU:
            # One binary, both backends: ggml enumerates no Vulkan device when
            # this is empty and falls back to CPU. GGML_VULKAN_DISABLE and
            # GGML_VK_DISABLE were both tried and are ignored.
            env["GGML_VK_VISIBLE_DEVICES"] = ""
        self.log(f"[tts] backend: {s.tts_device}")
        log_path = os.path.join(ROOT, "tts-server.log")
        self.log(f"[tts] launching: {' '.join(cmd)}")
        try:
            # Rolled the same way app.log is. This one is opened "ab" and
            # handed to a subprocess, so nothing was ever capping it -- it had
            # reached 15 MB of mostly the same twenty startup lines.
            if (os.path.exists(log_path)
                    and os.path.getsize(log_path) > LOG_MAX_BYTES):
                os.replace(log_path, log_path + ".1")
            logfile = open(log_path, "ab")
            self._tts_proc = subprocess.Popen(
                cmd, cwd=ROOT, stdout=logfile, stderr=logfile, env=env,
                start_new_session=True,
            )
        except OSError as e:
            return False, f"cannot start tts-server - {e}"

        deadline = time.time() + TTS_BOOT_TIMEOUT
        while time.time() < deadline:
            if self.tts_alive():
                self.log("[tts] server up")
                return True, f"Started tts-server (log: {log_path})."
            if self._tts_proc.poll() is not None:
                return False, f"tts-server exited immediately - see {log_path}"
            time.sleep(1.0)
        return False, f"tts-server did not answer within {TTS_BOOT_TIMEOUT}s - see {log_path}"

    # ---- whisper server ----------------------------------------------------
    def whisper_alive(self, timeout=1.5) -> bool:
        s = self.state.settings
        if not s.whisper_server_url:
            return False
        try:
            r = self.http_get(s.whisper_server_url.rstrip("/") + "/", timeout)
            return r is not None
        except Exception:
            return False

    def start_whisper_server(self):
        """Launch whisper-server if it is wanted and not already up.

        Non-fatal throughout: transcription falls back to spawning whisper-cli
        per clip, which is what it always did. A microphone that is slower than
        it could be beats one that reports an error.
        """
        s = self.state.settings
        if not s.whisper_server:
            self.whisper.server_url = ""
            return False, "resident whisper is off"
        if self.whisper_alive():
            self.whisper.server_url = s.whisper_server_url
            return True, "whisper-server already running."

        binary = os.path.join(ROOT, s.whisper_server_binary)
        model = os.path.join(ROOT, s.whisper_model)
        for label, path in (("binary", binary), ("model", model)):
            if not os.path.exists(path):
                self.whisper.server_url = ""
                return False, (f"no resident whisper - {label} not found at "
                               f"{path}; run ./setup-whisper.sh")

        port = s.whisper_server_url.rsplit(":", 1)[-1]
        cmd = [binary, "-m", model, "--host", "127.0.0.1", "--port", port,
               "-l", s.whisper_lang, "-t", str(self.whisper.threads)]
        try:
            logfile = open(os.path.join(ROOT, "whisper-server.log"), "ab")
            self._whisper_proc = subprocess.Popen(
                cmd, cwd=ROOT, stdout=logfile, stderr=logfile,
                start_new_session=True)
        except OSError as e:
            self.whisper.server_url = ""
            return False, f"could not start whisper-server - {e}"

        deadline = time.time() + 60
        while time.time() < deadline:
            if self.whisper_alive():
                self.whisper.server_url = s.whisper_server_url
                self.log(f"[whisper] resident on {s.whisper_server_url}")
                return True, "whisper-server up."
            if self._whisper_proc.poll() is not None:
                self.whisper.server_url = ""
                return False, "whisper-server exited - see whisper-server.log"
            time.sleep(0.5)
        self.whisper.server_url = ""
        return False, "whisper-server did not answer in 60s"

    @staticmethod
    def http_get(url, timeout):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                r.read(1)
                return r
        except urllib.error.HTTPError:
            # Answering at all is what "alive" means; whisper-server has no
            # health endpoint and 404s the root.
            return True
        except (urllib.error.URLError, OSError):
            return None

    def tts_pids(self):
        """PIDs of any running tts-server, ours or not.

        Ours is usually self._tts_proc, but not always: the server is started
        detached and survives the app, so after an app restart the one that is
        running was inherited rather than launched. /proc is the only thing
        that knows about both.
        """
        binary = os.path.join(ROOT, self.state.settings.tts_binary)
        found = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    argv = f.read().split(b"\0")
            except OSError:
                continue
            if argv and argv[0].decode("utf-8", "replace") == binary:
                found.append(int(entry))
        return found

    def stop_tts_server(self, timeout=15.0):
        """Stop tts-server and wait for it to actually let go of the card."""
        pids = self.tts_pids()
        if not pids:
            self._tts_proc = None
            return False, "tts-server was not running."
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline and self.tts_pids():
            time.sleep(0.3)
        left = self.tts_pids()
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self._tts_proc = None
        # VRAM is released asynchronously by the driver, and relaunching
        # before it lands is the whole eviction problem in miniature.
        time.sleep(1.5)
        return True, f"Stopped tts-server ({len(pids)} process(es))."

    def restart_tts_server(self, device=None):
        """Stop, wait for the card, and bring it back. -> (ok, message).

        Exists because the state it recovers from is invisible and permanent:
        a tts-server that allocated while the card was full runs about 3x
        slower for the rest of its life, and freeing VRAM afterwards does
        nothing. The only cure is relaunching it on an empty card, and before
        this that meant a terminal and remembering the order.
        """
        s = self.state.settings
        if device in TTS_DEVICES and device != s.tts_device:
            s.tts_device = device
            self.state.save()
        with self._tts_lock:
            _, stopped = self.stop_tts_server()
            before = vram_info()
            ok, msg = self._start_tts_server()
            if not ok:
                return False, f"{stopped} {msg}"
            # A fresh server has an empty registry.
            self.tts._registered.clear()
            problems = self.tts.ensure_registered(self.state.restorable())
        after = vram_info()
        note = ""
        if before:
            note = (f" Card was {before[0]:.1f}/{before[1]:.1f} GB when it "
                    f"allocated")
            if before[0] / before[1] > VRAM_WARN_FRACTION:
                note += (" - **that is full enough to have evicted it again**; "
                         "free VRAM and restart it once more")
            note += "."
        voices = len(self.state.restorable()) - len(problems)
        return True, (f"{stopped} Restarted on **{s.tts_device.upper()}**, "
                      f"{voices} voices registered.{note}")

    def boot_tts(self, autostart=True):
        """Get tts-server running and the roster registered on it.

        Called at app startup, which is the best possible moment to launch
        it: the card is at its emptiest before Ollama loads a model, and a
        server that starts with free VRAM keeps its Vulkan buffers there for
        the life of the process (see DEADINTERNET.md). Blocks for as long as
        the server takes to answer, so run it off the main thread.
        """
        with self._tts_lock:
            if not self.tts_alive():
                if not autostart:
                    n = len(self.state.restorable())
                    self.log(f"[boot] tts-server down - {n} saved voices will be "
                             "registered when it starts")
                    return False, "tts-server not running (autostart disabled)."
                ok, msg = self._start_tts_server()
                self.log(f"[boot] {msg}")
                if not ok:
                    return False, msg
                # A server we just launched has an empty registry, and so may
                # one that was restarted under a long-lived app.
                self.tts._registered.clear()

            roster = self.state.restorable()
            if not roster:
                return True, "tts-server up, no saved voices."
            problems = self.tts.ensure_registered(roster)
            good = len(roster) - len(problems)
            self.log(f"[boot] restored {good}/{len(roster)} voices")
            for p in problems:
                self.log(f"[boot] {p}")
            return not problems, f"tts-server up, {good}/{len(roster)} voices registered."

    def start_tts_boot(self, autostart=True):
        """Kick boot_tts off in the background so the web UI comes up now.

        Launching the server can take a couple of minutes on a cold model,
        and there is nothing about it the page needs to render.
        """
        if self._tts_boot and self._tts_boot.is_alive():
            return self._tts_boot
        self._tts_boot = threading.Thread(
            target=self._boot_tts_quietly, args=(autostart,),
            name="tts-boot", daemon=True)
        self._tts_boot.start()
        return self._tts_boot

    def _boot_tts_quietly(self, autostart):
        # Nothing is waiting on this thread, so a traceback would otherwise
        # vanish into the void.
        try:
            self.boot_tts(autostart)
        except Exception as e:
            self.log(f"[boot] tts-server startup failed: {e}")
        # After tts-server, deliberately: it is the one that must get the card
        # while it is empty, and whisper's ~0.45 GB is small enough to land
        # afterwards without evicting anything.
        try:
            ok, msg = self.start_whisper_server()
            self.log(f"[boot] {msg}")
        except Exception as e:
            self.log(f"[boot] whisper-server startup failed: {e}")

    # ---- logging ---------------------------------------------------------
    def log(self, msg):
        """Everything the app has to say: stdout, app.log, and the UI tail.

        The file is the point. Before this, log() only printed -- so a run
        started in a terminal instead of with `>> app.log` left no record, and
        the one time that mattered (a show that degenerated overnight) there
        was nothing to read back.
        """
        line = str(msg)
        print(line, flush=True)
        self._log_tail.append(line)
        del self._log_tail[:-40]
        stamped = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n"
        # Never let logging be the thing that breaks the app: a full disk or a
        # read-only checkout should cost the record, not the show.
        try:
            with self._log_lock:
                if (os.path.exists(LOG_PATH)
                        and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES):
                    os.replace(LOG_PATH, LOG_PATH + ".1")
                with open(LOG_PATH, "a") as f:
                    f.write(stamped)
        except OSError:
            pass

    # ---- banner ----------------------------------------------------------
    def banner(self):
        """The first thing the action line says, before you have done anything.

        Kept short. It shares a line with every button's confirmation, and the
        old version spent most of it restating a localhost URL and a file
        extension that are the same on every install.
        """
        url = self.state.settings.tts_url
        # Only worth naming when speech is coming from somewhere other than
        # this machine, which is the case that would surprise you.
        where = "" if _is_local(url) else f" at `{url}`"
        try:
            model = re.sub(r"\.gguf$", "", self.tts.model_id())
            return f"**tts-server** ready{where} — `{model}`"
        except Exception:
            if self._tts_boot and self._tts_boot.is_alive():
                return ("**tts-server starting** — watch the log on "
                        "**Diagnostics**, then press **Refresh voices**.")
            return (f"**tts-server not running**{where or ''} — press **Start**, "
                    "or launch it yourself (see DEADINTERNET.md).")

    def discord_hint(self):
        where = "`.env`" if self.env_file_found else "the environment"
        if os.environ.get("DISCORD_TOKEN"):
            return f"Token loaded from {where}. Press **Connect bot**."
        return ("**No `DISCORD_TOKEN` found.** Create a bot at discord.com/developers, "
                "enable the Message Content and Voice intents, then put the token in "
                f"`{ENV_PATH}` as `DISCORD_TOKEN=...` and restart the app. "
                "Everything except the Discord connection works without it.")

    def env_summary(self):
        """Which secrets are present -- never their values."""
        bits = []
        for label, key in (("Discord", "DISCORD_TOKEN"), ("OpenAI", "OPENAI_API_KEY")):
            bits.append(f"{label}: {'set' if os.environ.get(key) else 'missing'}")
        src = ".env loaded" if self.env_file_found else "no .env file"
        return f"{src} | " + " | ".join(bits)

    # ---- outputs -----------------------------------------------------------
    def _attach(self, runtime):
        """Hang a director and the shared services off a fresh runtime.

        Both outputs go through here. Written out once because the last time
        this was inline the local path would have been a copy that silently
        lacked the pipeline, or /sayas, or the raw feed.
        """
        self.runtime = runtime
        self.director = Director(self.state, self.tts, self.llm, runtime,
                                 log=self.log)
        self.director.pipeline = self.pipeline
        runtime.pipeline = self.pipeline
        runtime.on_text = self.director.push_user_text
        # /sayas needs the roster to offer and somewhere to send the line.
        runtime.on_say = self.director.say_now
        # Everyone with a clip, NOT state.active(). "Enabled" means eligible to
        # take turns on its own; say_now is an explicit override that works in
        # any mode and jumps the queue, and _handle_manual looks the speaker up
        # with state.get() without ever consulting the flag. Filtering by it
        # made /sayas useless in exactly the mode it is most wanted: manual is
        # where you untick everyone so the cast stops talking by itself, and
        # the whole roster vanished from the command with it. try_start()
        # already draws this same distinction.
        runtime.speaker_names = self.sayas_roster
        self.sync_voice_settings()
        return runtime

    def output_hint(self):
        """What to press to make anything audible, for the current output."""
        if self.state.settings.output == OUTPUT_LOCAL:
            return "Start the local output on the **Outputs** tab first."
        return "Connect the bot on the Outputs tab first."

    def stop_output(self):
        """Tear down whatever is playing, so the other output can take over."""
        if self.director:
            self.director.stop()
        rt = self.runtime
        if rt is not None and hasattr(rt, "stop"):
            try:
                rt.stop()
            except Exception as e:
                self.log(f"[output] could not stop cleanly: {e}")
        self.runtime = None
        self.director = None

    def active_output(self):
        """Which output is live right now: "discord", "local" or "".

        Discord counts from the moment the bot is logged in, not just while it
        is in a voice channel -- a connected bot is still taking /sayas and
        chat, and pulling the runtime out from under it would be a surprise.
        """
        rt = self.runtime
        if self.bot_ready():
            return "discord"
        if getattr(rt, "kind", "") == "local" and rt.connected():
            return "local"
        return ""

    def bot_ready(self):
        """Is the Discord bot logged in? Safe whatever holds the runtime slot:
        the local output has no `client` at all."""
        rt = self.runtime
        return (getattr(rt, "kind", "") == "discord"
                and rt.client is not None and rt.client.is_ready())

    def disconnect_discord(self):
        """Log the bot out and free the runtime slot. -> (ok, message)."""
        if getattr(self.runtime, "kind", "") != "discord":
            return False, "The bot is not connected."
        self.stop_output()
        self._channels = []
        self._text_channels = []
        return True, "Bot disconnected. Local output can be started now."

    def start_local(self):
        """Bring up local audio output. -> (ok, message)."""
        why = local_available()
        if why:
            return False, f"**Local output unavailable** - {why}"
        if self.active_output() == "discord":
            # Refused rather than swapped: one output at a time, and switching
            # should be something you meant to do, not a side effect.
            return False, ("**The Discord bot is connected.** Press "
                           "*Disconnect* under Discord first.")
        s = self.state.settings
        if (self.runtime is not None
                and getattr(self.runtime, "kind", "") == "local"
                and self.runtime.connected()):
            if self.runtime.sink != (s.local_sink or ""):
                # Changing device means a new paplay target; simplest correct
                # thing is to rebuild rather than mutate one mid-clip.
                self.stop_output()
            else:
                return True, f"Already playing to **{s.local_sink or 'system default'}**."
        # One output at a time -- the director holds a single runtime. Only a
        # dead runtime (a Discord login that failed) can still be here.
        if self.runtime is not None:
            self.stop_output()

        if s.local_sink and s.local_sink not in {name for name, _ in local_sinks()}:
            # Refused rather than started. paplay exits at once on a device
            # that does not exist and the show carries on in silence, with
            # "output ready" in the log -- and a null sink made with pactl is
            # gone after every reboot, so this is the normal way to get here.
            self.log(f"[local] no such output device: {s.local_sink}")
            return False, (
                f"**No output device called `{s.local_sink}`.** A virtual sink "
                "does not survive a reboot - press *Create* under *Virtual microphone* "
                "on this tab, or pick another device, then press this again.")

        runtime = LocalRuntime(sink=s.local_sink, log=self.log, settings=s)
        self._attach(runtime)
        if not runtime.start():
            err = runtime.error or "could not start local audio"
            self.runtime = None
            self.director = None
            return False, f"**Local output failed** - {err}"
        s.output = OUTPUT_LOCAL
        self.state.save()
        return True, (f"Local output ready, playing to "
                      f"**{s.local_sink or 'system default'}**. "
                      "Press Start on the Run tab.")

    def local_sink_choices(self):
        return [(desc, name) for name, desc in local_sinks()]

    # ---- discord ----------------------------------------------------------
    def connect_discord(self):
        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            return False, self.discord_hint()
        if self.active_output() == "local":
            return False, ("**Local output is playing.** Press *Stop* under "
                           "This machine first.")
        if self.bot_ready():
            self._channels = self.runtime.voice_channels()
            self._text_channels = self.runtime.text_channels()
            return True, f"Already connected as `{self.runtime.client.user}`."

        from deadinternet.bot import DiscordRuntime

        # A stopped local runtime can still hold the slot; clear it.
        if self.runtime is not None and getattr(self.runtime, "kind", "") != "discord":
            self.stop_output()
        self._attach(DiscordRuntime(token, log=self.log,
                                    on_topic=self.submit_crowd_topic))
        self.state.settings.output = OUTPUT_DISCORD
        self.state.save()

        if not self.runtime.start():
            err = self.runtime.error or "failed to connect (check the token and intents)"
            return False, f"**Discord connection failed** - {err}"

        self._channels = self.runtime.voice_channels()
        self._text_channels = self.runtime.text_channels()
        return True, (f"Connected as `{self.runtime.client.user}` - "
                      f"{len(self._channels)} voice channels, "
                      f"{len(self._text_channels)} text channels.")

    def sayas_roster(self):
        """Who /sayas offers, in the order it offers them.

        Discord caps an autocomplete at 25 and this roster is larger, so with
        an empty query some speakers are simply not on the list until you type
        a letter. Enabled first means the ones currently in the show are the
        ones guaranteed to be visible; the rest are a keystroke away.
        """
        roster = self.state.restorable()
        return ([sp.name for sp in roster if sp.enabled]
                + [sp.name for sp in roster if not sp.enabled])

    def resync_voices(self):
        """Re-upload the roster to tts-server. Returns (ok, message).

        The server keeps registrations in memory only, so restarting it --
        by hand, or after a crash -- silently empties the registry while the
        app still believes every voice is live. ensure_registered compares
        against the server's actual list and re-uploads the difference.
        """
        if not self.tts_alive():
            if self._tts_boot and self._tts_boot.is_alive():
                return False, ("tts-server is still starting up - give it a "
                               "moment and press Refresh voices again.")
            return False, (f"tts-server is not answering at "
                           f"{self.state.settings.tts_url}. Press Start on the Run "
                           "tab and the app will launch it.")
        roster = self.state.restorable()
        if not roster:
            return True, "No saved voices to register."
        with self._tts_lock:
            problems = self.tts.ensure_registered(roster)
        good = len(roster) - len(problems)
        msg = f"{good}/{len(roster)} voices registered."
        if problems:
            msg += " Problems - " + "; ".join(problems[:3])
        return not problems, msg

    def submit_crowd_topic(self, who: str, text: str):
        """Someone ran /topics in Discord. Called from the bot thread."""
        text = (text or "").strip()
        if not text:
            return
        s = self.state.settings
        with self.state.lock:
            # Author is packed in with the text so the announcement can credit
            # them; \x1f because it cannot appear in a Discord message.
            s.crowd_topics.append(f"{who}\x1f{text}")
            # Oldest go first, so trimming takes from the front.
            if len(s.crowd_topics) > int(s.crowd_max or 100):
                del s.crowd_topics[:-int(s.crowd_max or 100)]
        self.state.save()

    def channel_choices(self):
        return self._channels

    def text_channel_choices(self):
        return self._text_channels

    def _channel_id(self, label):
        for name, cid in self._channels:
            if name == label:
                return cid
        return None

    def text_channel_id(self, label):
        for name, cid in self._text_channels:
            if name == label:
                return cid
        return None

    def text_channel_ids(self, labels):
        """Labels from the pins checklist -> channel ids, skipping any that
        no longer exist."""
        ids = []
        for label in labels or []:
            cid = self.text_channel_id(label)
            if cid is not None:
                ids.append(str(cid))
        return ids

    def text_channel_labels(self, ids):
        """The reverse, for repopulating the checklist from a saved config."""
        wanted = {str(i) for i in ids or []}
        return [name for name, cid in self._text_channels if str(cid) in wanted]

    def topic_report(self):
        s = self.state.settings
        d = self.director.topic_debug if self.director else {}
        if not s.topic_rotation:
            return f"_Rotation off. Topic: {s.topic[:120]}_"
        prepared = getattr(self.director, "_prepared", None) if self.director else None
        if prepared is None:
            ready = "building..." if self.director and getattr(
                self.director, "_prepare_task", None) else "-"
        else:
            ready = (f"{prepared.pin.label()[:60]!r}"
                     + (" (announcement synthesised)" if prepared.wav else ""))
        rows = [
            ("interval", f"every {s.topic_interval_minutes:g} min"),
            ("pin channels", len(s.topic_channel_ids)),
            ("clears context", "yes" if s.topic_clears_context else "no"),
            ("pins in pool", d.get("pins_seen", 0)),
            ("last switch", d.get("last_switch") or "-"),
            ("next switch in", f"{d.get('next_in')}s" if d.get("next_in") is not None else "-"),
            ("next topic ready", ready),
            ("last switch was instant", "yes" if d.get("prepared") else "no"),
            ("current topic", (d.get("current") or s.topic)[:140]),
            ("pinned by", s.topic_author or "-"),
            ("image", d.get("image") or "-"),
            ("image description",
             (self.director.topic_image_desc or "-")[:160] if self.director else "-"),
            ("last error", d.get("last_error") or "-"),
        ]
        return "\n".join(f"- **{k}**: {v}" for k, v in rows)

    def join(self, label):
        if not self.bot_ready():
            return "Connect the bot first."
        cid = self._channel_id(label)
        if cid is None:
            return "Pick a voice channel."
        self.sync_voice_settings()
        try:
            name = self.runtime.join(cid)
        except Exception as e:
            return f"Join failed - {e}"
        n = self.runtime.occupants()[0]
        note = (f"Joined **{name}** - {n} listening." if n
                else f"Joined **{name}**, currently empty"
                     + (" - paused until someone arrives."
                        if self.state.settings.pause_when_empty else "."))
        return note + (" Real people join in by typing in a text channel in "
                       "this server.")

    def sync_voice_settings(self):
        """Push the connection-behaviour settings onto a live runtime."""
        if self.runtime:
            self.runtime.auto_rejoin = bool(self.state.settings.auto_rejoin)
            self.runtime.overlap_max = max(
                1, int(self.state.settings.sayas_overlap_max))

    # ---- persona mining ------------------------------------------------------
    def persona_progress(self, handle):
        """Kick off a history scan. Returns (future, progress_dict) so the UI
        can stream progress instead of blocking for minutes."""
        if not self.bot_ready():
            raise RuntimeError("Connect the bot on the Outputs tab first.")
        if not (handle or "").strip():
            raise RuntimeError("Enter a Discord handle.")
        progress = {}
        coro = self.runtime.mine_messages(
            handle.strip(), PERSONA_YEARS, PERSONA_SCAN_CAP, PERSONA_POOL_TARGET,
            progress=progress,
        )
        return self.runtime.submit_async(coro), progress

    def write_persona(self, resolved, samples, note=""):
        """Turn a pool of real messages into a system prompt.

        The quoted messages are kept in the prompt alongside the generated
        description: a summary tells the model who someone is, but the raw
        lines are what make it sound like them.
        """
        import random as _random

        chosen = (_random.sample(samples, PERSONA_SAMPLES)
                  if len(samples) > PERSONA_SAMPLES else list(samples))
        persona = self.llm.build_persona(resolved, chosen)
        if not persona:
            raise RuntimeError(
                "the LLM returned nothing - check the model is loaded and not a "
                "reasoning-only model")
        quoted = "\n".join(f'- "{c}"' for c in chosen)
        body = (
            f"{persona}\n\n"
            f"Here are real messages {resolved} wrote, as a reference for their "
            "voice. Match the vocabulary, opinions and attitude, but speak in "
            "ordinary spoken sentences rather than copying the typing style:\n"
            f"{quoted}"
        )
        return body, len(chosen)

    def leave(self):
        if not self.runtime:
            return "Not connected."
        try:
            self.runtime.leave()
        except Exception as e:
            return f"Leave failed - {e}"
        return "Left the voice channel."

    # ---- director ----------------------------------------------------------
    def try_start(self):
        """Bring the director up. Returns (ok, message) so callers other than
        the Start button -- notably the manual Say box -- can reuse it."""
        if self.director and self.director.running:
            return True, ""
        if not self.director:
            return False, self.output_hint()

        manual = self.state.settings.mode == "manual"
        roster = self.state.active() if not manual else self.state.restorable()
        if not roster:
            return False, "Configure at least one speaker with a reference clip."

        prefix = ""
        # Held across both steps: if the startup boot is still launching the
        # server or uploading voices, wait for it rather than starting a
        # second server or racing it through the registry.
        with self._tts_lock:
            if not self.tts_alive():
                ok, msg = self._start_tts_server()
                if not ok:
                    return False, f"**{msg}**"
                prefix = msg + " "
                # A fresh server has an empty registry.
                self.tts._registered.clear()

            # Manual lines are typed, so no LLM is needed for them.
            if not manual:
                ok, why = self.llm.available()
                if not ok:
                    return False, f"{prefix}**LLM unavailable - {why}**"

            problems = self.tts.ensure_registered(self.state.restorable())
        if problems:
            return False, "Voice registration failed - " + "; ".join(problems)
        if not manual and not self.runtime.connected():
            return False, "Join a voice channel first."

        self.director.start()
        return True, prefix

    def start_director(self):
        already = bool(self.director and self.director.running)
        ok, msg = self.try_start()
        if not ok:
            return msg
        if already:
            return f"Already running in **{self.state.settings.mode}** mode."
        prefix = msg
        msg = f"{prefix}Started in **{self.state.settings.mode}** mode."
        info = vram_info()
        # Only meaningful for the local backend -- OpenAI does not touch VRAM.
        if (info and info[0] / info[1] > VRAM_WARN_FRACTION
                and self.state.settings.provider == PROVIDER_OLLAMA):
            msg += (f" **Warning: {info[0]:.1f}/{info[1]:.1f} GB VRAM in use.** The driver "
                    "evicts tts-server's buffers to host memory and speech runs about 3x "
                    "slower - and it does **not** recover when the card frees up again. "
                    "Run `ollama stop <model>` on anything you are not using, then restart "
                    "tts-server so it reallocates on an empty card.")
        return msg

    def stop_director(self):
        if not self.director:
            return "Not running."
        self.director.stop()
        return "Stopped."

    def status_line(self):
        """The header strip. Bullet-separated, because pipes read as table
        syntax in markdown and the eye does not group on them."""
        bits = [f"mode: **{self.state.settings.mode}**"]
        local = getattr(self.runtime, "kind", "") == "local"
        if local:
            # "0 listening" is true of a sound card and says nothing. What
            # matters locally is which device the audio is going to.
            sink = self.runtime.sink or "system default"
            bits.append(f"out: **{sink}**" if self.runtime.connected()
                        else "out: local **stopped**")
        elif self.runtime and self.runtime.connected():
            n = self.runtime.occupants()[0]
            bits.append(f"voice: **connected** ({n} listening)"
                        if n else "voice: **connected** (empty)")
        elif self.runtime and self.runtime.target_channel_id:
            bits.append(f"voice: {self.runtime.voice_debug.get('state', 'dropped')}")
        else:
            bits.append("voice: disconnected")
        if self.director:
            bits.append(f"director: {self.director.status}")
        # Only when on. It costs seconds a line, so when speech has gone slow
        # this is the first thing worth ruling in or out -- and a strip that
        # says "thinking: off" all day is one nobody reads.
        if self.state.settings.thinking:
            bits.append("**thinking**")
        bits.append(self.vram_line())
        busy = gpu_busy_percent()
        if busy is not None:
            bits.append(f"gpu: {busy}%")
        return "  •  ".join(bits)

    def service_report(self):
        """Flat up/down list for the Diagnostics tab.

        Deliberately three plain lines rather than prose: the question this
        answers is "which of the three things is down", and that should be
        readable at a glance without parsing a sentence.
        """
        alive = self.tts_alive()
        rows = [("tts-server", alive,
                 "" if alive else f"no answer at {self.state.settings.tts_url}")]
        try:
            ok, why = self.llm.available()
        except Exception as e:
            ok, why = False, str(e)
        label = "ollama" if self.state.settings.provider == PROVIDER_OLLAMA else "openai"
        rows.append((label, ok, "" if ok else (why or "unavailable")))
        # Optional: absent until ./setup-whisper.sh has been run, and the app
        # is perfectly usable without it, so a miss reads as "off" not "down".
        w_ok, w_why = self.whisper.available()
        rows.append(("whisper", w_ok, "" if w_ok else "not set up"))
        # This process answered, or you would not be reading this.
        rows.append(("this server", True, ""))

        out = []
        for name, ok, detail in rows:
            mark = "okay" if ok else "**down**"
            out.append(f"- `{name}`: {mark}" + (f" — {detail}" if detail else ""))
        return "\n".join(out)

    def voice_report(self):
        """Markdown block tracing the voice connection and who can hear it."""
        if not self.runtime:
            return "_Bot not connected._"
        v = self.runtime.voice_debug
        s = self.state.settings
        rows = [
            ("state", v.get("state", "-")),
            ("channel", v.get("channel") or "-"),
            ("people listening", f"{v.get('humans', 0)}"
                + (f" ({v['names']})" if v.get("names") else "")),
            ("auto-rejoin", "on" if s.auto_rejoin else "**off**"),
            ("pause when empty", "on" if s.pause_when_empty else "off"),
            ("rejoins this session", v.get("rejoins", 0)),
            ("last drop", v.get("last_drop") or "-"),
            ("last error", v.get("last_error") or "-"),
        ]
        return "\n".join(f"- **{k}**: {v_}" for k, v_ in rows)

    def chat_report(self):
        """Markdown block for the chat-input debug panel.

        Text is the only way a real person reaches the conversation, so this
        traces a message from arriving to being answered.
        """
        if not self.runtime:
            return "_Bot not connected._"
        stats = self.runtime.text_stats
        d = self.director.text_debug if self.director else {}
        interactive = self.state.settings.mode == "interactive"

        if not self.runtime.connected():
            headline = ("**not in a voice channel** - messages are only picked up "
                        "from the server the bot is currently in")
        elif not interactive:
            headline = (f"**mode is {self.state.settings.mode}** - messages are "
                        "ignored until you switch to interactive")
        elif stats.get("seen", 0) == 0:
            headline = ("watching for messages, none seen yet - type in any text "
                        "channel in this server")
        else:
            headline = "receiving messages"
        rows = [
            ("state", headline),
            ("mode", "interactive" if interactive else f"**{self.state.settings.mode}**"),
            ("interrupt on message", "on" if self.state.settings.barge_in else "off"),
            ("cue on message", "on" if self.state.settings.ack_sound else "off"),
            ("messages seen", stats.get("seen", 0)),
            ("forwarded to director", stats.get("forwarded", 0)),
            ("queued for a reply", d.get("queued", 0)),
            ("answered", d.get("answered", 0)),
            ("waiting now",
             f"{d.get('depth', 0)} of {self.state.settings.user_queue_max}"),
            ("refused (queue full)", d.get("dropped", 0)),
            ("last message", stats.get("last") or "-"),
            ("last channel", stats.get("last_channel") or "-"),
            ("last routing", d.get("last_router") or "-"),
            ("last drop reason", d.get("last_drop") or "-"),
        ]
        return "\n".join(f"- **{k}**: {v}" for k, v in rows)

    def comedy_report(self):
        """Markdown block for the comedy debug panel; see deadinternet/comedy.py."""
        d = self.director
        if not d or not d.comedy_debug.get("lines"):
            return "_(nothing said yet)_"
        c = d.comedy_debug
        with self.state.lock:
            live = [t.text for t in self.state.transcript[d._topic_start:]
                    if t.kind == "bot"]
        # Everything below is model output built from Discord and web text.
        premises = " | ".join(f"{plain_md(k)}: {plain_md(v)}"
                              for k, v in d.premises().items())
        rows = [
            ("last move", plain_md(c.get("move") or "-")),
            ("takes of the last line", c.get("takes", 0)),
            ("lines re-rolled", f"{c.get('rerolled', 0)} of {c.get('lines', 0)}"),
            ("last re-roll because", plain_md(c.get("flags") or "-")),
            ("this segment so far", comedy.format_metrics(comedy.metrics(live))),
            ("last finished segment", c.get("segment") or "-"),
            ("premises", premises or "-"),
            ("remembered bits", plain_md(", ".join(d._bits)) or "-"),
        ]
        return "\n".join(f"- **{k}**: {v}" for k, v in rows)

    def norm_report(self):
        s = self.state.settings
        lines = []

        if s.speech_limit_enabled:
            frames = int(s.max_speech_seconds * 12.5)
            t = (self.director.last_truncate if self.director else None) or {}
            if t.get("truncated"):
                detail = f"**cut** {t.get('original')}s -> {t.get('duration')}s"
            elif t.get("duration") is not None:
                detail = f"last clip {t.get('duration')}s, under the limit"
            else:
                detail = "nothing played yet"
            lines.append(f"- **speech cap**: {s.max_speech_seconds:g}s "
                         f"(generation capped at {frames} frames) - {detail}")
        else:
            lines.append("- **speech cap**: off")

        if not s.normalize_audio:
            lines.append("- **normalisation**: off")
            return "\n".join(lines)
        info = (self.director.last_norm if self.director else None) or {}
        if not info:
            lines.append(f"- **normalisation**: target {s.target_dbfs} dBFS RMS, "
                         "nothing played yet")
        elif not info.get("applied"):
            lines.append(f"- **normalisation**: last clip skipped ({info.get('reason')})")
        else:
            lines.append(f"- **normalisation**: gain **{info.get('gain_db')} dB**, "
                         f"RMS {info.get('rms_in')} -> {info.get('rms_out')}, "
                         f"peak {info.get('peak_out')}"
                         + (" (peak-limited)" if info.get("limited") else ""))
        return "\n".join(lines)

    def vram_line(self):
        info = vram_info()
        if not info:
            return "vram: n/a"
        used, total = info
        frac = used / total
        # "until tts-server restarts" is the load-bearing half. Measured on
        # one process, in order: 0.16 s of compute per second of audio while
        # the card was empty, 0.53 after a 12 GB model loaded alongside it,
        # and still 0.53 once that model was unloaded again. Freeing VRAM
        # does nothing on its own -- the buffers are already in host memory
        # and stay there for the life of the process.
        flag = (" **FULL - speech degraded until tts-server restarts**"
                if frac > VRAM_WARN_FRACTION else "")
        return f"vram: {used:.1f}/{total:.1f} GB{flag}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tts", default=None, help="tts-server base URL")
    p.add_argument("--ollama", default=None, help="Ollama base URL")
    p.add_argument("--model", default=None, help="Ollama model name")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=7860)
    p.add_argument("--no-tts-autostart", action="store_true",
                   help="do not launch tts-server at startup (still registers "
                        "voices against one that is already running)")
    args = p.parse_args()

    app = DeadInternetApp(args)
    # In the background: the page has nothing to wait for, and the server can
    # take a couple of minutes to answer on a cold model.
    app.start_tts_boot(autostart=not args.no_tts_autostart)
    demo = build(app)
    # css belongs to launch() in gradio 6, not the Blocks constructor.
    demo.queue(default_concurrency_limit=4).launch(
        server_name=args.host, server_port=args.port, css=APP_CSS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
