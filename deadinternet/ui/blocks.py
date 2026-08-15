"""Gradio front-end: voice cloning plus Dead Internet Mode.

Layout notes, because gradio 6.22 constrains this more than it looks:

  * gr.Timer never fires, so the live feed is a streaming generator bound to
    demo.load. Inactive sub-tabs are NOT in the DOM -- gradio mounts a tab's
    contents only once it is selected -- but the server-side values are still
    applied, so a tab shows current data the moment you open it.
  * gr.Dataframe ignores value updates pushed from a handler. The roster is a
    Radio of (label, name) choices instead; choices= updates are known to work
    because every selector in this file relies on them.
  * css= and theme= moved from Blocks() to launch() in gradio 6 -- see app.py.
  * The status bar streams; the action line does not. Writing both from the
    feed is what used to wipe every button's confirmation within 1.5s.
"""
import os
import re
import time

import gradio as gr
import soundfile as sf

from ..config import (HELP, MODES, MODE_MANUAL, PERSONA_SAMPLES, PERSONA_YEARS,
                      RECOMMENDED, Speaker, TEMPLATE_VARS, VOICES_DIR)
from ..llm import PROVIDER_OPENAI, PROVIDERS

NEW = "<new speaker>"

# Live-update cadence, and how long one browser's feed runs before it has to
# be restarted by a reload or the Refresh button.
POLL_INTERVAL = 1.5
POLL_LIFETIME = 3600

# How long a freshly loaded page waits for the startup tts-server boot to
# register the roster before giving up and leaving it to Refresh voices.
# Generous: it covers loading the model plus re-encoding every speaker.
VOICE_BOOT_WAIT = 300

# How often the persona builder reports progress while the scan runs.
MINE_POLL = 1.0

_MD = re.compile(r"[*`_]+")

