"""The app's stylesheet.

Several elem_classes were already being set -- transcript-box, queue-box,
topic-box -- with nothing behind them; the boxes were held up by gradio's
height= alone. They are real hooks now.

Everything here uses gradio's own CSS variables rather than literal colours, so
the page still works in both the light and dark themes.
"""

APP_CSS = """
/* ---- tokens ----------------------------------------------------------- */
/* One scale for the whole sheet. The type sizes used to be nine different
   values between 0.74 and 1.02rem, chosen a rule at a time, and the corner
   radii were four; picking the next one meant guessing.
 *
 * These are all LITERALS, on purpose. A token that reached for a gradio
 * variable -- --qp-danger: var(--color-accent) -- would be wrong *here*: a
 * custom property substitutes its inner var() against the element it was
 * DECLARED on, and gradio emits its dark palette as `:root.dark, :root .dark`
 * -- that second form lands on the container, one level below <html>. So a
 * token declared at :root that reads a theme variable freezes on the LIGHT
 * value, which is the same trap documented under scrollbars below. Theme
 * colours therefore stay inline, at their point of use, everywhere in this
 * file. */
:root {
  --qp-fs-xs: 0.75rem;    /* stage chips, raw model output */
  --qp-fs-sm: 0.8rem;     /* status lines, the event log */
  --qp-fs-md: 0.85rem;    /* mic controls */
  --qp-fs-lg: 0.9rem;     /* the action line and the status strip */
  --qp-fs-xl: 1rem;       /* the topic */
  --qp-fs-brand: 1.6rem;  /* the masthead */

  /* Gradio's --radius-sm stays inline for anything that should match a gradio
     surface. These two are shapes it has no name for. */
  --qp-radius-xs: 3px;
  --qp-radius-pill: 999px;

  /* State. Colour never carries meaning alone in this sheet -- each of these
     lands with a fill, a border style or a word alongside it. */
  --qp-danger: #d13438;
  --qp-ok: #3fb950;
}

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
  font-size: var(--qp-fs-brand);
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
  font-size: var(--qp-fs-lg);
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
  border-radius: 0 var(--qp-radius-xs) 0 0;
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
  font-size: var(--qp-fs-lg);
  line-height: 1.35;
}
/* A long message wraps rather than stretching the header; two lines is plenty
   and anything longer scrolls in place instead of pushing the tabs down. */
.action-line.prose > span {
  display: block;
  max-height: 2.7em;
  overflow-y: auto;
}
/* Inline code here is a model name or a URL, not a code block. The one size
   in this sheet deliberately left off the scale above: em, not rem, so it
   tracks the line it sits in rather than fixing itself against the page. */
.action-line code {
  font-size: 0.85em;
  padding: 0.05em 0.3em;
  background: var(--background-fill-primary);
  border-radius: var(--qp-radius-xs);
}

/* ---- help ------------------------------------------------------------- */
/* One way to explain a control: the short line gradio draws under the label,
   and a ? after the label for the rest. ui/help.py splits the info string in
   the browser; this is what the pieces look like.
 *
 * The tooltip is a single node on <body>, fixed, because every gradio .block
 * is overflow:auto and the container is overflow:hidden -- anything positioned
 * inside a block is clipped at its edge. No ancestor uses transform, so fixed
 * is safe, and theme variables are declared on :root, so it resolves them. */
.qp-help-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.1em; height: 1.1em;
  margin-left: 0.35em; padding: 0;
  border: 1px solid var(--border-color-primary);
  border-radius: 50%;
  background: var(--background-fill-secondary);
  color: var(--body-text-color-subdued);
  font-size: var(--qp-fs-xs); line-height: 1;
  cursor: help; vertical-align: middle;
}
.qp-help-btn:hover, .qp-help-btn:focus-visible {
  color: var(--body-text-color);
  border-color: var(--color-accent);
  outline: none;
}
/* A Checkbox's label is a block-level flex row, so the ? fell onto its own
   line underneath. Shrink the label to its content when a ? follows it. */
label.checkbox-container:has(+ .qp-help-btn) { display: inline-flex; width: auto; }
#qp-tip {
  position: fixed; z-index: 10000; display: none;
  padding: 0.5rem 0.7rem;
  background: var(--background-fill-secondary);
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-drop-lg);
  color: var(--body-text-color);
  font-size: var(--qp-fs-sm); line-height: 1.45;
  pointer-events: none;
}
#qp-tip.open { display: block; }
#qp-tip code {
  font-size: 0.9em; padding: 0.05em 0.3em;
  background: var(--background-fill-primary);
  border-radius: var(--qp-radius-xs);
}
#qp-tip strong { font-weight: 600; }
@media (prefers-reduced-motion: no-preference) {
  #qp-tip.open { animation: qp-fade 0.12s ease-out; }
}
@keyframes qp-fade { from { opacity: 0; } to { opacity: 1; } }

/* ---- notes ------------------------------------------------------------ */
/* The one shape of prose allowed between controls: a single subdued line, for
   the components that cannot carry info= (buttons, audio, files). Anything
   longer belongs in a tooltip or in DEADINTERNET.md. Markdown gets the class
   on both .block and .prose, so the rule targets the paragraph. */
.qp-note p {
  margin: 0 0 0.35rem;
  font-size: var(--qp-fs-sm);
  line-height: 1.4;
  color: var(--body-text-color-subdued);
}
.qp-note code { font-size: 0.9em; }

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
  border-radius: var(--qp-radius-pill);
}
body::-webkit-scrollbar-thumb:hover,
body ::-webkit-scrollbar-thumb:hover {
  background: var(--body-text-color-subdued);
  background-clip: padding-box;
}
body::-webkit-scrollbar-corner,
body ::-webkit-scrollbar-corner { background: transparent; }

/* ---- roster strip ---------------------------------------------------- */
/* Radio options laid out as a horizontally scrolling block, three rows deep.
   Gradio stacks them vertically by default, which turned a long roster into
   the tallest thing on the page; one row instead meant a 34-name roster was
   several screens wide and you scrolled for a while to reach the end.
 *
 * A grid with `grid-auto-flow: column` is what gives three rows AND keeps the
 * scrolling horizontal -- it fills down the first column, then the next, so
 * the strip is a third as wide as it was. Flex-wrap cannot do this: it fills
 * across and wraps down, which needs a height cap and scrolls vertically. */
.roster-bar .wrap,
.roster-bar > div {
  display: grid !important;
  grid-auto-flow: column !important;
  grid-template-rows: repeat(3, auto) !important;
  /* max-content, so a name is never squeezed or padded to a shared width. */
  grid-auto-columns: max-content;
  justify-items: start;
  align-content: start;
  overflow-x: auto;
  gap: 0.2rem 0.35rem;
  padding-bottom: 0.3rem;
  scrollbar-width: thin;
}
.roster-bar label {
  white-space: nowrap;
}
/* The buttons that share the strip's row act on the whole roster. A Row
   stretches its children to the tallest, which here is three rows of chips;
   these sit centred at their own height instead. */
.roster-tool { align-self: center !important; flex: 0 0 auto !important; }

/* ---- scrolling read-only panes --------------------------------------- */
.transcript-box, .queue-box, .topic-box, .status-box {
  overflow-y: auto;
  background: var(--background-fill-secondary);
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.7rem;
}
.transcript-box p, .queue-box p, .status-box p { margin: 0.25rem 0; }
.topic-box { font-size: var(--qp-fs-xl); }

/* ---- microphone ------------------------------------------------------ */
/* The hidden seam: the recorder writes an uploaded file's server path here and
   a normal gradio change event picks it up. Hidden with CSS rather than
   visible=False so the textarea is guaranteed to be in the DOM to write to. */
.qp-hidden { display: none !important; }

/* The mic is hand-written markup rather than a gradio component, and it used
   to be the one part of the page styled through #ids -- a hundred points of
   specificity above every other rule here, for no reason beyond the ids being
   the handles its JS already held. The ids are still in the markup and still
   what the JS reaches for; nothing is *styled* through them any more. */
.qp-mic { margin-top: 0.25rem; }
.qp-mic-row { display: flex; align-items: center; gap: 0.4rem; }

/* One recipe for every control in the row. The two roles below change only
   what they must -- which is the whole of the difference between them. */
.qp-control {
  flex: 0 0 auto;
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-sm);
  padding: 0.3rem 0.4rem;
  font-size: var(--qp-fs-md);
}
.qp-select {
  background: var(--input-background-fill);
  color: var(--body-text-color);
}
.qp-btn {
  cursor: pointer; white-space: nowrap;
  padding: 0.3rem 0.7rem;
  background: var(--button-secondary-background-fill);
  color: var(--button-secondary-text-color);
}
/* The device name is the long one; the pause is four fixed options. */
.qp-grow { flex: 1 1 auto; min-width: 0; }
.qp-btn.qp-recording {
  background: var(--qp-danger); color: #fff; border-color: var(--qp-danger);
}

/* Live input level. A dead microphone reads as a bar that never moves, which
   is the thing that was impossible to see before. */
.qp-meter {
  flex: 0 0 70px; height: 6px; border-radius: var(--qp-radius-pill);
  background: var(--border-color-primary); overflow: hidden;
}
/* The width is a live measurement and stays on the element. The colour is a
   state, so it belongs here -- and it needs a resting value: the JS only ever
   wrote a colour on a change of state, so until you first spoke the bar was
   drawn at the right width in no colour at all, which looks exactly like the
   dead input this meter exists to rule out. */
.qp-meter i {
  display: block; height: 100%; width: 0%;
  background: var(--color-accent);
  transition: width 0.05s linear;
}
.qp-meter i.qp-voiced { background: var(--qp-ok); }
.qp-mic-status {
  font-size: var(--qp-fs-sm); opacity: 0.8;
  margin-top: 0.25rem; min-height: 1.1em;
}

/* Kept as a warning, though the rule it guarded is gone: do NOT cap the height
   of a recorder. An earlier version set max-height: 78px to stop gradio's
   Audio reserving waveform-editor space, but gradio's own .block sets
   overflow: hidden -- so the cap silently clipped the bottom 18px, which is
   exactly where Stop, pause and Resume live. Recording started, the waveform
   moved, and there was no way to finish the take. */

/* ---- the stage strip -------------------------------------------------- */
/* A row of state chips above the transcript. Wraps rather than scrolls: on a
   narrow window a stage you cannot see is a stage you will not check. */
.pipe-box.block { padding: 0 !important; }
.pipe { font-size: var(--qp-fs-xs); line-height: 1.3; margin-bottom: 0.35rem; }
.pipe-row { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.pipe-off { color: var(--body-text-color-subdued); font-style: italic; }
.pipe-chip {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.12rem 0.4rem;
  border: 1px solid var(--border-color-primary);
  border-radius: var(--qp-radius-pill);
  background: var(--background-fill-secondary);
  color: var(--body-text-color-subdued);
  white-space: nowrap;
}
/* The dot is the thing you read first, so it carries the state on its own --
   colour AND fill, never colour alone. */
.pipe-chip i {
  width: 6px; height: 6px; border-radius: 50%;
  border: 1px solid var(--border-color-primary);
  background: transparent;
}
.pipe-chip b { font-weight: 600; font-variant-numeric: tabular-nums; }
.pipe-chip u { text-decoration: none; opacity: 0.7; }
.pipe-chip.work {
  color: var(--body-text-color);
  border-color: var(--color-accent);
}
.pipe-chip.work i { background: var(--color-accent); border-color: var(--color-accent); }
.pipe-chip.bad { border-color: var(--qp-danger); }
.pipe-chip.bad i { background: var(--qp-danger); border-color: var(--qp-danger); }
.pipe-chip.note { border-style: dashed; }
.pipe-hold {
  color: var(--body-text-color);
  margin-bottom: 0.25rem;
  padding-left: 0.1rem;
}
.pipe-hold b { color: var(--color-accent); }

/* Raw model output. A Textbox for the same reason the event log is one -- it
   is unfiltered generation, and a stray backtick or hash in it must render as
   itself. Dimmer than the transcript below it: this is the working-out, not
   the show. */
.raw-box textarea {
  font-family: var(--font-mono);
  font-size: var(--qp-fs-xs);
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
  font-size: var(--qp-fs-sm);
  line-height: 1.45;
  white-space: pre;
  overflow-x: auto;
}
"""
