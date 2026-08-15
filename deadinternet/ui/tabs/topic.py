"""Topic tab: what the bot talks about, and where topics come from."""
import gradio as gr

from ..feed import crowd_md
from ...events import TOPIC
from ..feedback import note, warn


def set_topic(app, text):
    """Editing the topic by hand drops the credit: it is no longer the pinned
    message somebody posted."""
    state = app.state
    typed = (text or "").strip()
    if not typed:
        return gr.update(), "Topic left unchanged."
    with state.lock:
        changed = typed != state.settings.topic
        state.settings.topic = typed
        if changed:
            state.settings.topic_author = ""
    state.save()
    app.events.add(TOPIC, f"topic set by hand: {typed}")
    return gr.update(value=""), f"Topic set: **{typed[:80]}**"


def set_pin_channels(app, labels):
    """The checklist is empty until the bot connects and fills in the channel
    names. Saving that empty list would silently throw away the selection from
    the last session."""
    state = app.state
    if not app.text_channel_choices():
        n = len(state.settings.topic_channel_ids)
        return (f"Bot not connected - keeping the {n} pin channel(s) from "
                "last session.")
    with state.lock:
        state.settings.topic_channel_ids = app.text_channel_ids(labels)
    state.save()
    # A topic prefetched from the old pool is no longer representative.
    if app.director:
        app.director._discard_prepared()
    n = len(state.settings.topic_channel_ids)
    if state.settings.topic_rotation and not n:
        return "**Rotation is on but no pin channels are ticked** - nothing will rotate."
    return f"Pin pool: **{n} channel{'s' if n != 1 else ''}**."


def clear_crowd(app):
    state = app.state
    with state.lock:
        n = len(state.settings.crowd_topics)
        state.settings.crowd_topics = []
    state.save()
    return note(f"Cleared {n} submitted topic(s)."), crowd_md(app)


def switch_to_typed(app, text):
    """Make whatever is in the Topic box the topic, right now."""
    typed = (text or "").strip()
    if not typed:
        return warn("Type a topic in the box first.")
    if not app.director:
        return warn("Connect the bot first.")
    try:
        changed, queued = app.director.switch_to_topic(typed)
    except Exception as e:
        return warn(f"Failed - {e}")
    if queued:
        return note(f"Switching to your topic {queued}.")
    if not changed:
        d = app.director.topic_debug
        return warn(f"No switch - {d.get('last_error') or 'unknown'}.")
    return note(f"Topic now: **{typed[:70]}**")


def queue_typed(app, text):
    """Line the typed topic up to replace the next pin, without cutting in."""
    state = app.state
    typed = (text or "").strip()
    if not typed:
        return warn("Type a topic in the box first.")
    if not app.director:
        return warn("Connect the bot first.")
    app.director.queue_topic(typed)
    if state.settings.topic_rotation:
        mins = state.settings.topic_interval_minutes
        return note(f"Queued **{typed[:60]}** - it replaces the next pin "
                    f"(within {mins:g} min).")
    return note(f"Queued **{typed[:60]}**, but rotation is off so nothing will "
                "fire it. Use Switch to this now, or tick Rotate topic.")


def reseed_now(app):
    state = app.state
    if not app.director:
        return warn("Connect the bot first.")
    seed = app.director.reseed(state.settings.rng_seed or None)
    return note(f"Reshuffled - seed {seed}. Next cycle uses a new pin order.")


def rotate_now(app):
    if not app.director:
        return warn("Connect the bot first.")
    try:
        changed, msg = app.director.rotate_topic_now()
    except Exception as e:
        return warn(f"Failed - {e}")
    if msg:
        return note(f"Topic switch {msg}.")
    d = app.director.topic_debug
    if not changed:
        return warn(f"No switch - {d.get('last_error') or 'no pins found'}.")
    return note(f"Topic now: {d.get('current')}")
