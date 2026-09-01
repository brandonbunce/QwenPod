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
