"""The controls that list voices or speakers, and the one update that feeds them.

Four controls across three tabs read from two different sources -- the
tts-server registry and the saved roster -- so refreshing only the one next to
the button you pressed left the others stale until the app restarted. Every
mutation therefore returns a full set.

The set used to be a bare tuple with a comment naming the order, and a
separate `SELECTORS = 5` a thousand lines from the output list it had to agree
with. Both are gone: SelectorUpdates names its fields, so a mis-ordered return
is visible at the call site instead of silently filling the voice dropdown with
roster labels. Gradio accepts a NamedTuple wherever it accepts a tuple.
"""
from typing import Any, NamedTuple

import gradio as gr

NEW = "<new speaker>"


class SelectorUpdates(NamedTuple):
    """One gr.update() per voice/speaker control, in output-list order.

    The field order is the contract with UI.selector_outputs(); an assert in
    build() checks the two agree on length, and the names carry the rest.
    """
    voice: Any     # Testing tab: which voice to speak as
    roster: Any    # Speakers tab: the roster strip
    manual: Any    # Run tab: "say a line as"
    enabled: Any   # Run tab: who is talking (choices and ticks both move)


def voice_choices(app):
    try:
        return app.tts.server_voices()
    except Exception:
        return []


def speaker_names(app):
    return app.state.names()


def roster_choices(app):
    """(label, name) pairs for the roster radio.

    Doubles as the roster display and the speaker selector -- they used to be a
    Markdown table plus a dropdown saying the same thing twice.
    """
    state = app.state
    with state.lock:
        speakers = list(state.speakers)
    # The chip carries only what you cannot see once it is selected: whether
    # the speaker is on air, and whether there is a clip to speak with. Stims
    # and a persona excerpt used to be on it too, which made forty chips read
    # as a wall; they are in the editor the moment the chip is clicked.
    out = [(NEW, NEW)]
    for s in speakers:
        bits = []
        if s.enabled:
            bits.append("on")
        if not s.ref_wav:
            bits.append("no clip")
        label = s.name if not bits else f"{s.name}  ·  " + "  ·  ".join(bits)
        out.append((label, s.name))
    return out


def selector_updates(app, voice=None, sel=None, man=None):
    """Refresh every control that lists voices or speakers."""
    state = app.state
    vc = voice_choices(app)
    names = speaker_names(app)
    pick = lambda v: {"value": v} if v is not None else {}
    return SelectorUpdates(
        voice=gr.update(choices=vc, **pick(voice)),
        roster=gr.update(choices=roster_choices(app), **pick(sel)),
        manual=gr.update(choices=names, **pick(man)),
        enabled=gr.update(choices=[s.name for s in state.restorable()],
                          value=[s.name for s in state.active()]),
    )


def selectors_unchanged():
    return SelectorUpdates(*(gr.update() for _ in SelectorUpdates._fields))
