"""Where the time goes, live.

The status strip says *what* the director is doing; this says *what it is
waiting on*, which is a different question and the one you actually have when
the show has gone quiet. A stall looks identical from the outside whether the
LLM is thinking, tts-server is queued behind another request, whisper is
chewing on a clip, or the gap between turns is simply set long -- and until
this existed the only way to tell them apart was to read app.log afterwards.

Stages are counters, not a trace: each one knows how many calls are in flight,
when the oldest of them started, and how long the last one took. That is
enough to answer "what is holding this up and for how long" while it is still
happening, and it costs a dict update per call.

Written from the director thread, discord.py's player thread, gradio's workers
and whatever executor a synth landed on, so everything is under one lock.
Nothing is persisted -- it is a window on the current process.
"""
import threading
import time
from contextlib import contextmanager

# Fixed display order. A chip that moves position when another stage wakes up
# is a chip you have to re-find every time you look at it.
ROUTER, LLM, TTS, PLAY, GAP = "Router", "LLM", "TTS", "Playback", "Gap"
WHISPER, AD, EVOLVE, PERSONA, TOPIC = "Whisper", "Ad", "Evolve", "Persona", "Topic"

# Always shown, even at zero: "TTS 0ms, never called" is itself the answer
# when nothing is coming out.
CORE = (ROUTER, LLM, TTS, PLAY, GAP)
# Shown once they have actually run. A show with no microphone and no ad break
# should not carry three permanently empty chips.
OPTIONAL = (WHISPER, AD, EVOLVE, PERSONA, TOPIC)
ORDER = CORE + OPTIONAL

# Past this, a stage in flight is called out on its own line rather than just
# counting up in place. Chosen to sit above a normal LLM line plus a synth --
# a healthy turn does not spend four seconds in any single stage.
HOLDING_SECONDS = 4.0


def stage_for_label(label: str) -> str:
    """Map an llm call's raw-feed label onto a pipeline stage.

    The labels already exist -- they were added so the raw output box could
    say which generation it was showing -- so routing them here costs nothing
    and means every LLM call is instrumented at one choke point rather than at
    six call sites that can each be forgotten.
    """
    text = (label or "").strip()
    if text == "router":
        return ROUTER
    if text.startswith("evolve:"):
        return EVOLVE
    if text.startswith("ad:"):
        return AD
    if text.startswith("persona:"):
        return PERSONA
    if text.startswith("topic:"):
        return TOPIC
    return LLM


class _Stage:
    __slots__ = ("active", "starts", "calls", "last_ms", "total_ms",
                 "errors", "last_error")

    def __init__(self):
        self.active = 0
        self.starts = []
        self.calls = 0
        self.last_ms = 0.0
        self.total_ms = 0.0
        self.errors = 0
        self.last_error = ""


class NullPipeline:
    """The default. Every call is accepted and discarded, so an uninstrumented
    client needs no branches around its own work."""
    active = False

    @contextmanager
    def track(self, stage):
        yield

    def snapshot(self):
        return []

    def holding(self):
        return None

    def reset(self):
        pass


NULL_PIPELINE = NullPipeline()


class Pipeline:
    active = True

    def __init__(self, clock=time.monotonic):
        # Injected so tests can drive elapsed time instead of sleeping.
        self._clock = clock
        self._lock = threading.Lock()
        self._stages = {}

    # ---- recording ------------------------------------------------------
    @contextmanager
    def track(self, stage):
        started = self._begin(stage)
        try:
            yield
        except BaseException as e:
            self._end(stage, started, error=f"{type(e).__name__}: {e}"[:120])
            raise
        else:
            self._end(stage, started)

    def _begin(self, stage):
        now = self._clock()
        with self._lock:
            st = self._stages.get(stage)
            if st is None:
                st = self._stages[stage] = _Stage()
            st.active += 1
            st.starts.append(now)
        return now

    def _end(self, stage, started, error=""):
        now = self._clock()
        with self._lock:
            st = self._stages.get(stage)
            if st is None:
                return
            st.active = max(0, st.active - 1)
            try:
                st.starts.remove(started)
            except ValueError:
                pass
            st.calls += 1
            st.last_ms = (now - started) * 1000.0
            st.total_ms += st.last_ms
            if error:
                st.errors += 1
                st.last_error = error

    # ---- reading --------------------------------------------------------
    def snapshot(self):
        """One row per stage, in ORDER. Rows are plain dicts so the renderer
        does not reach back into the lock to format them."""
        now = self._clock()
        with self._lock:
            names = [n for n in ORDER if n in CORE or n in self._stages]
            names += sorted(n for n in self._stages if n not in ORDER)
            rows = []
            for name in names:
                st = self._stages.get(name)
                if st is None:
                    rows.append({"stage": name, "working": False, "ms": 0.0,
                                 "calls": 0, "active": 0, "avg_ms": 0.0,
                                 "errors": 0, "last_error": ""})
                    continue
                working = st.active > 0
                # Elapsed on the *oldest* call in flight: with three synths
                # running, what is holding the turn up is the slowest one.
                ms = ((now - min(st.starts)) * 1000.0 if working and st.starts
                      else st.last_ms)
                rows.append({
                    "stage": name,
                    "working": working,
                    "ms": ms,
                    "calls": st.calls,
                    "active": st.active,
                    "avg_ms": (st.total_ms / st.calls) if st.calls else 0.0,
                    "errors": st.errors,
                    "last_error": st.last_error,
                })
        return rows

    def holding(self):
        """The stage that has been in flight longest, if long enough to be
        worth naming. -> (stage, seconds) or None."""
        worst = None
        for row in self.snapshot():
            if row["working"] and row["ms"] / 1000.0 >= HOLDING_SECONDS:
                if worst is None or row["ms"] > worst["ms"]:
                    worst = row
        return (worst["stage"], worst["ms"] / 1000.0) if worst else None

    def reset(self):
        with self._lock:
            self._stages.clear()
