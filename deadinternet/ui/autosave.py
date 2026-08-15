"""Settings that persist the moment they change.

There is no Apply button anywhere in the Dead Internet tabs: sliders save on
release, boxes on blur, everything else on change. Settings used to have three
different save behaviours and no way to tell which applied to what.

Autosave holds the two things every binding needs -- the app, and the action
line every save reports to -- so the ~30 call sites stay one line each.
"""
from ..events import SETTING as EV_SETTING


class Autosave:
    def __init__(self, app, action_line):
        """action_line is the sticky Markdown every save writes its receipt to.

        Deliberately not the status bar: that one is rewritten by the live feed
        every POLL_INTERVAL, which is what used to wipe a save confirmation
        within 1.5 seconds of it appearing.
        """
        self.app = app
        self.action_line = action_line

    def handler(self, field, label, cast=None, after=None):
        """The callable gradio invokes. Saves, then reports what it saved."""
        state = self.app.state

        def save(value):
            with state.lock:
                setattr(state.settings, field, cast(value) if cast else value)
            state.save()
            self.app.events.add(EV_SETTING, f"{label} -> {value}")
            extra = after() if after else None
            shown = value if not isinstance(value, str) else value.strip()[:60]
            return f"Saved **{label}**: {shown}" + (f" - {extra}" if extra else "")

        return save

    def bind(self, comp, field, label, cast=None, event="change", after=None):
        """Wire one component to persist into one settings field."""
        getattr(comp, event)(self.handler(field, label, cast, after),
                             comp, self.action_line)
