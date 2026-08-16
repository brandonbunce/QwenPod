"""Run tab: start and stop the conversation, and speak a line by hand."""
import os

import gradio as gr
from gradio.utils import get_upload_folder

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
    """Transcribe an uploaded take and speak it as `speaker`, immediately.

    audio_path arrives as a string in a hidden textbox, written by the recorder
    in ui/mic.py after it POSTs the blob to gradio's upload endpoint. A
    generator, because transcription takes a second or two and a control that
    sits dead for that long reads as broken.

    One call is one sentence, not one press: the recorder cuts a continuous
    take at every pause. Clearing the textbox on the way out is therefore part
    of the protocol and not just tidiness -- the recorder watches for it before
    releasing the next clip, which is what keeps the lines in the order they
    were said.

    There is no review step by choice -- this is the "just talk" path. Whisper
    mistakes go out loud, which is the trade; the transcript is written to the
    event log either way so you can see what it actually heard.
    """
    # Fires on change, including this handler clearing the field on its way
    # out. Two no-op updates end that cycle rather than starting another.
    if not audio_path or not str(audio_path).strip():
        yield gr.update(), gr.update()
        return
    audio_path = str(audio_path).strip()

    # The path arrives from the browser, so it decides what the server opens.
    # Only files gradio itself just wrote are acceptable; without this, anyone
    # who can reach the page could name /etc/passwd and have it fed to ffmpeg.
    # Single-user app on localhost today, but the repo is public and this is
    # one line.
    cache = os.path.realpath(get_upload_folder())
    if os.path.commonpath([os.path.realpath(audio_path), cache]) != cache:
        app.events.add(SPEECH, f"rejected mic path outside the upload cache: {audio_path}")
        yield warn("That recording did not come from this page."), gr.update(value="")
        return

    if not speaker:
        yield warn("Pick a speaker first."), gr.update(value="")
        return

    ok, why = app.whisper.available()
    if not ok:
        yield warn(why), gr.update(value="")
        return

    yield "Transcribing...", gr.update()
    text, err = app.whisper.transcribe(audio_path)
    if err:
        app.events.add(SPEECH, f"transcription failed - {err}")
        yield warn(err), gr.update(value="")
        return

    app.events.add(SPEECH, f"heard: {text}")
    # Clear the field in the same update that reports the result.
    yield _speak_as(app, speaker, text), gr.update(value="")
