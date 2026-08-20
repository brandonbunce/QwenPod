"""Client-side autoscroll for the streaming boxes.

The live feed rewrites the transcript wholesale every POLL_INTERVAL, which
resets the scroll position to the top -- so new lines land below the fold
exactly when you want to read them. gr.Timer does not fire in 6.22 and there is
no scroll hook, so an observer is installed client-side instead. It sticks to
the bottom only while the reader is already near the bottom, so scrolling up to
read history is not yanked back on the next tick.
"""

AUTOSCROLL_JS = """
() => {
  const stick = (box) => {
    if (box.dataset.autoscroll) return;
    box.dataset.autoscroll = "1";
    let pinned = true;
    box.addEventListener("scroll", () => {
      pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    });
    new MutationObserver(() => {
      if (pinned) box.scrollTop = box.scrollHeight;
    }).observe(box, {childList: true, subtree: true, characterData: true});
    box.scrollTop = box.scrollHeight;
  };
  // A textarea is a different problem: gradio sets its .value property, which
  // mutates no nodes, so the observer above would never fire. Poll it instead
  // -- one element, four times a second, against a box that is being written
  // to five times a second anyway.
  const stickTextarea = (ta) => {
    if (ta.dataset.autoscroll) return;
    ta.dataset.autoscroll = "1";
    let pinned = true;
    ta.addEventListener("scroll", () => {
      pinned = ta.scrollHeight - ta.scrollTop - ta.clientHeight < 40;
    });
    setInterval(() => {
      if (pinned && ta.isConnected) ta.scrollTop = ta.scrollHeight;
    }, 250);
  };
  const scan = () => {
    document.querySelectorAll(".transcript-box, .queue-box").forEach(stick);
    document.querySelectorAll(".raw-box textarea").forEach(stickTextarea);
  };
  scan();
  // Sub-tabs mount lazily, so the boxes may not exist yet at load.
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
}
"""
