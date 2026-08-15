"""Typed handles on the components each panel builds.

These replace a bare namespace object that panel builders populated by
attribute assignment and the wiring block read back by name -- a contract
nothing declared and nothing checked, where a typo on either side surfaced a
thousand lines away as an AttributeError.

Every field is required. A panel that forgets a component is a TypeError at
construction, naming the field it missed, before the page is ever served.
frozen=True because a component handle is set once when the panel is built and
read for the rest of the page's life; rebinding one always means a mistake.

The prefixes the old namespace used (g_, c_, s_, m_...) are gone -- the
dataclass does that namespacing now, so a field is `ui.topic.rotate` rather
than `u.m_rot`.
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiagnosticsPanel:
    voice: Any
    chat: Any
    norm: Any
    topic: Any
