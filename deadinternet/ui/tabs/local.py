"""The local audio output: start it, stop it, pick a device.

Deliberately its own module rather than more of tabs/discord.py -- the whole
point of this output is that it has nothing to do with Discord.
"""
import gradio as gr

from ...events import VOICE
from ..feedback import note, warn


def start_local(app):
    ok, msg = app.start_local()
    app.events.add(VOICE, f"local output - {msg}")
    return note(msg) if ok else warn(msg)


def stop_local(app):
    if getattr(app.runtime, "kind", "") != "local":
        return warn("Local output is not the active output.")
    app.stop_output()
    app.events.add(VOICE, "local output stopped")
    return note("Local output stopped.")


def refresh_sinks(app):
    """Re-read the device list. Needed after creating a virtual sink, which
    is the first thing most people do here."""
    choices = app.local_sink_choices()
    return (gr.update(choices=choices),
            note(f"{len(choices) - 1} output device"
                 f"{'s' if len(choices) != 2 else ''} found."))


def set_sink(app, name):
    """Remember the device. Applied on the next start, or immediately if the
    output is already running -- start_local() rebuilds when it changed."""
    app.state.settings.local_sink = (name or "").strip()
    app.state.save()
    live = getattr(app.runtime, "kind", "") == "local"
    label = app.state.settings.local_sink or "system default"
    if live:
        ok, msg = app.start_local()
        return note(msg) if ok else warn(msg)
    return note(f"Output device set to **{label}**. Press Start local output.")


def set_stream(app, enabled, voice):
    """Turn the streaming voice on or off, or point it at someone else.

    Applied immediately when the local output is already running, because the
    whole point is a voice you are about to talk through.
    """
    s = app.state.settings
    s.stream_tts = bool(enabled)
    s.stream_tts_voice = (voice or "").strip()
    app.state.save()
    rt = app.runtime
    if getattr(rt, "kind", "") != "local":
        return note("Saved. It applies when the local output starts.")
    if not s.stream_tts or not s.stream_tts_voice:
        rt.close_stream()
        return note("Streaming voice off - back to buffered synthesis.")
    sp = app.state.get(s.stream_tts_voice)
    if sp is None:
        return warn(f"No speaker called {s.stream_tts_voice}.")
    if not rt.open_stream(sp):
        return warn(f"Could not open a streaming voice for {sp.name} - "
                    "see the log. Buffered synthesis still works.")
    app.events.add(VOICE, f"streaming voice: {sp.name}")
    return note(f"Streaming as **{sp.name}**. First line loads the model.")


def restart_tts(app, device):
    """Bring tts-server back up, optionally on the other backend.

    Slow on purpose -- it waits for the old process to release the card before
    launching the new one, because relaunching into VRAM that has not been
    freed yet reproduces the exact problem this button exists to fix.
    """
    try:
        ok, msg = app.restart_tts_server(device)
    except Exception as e:
        return warn(f"Restart failed - {e}")
    app.events.add(VOICE, f"tts-server restart ({device}) - {msg}")
    return note(msg) if ok else warn(msg)
