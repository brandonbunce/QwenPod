# QwenPod UI guidelines

Rules for anything drawn in `deadinternet/ui/`. They exist so a control added
next month looks like one added last year, and so the page stays something an
operator can read sideways during a live show.

## Who it is for, and its one job

One operator, one wide window, often glancing at it while a show is on air.
The page answers two questions: *what is on air right now*, and *how do I
change it without hunting*. Everything else is secondary and is drawn that way.

The vocabulary is broadcast: on air, cast, segment, ad break, hand-off, topic.
Name things by what the operator sees, not by how the code is organised.

## Tokens

Theme colours are always gradio's own variables at the point of use
(`var(--color-accent)`, `var(--body-text-color-subdued)`, ...), never
literals; that is what keeps light mode working. The table below is what
those variables resolve to in the dark theme the app is used in, and it is
the only palette:

| Name | Dark value | Used for |
| --- | --- | --- |
| amber | `#f97316`, hover `#ea580c` | the one accent: on air, primary buttons, the left rule on the action line and the topic card, a working stage chip |
| ink | zinc 950 `#09090b` page, zinc 900 `#18181b` panels | fills |
| rule | zinc 700 `#3f3f46` | borders, scrollbar thumbs, chip outlines |
| text | zinc 100 `#f4f4f5`; subdued zinc 400 `#a1a1aa` | body and secondary text |
| danger | `#d13438` (`--qp-danger`) | recording, a failed stage, destructive buttons |
| ok | `#3fb950` (`--qp-ok`) | a voiced microphone, a healthy stage |

The two state tokens are literals on purpose; `style.py` explains why a
`:root` token cannot read a theme variable.

**Type.** One family for the interface and one for raw streams, from the same
superfamily so they read as one system: IBM Plex Sans for everything gradio
draws, IBM Plex Mono for the raw model output and the event log. Sizes come
from the `--qp-fs-*` scale in `style.py` and nowhere else:

| Token | Size | Where |
| --- | --- | --- |
| `--qp-fs-xs` | 0.75rem | stage chips, raw model output, the ? button |
| `--qp-fs-sm` | 0.8rem | notes, tooltips, status lines, the event log |
| `--qp-fs-md` | 0.85rem | mic controls |
| `--qp-fs-lg` | 0.9rem | the action line and the status strip |
| `--qp-fs-xl` | 1rem | body-sized emphasis |
| `--qp-fs-hero` | 1.15rem | the current topic, and nothing else |
| `--qp-fs-brand` | 1.6rem | the masthead |

Radii: gradio's `--radius-sm`/`--radius-md` for anything that should match a
gradio surface; `--qp-radius-xs` (3px) for inline code; `--qp-radius-pill` for
chips and scrollbar thumbs.

## Page skeleton

```
masthead      name · Refresh · live status
action line   the last thing you did, and why it did or didn't work
tabs          Run · Speakers · Inputs · Outputs · Behaviour · Testing · Diagnostics
┌ tab ─────────────────────────────────────────────┐
│ open section: the thing this tab is for           │
│ ▸ closed accordion                                 │
│ ▸ closed accordion                                 │
└───────────────────────────────────────────────────┘
```

- Left-aligned throughout. Nothing is centred except the drop zone gradio
  draws inside an Audio or File component.
- Two equal columns only when a tab has a *drive it* side and a *watch it*
  side (Run, Speakers, Inputs, Diagnostics). Otherwise one column.
- A row holds at most three labelled controls; a row of plain buttons may be
  longer. Five sliders in a row is unreadable at
  1440px.
- The primary action of a tab is reachable without scrolling.

## Words on a control

Every control may carry exactly these, in this order, and nothing else:

| Piece | Where | Rule |
| --- | --- | --- |
| label | gradio `label=` | a noun phrase in sentence case; the unit goes in the label only when there is no info line to hold it |
| info | the first half of `help()` | one line, under about 90 characters, saying what it does, the unit, and what 0 or empty means |
| tooltip | the second half of `help()` | one or two sentences on *why*, or the trade-off; shown on a ? after the label |
| manual | DEADINTERNET.md | measured numbers, history, anything longer than two sentences |

