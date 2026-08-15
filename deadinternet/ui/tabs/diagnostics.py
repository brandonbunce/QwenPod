"""Diagnostics tab: the event log."""
from ...events import RUN
from ..feedback import note


def clear_log(app):
    """Empty the log, then immediately record that it was emptied.

    Leaving it blank makes a cleared log indistinguishable from a log that
    never received anything -- which is the wrong impression when you have just
    thrown away the last hour of a session.
    """
    app.events.clear()
    app.events.add(RUN, "event log cleared")
    return app.events.render(), note("Event log cleared.")
