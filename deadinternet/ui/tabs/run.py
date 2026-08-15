"""Run tab: start and stop the conversation, and speak a line by hand."""
from ..feedback import note, warn


def set_enabled(app, names):
    """One-press enable/disable straight from the Run tab."""
    state = app.state
    picked = set(names or [])
    with state.lock:
        for sp in state.speakers:
            if sp.ref_wav:
                sp.enabled = sp.name in picked
    state.save()
    n = len(picked)
    return note(f"{n} speaker{'s' if n != 1 else ''} active"
                + (f": {', '.join(sorted(picked))}" if 0 < n <= 6 else "."))


def start_run(app):
    return note(app.start_director())


def stop_run(app):
    return note(app.stop_director())


def clear_context(app):
    if app.director:
        # Also drops the pre-generated turn, which was written against the
        # context we are throwing away.
        return note(app.director.clear_context())
    app.state.clear_transcript()
    return note("LLM context cleared.")


def manual_say(app, speaker, text):
    if not speaker:
        return warn("Pick a speaker.")
    if not text or not text.strip():
        return warn("Type something for them to say.")

    # Say starts the director itself -- no need to press Start first.
    ok, msg = app.try_start()
    if not ok:
        return warn(msg)
    if not (app.runtime and app.runtime.connected()):
        return warn("Not in a voice channel - join one at the top, or nobody "
                    "will hear it.")
    try:
        app.director.say_now(speaker, text.strip())
    except Exception as e:
        return warn(f"Failed - {e}")
    return note(f"{msg}Queued for {speaker}.")
