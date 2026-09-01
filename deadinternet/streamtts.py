"""Speaking while the sentence is still being synthesised.

The HTTP path renders a whole clip and then plays it: measured, 2.21s of
silence before a single sample of a nine-second line. The engine does not
actually work that way -- `qwen-tts --stream-by-line --codec-fused -o -` emits
audio as it decodes, and the same line starts in **0.06s**. That is the
difference between a voice changer and a walkie-talkie.

The price is that a reference clip is fixed at process start, so one process
speaks one voice. That rules it out for the 33-speaker show and makes it
exactly right for the microphone, where you have picked a character and are
talking as them.

Wire format, confirmed rather than assumed: one 44-byte RIFF header per line,
mono 16-bit at 24 kHz. So the headers are stripped at each boundary and the
raw PCM is handed to a single long-lived paplay -- piping the stream straight
in would feed it a second WAV header mid-stream, which it will not take.

Backpressure is free and worth understanding: generation runs several times
faster than playback, so the pipe to paplay fills and the pump blocks. The
audio device paces the whole thing, and no buffer grows without bound.
"""
import os
import subprocess
import threading
import time

try:
    import numpy as np
except ImportError:            # gain is the only thing that needs it
    np = None

RATE = 24000
CHANNELS = 1
BYTES_PER_SEC = RATE * CHANNELS * 2
WAV_HEADER = 44

# How long stdout must be quiet before a line counts as fully generated.
# Generation is bursty; this is comfortably longer than the gap between
# chunks of one line and far shorter than the gap between lines.
IDLE_S = 0.25

# A line that never finishes must not park the caller forever.
GENERATE_TIMEOUT = 120.0

# Makeup gain, measured rather than guessed. The raw stream came out at
# 0.0871 RMS over voiced samples against the buffered path's -20 dBFS target
# of 0.1000, so the streaming engine is already very close and this is a trim,
# not a rescue. Kept as a knob because it is per-reference-clip.
DEFAULT_GAIN = 1.15


