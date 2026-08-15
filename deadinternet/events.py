"""A bounded, timestamped log of what the app did, for the Diagnostics tab.

The transcript answers "what was said". This answers "what happened" -- the bot
joined a channel, someone changed a setting, the director stopped, a line was
spoken. Those were previously only visible as a one-line action message that the
next action overwrote, so anything you did not happen to be looking at was gone.

Deliberately in memory and bounded: this is a live view for the running session,
not an audit trail. Nothing here is written to disk, which also means it never
carries a persona or a transcript into a file someone might publish.
"""
import threading
import time
from collections import deque

# Roughly an hour of a busy session at one event every few seconds. Bounded
# because a bot left running overnight would otherwise hold every line it ever
# spoke.
MAX_EVENTS = 500

# Categories, so the view can be filtered or colour-coded later without having
# to parse the message text.
SPEECH = "speech"
RUN = "run"
VOICE = "voice"
SETTING = "setting"
TOPIC = "topic"
ERROR = "error"


class EventLog:
    def __init__(self, limit=MAX_EVENTS):
        self._lock = threading.Lock()
        self._events = deque(maxlen=limit)

    def add(self, category, message):
        """Record one event. Safe to call from any thread.

        Called from the Discord event loop (speech) and from Gradio handler
        threads (everything else), so the lock is not optional.
        """
        with self._lock:
            self._events.append((time.time(), category, str(message)))

    def recent(self, n=200):
        with self._lock:
            items = list(self._events)
        return items[-n:]

    def clear(self):
        with self._lock:
            self._events.clear()

    def render(self, n=200):
        """Newest last, one line each -- the transcript box reads the same way.

        Plain text rather than markdown: these carry names and arbitrary
        message text, and a stray asterisk should not silently turn the rest
        of the log italic.
        """
        rows = self.recent(n)
        if not rows:
            return "(nothing yet)"
        out = []
        for ts, cat, msg in rows:
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            out.append(f"{stamp}  {cat:<8} {msg}")
        return "\n".join(out)
