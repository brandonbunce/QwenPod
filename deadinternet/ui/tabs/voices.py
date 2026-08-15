"""Testing tab: refresh the voice list, and speak a line with a chosen voice.

Registration used to live here too, behind a Clone tab. That tab is gone: the
Speakers tab already registers a voice as part of saving a speaker, so having a
second path that created voices with no persona attached was two ways to do one
thing.
"""
import soundfile as sf

from ..feedback import note, warn
from ..selectors import selector_updates


def refresh_voices(app):
    """Repopulate every voice list, re-registering with tts-server first.

    The list is built once when the page is created, so a tts-server that was
    down then -- or restarted since, which wipes its in-memory registry --
    leaves an empty dropdown with nothing to explain it.
    """
    ok, msg = app.resync_voices()
    return (*selector_updates(app), note(msg) if ok else warn(msg))


def do_synth(app, text, voice, instructions,
             temperature, top_k, top_p, rep_pen, seed, max_new):
    state, tts = app.state, app.tts
    if not text or not text.strip():
        return None, "Enter some text."
    if not voice:
        # An empty dropdown almost always means tts-server was down when the
        # page was built, or has been restarted since and lost its in-memory
        # registry. Say so instead of "pick a voice" from a list with nothing
        # in it.
        if not app.tts_alive():
            return None, ("**tts-server is not running** at "
                          f"`{state.settings.tts_url}`. Start it, then press "
                          "**Refresh voices**.")
        return None, "Pick a voice - press **Refresh voices** if the list is empty."
    gen = {
        "temperature": temperature,
        "top_k": int(top_k),
        "top_p": top_p,
        "repetition_penalty": rep_pen,
        "max_new_tokens": int(max_new),
    }
    if instructions and instructions.strip():
        gen["instructions"] = instructions.strip()
    if int(seed) >= 0:
        gen["seed"] = int(seed)
    try:
        wav = tts.synth(text, voice, **gen)
    except Exception as e:
        return None, f"Generation failed - {e}"
    out = "/tmp/qwentts_ui_out.wav"
    with open(out, "wb") as f:
        f.write(wav)
    info = sf.info(out)
    return out, f"{info.duration:.2f}s @ {info.samplerate} Hz"
