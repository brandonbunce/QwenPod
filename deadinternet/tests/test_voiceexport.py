"""The voice export zip holds what the server heard, and nothing else.

Checks the file names (safe, unique), the transcript files (byte-exact, empty
for a voice-only clone), the audio format and length, the manifest, and that a
missing clip is reported rather than fatal.

Bit-exactness of the resampler against src/audio-resample.h is not checked
here -- that needs the C++ side built. It was verified by hand against
audio_read_mono_buf() at 8/11.025/16/22.05/24/32/44.1/48/96 kHz, mono and
stereo: zero differing samples.

    .venv-app/bin/python deadinternet/tests/test_voiceexport.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import io
import json
import os
import sys
import tempfile
import zipfile

import numpy as np
import soundfile as sf

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

# Before deadinternet.config is imported: see test_ui_build.py.
os.environ["DEADINTERNET_CONFIG"] = os.path.join(
    tempfile.mkdtemp(prefix="qwentts-test-"), "roster.json")

from deadinternet.config import Speaker  # noqa: E402
from deadinternet.voiceexport import (  # noqa: E402
    FOLDER, MANIFEST, export_zip, resample, safe_name, unique_names)


def _clip(path, seconds, sr, channels=1):
    t = np.arange(int(seconds * sr)) / sr
    x = 0.4 * np.sin(2 * np.pi * 220 * t)
    sf.write(path, np.repeat(x[:, None], channels, axis=1), sr, subtype="PCM_16")
    return path


def test_safe_name():
    assert safe_name("Big Dave") == "big_dave"
    assert safe_name("Zoë-2") == "zoe-2"
    assert safe_name("xX_l33t_Xx!!") == "xx_l33t_xx"
    assert safe_name("a/b\\c:d") == "abcd"
    assert safe_name("../etc") == "etc"
    assert safe_name("🙂") == "speaker"


def test_collisions_in_roster_order():
    assert unique_names(["Big Dave", "big dave", "BIG DAVE", "big_dave_2"]) == \
        ["big_dave", "big_dave_2", "big_dave_3", "big_dave_2_2"]


def test_resample_length_matches_server():
    # ceil(sr_out * n / sr_in), as audio_resample() computes it.
    for sr, n in ((44100, 44101), (16000, 7), (22050, 1), (48000, 3)):
        assert len(resample(np.zeros(n), sr)) == -(-24000 * n // sr)


def test_export():
    work = tempfile.mkdtemp(prefix="qwentts-export-")
    path = os.path.join(work, "export.zip")
    speakers = [
        Speaker(name="Big Dave", ref_wav=_clip(os.path.join(work, "a.wav"), 2.0, 44100, 2),
                ref_text="Héllo — it's me.  ", persona="SECRET PERSONA",
                dynamic_persona="SECRET EVOLVED"),
        Speaker(name="big dave", ref_wav=_clip(os.path.join(work, "b.wav"), 1.5, 24000),
                ref_text=""),
        Speaker(name="Ghost", ref_wav=os.path.join(work, "gone.wav"), ref_text="boo"),
        Speaker(name="No Clip", ref_wav="", ref_text="never exported"),
    ]
    manifest, warnings = export_zip(speakers, path)

    z = zipfile.ZipFile(path)
    files = {n[len(FOLDER) + 1:]: z.read(n) for n in z.namelist()}
    assert sorted(files) == sorted([
        "big_dave.wav", "big_dave.txt", "big_dave_2.wav", "big_dave_2.txt", MANIFEST])
    assert all(n.startswith(FOLDER + "/") for n in z.namelist())
    assert len(warnings) == 1 and "Ghost" in warnings[0] and "missing" in warnings[0]

    for stem in ("big_dave", "big_dave_2"):
        info = sf.info(io.BytesIO(files[f"{stem}.wav"]))
        assert (info.samplerate, info.channels, info.subtype) == (24000, 1, "PCM_16")

    assert files["big_dave.txt"] == "Héllo — it's me.  ".encode("utf-8")
    assert files["big_dave_2.txt"] == b""

    # A 24 kHz clip passes through the server untouched, so it must here too.
    sent, _ = sf.read(speakers[1].ref_wav, dtype="int16")
    got, _ = sf.read(io.BytesIO(files["big_dave_2.wav"]), dtype="int16")
    assert np.array_equal(sent, got)

    assert json.loads(files[MANIFEST]) == manifest == {
        "big_dave": {"speaker": "Big Dave", "seconds": 2.0},
        "big_dave_2": {"speaker": "big dave", "seconds": 1.5},
    }

    # Nothing but audio and transcripts leaves the roster.
    for data in files.values():
        assert b"SECRET" not in data

    # The zip is the only thing written.
    assert sorted(os.listdir(work)) == ["a.wav", "b.wav", "export.zip"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
