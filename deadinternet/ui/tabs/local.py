"""The local audio output: start it, stop it, pick a device.

Deliberately its own module rather than more of tabs/discord.py -- the whole
point of this output is that it has nothing to do with Discord.
"""
import gradio as gr

from ... import audiodev
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


def _mic_result(app, ok, msg):
    """-> (device list, status line, action line), shared by the mic buttons.

    The device list is refreshed every time because making or removing the
    virtual sink is exactly what changes it.
    """
    app.events.add(VOICE, f"virtual mic - {msg}")
    return (gr.update(choices=app.local_sink_choices()), audiodev.report(),
            note(msg) if ok else warn(msg))


def create_mic(app):
    return _mic_result(app, *audiodev.create_mic())


def remove_mic(app):
    return _mic_result(app, *audiodev.remove_mic())


def monitor_on(app):
    return _mic_result(app, *audiodev.start_monitor(
        volume=app.state.settings.local_monitor_volume))


def monitor_off(app):
    return _mic_result(app, *audiodev.stop_monitor())


def set_monitor_volume(app, pct):
    app.state.settings.local_monitor_volume = float(pct)
    app.state.save()
    ok, msg = audiodev.set_monitor_volume(pct)
    return note(msg) if ok else warn(msg)


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


def stop_tts(app):
    """Shut tts-server down and hand the card back.

    Reports what was actually freed rather than just claiming success: the
    driver releases VRAM asynchronously, so "stopped" and "the card is yours"
    are not the same statement, and the second one is the one being asked for.
    """
    from ...config import vram_info
    before = vram_info()
    try:
        ok, msg = app.stop_tts_server()
    except Exception as e:
        return warn(f"Could not stop tts-server - {e}")
    if not ok:
        return note(msg)
    after = vram_info()
    freed = ""
    if before and after:
        freed = (f" Freed {max(0.0, before[0] - after[0]):.1f} GB - card is "
                 f"now {after[0]:.1f}/{after[1]:.1f} GB.")
    app.events.add(VOICE, f"tts-server stopped -{freed or ' ' + msg}")
    return note(f"{msg}{freed} Nothing can speak until it comes back; the next "
                "thing that needs it will start it again.")
