"""The live feed: what the page refreshes on a timer, and the two boot streams.

gr.Timer never fires in gradio 6.22, so these are generators the client streams
from via demo.load rather than anything scheduled server-side.

poll() used to return a bare 12-tuple, with the output list it had to agree
with sitting 550 lines away. Same failure mode as the selectors: wrong order,
matching arity, no exception -- the transcript would simply appear in the
diagnostics box. FeedUpdate names the fields at the point they are produced.
"""
import time
from typing import Any, NamedTuple

import gradio as gr

from .pipeview import pipeline_html

# Live-update cadence, and how long one browser's feed runs before it has to be
# restarted by a reload or the Refresh button.
POLL_INTERVAL = 1.5
POLL_LIFETIME = 3600

# The raw box updates on its own, faster cadence. It is the one thing here
# where 1.5s reads as stuttering rather than live, and it is also the cheapest
# thing to produce -- a string join off a bounded buffer, against a full poll's
# four reports and a transcript render. So the feed ticks at RAW_INTERVAL and
# does the expensive work only every POLL_INTERVAL.
#
# One generator, not two: gradio's queue has four concurrency slots for the
# whole app, and a second permanent stream per open tab would spend them.
RAW_INTERVAL = 0.3

# How long a freshly loaded page waits for the startup tts-server boot to
# register the roster before giving up and leaving it to Refresh voices.
# Generous: it covers loading the model plus re-encoding every speaker.
VOICE_BOOT_WAIT = 300

# How often the persona builder reports progress while the scan runs.
MINE_POLL = 1.0


class FeedUpdate(NamedTuple):
    """One value per component in the feed's output list, in that order.

    The action line is deliberately absent -- it is written only by handlers,
    so a button's confirmation survives longer than one POLL_INTERVAL. Adding
    it here is the single easiest way to reintroduce that bug.
    """
    status: Any        # header status bar
    pipe: Any          # Run tab: the live stage strip
    raw: Any           # Run tab: what the model is emitting, live
    transcript: Any    # Run tab
    run_topic: Any     # Run tab: current topic, with who pinned it
    run_queue: Any     # Run tab: upcoming topics
    topic_queue: Any   # Topic tab: the same queue again
    crowd: Any         # Topic tab: /topics submissions
    dbg_voice: Any     # Diagnostics
    dbg_chat: Any
    dbg_norm: Any
    dbg_topic: Any
    dbg_comedy: Any
    topic_box: Any     # Inputs tab text box -- only pushed when it changed
    topic_from: Any    # ...and who pinned it, on the same check
    services: Any      # Diagnostics: tts-server / LLM / this server
    log: Any           # Diagnostics: the event log


def queue_md(app):
    """Upcoming topics, so the rotation order is inspectable."""
    if not app.director:
        return "_(connect the bot to see the queue)_"
    # A switch in progress goes on top: it is a minute of stages that make no
    # sound, and without this a button press looks like it did nothing.
    switching = app.director.switching.render()
    head = [switching, "---"] if switching else []
    q = app.director.topic_queue()
    if not q:
        return "\n\n".join(head + [
            "_No source is both enabled and stocked. Give one weight on "
            "the Topics tab._"])
    # Already-formatted markdown lines, not a numbered list -- the sources are
    # pools with weights, not a single ordered sequence.
    return "\n\n".join(head + q[:24])


def crowd_md(app):
    """What people have submitted with /topic, oldest first."""
    pend = list(app.state.settings.crowd_topics)
    if not pend:
        return "_(none submitted yet)_"
    rows = []
    for raw in pend[:40]:
        who, _, text = raw.partition("\x1f")
        rows.append(f"- **{who or 'someone'}**: {text or who}")
    extra = len(pend) - len(rows)
    if extra > 0:
        rows.append(f"_...and {extra} more_")
    return "\n".join(rows)


