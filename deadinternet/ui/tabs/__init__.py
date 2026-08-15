"""Handlers grouped by the tab they belong to.

Every one of these is a plain function taking `app` first and the gradio inputs
after. They were nested in build() and closed over `app`, `state` and `tts`, but
`state` and `tts` are only ever `app.state` and `app.tts`, and nothing rebinds
anything from the enclosing scope -- so they lift out with their bodies intact.

build() binds them with bound(fn, app) at wiring time.
"""
