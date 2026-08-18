"""Loudness normalisation for synthesised speech.

Different clones come back at noticeably different levels -- measured RMS
across our reference voices ranged from 0.053 to 0.105, which is a ~6 dB jump
between speakers in the same conversation. This levels them to a common RMS
with a peak ceiling so nothing clips.
"""
import io

import numpy as np
import soundfile as sf

# -20 dBFS RMS is a normal speech target and sits comfortably inside the range
# the codec already produces, so gains stay small.
DEFAULT_TARGET_DBFS = -20.0
PEAK_CEILING = 0.97
# Never amplify more than this: a near-silent clip is a failed generation, not
# something to boost into a wall of noise.
MAX_GAIN = 8.0
SILENCE_RMS = 1e-4


def dbfs_to_rms(dbfs: float) -> float:
    return float(10.0 ** (dbfs / 20.0))


def normalize_array(samples: np.ndarray, target_dbfs: float = DEFAULT_TARGET_DBFS):
    """Return (normalised, info). Info describes what was done, for the UI."""
    if samples.size == 0:
        return samples, {"applied": False, "reason": "empty"}

    rms = float(np.sqrt(np.mean(np.square(samples))))
    peak_in = float(np.max(np.abs(samples)))
    if rms < SILENCE_RMS:
        return samples, {"applied": False, "reason": "silent", "rms_in": rms}

    gain = min(dbfs_to_rms(target_dbfs) / rms, MAX_GAIN)
    out = samples * gain

    # Scale back rather than clip if the gain pushed peaks over the ceiling.
    peak = float(np.max(np.abs(out)))
    limited = False
    if peak > PEAK_CEILING:
        out = out * (PEAK_CEILING / peak)
        gain *= PEAK_CEILING / peak
        limited = True

    return out, {
        "applied": True,
        "gain": round(float(gain), 3),
        "gain_db": round(float(20.0 * np.log10(max(gain, 1e-9))), 2),
        "rms_in": round(rms, 5),
        "rms_out": round(float(np.sqrt(np.mean(np.square(out)))), 5),
        "peak_in": round(peak_in, 3),
        "peak_out": round(float(np.max(np.abs(out))), 3),
        "limited": limited,
    }


def truncate_wav(wav: bytes, max_seconds: float, fade_ms: float = 80.0):
    """Hard-cap a clip's duration. Returns (wav, info).

    A rambling generation can otherwise hold the channel for a minute. The
    tail is faded rather than cut square, since an abrupt stop mid-vowel is a
    audible click.
    """
    if max_seconds <= 0:
        return wav, {"truncated": False, "reason": "no limit"}
    try:
        data, sr = sf.read(io.BytesIO(wav), always_2d=True, dtype="float32")
    except Exception as e:
        return wav, {"truncated": False, "reason": f"decode failed: {e}"}

    mono = data.mean(axis=1)
    limit = int(max_seconds * sr)
    original = len(mono) / sr
    if len(mono) <= limit:
        return wav, {"truncated": False, "duration": round(original, 2)}

    out = mono[:limit].copy()
    fade = min(int(sr * fade_ms / 1000.0), len(out))
    if fade > 0:
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

    buf = io.BytesIO()
    sf.write(buf, out, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), {
        "truncated": True,
        "duration": round(len(out) / sr, 2),
        "original": round(original, 2),
    }


def normalize_wav(wav: bytes, target_dbfs: float = DEFAULT_TARGET_DBFS):
    """Normalise WAV bytes in and out. On any decode problem the original
    bytes are returned untouched -- never lose a turn over loudness."""
    try:
        data, sr = sf.read(io.BytesIO(wav), always_2d=True, dtype="float32")
    except Exception as e:
        return wav, {"applied": False, "reason": f"decode failed: {e}"}

    mono = data.mean(axis=1)
    out, info = normalize_array(mono, target_dbfs)
    if not info.get("applied"):
        return wav, info

    buf = io.BytesIO()
    sf.write(buf, out, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), info


# ---- acknowledgement cue -------------------------------------------------
# Discord's wire format. The cue is mixed straight into the outgoing stream by
# bot.py, so generating it at exactly this rate and layout means no resampling
# and no ffmpeg on the path.
CUE_RATE = 48000
CUE_CHANNELS = 2
# Well under speech level. This plays *underneath* someone talking, so it has
# to read as a notification rather than as a third participant.
CUE_PEAK = 0.16


def _blip(freq: float, seconds: float, peak: float) -> np.ndarray:
    n = int(CUE_RATE * seconds)
    t = np.arange(n, dtype=np.float32) / CUE_RATE
    wave = np.sin(2.0 * np.pi * freq * t, dtype=np.float32) * peak
    # Raised-cosine edges. A square start on a pure tone is an audible click,
    # and a click is exactly what a quiet notification must not have.
    edge = max(1, int(CUE_RATE * 0.008))
    ramp = (1.0 - np.cos(np.linspace(0.0, np.pi, edge, dtype=np.float32))) * 0.5
    wave[:edge] *= ramp
    wave[-edge:] *= ramp[::-1]
    return wave


def ack_pcm(peak: float = CUE_PEAK) -> bytes:
    """A short two-note rise: 'got it'.

    Synthesised rather than shipped as a file so there is no asset to lose and
    no sample-rate to convert. Returns interleaved stereo 16-bit PCM at
    CUE_RATE, which is what discord.py's mixer wants.
    """
    gap = np.zeros(int(CUE_RATE * 0.02), dtype=np.float32)
    mono = np.concatenate([_blip(880.0, 0.06, peak), gap,
                           _blip(1320.0, 0.07, peak)])
    stereo = np.repeat(mono[:, None], CUE_CHANNELS, axis=1)
    return (stereo * 32767.0).astype("<i2").tobytes()