# The live feed rewrites the transcript wholesale every POLL_INTERVAL, which
# resets the scroll position to the top -- so new lines land below the fold
# exactly when you want to read them. gr.Timer does not fire in 6.22 and there
# is no scroll hook, so an observer is installed client-side instead. It sticks
# to the bottom only while the reader is already near the bottom, so scrolling
# up to read history is not yanked back on the next tick.
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
  const scan = () => document
    .querySelectorAll(".transcript-box, .queue-box")
    .forEach(stick);
  scan();
  // Sub-tabs mount lazily, so the boxes may not exist yet at load.
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
}
"""


class _Bag:
    """Somewhere to hang components so the panel builders can stay separate
    functions while the wiring below still reads as plain names."""


def build(app):
    """app is a DeadInternetApp -- see app.py."""
    state, tts = app.state, app.tts
    u = _Bag()

    # ---- feedback ---------------------------------------------------------
    def note(msg, level="info"):
        """Toast it and return it for the sticky action line.

        Every action reports twice on purpose: the toast is visible wherever
        you have scrolled to, the action line is still there a minute later.
        """
        text = _MD.sub("", str(msg)).strip()
        if text:
            try:
                (gr.Warning if level == "warn" else gr.Info)(text[:300])
            except Exception:  # never let a toast break the handler
                pass
        return msg

    def warn(msg):
        return note(msg, "warn")

    # ---- shared helpers -------------------------------------------------
    def voice_choices():
        try:
            return tts.server_voices()
        except Exception:
            return []

    def speaker_names():
        return state.names()

    def roster_choices():
        """(label, name) pairs for the roster radio.

        Doubles as the roster display and the speaker selector -- they used to
        be a Markdown table plus a dropdown saying the same thing twice.
        """
        with state.lock:
            speakers = list(state.speakers)
        out = [(NEW, NEW)]
        for s in speakers:
            tics = s.stim_list()
            bits = ["on" if s.enabled else "off"]
            if tics and s.stim_chance:
                bits.append(f"{tics[0][:14]}{'+' if len(tics) > 1 else ''} @{s.stim_chance:g}%")
            if not s.ref_wav:
                bits.append("no clip")
            persona = (s.persona or "").strip().replace("\n", " ")
            if persona:
                bits.append(persona[:40] + ("..." if len(persona) > 40 else ""))
            out.append((f"{s.name}  ·  " + "  ·  ".join(bits), s.name))
        return out

    # Order matters -- every mutation returns these four, in this order:
    #   g_voice, c_list, s_roster, man_speaker, r_enabled
    SELECTORS = 5

    def selector_updates(voice=None, sel=None, man=None):
        """Refresh every control that lists voices or speakers.

        They read from two different sources (the tts-server registry and the
        saved roster) and sit on different tabs, so refreshing only the ones
        next to the button left the others stale until the app restarted.
        """
        vc = voice_choices()
        names = speaker_names()
        pick = lambda v: {"value": v} if v is not None else {}
        return (
            gr.update(choices=vc, **pick(voice)),
            gr.update(choices=vc, **pick(voice)),
            gr.update(choices=roster_choices(), **pick(sel)),
            gr.update(choices=names, **pick(man)),
            # Run-tab enable/disable list: choices AND ticks both move when a
            # speaker is added, deleted or has its enabled flag changed.
            gr.update(choices=[s.name for s in state.restorable()],
                      value=[s.name for s in state.active()]),
        )

    def selectors_unchanged():
        return tuple(gr.update() for _ in range(SELECTORS))

    # ---- persistence ------------------------------------------------------
    def autosave(field, label, cast=None, after=None):
        """Persist one setting the moment it changes.

        There is no Apply button anywhere in this tab: sliders save on release,
        boxes on blur, everything else on change. Settings used to have three
        different save behaviours and no way to tell which applied to what.
        """
        def handler(value):
            with state.lock:
                setattr(state.settings, field, cast(value) if cast else value)
            state.save()
            extra = after() if after else None
            shown = value if not isinstance(value, str) else value.strip()[:60]
            return f"Saved **{label}**: {shown}" + (f" - {extra}" if extra else "")
        return handler

    def bind(comp, field, label, cast=None, event="change", after=None):
        getattr(comp, event)(autosave(field, label, cast, after), comp, u.sb_action)

    def tpl_vars(field):
        """The {placeholders} a template box accepts, for its tooltip."""
        pairs = TEMPLATE_VARS.get(field, {})
        return "Variables: " + ", ".join(f"{k} = {v}" for k, v in pairs.items())

    # ---- cloning tab ------------------------------------------------------
    def persist_clip(name, ref_audio):
        """Copy a reference clip into voices/ as mono WAV and return the path.

        The server only keeps registrations in memory, so without a clip on
        disk a restart loses the clone for good.
        """
        os.makedirs(VOICES_DIR, exist_ok=True)
        stored = os.path.join(VOICES_DIR, f"{name}.wav")
        data, sr = sf.read(ref_audio, always_2d=True)
        sf.write(stored, data.mean(axis=1), sr, format="WAV", subtype="PCM_16")
        return stored

    def do_register(name, ref_audio, ref_text):
        if not name or not name.strip():
            return (*selectors_unchanged(), warn("Give the voice a name."))
        if not ref_audio:
            return (*selectors_unchanged(), warn("Upload a reference clip."))
        name, ref_text = name.strip(), (ref_text or "").strip()

        stored = persist_clip(name, ref_audio)
        ok, msg = tts.register(name, stored, ref_text, force=True)
        if not ok:
            return (*selectors_unchanged(), warn(f"Registration failed - {msg}"))

        # Record it so boot can re-register it. Keep any persona/enabled state
        # if this name is already a Dead Internet speaker; otherwise park it
        # disabled so it does not silently join conversations.
        existing = state.get(name)
        state.upsert(Speaker(
            name=name, ref_wav=stored, ref_text=ref_text,
            persona=existing.persona if existing else "",
            enabled=existing.enabled if existing else False,
            # Preserve tics; re-registering a clip must not wipe them.
            stims=existing.stims if existing else "",
            stim_chance=existing.stim_chance if existing else 0.0,
        ))

        mode = "ICL (with transcript)" if ref_text else "speaker-embedding only"
        return (
            *selector_updates(voice=name, sel=name),
            note(f"Registered '{name}' - {mode}. Saved to `voices/{name}.wav`, "
                 "so it survives a tts-server restart."),
        )

    def do_delete_voice(name):
        if not name:
            return (*selectors_unchanged(), warn("Nothing selected."))
        tts.forget(name)
        # Drop the saved clip too, or boot would resurrect it.
        state.remove(name)
        stored = os.path.join(VOICES_DIR, f"{name}.wav")
        if os.path.exists(stored):
            os.remove(stored)
        return (*selector_updates(sel=NEW),
                note(f"Removed '{name}' and deleted its saved clip."))

    def refresh_voices():
        """Repopulate every voice list, re-registering with tts-server first.

        The list is built once when the page is created, so a tts-server that
        was down then -- or restarted since, which wipes its in-memory
        registry -- leaves an empty dropdown with nothing to explain it.
        """
        ok, msg = app.resync_voices()
        return (*selector_updates(), note(msg) if ok else warn(msg))

    def do_synth(text, voice, instructions,
                 temperature, top_k, top_p, rep_pen, seed, max_new):
        if not text or not text.strip():
            return None, "Enter some text."
        if not voice:
            # An empty dropdown almost always means tts-server was down when
            # the page was built, or has been restarted since and lost its
            # in-memory registry. Say so instead of "pick a voice" from a
            # list with nothing in it.
            if not app.tts_alive():
                return None, ("**tts-server is not running** at "
                              f"`{state.settings.tts_url}`. Start it, then press "
                              "**Refresh voices**.")
            return None, "Pick a voice - press **Refresh voices** if the list is empty."
        gen = {
            "temperature": temperature,
            "top_k": int(top_k),
            "top_p": top_p,
            "repetition_penalty": rep_pen,
            "max_new_tokens": int(max_new),
        }
        if instructions and instructions.strip():
            gen["instructions"] = instructions.strip()
        if int(seed) >= 0:
            gen["seed"] = int(seed)
        try:
            wav = tts.synth(text, voice, **gen)
        except Exception as e:
            return None, f"Generation failed - {e}"
        out = "/tmp/qwentts_ui_out.wav"
        with open(out, "wb") as f:
            f.write(wav)
        info = sf.info(out)
        return out, f"{info.duration:.2f}s @ {info.samplerate} Hz"

    # ---- speakers ---------------------------------------------------------
    def load_speaker(sel):
        blank = ("", None, "", "", "", 0)
        if not sel or sel == NEW:
            return (*blank, "New speaker - fill in the form and press Save.")
        sp = state.get(sel)
        if not sp:
            return (*blank, "Not found.")
        clip = sp.ref_wav if sp.ref_wav and os.path.exists(sp.ref_wav) else None
        return (sp.name, clip, sp.ref_text, sp.persona, sp.stims,
                sp.stim_chance, f"Editing **{sp.name}**.")

    def save_speaker(name, clip, ref_text, persona, stims, stim_pct):
        name = (name or "").strip()
        if not name:
            return (*selectors_unchanged(), warn("Give the speaker a name."))
        existing = state.get(name)
        stored = existing.ref_wav if existing else ""

        if clip:
            os.makedirs(VOICES_DIR, exist_ok=True)
            stored = os.path.join(VOICES_DIR, f"{name}.wav")
            # Normalise to mono WAV on the way in so the stored clip is
            # exactly what gets re-registered after a server restart.
            data, sr = sf.read(clip, always_2d=True)
            sf.write(stored, data.mean(axis=1), sr, format="WAV", subtype="PCM_16")
        if not stored:
            return (*selectors_unchanged(),
                    warn("Upload a reference clip for this speaker."))

        sp = Speaker(
            name=name, ref_wav=stored, ref_text=(ref_text or "").strip(),
            # Enabled lives on the Run tab's "Who's talking" list now; keep
            # whatever it is set to rather than round-tripping it through a
            # second control that could disagree.
            persona=(persona or "").strip(),
            enabled=existing.enabled if existing else False,
            stims=(stims or "").strip(), stim_chance=float(stim_pct or 0),
        )
        state.upsert(sp)
        ok, msg = tts.register(name, stored, sp.ref_text, force=True)
        out = selector_updates(sel=name, man=name)
        if ok:
            return (*out, note(f"Saved '{name}'."))
        return (*out, warn(f"Saved '{name}' but registration failed - {msg}"))

    def delete_speaker(sel):
        if not sel or sel == NEW:
            return (*selectors_unchanged(), warn("Nothing selected."))
        state.remove(sel)
        tts.forget(sel)
        stored = os.path.join(VOICES_DIR, f"{sel}.wav")
        if os.path.exists(stored):
            os.remove(stored)
        return (*selector_updates(sel=NEW), note(f"Deleted '{sel}'."))

    def build_persona(handle, current_name):
        """Read someone's message history and write a persona from it.

        A generator, because the scan makes hundreds of API calls and can run
        for minutes; the caller polls the future so the button reports
        progress instead of appearing hung.
        """
        try:
            fut, prog = app.persona_progress(handle)
        except Exception as e:
            yield gr.update(), gr.update(), warn(str(e))
            return

        yield gr.update(), gr.update(), "Starting scan..."
        while not fut.done():
            who = prog.get("resolved") or handle
            yield (gr.update(), gr.update(),
                   f"Reading history for **{who}** - {prog.get('scanned', 0):,} messages "
                   f"checked across {prog.get('channels', 0)} channels, "
                   f"**{prog.get('found', 0)}** of theirs found "
                   f"({prog.get('probes_done', 0)}/{prog.get('probes', 0)} probes).")
            time.sleep(MINE_POLL)

        try:
            resolved, samples, stats = fut.result()
        except Exception as e:
            yield gr.update(), gr.update(), warn(f"Scan failed - {e}")
            return

        if not samples:
            yield (gr.update(), gr.update(),
                   warn(f"No messages found for '{handle}' after reading "
                        f"{stats['scanned']:,} messages in {stats['channels']} channels. "
                        "Check the spelling, or try their user ID - the handle is matched "
                        "against username, display name and nickname."))
            return

        yield (gr.update(), gr.update(),
               f"Found {len(samples)} messages from **{resolved}**. "
               f"Sampling {PERSONA_SAMPLES} and writing the persona - this takes a moment.")
        try:
            persona, used = app.write_persona(resolved, samples)
        except Exception as e:
            yield gr.update(), gr.update(), warn(f"Could not write the persona - {e}")
            return

        # Only fill the name if it is still empty; overwriting a name the user
        # already typed would silently retarget the save.
        name_up = (gr.update(value=resolved) if not (current_name or "").strip()
                   else gr.update())
        yield (name_up, gr.update(value=persona),
               note(f"Built a persona for {resolved} from {used} of {len(samples)} "
                    f"messages ({stats['scanned']:,} scanned). Read it over, then "
                    "press Save speaker."))

    # ---- discord ----------------------------------------------------------
    def connect_bot():
        ok, msg = app.connect_discord()
        labels = [c[0] for c in app.text_channel_choices()]
        return (gr.update(choices=[c[0] for c in app.channel_choices()]),
                gr.update(choices=labels,
                          value=app.text_channel_labels(state.settings.topic_channel_ids)),
                note(msg) if ok else warn(msg))

    def resync_connection():
        """Repopulate the channel lists if the bot is already connected from
        before this page load -- a reload (or a second tab) never re-checks
        this on its own otherwise, so a live connection looks disconnected
        and the pin-channel checklist shows empty until Connect bot is
        pressed again, even though nothing actually dropped.

        Deliberately does not attempt a fresh connection: only resyncs an
        existing one, so opening the page never connects the bot by itself.
        """
        if app.runtime and app.runtime.client.is_ready():
            return connect_bot()
        return gr.update(), gr.update(), gr.update()

    def join_channel(label):
        return note(app.join(label))

    def leave_channel():
        return note(app.leave())

    # ---- topic ------------------------------------------------------------
    def set_topic(text):
        """Editing the topic by hand drops the credit: it is no longer the
        pinned message somebody posted."""
        typed = (text or "").strip()
        if not typed:
            return gr.update(), "Topic left unchanged."
        with state.lock:
            changed = typed != state.settings.topic
            state.settings.topic = typed
            if changed:
                state.settings.topic_author = ""
        state.save()
        return gr.update(value=""), f"Topic set: **{typed[:80]}**"

    def set_pin_channels(labels):
        """The checklist is empty until the bot connects and fills in the
        channel names. Saving that empty list would silently throw away the
        selection from the last session."""
        if not app.text_channel_choices():
            n = len(state.settings.topic_channel_ids)
            return (f"Bot not connected - keeping the {n} pin channel(s) from "
                    "last session.")
        with state.lock:
            state.settings.topic_channel_ids = app.text_channel_ids(labels)
        state.save()
        # A topic prefetched from the old pool is no longer representative.
        if app.director:
            app.director._discard_prepared()
        n = len(state.settings.topic_channel_ids)
        if state.settings.topic_rotation and not n:
            return "**Rotation is on but no pin channels are ticked** - nothing will rotate."
        return f"Pin pool: **{n} channel{'s' if n != 1 else ''}**."

    def set_enabled(names):
        """One-press enable/disable straight from the Run tab."""
        picked = set(names or [])
        with state.lock:
            for sp in state.speakers:
                if sp.ref_wav:
                    sp.enabled = sp.name in picked
        state.save()
        n = len(picked)
        return note(f"{n} speaker{'s' if n != 1 else ''} active"
                    + (f": {', '.join(sorted(picked))}" if 0 < n <= 6 else "."))

    def queue_md():
        """Upcoming topics, so the rotation order is inspectable."""
        if not app.director:
            return "_(connect the bot to see the queue)_"
        q = app.director.topic_queue()
        if not q:
            return ("_No source is both enabled and stocked. Give one weight on "
                    "the Topics tab._")
        # Already-formatted markdown lines, not a numbered list -- the sources
        # are pools with weights, not a single ordered sequence.
        return "\n\n".join(q[:24])

    def crowd_md():
        """What people have submitted with /topic, oldest first."""
        pend = list(state.settings.crowd_topics)
        if not pend:
            return "_(none submitted yet)_"
        rows = []
        for raw in pend[:40]:
            who, _, text = raw.partition("\x1f")
            rows.append(f"- **{who or 'someone'}**: {text or who}")
        extra = len(pend) - len(rows)
        if extra > 0:
            rows.append(f"_...and {extra} more_")
        return "\n".join(rows)

    def clear_crowd():
        with state.lock:
            n = len(state.settings.crowd_topics)
            state.settings.crowd_topics = []
        state.save()
        return note(f"Cleared {n} submitted topic(s)."), crowd_md()

    def switch_to_typed(text):
        """Make whatever is in the Topic box the topic, right now."""
        typed = (text or "").strip()
        if not typed:
            return warn("Type a topic in the box first.")
        if not app.director:
            return warn("Connect the bot first.")
        try:
            changed, queued = app.director.switch_to_topic(typed)
        except Exception as e:
            return warn(f"Failed - {e}")
        if queued:
            return note(f"Switching to your topic {queued}.")
        if not changed:
            d = app.director.topic_debug
            return warn(f"No switch - {d.get('last_error') or 'unknown'}.")
        return note(f"Topic now: **{typed[:70]}**")

    def queue_typed(text):
        """Line the typed topic up to replace the next pin, without cutting in."""
        typed = (text or "").strip()
        if not typed:
            return warn("Type a topic in the box first.")
        if not app.director:
            return warn("Connect the bot first.")
        app.director.queue_topic(typed)
        if state.settings.topic_rotation:
            mins = state.settings.topic_interval_minutes
            return note(f"Queued **{typed[:60]}** - it replaces the next pin "
                        f"(within {mins:g} min).")
        return note(f"Queued **{typed[:60]}**, but rotation is off so nothing will "
                    "fire it. Use Switch to this now, or tick Rotate topic.")

    def reseed_now():
        if not app.director:
            return warn("Connect the bot first.")
        seed = app.director.reseed(state.settings.rng_seed or None)
        return note(f"Reshuffled - seed {seed}. Next cycle uses a new pin order.")

    def rotate_now():
        if not app.director:
            return warn("Connect the bot first.")
        try:
            changed, msg = app.director.rotate_topic_now()
        except Exception as e:
            return warn(f"Failed - {e}")
        if msg:
            return note(f"Topic switch {msg}.")
        d = app.director.topic_debug
        if not changed:
            return warn(f"No switch - {d.get('last_error') or 'no pins found'}.")
        return note(f"Topic now: {d.get('current')}")

    # ---- settings ---------------------------------------------------------
    def set_mode(m):
        with state.lock:
            state.settings.mode = m
        state.save()
        # Cut the line that is mid-playback; the loop drops the pre-generated
        # one on its next pass.
        if m == MODE_MANUAL and app.runtime:
            app.runtime.interrupt()
        return note(f"Mode: **{m}**.")

    def rebuild_note():
        """Swap the backend in place so a running director picks it up on its
        next turn without a restart."""
        active = app.rebuild_llm()
        ok, why = active.available()
        return f"{state.settings.provider}: {active.model}" if ok else f"**{why}**"

    def reset_tuning():
        """Put the four conversation-tuning controls back to values that are
        known to sound right."""
        with state.lock:
            s = state.settings
            s.gap_seconds = RECOMMENDED["gap_seconds"]
            s.temperature = RECOMMENDED["temperature"]
            s.num_predict = RECOMMENDED["num_predict"]
            s.max_history = RECOMMENDED["max_history"]
        state.save()
        return (
            gr.update(value=RECOMMENDED["gap_seconds"]),
            gr.update(value=RECOMMENDED["temperature"]),
            gr.update(value=RECOMMENDED["num_predict"]),
            gr.update(value=RECOMMENDED["max_history"]),
            note("Tuning reset to recommended and saved."),
        )

    def refresh_models(provider):
        probe = app.make_client(provider)
        found = probe.models()
        if not found:
            ok, why = probe.available()
            return gr.update(), gr.update(), warn(why or "No models returned.")
        if provider == PROVIDER_OPENAI:
            return gr.update(), gr.update(choices=found), note(f"{len(found)} OpenAI models.")
        return gr.update(choices=found), gr.update(), note(f"{len(found)} Ollama models.")

    # ---- run --------------------------------------------------------------
    def start_run():
        return note(app.start_director())

    def stop_run():
        return note(app.stop_director())

    def clear_context():
        if app.director:
            # Also drops the pre-generated turn, which was written against the
            # context we are throwing away.
            return note(app.director.clear_context())
        state.clear_transcript()
        return note("LLM context cleared.")

    def manual_say(speaker, text):
        if not speaker:
            return warn("Pick a speaker.")
        if not text or not text.strip():
            return warn("Type something for them to say.")

        # Say starts the director itself -- no need to press Start first.
        ok, msg = app.try_start()
        if not ok:
            return warn(msg)
        if not (app.runtime and app.runtime.connected()):
            return warn("Not in a voice channel - join one at the top, or nobody "
                        "will hear it.")
        try:
            app.director.say_now(speaker, text.strip())
        except Exception as e:
            return warn(f"Failed - {e}")
        return note(f"{msg}Queued for {speaker}.")

    # ---- live feed --------------------------------------------------------
    def poll(last_topic=None):
        lines = []
        for t in state.recent(20):
            tag = "**you**" if t.kind == "user" else f"**{t.speaker}**"
            lines.append(f"{tag}: {t.text}")
        # Rotation rewrites the topic, so push it back into the box -- but
        # only when it actually changed, or the 1.5s feed would fight anyone
        # typing in there. The sender rides along on the same check, so
        # clearing it locally is not undone a second later.
        topic = state.settings.topic
        if topic == last_topic:
            topic_up, from_up = gr.update(), gr.update()
        else:
            topic_up = gr.update(value=topic)
            from_up = gr.update(value=state.settings.topic_author)
        author = state.settings.topic_author
        run_topic = (topic or "_(none)_") + (f"  \n_pinned by {author}_" if author else "")
        q = queue_md()
        return (app.status_line(),
                "\n\n".join(lines) if lines else "_(nothing yet)_",
                run_topic,
                q,
                q,               # same queue, shown on the Topics tab too
                crowd_md(),
                app.voice_report(),
                app.chat_report(),
                app.norm_report(),
                app.topic_report(),
                topic_up,
                from_up)

    def stream_voice_boot():
        """Fill the voice lists once the startup tts-server boot finishes.

        The page is built immediately so the UI is usable, which means it is
        usually built while the server is still loading its model and its
        voice registry is empty. Without this the lists stay empty until
        someone presses Refresh voices -- and the point of launching the
        server automatically is not having to.
        """
        # Always push once. The lists in the page were baked when build() ran,
        # so "the server has voices now" says nothing about what this page is
        # showing -- a tab opened an hour later still carries the empty list.
        yield selector_updates()
        if voice_choices():
            return          # server is up, so that push was the final answer
        deadline = time.monotonic() + VOICE_BOOT_WAIT
        while time.monotonic() < deadline:
            time.sleep(2.0)
            if voice_choices():
                yield selector_updates()
                return

    def stream_status():
        """Live status feed.

        gr.Timer never fires in gradio 6.22, so this is a generator the client
        streams from instead. It is bounded so an abandoned tab eventually
        releases its queue worker; the Refresh button covers the gap after it
        expires. The action line is deliberately not in its outputs.
        """
        deadline = time.monotonic() + POLL_LIFETIME
        last = None
        while time.monotonic() < deadline:
            out = poll(last)
            last = state.settings.topic
            yield out
            time.sleep(POLL_INTERVAL)
        yield poll(last)

    # ---- panels -------------------------------------------------------------
    init_voices = voice_choices()
    init_names = speaker_names()

    def panel_generate():
        with gr.Row():
            with gr.Column():
                u.g_text = gr.Textbox(label="Text", lines=4, value="Hello, this is a test.")
                with gr.Row():
                    u.g_voice = gr.Dropdown(
                        choices=init_voices, label="Voice", scale=4,
                        value=init_voices[0] if init_voices else None)
                    u.g_refresh = gr.Button("Refresh voices", scale=1)
                u.g_instruct = gr.Textbox(
                    label="Style instruction (CustomVoice/VoiceDesign only, rejected by Base)",
                    placeholder="e.g. speak slowly and warmly")
                with gr.Accordion("Sampling", open=False):
                    u.g_temp = gr.Slider(0.1, 2.0, value=0.9, step=0.05, label="Temperature")
                    u.g_topk = gr.Slider(0, 200, value=50, step=1, label="Top-k (0 disables)")
                    u.g_topp = gr.Slider(0.1, 1.0, value=1.0, step=0.05, label="Top-p")
                    u.g_rep = gr.Slider(1.0, 2.0, value=1.05, step=0.01,
                                        label="Repetition penalty")
                    u.g_seed = gr.Number(value=-1, precision=0, label="Seed (-1 = random)")
                    u.g_max = gr.Number(value=2048, precision=0, label="Max new frames")
                u.g_go = gr.Button("Generate", variant="primary")
            with gr.Column():
                # interactive= would otherwise default to False: gradio infers
                # it from whether the component is used as an *input* to any
                # event, and this one never is. Without this, the trim editor
                # silently never renders on a generated clip -- no error, the
                # controls just aren't there.
                u.g_audio = gr.Audio(label="Output", type="filepath", interactive=True)
                u.g_status = gr.Markdown()

    def panel_clone():
        gr.Markdown(
            "Register a voice from a reference clip. Supplying the transcript enables "
            "ICL mode, which tracks the reference more closely; leaving it blank falls "
            "back to speaker-embedding only. Cloning requires a **Base** model."
        )
        with gr.Row():
            with gr.Column():
                u.c_name = gr.Textbox(label="Voice name", placeholder="my-voice")
                u.c_audio = gr.Audio(label="Reference clip", type="filepath")
                gr.Markdown(
                    "_To trim: click the scissors icon to select a range, drag on the "
                    "waveform to highlight it, then click the small **Trim** button that "
                    "appears. Dragging alone does nothing - clicking Trim without a "
                    "highlighted range also does nothing, silently._")
                u.c_text = gr.Textbox(label="Reference transcript (optional)", lines=2)
                with gr.Row():
                    u.c_reg = gr.Button("Register", variant="primary")
                    u.c_refresh = gr.Button("Refresh list")
            with gr.Column():
                u.c_list = gr.Dropdown(choices=init_voices, label="Registered voices")
                u.c_del = gr.Button("Delete selected")
                u.c_status = gr.Markdown()

    def panel_header():
        """Status bar, action line and the connection row -- always visible."""
        with gr.Group():
            u.sb_status = gr.Markdown("_starting..._")
            u.sb_action = gr.Markdown(app.discord_hint())
        # Where the audio goes. Discord is the only output today; the
        # accordion is here so adding others does not move anything.
        with gr.Accordion("Output", open=True):
            gr.Markdown("**Discord**")
            with gr.Row():
                u.d_connect = gr.Button("Connect bot", variant="primary", scale=1)
                u.d_channel = gr.Dropdown(choices=[], label="Voice channel", scale=3,
                                          container=True)
                u.d_join = gr.Button("Join", scale=1)
                u.d_leave = gr.Button("Leave", scale=1)
                u.hdr_refresh = gr.Button("Refresh", scale=1)

    def panel_run():
        u.m_mode = gr.Radio(choices=MODES, value=state.settings.mode, label="Mode",
                            info="podcast - bots talk among themselves, chat ignored. "
                                 "interactive - someone typing in the server interrupts "
                                 "and a router picks who answers. "
                                 "manual - nothing automatic; you pick a speaker and a line.")
        with gr.Row():
            u.r_start = gr.Button("Start", variant="primary")
            u.r_stop = gr.Button("Stop")
            u.r_clear = gr.Button("Clear LLM context")
            u.r_rotate = gr.Button("Switch topic now")
        gr.Markdown("**Current topic**")
        u.run_topic = gr.Markdown(
            f"{state.settings.topic}", height=90, container=True,
            elem_classes=["topic-box"])
        # Enabling/disabling a speaker is the most common mid-conversation
        # change, so it lives here rather than one per speaker on another tab.
        u.r_enabled = gr.CheckboxGroup(
            choices=[s.name for s in state.restorable()],
            value=[s.name for s in state.active()],
            label="Who's talking",
            info="Tick to let someone join the conversation. Saves immediately.")
        gr.Markdown("**Up next**")
        u.run_queue = gr.Markdown(
            "_(no queue yet)_", height=180, container=True,
            elem_classes=["queue-box"])
        gr.Markdown("**Transcript**")
        u.r_transcript = gr.Markdown(
            "_(nothing yet)_", height=340, container=True,
            elem_classes=["transcript-box"])
        gr.Markdown("**Say a line as** (works in any mode, jumps the queue)")
        with gr.Row():
            u.man_speaker = gr.Dropdown(choices=init_names, label="Speaker", scale=1)
            u.man_text = gr.Textbox(label="Say this", scale=3)
            u.man_go = gr.Button("Say", scale=1, variant="primary")

    def panel_speakers():
        with gr.Row():
            with gr.Column(scale=2):
                u.s_roster = gr.Radio(choices=roster_choices(), value=NEW,
                                      label="Roster", info="Pick one to edit it.")
            with gr.Column(scale=3):
                u.s_name = gr.Textbox(label="Name")
                u.s_clip = gr.Audio(label="Reference clip", type="filepath")
                gr.Markdown(
                    "_To trim: click the scissors icon to select a range, drag on the "
                    "waveform to highlight it, then click the small **Trim** button that "
                    "appears. Dragging alone does nothing - clicking Trim without a "
                    "highlighted range also does nothing, silently._")
                u.s_reftext = gr.Textbox(label="Reference transcript (optional)", lines=2)
                u.s_persona = gr.Textbox(
                    label="System prompt / persona", lines=8,
                    placeholder="You are Dave. You are relentlessly upbeat and "
                                "derail every topic into cycling.")
                with gr.Group():
                    gr.Markdown(
                        "**Build a persona from their Discord history.** Reads this "
                        f"person's messages from the last {PERSONA_YEARS:g} years, "
                        f"samples {PERSONA_SAMPLES} at random, and has the LLM write "
                        "a system prompt from how they actually talk. The sampled "
                        "messages are quoted in the prompt too. Overwrites the box "
                        "above - it is not saved until you press **Save speaker**."
                    )
                    with gr.Row():
                        u.s_handle = gr.Textbox(
                            label="Discord handle", scale=3,
                            placeholder="username, display name, or user ID")
                        u.s_mine = gr.Button("Build persona", scale=1)
                u.s_stims = gr.Textbox(
                    label="Vocal stims", lines=3,
                    placeholder="jellyfishing\nI'm ready\nbarnacles",
                    info="Catchphrases this character blurts out. One per line "
                         "(commas work too); one is picked at random when the roll "
                         "succeeds.")
                u.s_stim_pct = gr.Slider(
                    0, 100, value=0, step=5, label="Stim chance (%)",
                    info="Share of this speaker's turns that carry a stim. 0 disables. "
                         "The model is asked to work it in; if it ignores that, the "
                         "phrase is appended so a successful roll always lands.")
                with gr.Row():
                    u.s_save = gr.Button("Save speaker", variant="primary")
                    u.s_delete = gr.Button("Delete")

    def panel_topic():
        gr.Markdown(
            "What they talk about. The **Topic** box is live - rotation "
            "overwrites it, so it always shows what is actually being "
            "discussed. Press Enter or click away to save an edit."
        )
        with gr.Row():
            u.m_topic = gr.Textbox(value=state.settings.topic, label="Topic",
                                   lines=2, scale=4)
            with gr.Column(scale=1, min_width=170):
                u.m_topic_now = gr.Button("Switch to this now", variant="primary")
                u.m_topic_queue = gr.Button("Queue as next topic")
        u.m_topic_from = gr.Textbox(
            value=state.settings.topic_author, label="Pinned by",
            interactive=False, max_lines=1,
            info="Who posted the pin this topic came from, and where. Blank once "
                 "you edit the topic by hand - there is no sender to credit then.")

        gr.Markdown("**Up next**")
        u.topic_queue_md = gr.Markdown(
            "_(no queue yet)_", height=200, container=True,
            elem_classes=["queue-box"])

        with gr.Row():
            u.m_rotate = gr.Checkbox(value=state.settings.topic_rotation,
                                     label="Rotate topic automatically")
            u.m_rot_mins = gr.Slider(1, 60, value=state.settings.topic_interval_minutes,
                                     step=1, label="Every N minutes")
        with gr.Row():
            u.m_rot_clear = gr.Checkbox(value=state.settings.topic_clears_context,
                                        label="Clear context on switch")
            u.m_rot_say = gr.Checkbox(value=state.settings.topic_announce,
                                      label="Announce the switch out loud")
            u.m_rot_instant = gr.Checkbox(
                value=state.settings.topic_switch_instant,
                label="Switch instantly",
                info="Cut the current speaker off mid-sentence instead of "
                     "waiting for the utterance to finish.")

        gr.Markdown(
            "### Sources\n"
            "Each switch picks a source at random, weighted by the sliders below. "
            "A source with weight 0, or with nothing in it, is never drawn - so "
            "50/0/0 and 5/0/0 behave identically."
        )
        with gr.Tabs():
            with gr.Tab("Discord (pins)"):
                u.w_pins = gr.Slider(
                    0, 100, value=state.settings.source_pins_weight, step=5,
                    label="Weight", info="How often the topic comes from a pinned message.")
                u.m_rot_chans = gr.CheckboxGroup(
                    choices=[], value=[], label="Pin channels",
                    info="Every ticked channel feeds one shared pool. Pins are drawn "
                         "from a shuffled bag, so all get used before any repeats. "
                         "Connect the bot to populate this list.")
                u.m_images = gr.Checkbox(
                    value=state.settings.topic_images,
                    label="Send pinned images to the model",
                    info="When a pin is an image, the announcer describes it out loud "
                         "and that description becomes everyone else's context - one "
                         "vision call per topic, not one per turn. Needs a multimodal "
                         "model.")
                with gr.Row():
                    u.m_seed = gr.Number(
                        value=state.settings.rng_seed, precision=0, label="RNG seed",
                        info="Drives pin order, speaker choice and stim rolls. 0 = "
                             "fresh seed each start. Applies on reconnect.")
                    u.m_reseed = gr.Button("Reshuffle now")
                    u.m_rot_next = gr.Button("Skip to next pin")

            with gr.Tab("Web search"):
                u.w_web = gr.Slider(
                    0, 100, value=state.settings.source_web_weight, step=5,
                    label="Weight", info="How often the topic comes from a web search.")
                u.web_subjects = gr.Textbox(
                    value=state.settings.web_subjects, lines=6,
                    label="Subjects to search",
                    placeholder="deep sea creatures\nweird food history\nunsolved mysteries",
                    info="One per line. The director works through them in order, and "
                         "through the results of each in random order, so a subject is "
                         "not used up before the next one gets a turn.")
                u.web_n = gr.Slider(
                    3, 20, value=state.settings.web_results_per_search, step=1,
                    label="Results per search",
                    info="Fetched once per subject and cached, then handed out one at "
                         "a time.")
                gr.Markdown(
                    "_Searches DuckDuckGo with no API key. If it ever stops returning "
                    "anything, the layout changed - check the app log for `[web]`._")

            with gr.Tab("Discord (crowd-sourced)"):
                u.w_crowd = gr.Slider(
                    0, 100, value=state.settings.source_crowd_weight, step=5,
                    label="Weight", info="How often the topic comes from a submission.")
                gr.Markdown(
                    "Anyone in the server types **`/topic something to talk about`** "
                    "in any channel and it lands in the queue. The bot reacts \u2705 to "
                    "confirm. Submissions are used oldest-first and are announced with "
                    "credit to whoever sent them.\n\n"
                    "It is a plain message prefix, not a registered slash command, so "
                    "Discord will show 'no command found' in the picker - sending it "
                    "anyway works."
                )
                u.crowd_pending = gr.Markdown(
                    "_(none submitted yet)_", height=200, container=True,
                    elem_classes=["queue-box"])
                with gr.Row():
                    u.crowd_clear = gr.Button("Clear submissions")
                    u.crowd_max = gr.Slider(
                        10, 500, value=state.settings.crowd_max, step=10,
                        label="Keep at most",
                        info="Oldest are dropped past this.")

    def panel_behaviour():
        gr.Markdown("### Language model")
        with gr.Row():
            u.m_provider = gr.Radio(choices=PROVIDERS, value=state.settings.provider,
                                    label="LLM provider")
            u.m_refresh_models = gr.Button("Refresh model list")
        gr.Markdown(
            "`ollama` runs locally but shares the GPU with the TTS server - keep the "
            "model small. `openai` is remote: it costs money and adds latency, but "
            "leaves the whole card free for speech.\n\n"
            "Secrets come from `.env` at the repo root (copy `.env.example`); they are "
            f"never entered or stored in this UI. Current state - `{app.env_summary()}`"
        )
        with gr.Row():
            u.m_model = gr.Dropdown(
                choices=app.ollama.models() or [state.settings.ollama_model],
                value=state.settings.ollama_model, label="Ollama model")
            u.m_oa_model = gr.Dropdown(
                choices=[state.settings.openai_model], value=state.settings.openai_model,
                label="OpenAI model", allow_custom_value=True)

        gr.Markdown("### Conversation tuning")
        with gr.Row():
            u.m_gap = gr.Slider(0, 3, value=state.settings.gap_seconds, step=0.1,
                                label="Gap between turns (s)", info=HELP["gap_seconds"])
            u.m_temp = gr.Slider(0.1, 1.5, value=state.settings.temperature, step=0.05,
                                 label="LLM temperature", info=HELP["temperature"])
        with gr.Row():
            u.m_pred = gr.Slider(20, 300, value=state.settings.num_predict, step=10,
                                 label="Max tokens per line", info=HELP["num_predict"])
            u.m_hist = gr.Slider(4, 40, value=state.settings.max_history, step=1,
                                 label="Context turns", info=HELP["max_history"])
        u.m_reset = gr.Button(
            "Reset the four above to recommended "
            f"(gap {RECOMMENDED['gap_seconds']}s, temp {RECOMMENDED['temperature']}, "
            f"{RECOMMENDED['num_predict']} tokens, {RECOMMENDED['max_history']} turns)")

        gr.Markdown("### Audio")
        with gr.Row():
            u.m_norm = gr.Checkbox(value=state.settings.normalize_audio,
                                   label="Normalise output loudness")
            u.m_dbfs = gr.Slider(-30, -12, value=state.settings.target_dbfs, step=0.5,
                                 label="Target loudness (dBFS RMS)")
        with gr.Row():
            u.m_cap_on = gr.Checkbox(value=state.settings.speech_limit_enabled,
                                     label="Hard limit on speech length")
            u.m_cap_sec = gr.Slider(
                3, 120, value=state.settings.max_speech_seconds, step=1,
                label="Max seconds per utterance",
                info="Ceiling on how long one line can hold the channel. Generation "
                     "is capped in frames so nothing is rendered and then discarded; "
                     "anything still over is cut with a short fade.")

        gr.Markdown("### Connection and interruption")
        with gr.Row():
            u.m_barge = gr.Checkbox(
                value=state.settings.barge_in,
                label="Interrupt the current line when someone types")
            u.m_rejoin = gr.Checkbox(
                value=state.settings.auto_rejoin, label="Rejoin automatically",
                info="Discord ends the call when the last person leaves, and "
                     "discord.py never comes back on its own. A watchdog rejoins as "
                     "soon as somebody returns.")
            u.m_pause_empty = gr.Checkbox(
                value=state.settings.pause_when_empty,
                label="Pause while the channel is empty",
                info="Stop generating speech nobody can hear. After five minutes "
                     "empty the conversation restarts instead of resuming.")

        gr.Markdown(
            "### Spoken lines\n"
            "One sentence **per line** in each box - a line is picked at random "
            "each time, so the same event doesn't sound identical every occurrence. "
            "Leave a box empty to say nothing.")

        u.m_open_on = gr.Checkbox(value=state.settings.opening_enabled,
                                  label="Speak an opening when the conversation starts")
        u.m_open_tpl = gr.Textbox(
            value=state.settings.opening_template, lines=4, label="Opening",
            info="Spoken once when you press Start. " + tpl_vars("opening_template"))

        u.m_topic_tpl = gr.Textbox(
            value=state.settings.topic_template, lines=4,
            label="Topic switch announcement",
            info="Read out on every topic change. " + tpl_vars("topic_template"))
        u.m_topic_img_tpl = gr.Textbox(
            value=state.settings.topic_image_template, lines=3,
            label="Topic switch - image pins",
            info="Added after the announcement when the pin is a picture, so "
                 "the channel knows why the subject changed to something with "
                 "no words in it. " + tpl_vars("topic_image_template"))

        u.m_bye_on = gr.Checkbox(value=state.settings.goodbye_enabled,
                                 label="Say goodbye when you press Stop")
        u.m_bye_tpl = gr.Textbox(
            value=state.settings.goodbye_template, lines=4, label="Goodbye",
            info="Spoken once after the current line is cut off, before the "
                 "bots go quiet. " + tpl_vars("goodbye_template"))

    def panel_diagnostics():
        gr.Markdown(
            "**Voice connection** - Discord terminates the call when the channel "
            "empties; the watchdog gets back in when someone returns."
        )
        u.dbg_voice = gr.Markdown("_(not connected)_")
        gr.Markdown(
            "**Chat input** - the only path a real person has into the conversation. "
            "Messages count only from the server the bot is currently in, and only "
            "act in interactive mode."
        )
        u.dbg_chat = gr.Markdown("_(not connected)_")
        gr.Markdown("**Output loudness**")
        u.dbg_norm = gr.Markdown("_(nothing played yet)_")
        gr.Markdown("**Topic rotation**")
        u.dbg_topic = gr.Markdown("_(rotation off)_")

    # ---- layout -------------------------------------------------------------
    with gr.Blocks(title="qwentts.cpp") as demo:
        gr.Markdown(f"# qwentts.cpp\n{app.banner()}")

        with gr.Tab("Generate"):
            panel_generate()
        with gr.Tab("Clone a voice"):
            panel_clone()

        with gr.Tab("Dead Internet Mode"):
            gr.Markdown(
                "Populate a voice channel with cloned personas driven by an LLM. "
                "**Only clone people who agreed to it, and keep the channel to people "
                "who know the voices are synthetic.** Real people join in by "
                "**typing in a text channel** - there is no speech recognition."
            )
            panel_header()
            with gr.Tabs():
                with gr.Tab("Run"):
                    panel_run()
                with gr.Tab("Speakers"):
                    panel_speakers()
                with gr.Tab("Topic and pins"):
                    panel_topic()
                with gr.Tab("Behaviour"):
                    panel_behaviour()
                with gr.Tab("Diagnostics"):
                    panel_diagnostics()

        # ---- wiring ---------------------------------------------------------
        u.g_go.click(do_synth,
                     [u.g_text, u.g_voice, u.g_instruct,
                      u.g_temp, u.g_topk, u.g_topp,
                      u.g_rep, u.g_seed, u.g_max],
                     [u.g_audio, u.g_status])

        # Every mutation refreshes all four selectors, so nothing on another
        # tab goes stale until the app restarts.
        sel_out = [u.g_voice, u.c_list, u.s_roster, u.man_speaker, u.r_enabled]
        u.g_refresh.click(refresh_voices, None, sel_out + [u.g_status])
        u.c_reg.click(do_register, [u.c_name, u.c_audio, u.c_text], sel_out + [u.c_status])
        u.c_del.click(do_delete_voice, u.c_list, sel_out + [u.c_status])
        u.c_refresh.click(lambda: (*selector_updates(), "Refreshed."),
                          None, sel_out + [u.c_status])

        u.d_connect.click(connect_bot, None, [u.d_channel, u.m_rot_chans, u.sb_action])
        u.d_join.click(join_channel, u.d_channel, u.sb_action)
        u.d_leave.click(leave_channel, None, u.sb_action)

        # Speakers
        u.s_roster.change(load_speaker, u.s_roster,
                          [u.s_name, u.s_clip, u.s_reftext, u.s_persona, u.s_stims,
                           u.s_stim_pct, u.sb_action])
        u.s_save.click(save_speaker,
                       [u.s_name, u.s_clip, u.s_reftext, u.s_persona, u.s_stims,
                        u.s_stim_pct],
                       sel_out + [u.sb_action])
        u.s_delete.click(delete_speaker, u.s_roster, sel_out + [u.sb_action])
        u.s_mine.click(build_persona, [u.s_handle, u.s_name],
                       [u.s_name, u.s_persona, u.sb_action])

        # Run
        u.r_start.click(start_run, None, u.sb_action)
        u.r_stop.click(stop_run, None, u.sb_action)
        u.r_clear.click(clear_context, None, u.sb_action)
        u.r_rotate.click(rotate_now, None, u.sb_action)
        u.man_go.click(manual_say, [u.man_speaker, u.man_text], u.sb_action)
        u.r_enabled.change(set_enabled, u.r_enabled, u.sb_action)
        u.man_text.submit(manual_say, [u.man_speaker, u.man_text], u.sb_action)
        u.m_mode.change(set_mode, u.m_mode, u.sb_action)

        # Topic. The topic box has its own handler because an edit also drops
        # the "pinned by" credit; the rest are plain autosaves.
        u.m_topic.blur(set_topic, u.m_topic, [u.m_topic_from, u.sb_action])
        u.m_topic.submit(set_topic, u.m_topic, [u.m_topic_from, u.sb_action])
        u.m_rot_chans.change(set_pin_channels, u.m_rot_chans, u.sb_action)
        u.m_reseed.click(reseed_now, None, u.sb_action)
        u.m_rot_next.click(rotate_now, None, u.sb_action)
        bind(u.w_pins, "source_pins_weight", "pins weight", float, "release")
        bind(u.w_web, "source_web_weight", "web weight", float, "release")
        bind(u.w_crowd, "source_crowd_weight", "crowd weight", float, "release")
        bind(u.web_subjects, "web_subjects", "web subjects", None, "blur")
        bind(u.web_n, "web_results_per_search", "results per search", int, "release")
        bind(u.crowd_max, "crowd_max", "crowd queue cap", int, "release")
        u.crowd_clear.click(clear_crowd, None, [u.sb_action, u.crowd_pending])
        u.m_topic_now.click(switch_to_typed, u.m_topic, u.sb_action)
        u.m_topic_queue.click(queue_typed, u.m_topic, u.sb_action)
        bind(u.m_rotate, "topic_rotation", "topic rotation", bool)
        bind(u.m_rot_mins, "topic_interval_minutes", "rotation interval", float, "release")
        bind(u.m_images, "topic_images", "pinned images", bool)
        bind(u.m_rot_clear, "topic_clears_context", "clear context on switch", bool)
        bind(u.m_rot_say, "topic_announce", "announce switches", bool)
        bind(u.m_rot_instant, "topic_switch_instant", "instant switching", bool)
        bind(u.m_seed, "rng_seed", "RNG seed", lambda v: int(v or 0), "blur")

        # Behaviour. No Apply button: everything persists as you change it.
        bind(u.m_provider, "provider", "LLM provider", after=rebuild_note)
        bind(u.m_model, "ollama_model", "Ollama model", after=rebuild_note)
        bind(u.m_oa_model, "openai_model", "OpenAI model",
             lambda v: (v or "").strip(), after=rebuild_note)
        bind(u.m_gap, "gap_seconds", "gap between turns", float, "release")
        bind(u.m_temp, "temperature", "LLM temperature", float, "release")
        bind(u.m_pred, "num_predict", "max tokens per line", int, "release")
        bind(u.m_hist, "max_history", "context turns", int, "release")
        bind(u.m_norm, "normalize_audio", "loudness normalisation", bool)
        bind(u.m_dbfs, "target_dbfs", "target loudness", float, "release")
        bind(u.m_cap_on, "speech_limit_enabled", "speech cap", bool)
        bind(u.m_cap_sec, "max_speech_seconds", "max seconds per utterance",
             float, "release")
        bind(u.m_barge, "barge_in", "interrupt on message", bool)
        bind(u.m_rejoin, "auto_rejoin", "auto-rejoin", bool,
             after=lambda: app.sync_voice_settings())
        bind(u.m_pause_empty, "pause_when_empty", "pause while empty", bool)
        bind(u.m_open_on, "opening_enabled", "opening announcement", bool)
        bind(u.m_open_tpl, "opening_template", "opening line", None, "blur")
        bind(u.m_topic_tpl, "topic_template", "topic announcement", None, "blur")
        bind(u.m_topic_img_tpl, "topic_image_template", "image announcement",
             None, "blur")
        bind(u.m_bye_on, "goodbye_enabled", "goodbye", bool)
        bind(u.m_bye_tpl, "goodbye_template", "goodbye line", None, "blur")
        u.m_refresh_models.click(refresh_models, u.m_provider,
                                 [u.m_model, u.m_oa_model, u.sb_action])
        u.m_reset.click(reset_tuning, None,
                        [u.m_gap, u.m_temp, u.m_pred, u.m_hist, u.sb_action])

        # Live feed. sb_action is deliberately absent: it is written only by
        # the handlers above, so a confirmation survives longer than 1.5s.
        outs = [u.sb_status, u.r_transcript, u.run_topic, u.run_queue,
                u.topic_queue_md, u.crowd_pending,
                u.dbg_voice, u.dbg_chat, u.dbg_norm, u.dbg_topic,
                u.m_topic, u.m_topic_from]
        # Wrapped so gradio sees a zero-argument callable: an explicit refresh
        # always pushes the topic, while the streaming feed passes the last
        # value it sent so it can skip an unchanged one.
        u.hdr_refresh.click(lambda: poll(), None, outs)
        # Restore the channel lists on page load when the bot is already
        # connected, so a reload or an app restart that left the connection
        # up does not present an empty pin-channel checklist.
        demo.load(resync_connection, None,
                  [u.d_channel, u.m_rot_chans, u.sb_action])
        u.hdr_refresh.click(resync_connection, None,
                            [u.d_channel, u.m_rot_chans, u.sb_action])
        demo.load(stream_status, None, outs)
        # Voice lists are read from the tts-server registry when the page is
        # built, which at startup is before the server has finished booting.
        demo.load(stream_voice_boot, None, sel_out)
        demo.load(None, None, None, js=AUTOSCROLL_JS)

    return demo