```python
gr.Slider(0, 3, label="Gap between turns (s)",
          info=help("Silence between speakers. 0 makes them cut each other off.",
                    "0.3-0.6 sounds like people talking. The next line is "
                    "generated during playback, so this is pacing only."))
```

`help()` is in `deadinternet/ui/help.py`. The tooltip half supports the same
mini-markdown gradio gives `info`: backticks, `**bold**`, `*em*`, links,
newlines. No HTML.

Components that cannot carry `info=` (Button, Audio, File, Markdown, HTML,
Accordion, Tab) get no tooltip. If they need a sentence, it is one line of
`gr.Markdown(..., elem_classes=["qp-note"])` directly under them, and it is
the only prose allowed between controls. A paragraph is never page content.

Never push an `info=` update to a component after it is built: the split is
done once per mount, and a prop change would redraw the unsplit string.

Writing: active voice, plain verbs, sentence case. A button says what happens
when it is pressed ("Save speaker", not "Submit"), and the action line's
receipt uses the same word ("Saved"). Errors say what went wrong and what to
do, in the interface's voice, without apologising.

## Sections

- A section is a `gr.Accordion` with a plain-noun title ("Rotation",
  "Speech engine"). No eyebrow labels, no numbering, no `###` headings
  between controls.
- Open by default only when the section *is* the tab's job. Everything else
  starts closed.
- No `gr.Group` inside an accordion; the accordion is already a box.
- One primary button per row. Destructive actions ("Delete", "Remove all")
  use `variant="stop"`, sized to their label rather than stretched.
- A closed accordion's children are not in the DOM until first opened.
  Server-pushed values are stashed and applied on mount, and a handler
  outside the accordion still receives its inputs' current values (verified
  in 6.22). Anything looked up once by id from injected JS (the microphone
  block) must therefore stay outside accordions and nested tabs.

## Choosing between alternatives

- Mutually exclusive *things* (topic sources, outputs) are nested `gr.Tabs`
  inside the tab, the pattern Inputs uses. The selected tab is the thing you
  are looking at; it is not itself a setting.
- A `gr.Radio` is used only when the value *is* a setting: mode, backend,
  where speech is generated.

## Lists

A list is never page content. It is one of:

- a bounded scroll box (`gr.Markdown(height=..., elem_classes=["queue-box"])`)
  with the count in its first line;
- a chip strip (`elem_classes=["roster-bar"]`: three rows, scrolls sideways
  with the wheel), for the roster and the cast;
- a dropdown or checkbox group.

## Controls that depend on configuration

A control that cannot act in the current configuration is hidden, not
disabled, and one `qp-note` says where it went. Visibility is set at build
from the same helper the handler uses, and toggled only by the handler that
changes the configuration. The live feed never writes visibility, accordion
state, or any other layout property.

When hiding a top-level tab, the same handler returns `gr.Tabs(selected=...)`
for the tab strip: hiding the selected tab otherwise leaves nothing selected.

## Feedback

- There is no Apply button. Sliders save on release, text boxes on blur or
  Enter, everything else on change (`Autosave.bind`).
- The action line is the only receipt, and every handler writes it. Toasts
  mirror it so a result is visible wherever you have scrolled.
- The status strip streams; the action line does not. The feed never writes
  the action line.
- State is words plus shape, never colour alone: a failed chip is red *and*
  says why; a recording button is red *and* says Recording.

## Motion

None on load. Accordions open and close, tooltips fade in over 120ms, and
both are off under `prefers-reduced-motion`. No hover transitions on cards,
no entrance animations.

## Adding a control: the checklist

1. Which tab, and is it the tab's job? If not, it goes in a closed accordion.
2. Label as a noun phrase; `info=help(short, more)`; nothing longer than two
   sentences on the page.
3. Add the handle to the panel's dataclass in `components.py` and copy it onto
   `u` in `blocks.py` (a missing field is a `TypeError` at build).
4. `bind()` it if it is a setting; otherwise return a `note()`/`warn()` string
   to the action line.
5. If it lists things, bound it. If it depends on configuration, hide it from
   the same helper the handler uses.
6. Run `deadinternet/tests/test_ui_build.py`, then look at it at 1440px wide.
