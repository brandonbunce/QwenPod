"""Speakers tab: edit the roster, and write a persona from real chat history."""
import os
import time

import gradio as gr
import soundfile as sf

from ...config import PERSONA_SAMPLES, Speaker, VOICES_DIR
from ..feed import MINE_POLL
from ..feedback import note, warn
from ..selectors import NEW, selector_updates, selectors_unchanged


def load_speaker(app, sel):
    state = app.state
    blank = ("", None, "", "", "", 0)
    if not sel or sel == NEW:
        return (*blank, "New speaker - fill in the form and press Save.")
    sp = state.get(sel)
    if not sp:
        return (*blank, "Not found.")
    clip = sp.ref_wav if sp.ref_wav and os.path.exists(sp.ref_wav) else None
    return (sp.name, clip, sp.ref_text, sp.persona, sp.stims,
            sp.stim_chance, f"Editing **{sp.name}**.")


def save_speaker(app, name, clip, ref_text, persona, stims, stim_pct):
    state, tts = app.state, app.tts
    name = (name or "").strip()
    if not name:
        return (*selectors_unchanged(), warn("Give the speaker a name."))
    existing = state.get(name)
    stored = existing.ref_wav if existing else ""

    if clip:
        os.makedirs(VOICES_DIR, exist_ok=True)
        stored = os.path.join(VOICES_DIR, f"{name}.wav")
        # Normalise to mono WAV on the way in so the stored clip is exactly
        # what gets re-registered after a server restart.
        data, sr = sf.read(clip, always_2d=True)
        sf.write(stored, data.mean(axis=1), sr, format="WAV", subtype="PCM_16")
    if not stored:
        return (*selectors_unchanged(),
                warn("Upload a reference clip for this speaker."))

    sp = Speaker(
        name=name, ref_wav=stored, ref_text=(ref_text or "").strip(),
        # Enabled lives on the Run tab's "Who's talking" list now; keep
        # whatever it is set to rather than round-tripping it through a second
        # control that could disagree.
        persona=(persona or "").strip(),
        enabled=existing.enabled if existing else False,
        stims=(stims or "").strip(), stim_chance=float(stim_pct or 0),
    )
    state.upsert(sp)
    ok, msg = tts.register(name, stored, sp.ref_text, force=True)
    out = selector_updates(app, sel=name, man=name)
    if ok:
        return (*out, note(f"Saved '{name}'."))
    return (*out, warn(f"Saved '{name}' but registration failed - {msg}"))


def delete_speaker(app, sel):
    state, tts = app.state, app.tts
    if not sel or sel == NEW:
        return (*selectors_unchanged(), warn("Nothing selected."))
    state.remove(sel)
    tts.forget(sel)
    stored = os.path.join(VOICES_DIR, f"{sel}.wav")
    if os.path.exists(stored):
        os.remove(stored)
    return (*selector_updates(app, sel=NEW), note(f"Deleted '{sel}'."))


def build_persona(app, handle, current_name):
    """Read someone's message history and write a persona from it.

    A generator, because the scan makes hundreds of API calls and can run for
    minutes; the caller polls the future so the button reports progress instead
    of appearing hung.
    """
    try:
        fut, prog = app.persona_progress(handle)
    except Exception as e:
        yield gr.update(), gr.update(), warn(str(e))
        return

    yield gr.update(), gr.update(), "Starting scan..."
    while not fut.done():
        who = prog.get("resolved") or handle
        yield (gr.update(), gr.update(),
               f"Reading history for **{who}** - {prog.get('scanned', 0):,} messages "
               f"checked across {prog.get('channels', 0)} channels, "
               f"**{prog.get('found', 0)}** of theirs found "
               f"({prog.get('probes_done', 0)}/{prog.get('probes', 0)} probes).")
        time.sleep(MINE_POLL)

    try:
        resolved, samples, stats = fut.result()
    except Exception as e:
        yield gr.update(), gr.update(), warn(f"Scan failed - {e}")
        return

    if not samples:
        yield (gr.update(), gr.update(),
               warn(f"No messages found for '{handle}' after reading "
                    f"{stats['scanned']:,} messages in {stats['channels']} channels. "
                    "Check the spelling, or try their user ID - the handle is matched "
                    "against username, display name and nickname."))
        return

    yield (gr.update(), gr.update(),
           f"Found {len(samples)} messages from **{resolved}**. "
           f"Sampling {PERSONA_SAMPLES} and writing the persona - this takes a moment.")
    try:
        persona, used = app.write_persona(resolved, samples)
    except Exception as e:
        yield gr.update(), gr.update(), warn(f"Could not write the persona - {e}")
        return

    # Only fill the name if it is still empty; overwriting a name the user
    # already typed would silently retarget the save.
    name_up = (gr.update(value=resolved) if not (current_name or "").strip()
               else gr.update())
    yield (name_up, gr.update(value=persona),
           note(f"Built a persona for {resolved} from {used} of {len(samples)} "
                f"messages ({stats['scanned']:,} scanned). Read it over, then "
                "press Save speaker."))
