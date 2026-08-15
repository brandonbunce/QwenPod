"""User feedback, and the two helpers that depend on nothing else.

These were nested inside build() but closed over nothing from it, so they lift
out unchanged -- same names, same signatures, same bodies.
"""
import os
import re

import gradio as gr
import soundfile as sf

from ..config import TEMPLATE_VARS, VOICES_DIR

_MD = re.compile(r"[*`_]+")


def note(msg, level="info"):
    """Toast it and return it for the sticky action line.

    Every action reports twice on purpose: the toast is visible wherever you
    have scrolled to, the action line is still there a minute later.
    """
    text = _MD.sub("", str(msg)).strip()
    if text:
        try:
            (gr.Warning if level == "warn" else gr.Info)(text[:300])
        except Exception:  # never let a toast break the handler
            pass
    return msg


def warn(msg):
    return note(msg, "warn")


def tpl_vars(field):
    """The {placeholders} a template box accepts, for its tooltip."""
    pairs = TEMPLATE_VARS.get(field, {})
    return "Variables: " + ", ".join(f"{k} = {v}" for k, v in pairs.items())


def persist_clip(name, ref_audio):
    """Copy a reference clip into voices/ as mono WAV and return the path.

    The server only keeps registrations in memory, so without a clip on disk a
    restart loses the clone for good.
    """
    os.makedirs(VOICES_DIR, exist_ok=True)
    stored = os.path.join(VOICES_DIR, f"{name}.wav")
    data, sr = sf.read(ref_audio, always_2d=True)
    sf.write(stored, data.mean(axis=1), sr, format="WAV", subtype="PCM_16")
    return stored
