"""Wheel-to-horizontal for the Speakers roster strip.

The roster is a single row that scrolls sideways once there are more speakers
than fit. A mouse wheel only produces vertical deltas, so the strip was
unreachable without dragging its scrollbar or using the arrow buttons -- the
wheel scrolled the page behind it instead.

Delegated on document rather than bound to the element: sub-tabs mount lazily,
so at page load the Speakers tab is not in the DOM at all.
"""

ROSTER_SCROLL_JS = """
() => {
  if (window.__qpRosterWheel) return;
  window.__qpRosterWheel = true;

  // The element that actually scrolls is a .wrap inside the fieldset, and
  // which one depends on how gradio nested things that render. Ask, rather
  // than hard-coding a path that a version bump can move.
  const stripFor = (target) => {
    const bar = target && target.closest && target.closest('.roster-bar');
    if (!bar) return null;
    for (const el of [bar, ...bar.querySelectorAll('.wrap')]) {
      if (el.scrollWidth > el.clientWidth + 1) return el;
    }
    return null;
  };

  document.addEventListener('wheel', (e) => {
    const strip = stripFor(e.target);
    if (!strip) return;
    // A real horizontal gesture -- trackpad swipe, shift+wheel -- already does
    // the right thing. Only vertical wheel needs translating.
    if (Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
    const before = strip.scrollLeft;
    strip.scrollLeft = before + e.deltaY;
    // Swallow the event only if the strip actually moved. At either end the
    // page should scroll as normal, or the roster becomes a wheel trap you
    // have to steer around.
    if (strip.scrollLeft !== before) e.preventDefault();
  }, { passive: false });
}
"""
