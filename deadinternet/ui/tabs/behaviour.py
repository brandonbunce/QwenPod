"""Behaviour tab: mode, LLM backend, and the conversation-tuning knobs."""
import os
import shutil

import gradio as gr

from ...config import MODE_MANUAL, RECOMMENDED
from ...llm import PROVIDER_OPENAI
from ...events import SETTING
from ..feedback import note, warn


def set_mode(app, m):
    state = app.state
    with state.lock:
        state.settings.mode = m
    state.save()
    # Cut the line that is mid-playback; the loop drops the pre-generated one
    # on its next pass.
    if m == MODE_MANUAL and app.runtime:
        app.runtime.interrupt()
    app.events.add(SETTING, f"mode -> {m}")
    return note(f"Mode: **{m}**.")


def rebuild_note(app):
    """Swap the backend in place so a running director picks it up on its next
    turn without a restart."""
    active = app.rebuild_llm()
    ok, why = active.available()
    return f"{app.state.settings.provider}: {active.model}" if ok else f"**{why}**"


def reset_tuning(app):
    """Put the four conversation-tuning controls back to values that are known
    to sound right."""
    state = app.state
    with state.lock:
        s = state.settings
        s.gap_seconds = RECOMMENDED["gap_seconds"]
        s.temperature = RECOMMENDED["temperature"]
        s.num_predict = RECOMMENDED["num_predict"]
        s.max_history = RECOMMENDED["max_history"]
    state.save()
    return (
        gr.update(value=RECOMMENDED["gap_seconds"]),
        gr.update(value=RECOMMENDED["temperature"]),
        gr.update(value=RECOMMENDED["num_predict"]),
        gr.update(value=RECOMMENDED["max_history"]),
        note("Tuning reset to recommended and saved."),
    )


def refresh_models(app, provider):
    probe = app.make_client(provider)
    found = probe.models()
    if not found:
        ok, why = probe.available()
        return gr.update(), gr.update(), warn(why or "No models returned.")
    if provider == PROVIDER_OPENAI:
        return gr.update(), gr.update(choices=found), note(f"{len(found)} OpenAI models.")
    return gr.update(choices=found), gr.update(), note(f"{len(found)} Ollama models.")

def music_report(app):
    """What is in music/, as markdown."""
    from ...config import music_tracks
    tracks = music_tracks()
    if not tracks:
        return "_No music uploaded - ad breaks will play dry._"
    head = f"**{len(tracks)} track{'s' if len(tracks) != 1 else ''}**, one picked at random each break."
    return head + "\n\n" + "\n".join(f"- `{os.path.basename(t)}`" for t in tracks)


def upload_music(app, files):
    """Copy uploaded beds into music/. -> (report, action line)."""
    from ...config import MUSIC_DIR, MUSIC_EXTS
    if not files:
        return music_report(app), warn("Nothing uploaded.")
    os.makedirs(MUSIC_DIR, exist_ok=True)
    added, skipped = [], []
    for f in files:
        src = getattr(f, "name", f)
        base = os.path.basename(src)
        if not base.lower().endswith(MUSIC_EXTS):
            skipped.append(base)
            continue
        # basename() only -- an uploaded filename is attacker-controlled in
        # principle, and a path in it would write outside music/.
        shutil.copyfile(src, os.path.join(MUSIC_DIR, base))
        added.append(base)
    app.events.add(SETTING, f"uploaded music: {', '.join(added) or 'nothing'}")
    msg = f"Added {len(added)} track{'s' if len(added) != 1 else ''}."
    if skipped:
        msg += f" Skipped {len(skipped)} unsupported: {', '.join(skipped[:3])}."
    return music_report(app), note(msg)


def clear_music(app):
    """Empty music/. Ad breaks fall back to playing dry."""
    from ...config import MUSIC_DIR, music_tracks
    tracks = music_tracks()
    for t in tracks:
        try:
            os.remove(t)
        except OSError:
            pass
    app.events.add(SETTING, f"removed {len(tracks)} music track(s)")
    return music_report(app), note(
        f"Removed {len(tracks)} track{'s' if len(tracks) != 1 else ''}. "
        "Ad breaks will play dry until you upload more.")