class StreamingVoice:
    """One persistent qwen-tts process, one voice, piped at an audio device."""

    def __init__(self, binary, model, codec, ref_wav, voice, sink="",
                 ref_text="", gain=DEFAULT_GAIN, log=print):
        self.binary = binary
        self.model = model
        self.codec = codec
        self.ref_wav = ref_wav
        self.ref_text = ref_text or ""
        self.voice = voice
        self.sink = sink or ""
        self.gain = float(gain)
        self.log = log
        self.proc = None
        self.play = None
        self._pump = None
        self._lock = threading.Lock()
        # Bytes of PCM forwarded for the line in flight, and when the last one
        # arrived -- the pair the idle check is made of.
        self._bytes = 0
        self._last = 0.0
        self._stop = threading.Event()

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> bool:
        for label, path in (("qwen-tts", self.binary), ("model", self.model),
                            ("codec", self.codec), ("reference clip", self.ref_wav)):
            if not path or not os.path.exists(path):
                self.log(f"[stream] no streaming voice - {label} not found at {path}")
                return False
        cmd = [self.binary, "--model", self.model, "--codec", self.codec,
               "--ref-wav", self.ref_wav, "--stream-by-line", "--codec-fused",
               "-o", "-"]
        if self.ref_text:
            cmd += ["--ref-text", self.ref_text]
        play = [ "paplay", "--raw", f"--format=s16le", f"--rate={RATE}",
                 f"--channels={CHANNELS}"]
        if self.sink:
            play.append(f"--device={self.sink}")
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
            self.play = subprocess.Popen(
                play, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except OSError as e:
            self.log(f"[stream] could not start - {e}")
            self.stop()
            return False
        self._stop.clear()
        self._pump = threading.Thread(target=self._forward, daemon=True,
                                      name="stream-tts")
        self._pump.start()
        return True

    def alive(self) -> bool:
        return (self.proc is not None and self.proc.poll() is None
                and self.play is not None and self.play.poll() is None)

    def stop(self):
        # Flag first, then join, then drop the handles. Nulling them while the
        # pump was still in flight is an AttributeError on a dying thread.
        self._stop.set()
        pump, self._pump = self._pump, None
        if pump is not None and pump.is_alive():
            pump.join(timeout=2.0)
        for proc in (self.proc, self.play):
            if proc is None:
                continue
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass
        self.proc = self.play = None

    # ---- speaking -------------------------------------------------------
    def say(self, text: str):
        """Feed one line. -> seconds of audio produced, or None on failure.

        Returns once the line has finished *generating*, which is well before
        it has finished *playing* -- the caller sleeps out the difference. The
        two are deliberately separate: knowing the duration is what lets the
        next line be queued at the right moment instead of on a guess.
        """
        line = " ".join((text or "").split())
        if not line or not self.alive():
            return None
        with self._lock:
            self._bytes = 0
            self._last = time.monotonic()
            try:
                self.proc.stdin.write((line + "\n").encode())
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.log(f"[stream] lost the synthesiser - {e}")
                return None

        deadline = time.monotonic() + GENERATE_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(0.02)
            if not self.alive():
                return None
            with self._lock:
                produced, quiet = self._bytes, time.monotonic() - self._last
            if produced and quiet > IDLE_S:
                return produced / BYTES_PER_SEC
        self.log("[stream] a line never finished generating")
        return None

    # ---- plumbing -------------------------------------------------------
    def _forward(self):
        """qwen-tts stdout -> paplay stdin, minus the per-line WAV headers."""
        pending = b""
        while not self._stop.is_set():
            try:
                chunk = self.proc.stdout.read(4096)
            except (ValueError, OSError):
                break
            if not chunk:
                break
            pending += chunk
            # A header only ever appears at the start of a line's output, so
            # this looks for the magic rather than assuming a fixed cadence.
            while True:
                at = pending.find(b"RIFF")
                if at < 0:
                    # None in sight. Forward everything but the last three
                    # bytes, which could be the beginning of one.
                    if len(pending) > 3:
                        self._write(pending[:-3])
                        pending = pending[-3:]
                    break
                if len(pending) < at + WAV_HEADER:
                    # A header has started but has not all arrived. Forward
                    # the audio in front of it and wait for the rest --
                    # writing here regardless is how the partial header used
                    # to reach the device as a burst of noise.
                    if at:
                        self._write(pending[:at])
                        pending = pending[at:]
                    break
                self._write(pending[:at])
                pending = pending[at + WAV_HEADER:]
        self._write(pending)

    def _write(self, data: bytes):
        play = self.play
        if not data or play is None or self._stop.is_set():
            return
        data = self._amplify(data)
        with self._lock:
            self._bytes += len(data)
            self._last = time.monotonic()
        try:
            # Blocks once paplay's pipe is full, which is the backpressure
            # that paces generation to realtime.
            play.stdin.write(data)
            play.stdin.flush()
        except (BrokenPipeError, OSError, AttributeError):
            self._stop.set()

    def _amplify(self, data: bytes) -> bytes:
        """Fixed makeup gain, with a hard limit.

        The buffered path runs every clip through normalize_wav on the way out.
        Nothing can do that here -- levelling needs the whole clip and the
        whole point is not to have it -- so a streamed line arrived about 20 dB
        below everything else, which on a game's microphone input is inaudible.

        A constant is right where per-chunk normalisation would be wrong:
        measuring each chunk separately would pump the level within a single
        sentence. One voice, one reference clip, one gain.
        """
        if self.gain == 1.0 or np is None:
            return data
        # An odd trailing byte cannot be part of a sample yet; hold it back
        # rather than mangling the frame it belongs to.
        keep = len(data) - (len(data) % 2)
        if keep <= 0:
            return data
        samples = np.frombuffer(data[:keep], dtype="<i2").astype(np.float32)
        samples *= self.gain
        np.clip(samples, -32768, 32767, out=samples)
        return samples.astype("<i2").tobytes() + data[keep:]
