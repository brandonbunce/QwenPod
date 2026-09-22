"""One way to explain a control: a short line under the label, and a ? for the rest.

Help used to arrive three ways -- `info=` strings of any length, unclassed
gr.Markdown paragraphs between rows, and module-level hint constants -- with
nothing deciding which went where. Behaviour alone carried six thousand
characters of it on screen. This is the one mechanism that replaces all three:

    gr.Slider(..., info=help("Silence between speakers, in seconds.",
                             "0 makes them cut each other off; 0.3-0.6 sounds "
                             "like people talking."))

The short half stays where gradio draws `info`. The long half is moved, in the
browser, into a tooltip on a small ? button after the label. Nothing here is a
gradio feature: `info` is rendered as escaped text with a mini-markdown pass
(backticks, **bold**, *em*, links, newlines), so the split has to happen in
the DOM after the fact. HELP_JS does that, with the same re-scan pattern as
autoscroll.py, because tabs and closed accordions mount lazily and a hidden
Row is rebuilt from scratch when shown.

Components that take `info=` in gradio 6.22: Textbox, Slider, Checkbox,
CheckboxGroup, Radio, Dropdown, Number. Buttons, Audio, File, Markdown and
Accordion do not; a note they need is one short gr.Markdown with
elem_classes=["qp-note"], and anything longer goes in DEADINTERNET.md.

Never push an `info=` update to a component. The split happens once per mount,
and a prop change would redraw the unsplit string.
"""

# Visible on purpose. If the script ever fails to run, the info line reads as
# "short ‖ long" instead of silently losing the long half.
SEP = " ‖ "


def help(short, more=""):
    """The string for info=. `short` stays under the label; `more` becomes the
    ? tooltip. Either may be empty."""
    short = (short or "").strip()
    more = (more or "").strip()
    if not more:
        return short
    return f"{short}{SEP}{more}"


HELP_JS = r"""
() => {
  if (window.__qpHelp) return;
  window.__qpHelp = true;
  const SEP = " ‖ ";

  // One tooltip node for the whole page, on body so it escapes every
  // overflow:hidden gradio wraps a block in, and fixed so no scroll parent
  // can clip it. Theme variables are declared on :root, so it resolves them.
  const tip = document.createElement("div");
  tip.id = "qp-tip";
  tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);
  let owner = null;

  const place = (btn) => {
    const r = btn.getBoundingClientRect();
    const w = Math.min(360, window.innerWidth - 16);
    tip.style.width = w + "px";
    tip.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
    tip.style.top = (r.bottom + 6) + "px";
    if (r.bottom + 6 + tip.offsetHeight > window.innerHeight - 8) {
      tip.style.top = Math.max(8, r.top - 6 - tip.offsetHeight) + "px";
    }
  };
  const show = (btn) => {
    owner = btn;
    // Gradio already escaped and mini-markdowned this, so it is safe HTML.
    tip.innerHTML = btn.dataset.qpTip;
    tip.classList.add("open");
    place(btn);
  };
  const hide = () => { owner = null; tip.classList.remove("open"); };

  const wire = (info) => {
    if (info.dataset.qpHelp) return;
    info.dataset.qpHelp = "1";
    const html = info.innerHTML;
    const i = html.indexOf(SEP);
    if (i < 0) return;
    const short = html.slice(0, i).trim();
    const long = html.slice(i + SEP.length).trim();
    info.innerHTML = short;
    if (!short) info.style.display = "none";
    // The label is the previous sibling for every component that renders
    // info: span.block-info, or label.checkbox-container for a Checkbox.
    // The button goes AFTER it as a sibling, never inside a <label> -- on a
    // Checkbox a click inside the label toggles the box.
    const label = info.previousElementSibling;
    if (!label) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "qp-help-btn";
    btn.textContent = "?";
    const name = (label.textContent || "").trim();
    btn.setAttribute("aria-label", name ? "More about " + name : "More about this setting");
    btn.dataset.qpTip = long;
    label.insertAdjacentElement("afterend", btn);
    btn.addEventListener("mouseenter", () => show(btn));
    btn.addEventListener("focus", () => show(btn));
    btn.addEventListener("mouseleave", hide);
    btn.addEventListener("blur", hide);
    // Click toggles, for touch and for pinning it open while reading.
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      owner === btn && tip.classList.contains("open") ? hide() : show(btn);
    });
    btn.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  };

  const scan = () => document.querySelectorAll(".info-text:not([data-qp-help])").forEach(wire);
  scan();
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
}
"""
