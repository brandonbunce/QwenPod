"""The app's stylesheet.

Several elem_classes were already being set -- transcript-box, queue-box,
topic-box -- with nothing behind them; the boxes were held up by gradio's
height= alone. They are real hooks now.

Everything here uses gradio's own CSS variables rather than literal colours, so
the page still works in both the light and dark themes.
"""

APP_CSS = """
/* ---- masthead ------------------------------------------------------- */
.masthead {
  align-items: center;
  gap: 1rem;
  border-bottom: 1px solid var(--border-color-primary);
  padding-bottom: 0.4rem;
  margin-bottom: 0.2rem;
}
.masthead .brand h1 {
  margin: 0;
  font-size: 1.6rem;
  letter-spacing: -0.02em;
  white-space: nowrap;
}
.brand { flex: 0 0 auto !important; min-width: 0 !important; }

/* The live status strip. Bullet-separated rather than piped, and allowed to
   wrap on a narrow window instead of overflowing the row. */
.status-strip {
  flex: 1 1 auto !important;
  font-size: 0.92rem;
  line-height: 1.35;
  color: var(--body-text-color-subdued);
}
.status-strip p {
  margin: 0;
  white-space: normal;
}
.status-strip strong { color: var(--body-text-color); font-weight: 600; }

/* The sticky one-line receipt for whatever you last did. Distinct from the
   status strip above it, which the live feed rewrites every 1.5s. */
.action-line {
  font-size: 0.9rem;
  padding: 0.35rem 0.6rem;
  border-left: 3px solid var(--color-accent);
  background: var(--background-fill-secondary);
  border-radius: 0 4px 4px 0;
  margin-bottom: 0.5rem;
}
.action-line p { margin: 0; }

/* ---- roster strip ---------------------------------------------------- */
/* Radio options laid out as a horizontal scrolling strip. Gradio stacks them
   vertically by default, which turned a long roster into the tallest thing on
   the page. */
.roster-bar .wrap,
.roster-bar fieldset > div {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  overflow-x: auto;
  gap: 0.35rem;
  padding-bottom: 0.3rem;
  scrollbar-width: thin;
}
.roster-bar label {
  flex: 0 0 auto !important;
  white-space: nowrap;
}

/* ---- scrolling read-only panes --------------------------------------- */
.transcript-box, .queue-box, .topic-box, .status-box {
  overflow-y: auto;
  background: var(--background-fill-secondary);
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.7rem;
}
.transcript-box p, .queue-box p, .status-box p { margin: 0.25rem 0; }
.topic-box { font-size: 1.02rem; }

/* ---- microphone ------------------------------------------------------ */
/* The recorder is a full Audio component; without a cap it reserves the same
   vertical space as a waveform editor for what is one button most of the time. */
.mic-row { max-height: 78px; }
.mic-hint { font-size: 0.8rem; margin-top: -0.3rem; opacity: 0.75; }

/* The event log is a Textbox so arbitrary message text cannot be read as
   markdown; monospace keeps its timestamp column aligned. */
.log-box textarea {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  line-height: 1.45;
  white-space: pre;
  overflow-x: auto;
}
"""
