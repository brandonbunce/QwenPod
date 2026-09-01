"""The stage strip: what each part of the pipeline is doing, right now.

This answers the question the status bar cannot. The status bar says whether
the director is running; this says what it is *waiting on*. A stall looks
identical from outside whether the model is thinking, tts-server is queued
behind another request, whisper is chewing on a clip, or the gap between turns
is simply set to three seconds -- and telling those apart used to mean reading
app.log afterwards.

Rendered as HTML rather than Markdown because it is a row of chips with state
colours. Nothing user-written reaches it: every string is a stage name from
pipeline.ORDER or a number formatted here. The one value that comes from
outside -- an exception message on a failed stage -- is escaped.
"""
import html

from ..pipeline import HOLDING_SECONDS


def fmt_ms(ms: float) -> str:
    """Milliseconds while that is readable, seconds once it is not.

    A stage that has been running for 12000ms is a number you have to count
    the digits of; 12.0s is one you can read at a glance, which is the whole
    point of the strip.
    """
    if ms < 1000:
        return f"{int(round(ms))}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{int(ms // 60000)}m{int((ms % 60000) // 1000):02d}s"


def counters(app):
    """The numbers that explain a stall the stages alone do not.

    A pipeline that is idle across the board while eight messages sit unread
    is a different fault from one that is idle with nothing waiting, and the
    stage chips cannot tell you which you are looking at.
    """
    out = []
    director = getattr(app, "director", None)
    runtime = getattr(app, "runtime", None)
    if director is not None:
        depth = director.text_debug.get("depth", 0)
        if depth:
            out.append(("queued", str(depth)))
        refused = director.manual_debug.get("refused", 0)
        if refused:
            out.append(("dropped", str(refused)))
    if runtime is not None:
        try:
            talking = runtime.talking()
        except Exception:
            talking = 0
        if talking > 1:
            # Only worth saying when it is more than one -- that is the
            # interesting case, and it is the one /sayas overlap creates.
            out.append(("voices", str(talking)))
    return out


def render(rows, holding=None, extra=()):
    """-> the strip's HTML."""
    if not rows:
        return "<div class='pipe pipe-off'>pipeline: not started</div>"

    chips = []
    for row in rows:
        working = row["working"]
        cls = "pipe-chip work" if working else "pipe-chip"
        if row["errors"] and not working:
            cls += " bad"
        name = html.escape(str(row["stage"]))
        # A stage that has never run says so instead of claiming 0ms, which
        # reads as "fast" when it means "never happened".
        if not working and not row["calls"]:
            value = "&ndash;"
        else:
            value = html.escape(fmt_ms(row["ms"]))
        count = ""
        if row["active"] > 1:
            count = f"<u>&times;{row['active']}</u>"
        title = html.escape(
            f"{row['stage']}: {row['calls']} calls"
            + (f", avg {fmt_ms(row['avg_ms'])}" if row["calls"] else "")
            + (f", {row['errors']} failed" if row["errors"] else "")
            + (f" -- {row['last_error']}" if row["last_error"] else ""))
        chips.append(
            f"<span class='{cls}' title='{title}'><i></i>{name}{count}"
            f"<b>{value}</b></span>")

    for label, value in extra:
        chips.append(f"<span class='pipe-chip note'>{html.escape(label)}"
                     f"<b>{html.escape(value)}</b></span>")

    head = ""
    if holding:
        stage, seconds = holding
        head = (f"<div class='pipe-hold'>holding on <b>{html.escape(str(stage))}</b>"
                f" for {html.escape(fmt_ms(seconds * 1000))}</div>")
    return f"<div class='pipe'>{head}<div class='pipe-row'>{''.join(chips)}</div></div>"


def pipeline_html(app):
    """Everything the strip needs, gathered and rendered."""
    if not getattr(app.state.settings, "pipeline_view", True):
        return ""
    pipe = getattr(app, "pipeline", None)
    if pipe is None:
        return ""
    return render(pipe.snapshot(), pipe.holding(), counters(app))
