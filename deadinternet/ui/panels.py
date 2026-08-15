"""Panel builders: the gradio component tree, one function per tab.

Each returns a typed handle from components.py rather than writing into a
shared namespace.

These must be called from inside a `with gr.Blocks()` block. Gradio tracks the
render context on a thread-local stack rather than lexically, so a builder
defined here renders wherever it is *called* -- which is why the layout in
blocks.py still reads top to bottom. The one rule that follows from that: never
construct a component at import time.
"""
import gradio as gr

from .components import DiagnosticsPanel


def build_diagnostics():
    gr.Markdown(
        "**Voice connection** - Discord terminates the call when the channel "
        "empties; the watchdog gets back in when someone returns."
    )
    voice = gr.Markdown("_(not connected)_")
    gr.Markdown(
        "**Chat input** - the only path a real person has into the conversation. "
        "Messages count only from the server the bot is currently in, and only "
        "act in interactive mode."
    )
    chat = gr.Markdown("_(not connected)_")
    gr.Markdown("**Output loudness**")
    norm = gr.Markdown("_(nothing played yet)_")
    gr.Markdown("**Topic rotation**")
    topic = gr.Markdown("_(rotation off)_")
    return DiagnosticsPanel(voice=voice, chat=chat, norm=norm, topic=topic)