def poll(app, last_topic=None):
    state = app.state
    lines = []
    for t in state.recent(20):
        if t.kind == "user":
            tag = "**you**"
        elif t.kind == "ad":
            # Labelled, or a sponsor read is indistinguishable from the host
            # saying something very strange.
            tag = f"**{t.speaker}** _(ad)_"
        else:
            tag = f"**{t.speaker}**"
        lines.append(f"{tag}: {t.text}")
    # Rotation rewrites the topic, so push it back into the box -- but only
    # when it actually changed, or the 1.5s feed would fight anyone typing in
    # there. The sender rides along on the same check, so clearing it locally
    # is not undone a second later.
    topic = state.settings.topic
    if topic == last_topic:
        topic_up, from_up = gr.update(), gr.update()
    else:
        topic_up = gr.update(value=topic)
        from_up = gr.update(value=state.settings.topic_author)
    author = state.settings.topic_author
    run_topic = (topic or "_(none)_") + (f"  \n_pinned by {author}_" if author else "")
    q = queue_md(app)
    return FeedUpdate(
        status=app.status_line(),
        pipe=pipeline_html(app),
        raw=app.raw.render(),
        transcript="\n\n".join(lines) if lines else "_(nothing yet)_",
        run_topic=run_topic,
        run_queue=q,
        topic_queue=q,          # same queue, shown on the Topics tab too
        crowd=crowd_md(app),
        dbg_voice=app.voice_report(),
        dbg_chat=app.chat_report(),
        dbg_norm=app.norm_report(),
        dbg_topic=app.topic_report(),
        dbg_comedy=app.comedy_report(),
        topic_box=topic_up,
        topic_from=from_up,
        services=app.service_report(),
        log=app.events.render(),
    )


def raw_only(app):
    """A feed update that touches nothing but the raw box.

    gr.update() with no arguments is a no-op the client applies as "leave this
    component alone" -- the same trick poll() uses to avoid fighting anyone
    typing in the topic box. Everything else here rides on that.
    """
    fields = {name: gr.update() for name in FeedUpdate._fields}
    fields["raw"] = app.raw.render()
    # On the fast tick too: a stage chip that only counts up every 1.5s reads
    # as frozen, which is the exact impression the strip exists to dispel.
    fields["pipe"] = pipeline_html(app)
    return FeedUpdate(**fields)


def stream_status(app):
    """Live status feed.

    Two cadences in one generator: the raw box every RAW_INTERVAL, everything
    else every POLL_INTERVAL. A tick with nothing new in the box yields
    nothing at all rather than an update in which every field is a no-op.

    Bounded so an abandoned tab eventually releases its queue worker; the
    Refresh button covers the gap after it expires.
    """
    deadline = time.monotonic() + POLL_LIFETIME
    last = None
    seen = None
    next_full = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_full:
            next_full = now + POLL_INTERVAL
            last = app.state.settings.topic
            seen = app.raw.version
            yield poll(app, last)
        else:
            version = app.raw.version
            if version != seen:
                seen = version
                yield raw_only(app)
        time.sleep(RAW_INTERVAL)
    yield poll(app, last)


def stream_voice_boot(app, selector_updates, voice_choices):
    """Fill the voice lists once the startup tts-server boot finishes.

    The page is built immediately so the UI is usable, which means it is
    usually built while the server is still loading its model and its voice
    registry is empty. Without this the lists stay empty until someone presses
    Refresh voices -- and the point of launching the server automatically is
    not having to.
    """
    # Always push once. The lists in the page were baked when build() ran, so
    # "the server has voices now" says nothing about what this page is showing
    # -- a tab opened an hour later still carries the empty list.
    yield selector_updates()
    if voice_choices():
        return              # server is up, so that push was the final answer
    deadline = time.monotonic() + VOICE_BOOT_WAIT
    while time.monotonic() < deadline:
        time.sleep(2.0)
        if voice_choices():
            yield selector_updates()
            return
