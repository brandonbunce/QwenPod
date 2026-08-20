"""The app's stylesheet.

Several elem_classes were already being set -- transcript-box, queue-box,
topic-box -- with nothing behind them; the boxes were held up by gradio's
height= alone. They are real hooks now.

Everything here uses gradio's own CSS variables rather than literal colours, so
the page still works in both the light and dark themes.
"""

APP_CSS = """
/* ---- masthead ------------------------------------------------------- */
/* Masthead and action line are one header block: the rule that closes it sits
   under the action line, not between them. */
.masthead {
  align-items: center;
  gap: 1rem;
  padding-bottom: 0.25rem;
  margin-bottom: 0 !important;
}
.masthead .brand h1 {
  margin: 0;
  font-size: 1.6rem;
  letter-spacing: -0.02em;
  white-space: nowrap;
}
/* gradio's .block sets width: 100%, and flex-basis: auto resolves to the width
   property -- so every child of the masthead claimed the full row and the
   wrap put each on its own line. Setting flex alone did nothing; the width
   override is what actually makes this one row. */
.masthead > * { width: auto !important; }
.brand { flex: 0 0 auto !important; min-width: 0 !important; }

/* Refresh and the status read as one group on the right. The auto margin does
   the pushing, so the status must not grow -- if it did it would swallow the
   free space and the margin would have nothing left to push with. */
.masthead > button { flex: 0 0 auto !important; margin-left: auto !important; }

/* The live status strip. Bullet-separated rather than piped, and allowed to
   wrap on a narrow window instead of overflowing the row. */
.status-strip {
  flex: 0 1 auto !important;
  text-align: right;
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
   status strip above it, which the live feed rewrites every 1.5s. Closes the
   header block, so it carries the bottom rule.
 *
 * IMPORTANT: gradio puts elem_classes on BOTH the outer .block and the inner
 * .prose div, so a bare `.action-line { padding; border }` applies twice --
 * double padding, and a second bottom rule drawn above the real one. Decorate
 * the block; flatten the prose. */
.action-line.block {
  padding: 0.25rem 0.55rem;
  border-left: 3px solid var(--color-accent);
  border-bottom: 1px solid var(--border-color-primary);
  background: var(--background-fill-secondary);
  border-radius: 0 3px 0 0;
  margin-bottom: 0.4rem !important;
}
.action-line.prose {
  padding: 0 !important;
  margin: 0 !important;
  border: 0 !important;
  background: none !important;
}
.action-line p {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.35;
}
/* A long message wraps rather than stretching the header; two lines is plenty
   and anything longer scrolls in place instead of pushing the tabs down. */
.action-line.prose > span {
  display: block;
  max-height: 2.7em;
  overflow-y: auto;
}
/* Inline code here is a model name or a URL, not a code block. */
.action-line code {
  font-size: 0.85em;
  padding: 0.05em 0.3em;
  background: var(--background-fill-primary);
  border-radius: 3px;
}

/* ---- scrollbars ------------------------------------------------------- */
/* Scoped to body and below, deliberately not :root. Gradio defines its theme
   variables on the container, so on <html> they still resolve to the LIGHT
   fallbacks -- --border-color-primary is #e4e4e7 there and #3f3f46 one element
   down. A scrollbar styled at :root is pale grey on a dark page.
 *
 * Both syntaxes on purpose: Chrome 121+ honours scrollbar-width/-color and
 * ignores the ::-webkit- rules when they are set, older builds do the reverse.
 *
 * The viewport scrollbar is covered without naming <html>: the spec applies
 * BODY's scrollbar-width/-color to the viewport while HTML's is auto, and
 * leaving <html> alone is exactly what keeps it auto. */
body, body * {
  scrollbar-width: thin;
  scrollbar-color: var(--border-color-primary) transparent;
}
body::-webkit-scrollbar,
body ::-webkit-scrollbar { width: 10px; height: 10px; }
body::-webkit-scrollbar-track,
body ::-webkit-scrollbar-track { background: transparent; }
body::-webkit-scrollbar-thumb,
body ::-webkit-scrollbar-thumb {
  background: var(--border-color-primary);
  border: 2px solid transparent;
  background-clip: padding-box;
  border-radius: 6px;
}
body::-webkit-scrollbar-thumb:hover,
body ::-webkit-scrollbar-thumb:hover {
  background: var(--body-text-color-subdued);
  background-clip: padding-box;
}
body::-webkit-scrollbar-corner,
body ::-webkit-scrollbar-corner { background: transparent; }

/* ---- roster strip ---------------------------------------------------- */
/* Radio options laid out as a horizontal scrolling strip. Gradio stacks them
   vertically by default, which turned a long roster into the tallest thing on
   the page. */
.roster-bar .wrap,
.roster-bar > div {
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
/* The hidden seam: the recorder writes an uploaded file's server path here and
   a normal gradio change event picks it up. Hidden with CSS rather than
   visible=False so the textarea is guaranteed to be in the DOM to write to. */
.qp-hidden { display: none !important; }

#qp-mic { margin-top: 0.25rem; }
.qp-mic-row { display: flex; align-items: center; gap: 0.4rem; }
#qp-mic-dev, #qp-mic-gap {
  background: var(--input-background-fill);
  color: var(--body-text-color);
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-sm);
  padding: 0.3rem 0.4rem; font-size: 0.85rem;
}
/* The device name is the long one; the pause is four fixed options. */
#qp-mic-dev { flex: 1 1 auto; min-width: 0; }
#qp-mic-gap { flex: 0 0 auto; }
#qp-mic-rec, #qp-mic-file-label {
  flex: 0 0 auto; cursor: pointer; white-space: nowrap;
  background: var(--button-secondary-background-fill);
  color: var(--button-secondary-text-color);
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-sm);
  padding: 0.3rem 0.7rem; font-size: 0.85rem;
}
#qp-mic-rec.qp-recording {
  background: #d13438; color: #fff; border-color: #d13438;
}
#qp-mic-file { display: none; }
/* Live input level. A dead microphone reads as a bar that never moves, which
   is the thing that was impossible to see before. */
#qp-mic-meter {
  flex: 0 0 70px; height: 6px; border-radius: 3px;
  background: var(--border-color-primary); overflow: hidden;
}
#qp-mic-meter i { display: block; height: 100%; width: 0%; transition: width 0.05s linear; }
#qp-mic-status { font-size: 0.8rem; opacity: 0.8; margin-top: 0.25rem; min-height: 1.1em; }

/* Do NOT cap the height of this component. An earlier version set
   max-height: 78px to stop the recorder reserving waveform-editor space, but
   gradio's own .block sets overflow: hidden -- so the cap silently clipped the
   bottom 18px, which is exactly where Stop, pause and Resume live. Recording
   started, the waveform moved, and there was no way to finish the take. Let it
   size itself. */
.mic-hint { font-size: 0.8rem; margin-top: -0.3rem; opacity: 0.75; }

/* Raw model output. A Textbox for the same reason the event log is one -- it
   is unfiltered generation, and a stray backtick or hash in it must render as
   itself. Dimmer than the transcript below it: this is the working-out, not
   the show. */
.raw-box textarea {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  line-height: 1.4;
  color: var(--body-text-color-subdued);
  background: var(--background-fill-primary);
  white-space: pre-wrap;
  /* No resize handle: the box is a window on a stream, and a dragged height
     is undone by the next value gradio pushes into it. */
  resize: none;
}

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
