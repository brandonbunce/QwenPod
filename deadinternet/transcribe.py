"""Speech to text via whisper.cpp, for the microphone on the Run tab.

A subprocess rather than a Python binding, and CPU rather than GPU, both for
the same reason: this machine's scarce resource is VRAM, not compute. The card
sits around 63% full with tts-server alone, and once the driver evicts
tts-server's Vulkan buffers speech stays ~5x slow until the process restarts
(see the VRAM section in DEADINTERNET.md). Measured here, small.en on the
9950X does 11 seconds of speech in under a second with the GPU untouched.

Run ./setup-whisper.sh once to build the binary and fetch a model. Nothing here
is required for the app to start -- with no binary present the microphone
simply reports that it is not set up.
"""
import os
import re
import subprocess
import tempfile

from .pipeline import NULL_PIPELINE, WHISPER

# whisper.cpp accepts 16 kHz mono PCM and nothing else, so whatever the browser
# recorded has to be converted first. ffmpeg is already a dependency: the bot
# uses it to feed Discord.
SAMPLE_RATE = 16000

# Generous. small.en runs ~11x faster than realtime here, so this only trips on
# something genuinely wrong rather than on a long recording.
TIMEOUT = 180.0

# whisper.cpp emits these for non-speech audio; they are annotations, not words,
# and passing them to the TTS would have a speaker say "blank audio" out loud.
_NOISE = re.compile(r"\[(BLANK_AUDIO|INAUDIBLE|SILENCE|MUSIC|SOUND|NOISE)[^\]]*\]",
                    re.IGNORECASE)

# Peak below this is treated as "nothing was captured". Measured on this
# machine: a quiet room through a working mic peaks around -49 dBFS, while
# takes recorded from the wrong input peak at -65 to -70 with a -91 mean, which
# is the 16-bit noise floor. -60 sits in the gap with room on both sides, so a
# genuinely quiet speaker is not accused of having a broken microphone.
SILENCE_DBFS = -60.0


class Whisper:
    """Wraps one whisper-cli invocation per utterance.

    Stateless on purpose: whisper-cli loads the model on each call, which costs
    ~200 ms for small.en and buys back not having a multi-hundred-MB model
    resident for a feature used a few times an hour.
    """

    def __init__(self, root, binary, model, lang="en", threads=0):
        self.root = root
        self.binary = binary
        self.model = model
        self.pipeline = NULL_PIPELINE
        self.lang = lang or "en"
        # whisper.cpp defaults to 4. Half the logical CPUs is a reasonable
        # ceiling on an SMT part -- past the physical core count the extra
        # threads mostly contend.
        self.threads = threads or max(1, min(16, (os.cpu_count() or 4) // 2))

    # ---- paths ---------------------------------------------------------
    def _abs(self, path):
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    def available(self):
        """-> (ok, reason). Reason is user-facing when not ok."""
        binary, model = self._abs(self.binary), self._abs(self.model)
        if not os.path.exists(binary):
            return False, f"whisper.cpp not built - run ./setup-whisper.sh (looked in {self.binary})"
        if not os.path.exists(model):
            return False, f"whisper model missing - run ./setup-whisper.sh (looked in {self.model})"
        if not _ffmpeg():
            return False, "ffmpeg not on PATH - needed to resample microphone audio"
        return True, ""

    # ---- transcription -------------------------------------------------
    def transcribe(self, audio_path):
        """-> (text, error). Exactly one of the two is truthy.

        Never raises: this sits behind a UI button, and a failed transcription
        should report itself rather than take the handler down.
        """
        with self.pipeline.track(WHISPER):
            return self._transcribe(audio_path)

    def _transcribe(self, audio_path):
        ok, why = self.available()
        if not ok:
            return "", why
        if not audio_path or not os.path.exists(audio_path):
            return "", "No recording - hold the mic button and speak first."

        with tempfile.TemporaryDirectory(prefix="qwenpod-stt-") as tmp:
            wav = os.path.join(tmp, "in.wav")
            err = _to_16k_mono(audio_path, wav)
            if err:
                return "", err

            # A silent take is the single most likely thing to go wrong here,
            # and it is indistinguishable from a bad transcription unless it is
            # checked for: the browser hands back a perfectly well-formed WAV
            # of exactly the right length containing nothing at all. That
            # happens whenever Chrome's chosen input is not the microphone you
            # are speaking into -- its default is its own setting, not the
            # system's, so an unplugged line-in gets recorded quite happily.
            # Saying so beats "nothing recognisable", which sounds like Whisper
            # struggled with your accent.
            peak = _peak_dbfs(wav)
            if peak is not None and peak < SILENCE_DBFS:
                return "", (
                    f"That recording is silent (peak {peak:.0f} dBFS). The "
                    "browser captured audio of the right length with nothing "
                    "in it, which almost always means it is recording from the "
                    "wrong input. Check the microphone Chrome is using -- the "
                    "padlock in the address bar, or chrome://settings/content/"
                    "microphone -- rather than the system default."
                )
            try:
                out = subprocess.run(
                    [self._abs(self.binary),
                     "-m", self._abs(self.model),
                     "-f", wav,
                     "-l", self.lang,
                     "-t", str(self.threads),
                     "-nt",      # no timestamps -- we want the words only
                     "-np"],     # no progress chatter on stdout
                    capture_output=True, text=True, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                return "", f"transcription timed out after {TIMEOUT:g}s"
            except OSError as e:
                return "", f"could not run whisper-cli - {e}"

        if out.returncode != 0:
            tail = (out.stderr or "").strip().splitlines()
            return "", f"whisper-cli failed - {tail[-1] if tail else 'no output'}"

        text = _NOISE.sub(" ", out.stdout)
        text = " ".join(text.split())
        if not text:
            return "", "Nothing recognisable in that recording."
        return text, ""


def _ffmpeg():
    from shutil import which
    return which("ffmpeg")


def _peak_dbfs(path):
    """Loudest sample in the file, in dBFS. None if it cannot be measured.

    Peak rather than mean, because a take that is mostly silence with a few
    words in it is fine and should not be rejected -- only one with no peaks
    at all is broken.
    """
    try:
        import numpy as np
        import soundfile as sf
        data, _ = sf.read(path, dtype="float32", always_2d=True)
    except Exception:
        return None
    if data.size == 0:
        return -np.inf
    peak = float(np.abs(data).max())
    if peak <= 0.0:
        return -999.0
    import math
    return 20.0 * math.log10(peak)


def _to_16k_mono(src, dst):
    """-> error string, or '' on success."""
    try:
        out = subprocess.run(
            [_ffmpeg(), "-nostdin", "-loglevel", "error", "-y",
             "-i", src, "-ar", str(SAMPLE_RATE), "-ac", "1",
             "-c:a", "pcm_s16le", dst],
            capture_output=True, text=True, timeout=60.0)
    except (OSError, subprocess.SubprocessError) as e:
        return f"could not convert the recording - {e}"
    if out.returncode != 0 or not os.path.exists(dst):
        tail = (out.stderr or "").strip().splitlines()
        return f"could not convert the recording - {tail[-1] if tail else 'ffmpeg failed'}"
    return ""
