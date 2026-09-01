"""Speaking out of this machine's own sound card instead of into Discord.

A second output, not a replacement. Everything the director needs from a
"runtime" is ten calls -- play a clip, stop a clip, sound a cue, layer a voice,
say whether anyone is listening -- and none of that is inherently about
Discord. This provides the same surface over PulseAudio/PipeWire, so the show
can run with no bot, no token and no voice channel at all.

The obvious use is a virtual microphone: pair a null sink with a remapped
source and every game and voice client on the box sees the cast as an input
device.

    pactl load-module module-null-sink sink_name=qwenpod \\
        sink_properties=device.description=QwenPod_Output
    pactl load-module module-remap-source master=qwenpod.monitor \\
        source_name=qwenpod_mic source_properties=device.description=QwenPod_Microphone

Two things are markedly easier here than they are over Discord:

  * **Overlapping voices are free.** A voice client plays exactly one source,
    which is why bot.py has to sum extra speakers into the outgoing frames by
    hand. PulseAudio mixes concurrent streams itself, so an overlaid line is
    just a second paplay.
  * **Interrupting is killing a process**, rather than reaching into a player
    thread.

What is genuinely lost is everything that *is* Discord: pinned messages as a
topic source, images, and people typing at the cast. Those return empty here
rather than raising, so the same director code runs either way.
"""
import asyncio
import shutil
import subprocess
import threading
from typing import List, Optional

from .streamtts import StreamingVoice

# paplay resamples and converts for us, so the 24 kHz mono WAV tts-server
# returns needs no preparation. Reading it on stdin avoids a temp file per
# line.
PAPLAY = "paplay"
PACTL = "pactl"

# What ack_pcm() produces: interleaved stereo 16-bit at 48 kHz. Raw, so the
# format has to be stated.
CUE_ARGS = ["--raw", "--format=s16le", "--rate=48000", "--channels=2"]


def available() -> Optional[str]:
    """-> None if local output can work, else why it cannot."""
    if not shutil.which(PAPLAY):
        return ("paplay not found - install pulseaudio-utils "
                "(it works against PipeWire too)")
    return None


def sinks():
    """[(name, description)] of the output devices paplay can target.

    The empty first entry is the system default, which is what most people
    want and what avoids hard-coding a device name into the settings file.
    """
    out = [("", "system default")]
    if not shutil.which(PACTL):
        return out
    try:
        proc = subprocess.run([PACTL, "list", "short", "sinks"],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return out
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1]:
            out.append((parts[1], parts[1]))
    return out


