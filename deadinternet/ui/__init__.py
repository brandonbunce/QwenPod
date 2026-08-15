"""Gradio front-end.

A package rather than a module because ui.py had grown to 1247 lines, nearly
all of it inside a single build() function. The split is being done in stages;
this re-export is what keeps `from deadinternet.ui import build` working in
app.py while the pieces move underneath it.
"""
from .blocks import build
from .style import APP_CSS

__all__ = ["APP_CSS", "build"]
