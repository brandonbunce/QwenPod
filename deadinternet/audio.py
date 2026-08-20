"""Loudness normalisation for synthesised speech.

Different clones come back at noticeably different levels -- measured RMS
across our reference voices ranged from 0.053 to 0.105, which is a ~6 dB jump
between speakers in the same conversation. This levels them to a common RMS
with a peak ceiling so nothing clips.
"""
import io
import os
import subprocess

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


# ---- ad break bed --------------------------------------------------------
# Music runs before the read starts and after it ends, so the break sounds
# like a segment rather than a line with something behind it.
BED_LEAD_IN = 1.6
BED_TAIL = 2.0
BED_FADE = 1.2
# Ceiling on the bed's own normalisation gain, for the same reason MAX_GAIN
# exists above: a near-silent or badly-encoded track is a broken file, not
# something to amplify eighty-fold into hiss.
BED_MAX_SCALE = 8.0


def _decode(path: str, rate: int):
    """Any audio file -> mono float32 at `rate`, via ffmpeg.

    soundfile cannot read mp3/m4a/opus, which is most of what anyone has
    lying around to use as a bed. ffmpeg is already a hard dependency here --
    discord.py pipes through it and transcribe.py shells out to it.
    """
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "quiet", "-i", path,
         "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(f"could not decode {os.path.basename(path)}")
    return np.frombuffer(out.stdout, dtype="<f4").astype(np.float32)


def bed_under(speech_wav: bytes, music_path: str, gain: float = 0.22):
    """Lay a music bed under a spoken clip. -> (wav bytes, info).

    `gain` is a ratio against the speech, not an absolute multiplier: 0.22
    means the bed sits at 22% of the read's RMS, about 13 dB under it. It used
    to multiply the decoded file directly, which made the setting meaningless
    across a folder -- uploaded tracks are mastered anywhere from -20 to -6
    dBFS, so one bed was inaudible and the next buried the read, at the same
    slider position. Measuring both and scaling to the ratio is what makes the
    number mean the same thing for every track.

    The bed is measured over the window actually used, not the whole file: a
    track that opens on a quiet intro and lands on a chorus would otherwise be
    scaled by a level that appears nowhere in what gets played.

    Never raises: an ad break that cannot find its music should still be an ad
    break. On any failure the speech comes back untouched with the reason in
    info, and the caller plays it dry.
    """
    try:
        speech, sr = sf.read(io.BytesIO(speech_wav), always_2d=True, dtype="float32")
    except Exception as e:
        return speech_wav, {"bed": False, "reason": f"speech decode failed: {e}"}
    speech = speech.mean(axis=1)

    if not music_path:
        return speech_wav, {"bed": False, "reason": "no music uploaded"}
    try:
        music = _decode(music_path, sr)
    except Exception as e:
        return speech_wav, {"bed": False, "reason": str(e)}
    if music.size == 0:
        return speech_wav, {"bed": False, "reason": "empty track"}

    lead, tail = int(BED_LEAD_IN * sr), int(BED_TAIL * sr)
    total = lead + len(speech) + tail
    # Loop a short track rather than letting the bed stop under the read.
    if len(music) < total:
        music = np.tile(music, int(np.ceil(total / len(music))))
    # Start somewhere other than the top, so a bed reused every break does not
    # open on the same two seconds every time.
    start = 0 if len(music) <= total else int(np.random.default_rng().integers(
        0, len(music) - total))
    bed = music[start:start + total].copy()

    speech_rms = float(np.sqrt(np.mean(np.square(speech)))) if speech.size else 0.0
    bed_rms = float(np.sqrt(np.mean(np.square(bed)))) if bed.size else 0.0
    if speech_rms > SILENCE_RMS and bed_rms > SILENCE_RMS:
        scale = min(float(gain) * speech_rms / bed_rms, BED_MAX_SCALE)
        levelled = True
    else:
        # Nothing to measure against. Fall back to the old literal multiplier
        # rather than dividing by a level that is effectively zero.
        scale = float(gain)
        levelled = False
    bed *= scale

    fade = min(int(BED_FADE * sr), total // 2)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        bed[:fade] *= ramp
        bed[-fade:] *= ramp[::-1]

    mixed = bed
    mixed[lead:lead + len(speech)] += speech

    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > PEAK_CEILING:
        mixed *= PEAK_CEILING / peak

    buf = io.BytesIO()
    sf.write(buf, mixed, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), {
        "bed": True,
        "track": os.path.basename(music_path),
        "gain": round(float(gain), 3),
        # What the ratio actually cost this track, so a bed that still sounds
        # wrong can be told apart from a setting that was never applied.
        "scale": round(scale, 3),
        "levelled": levelled,
        "speech_rms": round(speech_rms, 5),
        "bed_rms_in": round(bed_rms, 5),
        "duration": round(total / sr, 2),
        "speech": round(len(speech) / sr, 2),
    }
