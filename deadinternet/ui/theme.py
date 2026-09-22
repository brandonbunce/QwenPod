"""The theme object: the palette and the two typefaces, set once.

Everything in style.py reads gradio's variables, so the palette lives here
and nowhere else. Amber is the one accent, zinc the neutrals; both are what
the page already resolved to, now named. IBM Plex Sans replaces Source Sans
Pro for wider apertures and clearer numerals at the 0.75-0.8rem the stage
chips, VRAM readout and timings are set in, and pairs with the IBM Plex Mono
gradio already uses for the raw model output and the event log. The Google
fonts are a fallback-safe enhancement: offline, the stacks land on the system
faces and nothing else changes.

theme= belongs to launch() in gradio 6, next to css= -- see app.py.
"""
import gradio as gr

APP_THEME = gr.themes.Default(
    primary_hue="orange",
    neutral_hue="zinc",
    font=[gr.themes.GoogleFont("IBM Plex Sans"),
          "ui-sans-serif", "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("IBM Plex Mono"),
               "ui-monospace", "Consolas", "monospace"],
)