class LocalRuntime:
    """The director's runtime interface, over the local sound card.

    Shaped deliberately like DiscordRuntime rather than sharing a base class
    with it: the two have almost no implementation in common, and the surface
    is small enough that a common ancestor would be a place to look things up
    rather than a place that does anything.
    """

    kind = "local"

    def __init__(self, sink: str = "", log=print, settings=None):
        self.sink = sink or ""
        self.log = log
        # Needed for the streaming voice's model paths and gain; optional so a
        # bare LocalRuntime is still constructible in a test.
        self.settings = settings
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.ready = threading.Event()
        self.error: Optional[str] = None
        self.pipeline = None            # set by app.py, like the Discord one
        self.overlap_max = 3
        # Set by app.py so the same call sites work; nothing local consumes
        # them, but leaving them undefined would mean guards elsewhere.
        self.on_text = None
        self.on_say = None
        self.speaker_names = None
        self.auto_rejoin = False
        self.target_channel_id = None
        self.client = None
        # A resident single-voice streamer, when one is configured.
        self.stream: Optional[StreamingVoice] = None
        self._procs: List[asyncio.subprocess.Process] = []
        self._current: Optional[asyncio.subprocess.Process] = None
        self._lock = threading.Lock()
        self.voice_debug = {"state": "not started", "humans": 0, "names": "",
                            "channel": "", "rejoins": 0, "last_drop": None,
                            "last_error": None}
        self.text_stats = {"seen": 0, "accepted": 0, "ignored": 0,
                           "last": "", "last_error": None}

    # ---- lifecycle ---------------------------------------------------
    def start(self, timeout: float = 10.0) -> bool:
        """Bring up the loop this runtime's playback runs on.

        A loop of its own, in its own thread, for the same reason the Discord
        one has one: the director hands work to `self.loop` from gradio's
        threads and expects it to be running somewhere.
        """
        why = available()
        if why:
            self.error = why
            self.voice_debug["state"] = "unavailable"
            self.voice_debug["last_error"] = why
            return False
        if self.thread and self.thread.is_alive():
            return True

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            self.ready.set()
            loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True,
                                       name="local-audio")
        self.thread.start()
        if not self.ready.wait(timeout):
            self.error = "local audio loop did not start"
            return False
        self.voice_debug["state"] = f"playing to {self.sink or 'system default'}"
        self.log(f"[local] output ready -> {self.sink or 'system default'}")
        return True

    def open_stream(self, voice) -> bool:
        """Hold a streaming synthesiser open for one speaker. -> ready.

        Rebuilt rather than mutated when the voice changes: the reference clip
        is a process argument, so a different speaker is a different process.
        """
        s = self.settings
        if self.stream is not None and self.stream.voice == voice.name and self.stream.alive():
            return True
        self.close_stream()
        if not voice.ref_wav:
            self.log(f"[stream] {voice.name} has no reference clip")
            return False
        self.stream = StreamingVoice(
            binary=self._abs(s.tts_cli_binary), model=self._abs(s.tts_model),
            codec=self._abs(s.tts_codec), ref_wav=voice.ref_wav,
            voice=voice.name, sink=self.sink, gain=s.stream_tts_gain,
            log=self.log)
        if not self.stream.start():
            self.stream = None
            return False
        self.log(f"[stream] holding {voice.name} open on {self.sink or 'system default'}")
        return True

    def close_stream(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        # Carry the level it learned into the settings, so the first line of
        # the next session is not the loud one.
        if self.settings is not None and stream.gain:
            self.settings.stream_tts_gain = round(float(stream.gain), 3)
        stream.stop()

    def stream_say(self, text: str):
        """Speak through the resident streamer. -> seconds of audio, or None.

        Runs on an executor thread: say() blocks until the line has finished
        generating, and generation is several times faster than playback, so
        this returns while the audio is still going out.
        """
        if self.stream is None or not self.stream.alive():
            return None
        return self.stream.say(text)

    @staticmethod
    def _abs(path):
        import os
        from .config import ROOT
        return path if os.path.isabs(path) else os.path.join(ROOT, path)

    def stop(self):
        """Stop playing and unwind the loop.

        The tasks have to be cancelled and awaited before the loop stops, not
        merely left behind: stopping a loop out from under a running director
        turn produces "Task was destroyed but it is pending" and, worse, leaves
        whatever that turn was holding un-run.
        """
        self.close_stream()
        self.interrupt()
        loop, self.loop = self.loop, None
        if loop and loop.is_running():
            async def drain():
                current = asyncio.current_task()
                pending = [t for t in asyncio.all_tasks(loop) if t is not current]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                loop.stop()

            asyncio.run_coroutine_threadsafe(drain(), loop)
            if self.thread:
                # Bounded: a task that refuses to unwind must not hang the UI
                # thread that pressed Stop.
                self.thread.join(timeout=5.0)
        self.thread = None
        self.ready.clear()
        self.voice_debug["state"] = "stopped"

    # ---- what the director asks -------------------------------------
    def connected(self) -> bool:
        return bool(self.loop) and self.ready.is_set()

    def humans_present(self) -> bool:
        """Always. There is nobody to count, and a local output that paused
        itself for an empty room would never speak at all -- pause_when_empty
        exists because Discord charges you for talking to nobody."""
        return True

    def occupants(self):
        return 0, []

    async def play_wav(self, wav: bytes, timeout: Optional[float] = None):
        """Play a clip and wait for it to finish."""
        proc = await self._spawn(wav, [])
        if proc is None:
            return
        with self._lock:
            self._current = proc
        try:
            await asyncio.wait_for(proc.wait(), timeout) if timeout else await proc.wait()
        except asyncio.TimeoutError:
            self.log(f"[local] clip did not finish within {timeout:g}s - cutting it")
            self._kill(proc)
        finally:
            with self._lock:
                if self._current is proc:
                    self._current = None
                if proc in self._procs:
                    self._procs.remove(proc)

    def overlay(self, wav: bytes) -> str:
        """Lay a voice over whatever is already playing.

        No mixer, no frame arithmetic: the sound server does it. The cap is
        still honoured so /sayas spam cannot open thirty processes.
        """
        if not self.connected():
            return "offline"
        with self._lock:
            live = [p for p in self._procs if p.returncode is None]
            self._procs = live
            if not live:
                return "idle"
            if len(live) > max(1, int(self.overlap_max)):
                return "full"
        asyncio.run_coroutine_threadsafe(self._play_detached(wav), self.loop)
        return "on"

    def talking(self) -> int:
        with self._lock:
            return len([p for p in self._procs if p.returncode is None])

    def play_cue(self, pcm: bytes) -> bool:
        """The acknowledgement blip, over the top of anything playing."""
        if not self.connected():
            return False
        asyncio.run_coroutine_threadsafe(
            self._play_detached(pcm, CUE_ARGS), self.loop)
        return True

    def interrupt(self):
        """Cut everything currently making noise."""
        with self._lock:
            procs, self._procs = list(self._procs), []
            self._current = None
        for proc in procs:
            self._kill(proc)

    # ---- the Discord-shaped surface, empty here ----------------------
    # Present rather than absent so nothing upstream needs a hasattr guard.
    # A local output genuinely has no pins, no images and no chat.
    def fetch_pins(self, *a, **kw):
        return []

    def load_image(self, *a, **kw):
        return None

    def voice_channels(self):
        return []

    def text_channels(self):
        return []

    def join(self, *a, **kw):
        return False, "local output does not join channels"

    def leave(self):
        return "local output has no channel to leave"

    def mine_messages(self, *a, **kw):
        raise RuntimeError("mining history needs the Discord bot")

    def submit_async(self, coro, timeout=None):
        if not self.loop:
            raise RuntimeError("local output is not running")
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout) if timeout else fut.result()

    # ---- plumbing -----------------------------------------------------
    def _args(self, extra):
        args = [PAPLAY]
        if self.sink:
            args.append(f"--device={self.sink}")
        args += extra or ["--file-format=wav"]
        return args

    async def _spawn(self, data: bytes, extra):
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._args(extra),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError as e:
            self.voice_debug["last_error"] = str(e)
            self.log(f"[local] could not play: {e}")
            return None
        with self._lock:
            self._procs.append(proc)
        # Written and closed as one step: paplay reads until EOF, so a stdin
        # left open would leave it waiting forever on a clip it already has.
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # paplay exited early -- a bad device name, usually. Not fatal:
            # the show carries on silently rather than stopping.
            pass
        return proc

    async def _play_detached(self, data: bytes, extra=None):
        """Fire and forget, for cues and overlaid voices."""
        proc = await self._spawn(data, extra)
        if proc is None:
            return
        try:
            await proc.wait()
        finally:
            with self._lock:
                if proc in self._procs:
                    self._procs.remove(proc)

    @staticmethod
    def _kill(proc):
        try:
            if proc.returncode is None:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
