"""Binding a module-level handler to the app instance gradio will call it with.

Handlers take `app` first; gradio supplies only the component values. A partial
closes that gap, but two details matter and neither is obvious:

  * functools.partial has no __name__, and gradio falls back to
    fn.__class__.__name__ when deriving api_name -- so without setting it every
    endpoint ends up called "partial", "partial_1", "partial_2"... The UI still
    works; the generated API surface and the "Use via API" panel become
    useless. partial objects accept attribute assignment, so one line fixes it.

  * Do NOT reach for functools.update_wrapper to set the name. It also sets
    __wrapped__, which makes inspect.signature follow through to the *original*
    function -- gradio would then see `app` as a parameter it must supply a
    component for, and expect one extra input than the call site provides.

inspect.isgeneratorfunction sees through a partial to the wrapped function, so
streaming handlers keep streaming. Verified on the pinned gradio 6.22.0.
"""
import functools


def bound(fn, app):
    """fn(app, *inputs) -> a callable gradio can introspect as fn(*inputs)."""
    p = functools.partial(fn, app)
    p.__name__ = fn.__name__
    return p
