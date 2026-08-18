"""Speakers tab: edit the roster, and write a persona from real chat history."""
import os
import time

import gradio as gr
import soundfile as sf

from ...config import PERSONA_SAMPLES, Speaker, VOICES_DIR
from ...events import VOICE
from ..feed import MINE_POLL
from ..feedback import note, warn
from ..selectors import (NEW, roster_choices, selector_updates,
                         selectors_unchanged)


# Shown in the dynamic box when a character has not evolved yet. Not a real
# value -- reset_dynamic and save_speaker both ignore it.
NO_DYNAMIC = ""


def load_speaker(app, sel):
    state = app.state
    blank = ("", None, "", "", NO_DYNAMIC, "", 0)
    if not sel or sel == NEW:
        return (*blank, "New speaker - fill in the form and press Save.")
    sp = state.get(sel)
    if not sp:
        return (*blank, "Not found.")
    clip = sp.ref_wav if sp.ref_wav and os.path.exists(sp.ref_wav) else None
    return (sp.name, clip, sp.ref_text, sp.persona, sp.dynamic_persona,
            sp.stims, sp.stim_chance, f"Editing **{sp.name}**.")


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
        # Read from the stored speaker, never from the form. The dynamic box
        # is display-only and a rewrite can land between the page rendering it
        # and you pressing Save -- taking it from the form would quietly undo
        # an evolution that had already happened.
        dynamic_persona=existing.dynamic_persona if existing else "",
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


def transcribe_clip(app, clip):
    """Fill the reference transcript from the reference clip itself.

    The transcript is what the voice is cloned against, so typing it out by
    hand is both the slowest part of adding a speaker and the easiest to get
    subtly wrong -- a missing word makes the clone worse, and nothing tells
    you. Whisper reads the clip you just gave it, which is the same audio the
    cloner will hear.

    Wired to the clip's upload and stop_recording events, not its change event:
    change also fires when picking a speaker off the roster loads their saved
    clip, which would re-transcribe on every click and overwrite a transcript
    that was already correct. A generator, so a long clip reports progress.
    """
    if not clip:
        yield gr.update(), warn("Add a reference clip first.")
        return

    ok, why = app.whisper.available()
    if not ok:
        # Not fatal: the transcript is optional, so say what is missing and
        # leave the box alone rather than making this look like a failure.
        yield gr.update(), warn(why)
        return

    yield gr.update(), "Reading the reference clip..."
    text, err = app.whisper.transcribe(clip)
    if err:
        app.events.add(VOICE, f"reference transcript failed - {err}")
        yield gr.update(), warn(err)
        return

    app.events.add(VOICE, f"reference transcript: {text}")
    yield (gr.update(value=text),
           note("Transcribed the reference clip. **Read it over** - a wrong "
                "word here makes the clone worse and nothing else will tell "
                "you. Then press **Save speaker**."))


def reset_dynamic(app, sel):
    """Throw away the evolved prompt and go back to what you wrote."""
    state = app.state
    if not sel or sel == NEW:
        return gr.update(), warn("Pick a speaker first.")
    sp = state.get(sel)
    if not sp:
        return gr.update(), warn("Not found.")
    if not (sp.dynamic_persona or "").strip():
        return gr.update(), note(f"{sp.name} has not evolved yet - nothing to reset.")
    with state.lock:
        sp.dynamic_persona = ""
    state.save()
    app.events.add(VOICE, f"reset {sp.name} to their base system prompt")
    return (gr.update(value=NO_DYNAMIC),
            note(f"**{sp.name}** is back to their base system prompt."))


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


def _step_roster(app, current, delta):
    """Move the roster selection by one, wrapping at both ends.

    The roster is a horizontal strip that scrolls sideways once there are more
    speakers than fit; the arrows exist so it stays reachable without a
    horizontal scroll gesture. Wrapping rather than clamping means holding one
    arrow always eventually reaches everything.
    """
    names = [name for _, name in roster_choices(app)]
    if not names:
        return gr.update()
    try:
        i = names.index(current)
    except ValueError:
        # Nothing selected, or the selection was just deleted: start at the top.
        return gr.update(value=names[0])
    return gr.update(value=names[(i + delta) % len(names)])


def roster_prev(app, current):
    return _step_roster(app, current, -1)


def roster_next(app, current):
    return _step_roster(app, current, +1)
