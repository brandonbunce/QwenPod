"""Generate and Clone tabs: register, delete and speak with a cloned voice."""
import os

import soundfile as sf

from ...config import Speaker, VOICES_DIR
from ..feedback import note, persist_clip, warn
from ..selectors import NEW, selector_updates, selectors_unchanged


def do_register(app, name, ref_audio, ref_text):
    state, tts = app.state, app.tts
    if not name or not name.strip():
        return (*selectors_unchanged(), warn("Give the voice a name."))
    if not ref_audio:
        return (*selectors_unchanged(), warn("Upload a reference clip."))
    name, ref_text = name.strip(), (ref_text or "").strip()

    stored = persist_clip(name, ref_audio)
    ok, msg = tts.register(name, stored, ref_text, force=True)
    if not ok:
        return (*selectors_unchanged(), warn(f"Registration failed - {msg}"))

    # Record it so boot can re-register it. Keep any persona/enabled state if
    # this name is already a Dead Internet speaker; otherwise park it disabled
    # so it does not silently join conversations.
    existing = state.get(name)
    state.upsert(Speaker(
        name=name, ref_wav=stored, ref_text=ref_text,
        persona=existing.persona if existing else "",
        enabled=existing.enabled if existing else False,
        # Preserve tics; re-registering a clip must not wipe them.
        stims=existing.stims if existing else "",
        stim_chance=existing.stim_chance if existing else 0.0,
    ))

    mode = "ICL (with transcript)" if ref_text else "speaker-embedding only"
    return (
        *selector_updates(app, voice=name, sel=name),
        note(f"Registered '{name}' - {mode}. Saved to `voices/{name}.wav`, "
             "so it survives a tts-server restart."),
    )


def do_delete_voice(app, name):
    state, tts = app.state, app.tts
    if not name:
        return (*selectors_unchanged(), warn("Nothing selected."))
    tts.forget(name)
    # Drop the saved clip too, or boot would resurrect it.
    state.remove(name)
    stored = os.path.join(VOICES_DIR, f"{name}.wav")
    if os.path.exists(stored):
        os.remove(stored)
    return (*selector_updates(app, sel=NEW),
            note(f"Removed '{name}' and deleted its saved clip."))


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
