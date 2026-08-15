"""Behaviour tab: mode, LLM backend, and the conversation-tuning knobs."""
import gradio as gr

from ...config import MODE_MANUAL, RECOMMENDED
from ...llm import PROVIDER_OPENAI
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
