"""Run tab: start and stop the conversation, and speak a line by hand."""
import gradio as gr

from ...events import RUN, SETTING, SPEECH
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
    app.events.add(SETTING, f"who's talking: {', '.join(sorted(picked)) or 'nobody'}")
    n = len(picked)
    return note(f"{n} speaker{'s' if n != 1 else ''} active"
                + (f": {', '.join(sorted(picked))}" if 0 < n <= 6 else "."))


def start_run(app):
    msg = app.start_director()
    app.events.add(RUN, f"start pressed - {msg}")
    return note(msg)


def stop_run(app):
    msg = app.stop_director()
    app.events.add(RUN, f"stop pressed - {msg}")
    return note(msg)


def clear_context(app):
    if app.director:
        # Also drops the pre-generated turn, which was written against the
        # context we are throwing away.
        msg = app.director.clear_context()
    else:
        app.state.clear_transcript()
        msg = "LLM context cleared."
    app.events.add(RUN, msg)
    return note(msg)


def _speak_as(app, speaker, text):
    """Put one line in a speaker's mouth. -> a note()/warn() message.

    Shared by the typed box and the microphone so the two cannot drift: the
    guards below are the difference between a line being spoken and a line
    silently going nowhere, and having them in one place is what stops the mic
    path quietly missing one.
    """
    if not speaker:
        return warn("Pick a speaker.")
    if not text or not text.strip():
        return warn("Nothing to say.")

    # Say starts the director itself -- no need to press Start first.
    ok, msg = app.try_start()
    if not ok:
        return warn(msg)
    if not (app.runtime and app.runtime.connected()):
        return warn("Not in a voice channel - join one on the **Outputs** tab, "
                    "or nobody will hear it.")
    try:
        app.director.say_now(speaker, text.strip())
    except Exception as e:
        return warn(f"Failed - {e}")
    app.events.add(SPEECH, f"say as {speaker}: {text.strip()}")
    return note(f"{msg}Queued for {speaker}.")


def manual_say(app, speaker, text):
    if not text or not text.strip():
        return warn("Type something for them to say.")
    return _speak_as(app, speaker, text)


def say_from_mic(app, speaker, audio_path):
    """Transcribe a recording and speak it as `speaker`, immediately.

    A generator: transcription takes a second or two, and a button that sits
    dead for that long reads as broken. Yields progress to the action line,
    then clears the recorder so the next take does not append to the last.

    There is no review step by choice -- this is the "just talk" path. Whisper
    mistakes go out loud, which is the trade; the transcript is written to the
    event log either way so you can see what it actually heard.
    """
    if not speaker:
        yield warn("Pick a speaker first."), gr.update()
        return
    if not audio_path:
        yield warn("No recording - hold the mic button and speak first."), gr.update()
        return

    ok, why = app.whisper.available()
    if not ok:
        yield warn(why), gr.update()
        return

    yield "Transcribing...", gr.update()
    text, err = app.whisper.transcribe(audio_path)
    if err:
        app.events.add(SPEECH, f"transcription failed - {err}")
        yield warn(err), gr.update(value=None)
        return

    app.events.add(SPEECH, f"heard: {text}")
    # Clear the recorder in the same update that reports the result.
    yield _speak_as(app, speaker, text), gr.update(value=None)
