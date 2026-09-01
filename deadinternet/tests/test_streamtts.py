"""Streaming TTS: WAV-header stripping, makeup gain, and the fallbacks.

Lives in the repo rather than in a scratch file because the header stripping is
the one piece here with no visible failure mode -- a partial header forwarded
to the sound card is a burst of noise inside otherwise fine speech, which
sounds like a model glitch rather than a framing bug. That exact bug existed
and this suite is what found it.

No qwen-tts, no paplay and no audio device: the process pair is faked, so this
runs on a machine with no GPU and no sound.

Runs two ways, matching test_ui_build.py:

    .venv-app/bin/python deadinternet/tests/test_streamtts.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import asyncio
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

from deadinternet import streamtts as S

HEADER = b"RIFF" + b"\x00" * 40      # exactly WAV_HEADER bytes, like the real one


def _wav(pcm):
    return HEADER + pcm


def _voice(gain=1.0):
    v = S.StreamingVoice("b", "m", "c", "r", "Alice", gain=gain, log=lambda m: None)
    sink = io.BytesIO()
    sink.flush = lambda: None
    v.play = types.SimpleNamespace(stdin=sink, poll=lambda: None)
    return v


class _Chunks:
    """A stdout that hands back the reads it was given, in order."""

    def __init__(self, parts):
        self.parts = list(parts)

    def read(self, n):
        return self.parts.pop(0) if self.parts else b""


def test_payloads_survive_and_headers_do_not():
    a, b = bytes(range(0, 200)), bytes(range(50, 150))
    v = _voice()
    v.proc = types.SimpleNamespace(stdout=io.BytesIO(_wav(a) + _wav(b)),
                                   poll=lambda: None)
    v._forward()
    out = v.play.stdin.getvalue()
    assert out == a + b, f"{len(out)} bytes, expected {len(a + b)}"
    assert b"RIFF" not in out


def test_header_split_across_reads():
    """The bug this suite was written for: a header arriving in pieces used to
    be forwarded as audio by the hold-back write."""
    pcm = bytes(range(0, 200))
    blob = _wav(pcm)
    v = _voice()
    v.proc = types.SimpleNamespace(
        stdout=_Chunks([blob[:2], blob[2:10], blob[10:]]), poll=lambda: None)
    v._forward()
    assert v.play.stdin.getvalue() == pcm


def test_audio_before_an_incomplete_header_still_flows():
    pcm, nxt = bytes(range(0, 100)), bytes(range(10, 60))
    blob = pcm + _wav(nxt)
    v = _voice()
    v.proc = types.SimpleNamespace(
        stdout=_Chunks([blob[:120], blob[120:]]), poll=lambda: None)
    v._forward()
    assert v.play.stdin.getvalue() == pcm + nxt


def test_gain_is_applied_and_clipped():
    raw = np.array([1000, -1000, 20000, -20000], dtype="<i2").tobytes()
    got = np.frombuffer(_voice(gain=2.0)._amplify(raw), dtype="<i2")
    assert got[0] == 2000 and got[1] == -2000
    # int16 floor is -32768; clipping, never wrapping.
    assert got[2] == 32767 and got[3] == -32768


def test_gain_of_one_is_a_no_op():
    raw = np.array([1, -1, 300], dtype="<i2").tobytes()
    assert _voice(gain=1.0)._amplify(raw) == raw


def test_odd_trailing_byte_is_carried_not_emitted():
    """It must NOT be appended to this chunk's output -- that was the bug.
    It belongs to a sample whose second half has not arrived, so it waits and
    is parsed with its partner on the next call."""
    v = _voice(gain=2.0)
    out = v._amplify(np.array([1000, -1000], dtype="<i2").tobytes() + b"\x07")
    assert len(out) == 4, f"emitted {len(out)} bytes, expected 4"
    assert v._carry == b"\x07"
    # The held byte reappears, in the right place, once its partner lands.
    rest = v._amplify(b"\x00")
    assert np.frombuffer(rest, dtype="<i2")[0] == 7 * 2
    assert v._carry == b""


def test_say_refuses_when_dead_or_empty():
    v = _voice()
    v.proc = None
    assert v.say("hello") is None
    v = _voice()
    v.proc = types.SimpleNamespace(
        stdout=io.BytesIO(), poll=lambda: None,
        stdin=types.SimpleNamespace(write=lambda b: None, flush=lambda: None,
                                    closed=False))
    assert v.say("   ") is None


def _director(stream_voice):
    from deadinternet.config import Speaker, State
    from deadinternet.director import Director
    d = Director.__new__(Director)
    state = State.__new__(State)
    state.speakers = [Speaker(name="Alice", ref_wav="/x.wav", enabled=True)]
    state.transcript = []
    state.events = None
    import threading
    state.lock = threading.RLock()
    state.settings = types.SimpleNamespace(max_history=20)
    d.state = state
    d.log = lambda m: None
    d._manual_q = asyncio.Queue()
    d.runtime = types.SimpleNamespace(
        stream=types.SimpleNamespace(voice=stream_voice, alive=lambda: True),
        stream_say=lambda text: 0.01)
    return d


def test_matching_speaker_streams():
    d = _director("Alice")
    used = asyncio.run(d._try_stream(("Alice", "hello")))
    assert used is True
    assert [t.text for t in d.state.transcript] == ["hello"]


def test_other_speaker_is_put_back_for_the_buffered_path():
    d = _director("Alice")
    used = asyncio.run(d._try_stream(("Bob", "hello")))
    assert used is False
    assert d._manual_q.qsize() == 1


def test_no_streamer_falls_through():
    from deadinternet.director import Director
    d = Director.__new__(Director)
    d.runtime = types.SimpleNamespace(stream=None)
    d._manual_q = asyncio.Queue()
    assert asyncio.run(d._try_stream(("Alice", "hi"))) is False


def test_odd_chunks_keep_the_sample_grid():
    """The static bug: _forward slices on arbitrary byte boundaries, so an
    odd-length chunk used to leave the next one starting half a sample late.
    Parsed as int16 that byte-swaps every sample -- garbage values, a wildly
    wrong level estimate, and audible noise inside otherwise fine speech."""
    pcm = np.arange(-3000, 3000, 7, dtype="<i2").tobytes()
    whole = _voice(gain=1.0)._amplify(pcm)
    # Same bytes, handed over in awkward pieces.
    v = _voice(gain=1.0)
    pieces, i = [], 0
    for n in (1, 3, 5, 2, 7, 11):
        pieces.append(v._amplify(pcm[i:i + n]))
        i += n
    pieces.append(v._amplify(pcm[i:]))
    assert b"".join(pieces) == whole, "odd chunking changed the samples"


def test_level_estimate_survives_odd_chunking():
    pcm = (np.sin(np.arange(4000) / 8.0) * 8000).astype("<i2").tobytes()
    a = _voice(gain=1.0)
    a._amplify(pcm)
    straight = np.concatenate(a._level)
    b = _voice(gain=1.0)
    i = 0
    for n in (1, 3, 5, 2, 7, 11, 1001):
        b._amplify(pcm[i:i + n])
        i += n
    b._amplify(pcm[i:])
    chunked = np.concatenate(b._level)
    ra = float(np.sqrt((straight ** 2).mean()))
    rb = float(np.sqrt((chunked ** 2).mean()))
    assert abs(ra - rb) < 1e-6, f"level estimate differs: {ra:.5f} vs {rb:.5f}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ok    {name}")
        except Exception as e:
            failures += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print("PASS" if not failures else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
