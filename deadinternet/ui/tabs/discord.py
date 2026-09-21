"""Discord connection: connect, resync, join and leave a voice channel."""
import gradio as gr

from ...events import VOICE
from ..feedback import note, warn


def connect_bot(app):
    state = app.state
    ok, msg = app.connect_discord()
    app.events.add(VOICE, f"connect bot - {msg}")
    labels = [c[0] for c in app.text_channel_choices()]
    return (gr.update(choices=[c[0] for c in app.channel_choices()]),
            gr.update(choices=labels,
                      value=app.text_channel_labels(state.settings.topic_channel_ids)),
            note(msg) if ok else warn(msg))


def disconnect_bot(app):
    ok, msg = app.disconnect_discord()
    app.events.add(VOICE, f"disconnect bot - {msg}")
    # Only the voice channels are cleared. The pin-channel checklist saves on
    # every change, so emptying it here would erase the saved selection.
    return gr.update(choices=[], value=None), note(msg) if ok else warn(msg)


def resync_connection(app):
    """Repopulate the channel lists if the bot is already connected from
    before this page load -- a reload (or a second tab) never re-checks this on
    its own otherwise, so a live connection looks disconnected and the
    pin-channel checklist shows empty until Connect bot is pressed again, even
    though nothing actually dropped.

    Deliberately does not attempt a fresh connection: only resyncs an existing
    one, so opening the page never connects the bot by itself.
    """
    if app.bot_ready():
        return connect_bot(app)
    return gr.update(), gr.update(), gr.update()


def join_channel(app, label):
    msg = app.join(label)
    app.events.add(VOICE, f"join {label} - {msg}")
    return note(msg)


def leave_channel(app):
    msg = app.leave()
    app.events.add(VOICE, f"leave - {msg}")
    return note(msg)
