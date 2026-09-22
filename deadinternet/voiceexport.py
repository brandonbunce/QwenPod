"""Export every cloned voice as a reference clip and its transcript.

Backs the Speakers tab's "Download all voices" button. Writes one zip that
unpacks to a qwen-voices-export/ folder with a pair per speaker
that has a reference clip:

    <name>.wav   mono, 16-bit PCM, 24 kHz -- the audio tts-server clones from
    <name>.txt   ref_text exactly as stored, UTF-8; empty for a voice-only clone

plus manifest.json mapping each <name> to the speaker's real name and the clip
length. Audio and transcripts only: no personas, conversation, settings, .env
or logs -- the roster is read for ref_wav and ref_text and nothing else.
Nothing is written into the repo; the only file on disk is the zip the caller
asks for.

The .wav is what the server actually heard, not a fresh conversion of the
file in voices/. It starts from tts.upload_wav(), the same bytes register()
posts, and then does what tts-server does on receipt (tools/tts-server.cpp,
src/audio-io.h): int16 / 32768, then the torchaudio-compatible sinc resample
in src/audio-resample.h to 24 kHz. Neither side trims the clip, so neither does
this. A clip already at 24 kHz comes out sample-for-sample identical to what
was sent.
"""
import io
import json
import math
import os
import re
import unicodedata
import zipfile

import numpy as np
import soundfile as sf

from .tts import upload_wav

FOLDER = "qwen-voices-export"
MANIFEST = "manifest.json"
SERVER_RATE = 24000

# src/audio-resample.h
_LPFW = 6
_ROLLOFF = 0.99


# ---- names -----------------------------------------------------------------
def safe_name(name: str) -> str:
    """Lowercase, a-z 0-9 _ - only, spaces to underscores.

    Accents are folded rather than dropped, so "Zoë" is "zoe" and not "zo".
    Anything else outside the set is removed. A name with nothing left becomes
    "speaker" -- a file called ".wav" is not a file anyone can find.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    out = re.sub(r"[^a-z0-9_-]", "", folded.lower().replace(" ", "_"))
    return out or "speaker"


def unique_names(names):
    """safe_name() for each, with _2, _3... on collisions, in roster order."""
    taken, out = set(), []
    for name in names:
        base = safe_name(name)
        stem, n = base, 1
        while stem in taken:
            n += 1
            stem = f"{base}_{n}"
        taken.add(stem)
        out.append(stem)
    return out


# ---- audio -----------------------------------------------------------------
def _sinc_kernel(orig: int, new: int):
    """audio_resample_build_kernel(): Hann-windowed sinc, [new, K] float32."""
    base = min(orig, new) * _ROLLOFF
    width = math.ceil(_LPFW * orig / base)
    k = np.arange(2 * width + orig, dtype=np.float64)
    j = np.arange(new, dtype=np.float64)[:, None]
    t = np.clip(((k - width) / orig - j / new) * base, -_LPFW, _LPFW)
    window = np.cos(t * np.pi / _LPFW / 2.0) ** 2
    tp = t * np.pi
    with np.errstate(invalid="ignore", divide="ignore"):
        sinc = np.where(tp == 0.0, 1.0, np.sin(tp) / tp)
    return (sinc * window * (base / orig)).astype(np.float32), width


def resample(x: np.ndarray, sr_in: int, sr_out: int = SERVER_RATE) -> np.ndarray:
    """audio_resample() for one channel: same kernel, same padding, same length.

    Bit-exact with the server, which takes care: the taps are accumulated one
    at a time in float32, multiply then add, in the C++ loop's order. A matmul
    sums in a different order and rounds ~1 sample in 3000 one int16 step off.
    tts-server is built -O3 without -march, so the compiler neither fuses the
    multiply-add nor reorders the sum; a build that did either would drift
    from this by the same 1 LSB.
    """
    x = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out or not len(x):
        return x.copy()
    g = math.gcd(sr_in, sr_out)
    orig, new = sr_in // g, sr_out // g
    kernel, width = _sinc_kernel(orig, new)
    size = kernel.shape[1]
    padded = np.zeros(len(x) + 2 * width + orig, dtype=np.float32)
    padded[width:width + len(x)] = x
    frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::orig]
    acc = np.zeros((len(frames), new), dtype=np.float32)
    for k in range(size):
        acc += frames[:, k:k + 1] * kernel[:, k]
    return acc.reshape(-1)[:math.ceil(sr_out * len(x) / sr_in)]


def server_pcm(wav_path: str) -> np.ndarray:
    """The reference clip as tts-server holds it: float32 mono at 24 kHz."""
    sent, sr = sf.read(io.BytesIO(upload_wav(wav_path)), dtype="int16")
    # wav.h decodes PCM16 as s / 32768; the upload is already mono, so the
    # server's 0.5 * (L + R) downmix of a duplicated channel is a no-op.
    return resample(sent.astype(np.float32) / 32768.0, sr)


def to_int16(x: np.ndarray) -> np.ndarray:
    """Inverse of the server's decode, so a 24 kHz clip round-trips exactly."""
    return np.clip(np.round(x * 32768.0), -32768, 32767).astype(np.int16)


# ---- export ----------------------------------------------------------------
def export_zip(speakers, zip_path: str):
    """Write the export to zip_path. Returns (manifest, warnings).

    `speakers` is anything with .name, .ref_wav and .ref_text -- in practice
    State().restorable(), the same set the app registers at boot. A speaker
    whose clip is missing or unreadable is skipped with a warning; the rest
    still export.
    """
    speakers = [s for s in speakers if s.ref_wav]
    warnings = []
    manifest = {}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for sp, stem in zip(speakers, unique_names(s.name for s in speakers)):
            if not os.path.exists(sp.ref_wav):
                warnings.append(f"{sp.name}: reference clip missing: {sp.ref_wav}")
                continue
            try:
                pcm = server_pcm(sp.ref_wav)
            except Exception as e:
                warnings.append(f"{sp.name}: could not read {sp.ref_wav}: {e}")
                continue
            text = sp.ref_text or ""
            if "\n" in text or "\r" in text:
                warnings.append(f"{sp.name}: ref_text has a line break; written "
                                "as stored, so the .txt is not one line")

            wav = io.BytesIO()
            sf.write(wav, to_int16(pcm), SERVER_RATE, format="WAV", subtype="PCM_16")
            z.writestr(f"{FOLDER}/{stem}.wav", wav.getvalue())
            # The stored string byte for byte: no newline added, none translated.
            z.writestr(f"{FOLDER}/{stem}.txt", text.encode("utf-8"))
            manifest[stem] = {
                "speaker": sp.name,
                "seconds": round(len(pcm) / SERVER_RATE, 3),
            }

        z.writestr(f"{FOLDER}/{MANIFEST}",
                   json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest, warnings
