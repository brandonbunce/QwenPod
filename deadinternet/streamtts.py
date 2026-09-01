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

# Levelling target, the same one normalize_wav uses on the buffered path:
# -20 dBFS RMS. A *fixed* gain was the first attempt and it was wrong -- output
# level tracks the reference clip, and measured across the roster those range
# from 0.053 to 0.105 RMS. One voice came out at 0.2829 against this 0.1000,
# nine decibels hot, which downstream is somebody's Bluetooth headset
# distorting. So the gain is derived, not chosen.
TARGET_RMS = 0.1
DEFAULT_GAIN = 1.0
# Never trust a measurement enough to swing the level more than this.
MIN_GAIN, MAX_GAIN = 0.05, 6.0
# Headroom. Measured, the engine's own output varies about sixfold in level
# between consecutive lines of the *same* voice -- 0.038 to 0.225 RMS -- so a
# gain fitted to the last line overshoots on the next one and clips. Distortion
# is far worse than quiet here, so the gain is additionally capped by the peak
# actually seen, and moved toward the target rather than snapped to it.
PEAK_CEILING = 0.95
SMOOTHING = 0.35
# Samples quieter than this are the gaps between words; including them drags
# the estimate down and the correction up.
VOICED_FLOOR = 0.02


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
        # Raw (pre-gain) samples of the line in flight, kept only long enough
        # to re-derive the gain once the line is done.
        self._level = []
        # Half a sample held over from the previous chunk -- see _amplify.
        # Pump-thread state: nothing else may touch it.
        self._carry = b""
        self._expect_header = True
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
    # There is deliberately no guess from the reference clip. That was tried:
    # a clip at 0.0609 RMS produced output at 0.356 raw, and another voice with
    # a louder reference produced 0.0871. The model normalises the reference
    # internally, so output level does not track it at all and seeding from it
    # made the first line worse rather than better. Measure the real output
    # instead, and remember it across restarts via settings.stream_tts_gain.

    def _relevel(self):
        """Correct the gain from what the last line actually produced."""
        if np is None:
            return
        with self._lock:
            chunks, self._level = self._level, []
        if not chunks:
            return
        raw = np.concatenate(chunks)
        voiced = raw[np.abs(raw) > VOICED_FLOOR]
        if voiced.size < 100:
            return
        # `raw` is pre-gain, so what was heard is rms * gain.
        rms = float(np.sqrt((voiced ** 2).mean()))
        if rms <= 1e-4:
            return
        want = TARGET_RMS / rms
        # Never ask for a gain that would have clipped the line just measured.
        peak = float(np.abs(raw).max())
        if peak > 1e-6:
            want = min(want, PEAK_CEILING / peak)
        want = min(max(want, MIN_GAIN), MAX_GAIN)
        # Damped, because the next line is not this line. Snapping was tried:
        # it fitted line one perfectly and drove line two into the ceiling.
        self.gain = min(max(self.gain + (want - self.gain) * SMOOTHING,
                            MIN_GAIN), MAX_GAIN)

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
            # A header is due at the start of this line's output.
            self._expect_header = True
            # NOTE: _carry is deliberately NOT reset here. It belongs to the
            # pump thread, which may still be draining the previous line, and
            # dropping its held half-sample byte-shifts everything after it --
            # which is static, arriving at random depending on how the two
            # threads happen to interleave. That was a real bug.
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
                self._relevel()
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
                with self._lock:
                    expecting = self._expect_header
                at = pending.find(b"RIFF") if expecting else -1
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
                # One header per line, so stop looking. Four bytes of PCM can
                # spell RIFF by accident, and stripping 44 bytes of real audio
                # in the middle of a word is an audible click.
                with self._lock:
                    self._expect_header = False
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
        """Apply the learned gain, keeping the 16-bit sample grid intact.

        The carry is the whole point, and without it this was a real bug.
        Chunks arrive on arbitrary byte boundaries -- _forward slices at
        `[:-3]` to hold back a possible header -- so an odd-length chunk leaves
        the next one starting half a sample late. Parsed as int16 that
        byte-swaps every sample in it: garbage values, which is both a wildly
        wrong level estimate and, written to the device, audible static inside
        otherwise fine speech.

        Preserving byte *content* is not enough, which is what the first
        version did. The pipe does not care where writes land, but this
        function parses each chunk, so alignment has to be carried across
        calls rather than patched up at the end of each one.
        """
        if np is None:
            return data
        data = self._carry + data
        keep = len(data) - (len(data) % 2)
        self._carry = data[keep:]
        if keep <= 0:
            return b""
        samples = np.frombuffer(data[:keep], dtype="<i2").astype(np.float32)
        # Recorded before the gain is applied, so the correction is computed
        # against what the engine produced rather than against itself.
        with self._lock:
            self._level.append(samples / 32768.0)
        samples = samples * self.gain
        np.clip(samples, -32768, 32767, out=samples)
        return samples.astype("<i2").tobytes()
