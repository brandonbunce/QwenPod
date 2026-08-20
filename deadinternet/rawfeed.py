"""What the model is actually emitting, while it emits it.

The transcript shows what was *said*: cleaned by BaseLLM._clean, trimmed back
to the last complete sentence, and only the lines that survived at all. This
shows the other thing -- the reasoning, the false starts, the JSON the router
answers with, the persona rewrite that never reaches the channel, and the
empty completion behind a turn that silently did not happen. When the show goes
strange that is usually the only place the answer is.

Written from the director thread and read by gradio's workers, so every access
is under one lock.

Calls are kept apart rather than concatenated into one stream. Persona
evolution runs in the background while the ad break plays, and two generations
interleaved token by token would be unreadable -- you would not even be able to
tell there were two.

Nothing here is persisted. It is a window on the current process and it dies
with it, which is also what keeps model output about real people out of any
file the repo might see.
"""
import threading
from collections import deque

# How many calls stay in the box. Enough to see the shape of a turn -- router,
# then the speaker, then the next one -- without the box becoming a log.
MAX_CALLS = 8

# Per call, and for the rendered whole. Both keep the *tail*: the interesting
# end of a runaway generation is the end.
MAX_CALL_CHARS = 3000
MAX_CHARS = 8000

# Deltas arrive one token at a time, so the parts list is compacted once it
# gets long rather than on every append.
COMPACT_AT = 400


class _Call:
    """One generation. Handed to the client, which appends to it as it reads.

    Mutating methods take the feed's lock: the writer is whichever thread is
    driving the request, the reader is a gradio worker rendering the box.
    """
    __slots__ = ("label", "_feed", "_out", "_think", "_note", "_done")

    def __init__(self, label, feed):
        self.label = label
        self._feed = feed
        self._out = []
        self._think = []
        self._note = ""
        self._done = False

    def delta(self, text, thinking=False):
        if not text:
            return
        with self._feed._lock:
            buf = self._think if thinking else self._out
            buf.append(text)
            if len(buf) > COMPACT_AT:
                buf[:] = ["".join(buf)[-MAX_CALL_CHARS:]]
            self._feed._version += 1

    def end(self, note=""):
        with self._feed._lock:
            self._done = True
            self._note = note
            self._feed._version += 1

    # Called with the lock already held, by RawFeed.render.
    def _render(self):
        head = f"── {self.label} " + ("─" * 8 if self._done else "(live) ───")
        body = "".join(self._out)[-MAX_CALL_CHARS:].strip()
        think = "".join(self._think)[-MAX_CALL_CHARS:].strip()
        rows = [head]
        if think:
            # Marked, not hidden. Reasoning is most of what a thinking model
            # produces and it is exactly what the transcript throws away.
            rows += ["· " + ln for ln in think.splitlines()]
        if body:
            rows.append(body)
        elif self._done:
            # An empty completion is a finding, not a blank. This is what a
            # turn that never got spoken looks like.
            rows.append(f"({self._note or 'no output'})")
        elif self._note:
            rows.append(f"({self._note})")
        return "\n".join(rows)


class _NullCall:
    """Accepted and discarded, so a client with no tap needs no branches."""

    def delta(self, text, thinking=False):
        pass

    def end(self, note=""):
        pass


class NullFeed:
    """The default tap. `active` False is also what keeps both providers on
    their original non-streaming request path."""
    active = False
    version = 0

    def begin(self, label=""):
        return _NULL_CALL

    def render(self):
        return ""

    def clear(self):
        pass


_NULL_CALL = _NullCall()
NULL_TAP = NullFeed()


class RawFeed:
    active = True

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = deque(maxlen=MAX_CALLS)
        self._version = 0

    @property
    def version(self):
        """Bumped by every write. The UI compares this instead of rendering
        five times a second to find out whether anything changed."""
        with self._lock:
            return self._version

    def begin(self, label=""):
        call = _Call(label or "model", self)
        with self._lock:
            self._calls.append(call)
            self._version += 1
        return call

    def render(self):
        with self._lock:
            blocks = [c._render() for c in self._calls]
        text = "\n".join(b for b in blocks if b)
        return text[-MAX_CHARS:]

    def clear(self):
        with self._lock:
            self._calls.clear()
            self._version += 1
