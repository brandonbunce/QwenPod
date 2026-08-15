"""The controls that list voices or speakers, and the one update that feeds them.

Five controls across four tabs read from two different sources -- the
tts-server registry and the saved roster -- so refreshing only the one next to
the button you pressed left the others stale until the app restarted. Every
mutation therefore returns a full set.

The set used to be a bare 5-tuple with a comment naming the order, and a
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
    voice: Any     # Generate tab: which voice to speak as
    clone: Any     # Clone tab: registered voices, for deletion
    roster: Any    # Speakers tab: the roster radio
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
    out = [(NEW, NEW)]
    for s in speakers:
        tics = s.stim_list()
        bits = ["on" if s.enabled else "off"]
        if tics and s.stim_chance:
            bits.append(f"{tics[0][:14]}{'+' if len(tics) > 1 else ''} @{s.stim_chance:g}%")
        if not s.ref_wav:
            bits.append("no clip")
        persona = (s.persona or "").strip().replace("\n", " ")
        if persona:
            bits.append(persona[:40] + ("..." if len(persona) > 40 else ""))
        out.append((f"{s.name}  ·  " + "  ·  ".join(bits), s.name))
    return out


def selector_updates(app, voice=None, sel=None, man=None):
    """Refresh every control that lists voices or speakers."""
    state = app.state
    vc = voice_choices(app)
    names = speaker_names(app)
    pick = lambda v: {"value": v} if v is not None else {}
    return SelectorUpdates(
        voice=gr.update(choices=vc, **pick(voice)),
        clone=gr.update(choices=vc, **pick(voice)),
        roster=gr.update(choices=roster_choices(app), **pick(sel)),
        manual=gr.update(choices=names, **pick(man)),
        enabled=gr.update(choices=[s.name for s in state.restorable()],
                          value=[s.name for s in state.active()]),
    )


def selectors_unchanged():
    return SelectorUpdates(*(gr.update() for _ in SelectorUpdates._fields))
