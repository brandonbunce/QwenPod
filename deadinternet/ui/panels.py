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

from .. import audiodev
from .components import (BehaviourPanel, DiagnosticsPanel, GeneratePanel,
                         HeaderPanel, OutputsPanel, RunPanel, SpeakersPanel,
                         TopicPanel)
from .feedback import tpl_vars
from .help import help
from .tabs.behaviour import music_report as t_behaviour_music
from .tabs.local import local_visible as t_local_visible
from .tabs.local import remote_note as t_remote_note_text
from .mic import MIC_HTML
from .selectors import NEW, roster_choices, voice_choices
from ..config import (HELP, MODES, PERSONA_SAMPLES, PERSONA_YEARS,
                      RECOMMENDED, TTS_DEVICES, TTS_HERE, TTS_REMOTE,
                      TTS_WHERE, is_local_tts)
from ..comedy import DEFAULT_MOVES
from ..llm import DEFAULT_AD_PROMPT, PROVIDERS

def build_diagnostics(app):
    """Two columns: services and the event log on the left, the four subsystem
    reports on the right.

    The reports use the same scrolling boxes as the transcript rather than bare
    Markdown, so a long report scrolls in place instead of pushing everything
    below it off the screen.
    """
    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            gr.Markdown("**Services**")
            services = gr.Markdown(app.service_report(), container=True,
                                   elem_classes=["status-box"])
            with gr.Row():
                gr.Markdown("**Event log** — what happened, newest last")
                log_clear = gr.Button("Clear", scale=0, min_width=80)
            # Textbox, not Markdown: the log carries names and arbitrary
            # message text, and a stray asterisk should not turn the rest of
            # it italic. Monospace via CSS so the timestamps line up.
            log = gr.Textbox(
                value=app.events.render(), label=None, container=False,
                lines=26, max_lines=26, interactive=False,
                elem_classes=["log-box"])

        with gr.Column(scale=1):
            gr.Markdown(
                "**Voice connection** - Discord terminates the call when the channel "
                "empties; the watchdog gets back in when someone returns."
            )
            voice = gr.Markdown("_(not connected)_", height=150, container=True,
                                elem_classes=["status-box"])
            gr.Markdown(
                "**Chat input** - the only path a real person has into the "
                "conversation. Messages count only from the server the bot is "
                "currently in, and only act in interactive mode."
            )
            chat = gr.Markdown("_(not connected)_", height=150, container=True,
                               elem_classes=["status-box"])
            gr.Markdown("**Output loudness**")
            norm = gr.Markdown("_(nothing played yet)_", height=110, container=True,
                               elem_classes=["status-box"])
            gr.Markdown("**Topic rotation**")
            topic = gr.Markdown("_(rotation off)_", height=150, container=True,
                                elem_classes=["status-box"])
            gr.Markdown(
                "**Comedy** — what the last line was told to do, and how the "
                "last segment measured. The numbers are proxies: none of them "
                "is *funny*, but each is something a dull transcript gets wrong."
            )
            comedy = gr.Markdown("_(nothing said yet)_", height=150, container=True,
                                 elem_classes=["status-box"])

    return DiagnosticsPanel(voice=voice, chat=chat, norm=norm, topic=topic,
                            comedy=comedy,
                            services=services, log=log, log_clear=log_clear)


def build_outputs(app):
    """Where the audio goes. Two outputs, one at a time -- the director holds
    a single runtime, so each refuses to start while the other is live.

    The two outputs are nested tabs, the same shape as the topic sources on
    Inputs: you look at the one you are using. Below them, two closed
    accordions hold what applies to both.
    """
    s = app.state.settings
    with gr.Tabs():
        with gr.Tab("Discord"):
            with gr.Row():
                d_connect = gr.Button("Connect bot", variant="primary", scale=1)
                d_channel = gr.Dropdown(
                    choices=[], label="Voice channel", scale=3,
                    info=help("Nothing is audible until the bot is connected "
                              "and in a channel.",
                              "One output at a time: while the bot is "
                              "connected, local output cannot start. "
                              "Disconnect first."))
                d_join = gr.Button("Join", scale=1)
                d_leave = gr.Button("Leave", scale=1)
                d_disconnect = gr.Button("Disconnect", scale=1)
            gr.Markdown(app.discord_hint(), elem_classes=["qp-note"])

        with gr.Tab("This machine"):
            with gr.Row():
                l_start = gr.Button("Start local output", variant="primary", scale=1)
                l_sink = gr.Dropdown(
                    choices=app.local_sink_choices(), value=s.local_sink,
                    label="Output device", scale=3,
                    info=help("Plays out of a sound device here. No token, no "
                              "bot, no voice channel.",
                              "One output at a time: while this is playing "
                              "the bot cannot connect. Stop first."))
                l_refresh = gr.Button("Refresh devices", scale=1)
                l_stop = gr.Button("Stop", scale=1)
            with gr.Accordion("Virtual microphone", open=False):
                with gr.Row():
                    v_create = gr.Button("Create", variant="primary", scale=1)
                    v_remove = gr.Button("Remove", scale=1)
                    v_mon_on = gr.Button("Monitor on", scale=1)
                    v_mon_off = gr.Button("Monitor off", scale=1)
                v_volume = gr.Slider(
                    0, audiodev.MONITOR_MAX_VOLUME, step=5,
                    value=s.local_monitor_volume,
                    label="Monitor volume (%)",
                    info=help("How loud you hear it. Anything recording the "
                              "microphone still gets the full level.",
                              "A virtual device has no speakers, so you go "
                              "deaf to what you are sending. Monitor echoes "
                              "it to your default output as its own stream, "
                              "which `pavucontrol` can also adjust. None of "
                              "this survives a reboot; press Create again."))
                v_status = gr.Markdown(audiodev.report(), elem_classes=["qp-note"])
                # What the virtual microphone is for. The pactl commands
                # behind the buttons are in DEADINTERNET.md.
                gr.Markdown(
                    "Turns this machine's output into an input any game or "
                    f"voice client can select: pick **{audiodev.SINK}** as the "
                    f"output device above and **{audiodev.SOURCE_DESC}** as "
                    "the microphone in the other application.",
                    elem_classes=["qp-note"])

    with gr.Accordion("Loudness and length", open=False):
        with gr.Row():
            m_norm = gr.Checkbox(value=s.normalize_audio,
                                 label="Normalise output loudness",
                                 info=help("Applies to both outputs."))
            m_dbfs = gr.Slider(-30, -12, value=s.target_dbfs, step=0.5,
                               label="Target loudness (dBFS RMS)")
        with gr.Row():
            m_cap_on = gr.Checkbox(value=s.speech_limit_enabled,
                                   label="Hard limit on speech length")
            m_cap_sec = gr.Slider(
                3, 120, value=s.max_speech_seconds, step=1,
                label="Max seconds per utterance",
                info=help("Ceiling on how long one line can hold the channel.",
                          "Generation is capped in frames so nothing is "
                          "rendered and then discarded; anything still over "
                          "is cut with a short fade."))

    with gr.Accordion("Speech engine", open=False):
        remote = not is_local_tts(s.tts_url)
        with gr.Row():
            t_where = gr.Radio(
                choices=TTS_WHERE, value=TTS_REMOTE if remote else TTS_HERE,
                label="Where speech is generated", scale=2,
                info=help("Shared by both outputs.",
                          "Remote speaks through a tts-server on another "
                          "machine, worth having when this card is busy or "
                          "the model will not fit on it. Voices live in the "
                          "server's memory, so pointing at a new one "
                          "re-uploads every reference clip, which takes a "
                          "minute or two. A remote server's voices are never "
                          "changed from here."))
            t_url = gr.Textbox(
                value=s.tts_url if remote else s.tts_remote_url,
                label="Remote tts-server", scale=2,
                placeholder="http://voice.example.internal:8080",
                info=help("Address only, no path.",
                          f"Used when Where is set to '{TTS_REMOTE}'."))
            t_use = gr.Button("Use this server", scale=1)
        # The three controls below drive tts-server on THIS machine and say
        # nothing about one somewhere else, so while speech comes from
        # elsewhere they are off the page and one line says why. Column, not
        # Group: it is a visibility container, not a box.
        local = t_local_visible(app)
        with gr.Column(visible=local) as t_local_box:
            with gr.Row():
                t_device = gr.Radio(
                    choices=TTS_DEVICES, value=s.tts_device,
                    label="Backend", scale=2,
                    info=help("gpu is faster; cpu needs no VRAM.",
                              "tts-server allocates once, at startup. One "
                              "that started on a full card runs about 3x "
                              "slower for the rest of its life, and freeing "
                              "VRAM afterwards does not undo it: Restart "
                              "does. Stop frees the card entirely, about "
                              "6.5 GB, and the next thing that needs speech "
                              "starts it again. See *VRAM* in "
                              "DEADINTERNET.md."))
                t_restart = gr.Button("Restart tts-server", scale=1)
                t_stop = gr.Button("Stop tts-server", scale=1)
        t_remote_note = gr.Markdown(t_remote_note_text(s.tts_url),
                                    visible=not local, elem_classes=["qp-note"])
    return OutputsPanel(
        d_connect=d_connect,
        d_channel=d_channel,
        d_join=d_join,
        d_leave=d_leave,
        d_disconnect=d_disconnect,
        l_start=l_start,
        l_stop=l_stop,
        l_sink=l_sink,
        l_refresh=l_refresh,
        v_create=v_create,
        v_remove=v_remove,
        v_mon_on=v_mon_on,
        v_mon_off=v_mon_off,
        v_volume=v_volume,
        v_status=v_status,
        m_norm=m_norm,
        m_dbfs=m_dbfs,
        m_cap_on=m_cap_on,
        m_cap_sec=m_cap_sec,
        t_restart=t_restart,
        t_stop=t_stop,
        t_device=t_device,
        t_where=t_where,
        t_url=t_url,
        t_use=t_use,
        t_local_box=t_local_box,
        t_remote_note=t_remote_note,
    )


def build_generate(init_voices):
    with gr.Row():
        with gr.Column():
            g_text = gr.Textbox(label="Text", lines=4, value="Hello, this is a test.")
            # Refresh voices is on the Speakers roster row, not here: it
            # refills selectors on three tabs, and this tab is hidden
            # while speech comes from a remote server.
            g_voice = gr.Dropdown(
                choices=init_voices, label="Voice",
                value=init_voices[0] if init_voices else None)
            g_instruct = gr.Textbox(
                label="Style instruction (CustomVoice/VoiceDesign only, rejected by Base)",
                placeholder="e.g. speak slowly and warmly")
            with gr.Accordion("Sampling", open=False):
                g_temp = gr.Slider(0.1, 2.0, value=0.9, step=0.05, label="Temperature")
                g_topk = gr.Slider(0, 200, value=50, step=1, label="Top-k (0 disables)")
                g_topp = gr.Slider(0.1, 1.0, value=1.0, step=0.05, label="Top-p")
                g_rep = gr.Slider(1.0, 2.0, value=1.05, step=0.01,
                                  label="Repetition penalty")
                g_seed = gr.Number(value=-1, precision=0, label="Seed (-1 = random)")
                g_max = gr.Number(value=2048, precision=0, label="Max new frames")
            g_go = gr.Button("Generate", variant="primary")
        with gr.Column():
            # interactive= would otherwise default to False: gradio infers
            # it from whether the component is used as an *input* to any
            # event, and this one never is. Without this, the trim editor
            # silently never renders on a generated clip -- no error, the
            # controls just aren't there.
            g_audio = gr.Audio(label="Output", type="filepath", interactive=True)
            g_status = gr.Markdown()
    return GeneratePanel(
        g_text=g_text,
        g_instruct=g_instruct,
        g_go=g_go,
        g_audio=g_audio,
        g_status=g_status,
        g_voice=g_voice,
        g_temp=g_temp,
        g_topk=g_topk,
        g_topp=g_topp,
        g_rep=g_rep,
        g_seed=g_seed,
        g_max=g_max,
    )


def build_header(app):
    """The masthead: title, live status strip, and the sticky action line.

    The status strip used to sit inside the Dead Internet tab, which meant the
    mode and VRAM readings vanished whenever you were on another tab -- exactly
    when a stalled director or a full card is easiest to miss. It lives above
    the tabs now, so it is always on screen.
    """
    # Name hard left; Refresh and the live status as a pair on the right. One
    # line, so the page is masthead / action line / tabs and nothing else above
    # the fold.
    with gr.Row(elem_classes=["masthead"]):
        gr.Markdown("# QwenPod", elem_classes=["brand"])
        hdr_refresh = gr.Button("Refresh", scale=0, min_width=90)
        sb_status = gr.Markdown("_starting..._", elem_classes=["status-strip"])
    sb_action = gr.Markdown(app.banner(), elem_classes=["action-line"])
    return HeaderPanel(
        sb_status=sb_status,
        sb_action=sb_action,
        hdr_refresh=hdr_refresh,
    )


def build_run(app, init_names):
    """Two columns: driving the show on the left, watching it on the right.

    Left runs top-down in the order you actually use it: pick a mode, start it,
    see what is coming, see what is on now, then the four ways to change it.
    Right is the things you read -- who is in, what they said -- with the one
    control that interrupts them at the bottom.

    Start/Stop used to span both columns above everything. They belong with
    Mode: choosing how it runs and making it run are one decision, and putting
    them in the left column stops the top of the tab being a row of four
    buttons that do unrelated things.
    """
    state = app.state
    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            m_mode = gr.Radio(
                choices=MODES, value=state.settings.mode, label="Mode",
                info="podcast - bots talk among themselves, chat ignored. "
                     "interactive - someone typing in the server interrupts "
                     "and a router picks who answers. "
                     "manual - nothing automatic; you pick a speaker and a line.")
            with gr.Row():
                r_start = gr.Button("Start", variant="primary", scale=1)
                r_stop = gr.Button("Stop", scale=1)

            gr.Markdown("**Up next**")
            run_queue = gr.Markdown(
                "_(no queue yet)_", height=150, container=True,
                elem_classes=["queue-box"])

            gr.Markdown("**Current topic**")
            run_topic = gr.Markdown(
                f"{state.settings.topic}", height=110, container=True,
                elem_classes=["topic-box"])

            # A second way into switch_to_typed/queue_typed, so a topic can be
            # injected without leaving the tab you are watching. Its own box
            # rather than a mirror of the one on Inputs: two components bound
            # to one setting would fight each other on every feed tick.
            run_inject = gr.Textbox(
                label="Insert a topic", lines=2,
                placeholder="something for them to talk about")
            # Labelled by what they act on, not by when. "Switch to this now"
            # next to "Switch topic now" was two buttons a word apart that did
            # different things -- one uses the box above, the other takes the
            # next thing off the queue.
            run_inject_now = gr.Button("Switch to typed topic now",
                                       variant="primary")
            r_rotate = gr.Button("Switch to queued topic now")
            run_inject_queue = gr.Button("Queue typed topic")
            r_clear = gr.Button("Clear LLM context")

        with gr.Column(scale=1):
            # Enabling/disabling a speaker is the most common mid-conversation
            # change, so it lives here rather than one per speaker on another
            # tab.
            r_enabled = gr.CheckboxGroup(
                choices=[s.name for s in state.restorable()],
                value=[s.name for s in state.active()],
                label="Who's talking",
                info="Tick to let someone join the conversation. Saves immediately.")

            # What each part of the pipeline is doing, above what it
            # produced. HTML rather than Markdown: it is a row of state chips,
            # and nothing user-written reaches it -- see ui/pipeview.py.
            r_pipe = gr.HTML(value="", elem_classes=["pipe-box"])

            # What the model is emitting, above what it actually said.
            # A Textbox, not Markdown: this is unfiltered output, and a stray
            # backtick or hash in it must render as itself rather than
            # rearranging the page.
            gr.Markdown("**Raw model output**")
            r_raw = gr.Textbox(
                value="", placeholder="(nothing yet)",
                lines=8, max_lines=8, show_label=False,
                interactive=False, container=False, elem_classes=["raw-box"])

            gr.Markdown("**Transcript**")
            r_transcript = gr.Markdown(
                "_(nothing yet)_", height=380, container=True,
                elem_classes=["transcript-box"])

            gr.Markdown("**Say a line as** (works in any mode, jumps the queue)")
            with gr.Row():
                man_speaker = gr.Dropdown(choices=init_names, label="Speaker", scale=1)
                man_text = gr.Textbox(label="Say this", scale=3)
                man_go = gr.Button("Say", scale=1, variant="primary")
            # Speak instead of typing. Transcribed by whisper.cpp on the CPU and
            # said straight away -- there is no review step, so what Whisper
            # heard is what goes out. The event log records every transcript.
            # A recorder that is not a gr.Audio -- see ui/mic.py for why.
            # Nothing here decodes the clip in the browser, so a long take
            # cannot freeze the page, and the input device is chosen
            # explicitly instead of inheriting whatever Chrome defaults to.
            gr.HTML(MIC_HTML)
            # The seam back into gradio. Carries the uploaded file's server
            # path; hidden by CSS rather than visible=False so the textarea is
            # guaranteed to exist in the DOM for the JS to write to.
            man_mic = gr.Textbox(elem_id="qp-mic-path", label=None,
                                 container=False, elem_classes=["qp-hidden"])

    return RunPanel(
        m_mode=m_mode,
        run_topic=run_topic,
        r_enabled=r_enabled,
        run_queue=run_queue,
        run_inject=run_inject,
        run_inject_now=run_inject_now,
        run_inject_queue=run_inject_queue,
        r_pipe=r_pipe,
        r_raw=r_raw,
        r_transcript=r_transcript,
        r_start=r_start,
        r_stop=r_stop,
        r_clear=r_clear,
        r_rotate=r_rotate,
        man_speaker=man_speaker,
        man_text=man_text,
        man_go=man_go,
        man_mic=man_mic,
    )


def build_speakers(app):
    """Roster along the top, editor in two columns underneath.

    The roster was a vertical radio in a narrow left column, which pushed the
    editor into a single tall scroll. Laid flat it costs one row whatever the
    roster size, and the editor fits without scrolling.

    Refresh voices and Export sit on the roster row because they act on the
    whole roster, not on the speaker being edited. Refresh used to live on
    Testing, which is hidden while speech comes from a remote server.
    """
    with gr.Group():
        with gr.Row():
            s_prev = gr.Button("‹", scale=0, min_width=44)
            # The CSS turns this radio's options into a horizontal strip that
            # scrolls sideways; the arrows step the selection so a long roster
            # is still reachable without a trackpad gesture.
            s_roster = gr.Radio(
                choices=roster_choices(app), value=NEW, label=None,
                container=False, scale=20, elem_classes=["roster-bar"])
            s_next = gr.Button("›", scale=0, min_width=44)
            s_refresh = gr.Button("Refresh voices", size="sm", scale=0,
                                  min_width=120, elem_classes=["roster-tool"])
            s_export = gr.Button("Export all (.zip)", size="sm", scale=0,
                                 min_width=120, elem_classes=["roster-tool"])
    # Hidden until there is something in it: an empty file box reads as a
    # drop target, which this is not.
    s_export_file = gr.File(label="Voice export", visible=False,
                            interactive=False)

    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            s_name = gr.Textbox(label="Name")
            s_clip = gr.Audio(label="Reference clip", type="filepath")
            # Audio takes no info=, so this is the one note on the tab.
            gr.Markdown(
                "To trim: click the scissors, drag a range on the waveform, "
                "then press the small Trim button that appears.",
                elem_classes=["qp-note"])
            s_reftext = gr.Textbox(
                label="Reference transcript (optional)", lines=3,
                info=help("Filled in by whisper when you add a clip. Check it "
                          "before saving.",
                          "A wrong word makes the clone worse, and nothing "
                          "else reports it."))
            # Also a button, because the automatic pass only fires on a clip
            # you just added: selecting a speaker off the roster loads their
            # saved clip without re-transcribing it.
            s_transcribe = gr.Button("Transcribe clip", size="sm")
            # Only interesting when speech comes from a server that names its
            # voices differently -- which is why it is a dropdown of what that
            # server actually has, with free text for a name it does not have
            # yet. Empty means "same as the name above".
            s_server_voice = gr.Dropdown(
                choices=voice_choices(app), value=None,
                label="Voice on the server", allow_custom_value=True,
                info=help("Leave empty when the server knows this speaker by "
                          "the name above.",
                          "A remote server has its own names. Pick theirs "
                          "here and the roster keeps ours."))

        with gr.Column(scale=1):
            s_persona = gr.Textbox(
                label="Base system prompt", lines=6,
                placeholder="You are Dave. You are relentlessly upbeat and "
                            "derail every topic into cycling.",
                info=help("Yours. The app never rewrites this.",
                          "It is the anchor every evolution starts from, and "
                          "what Reset returns to."))
            # Display-only. Written by the evolution pass between topics, and
            # deliberately not an input to Save: a rewrite can land between
            # this box rendering and you pressing Save, and taking the value
            # from the form would quietly undo it.
            s_dynamic = gr.Textbox(
                label="Dynamic system prompt", lines=6, interactive=False,
                placeholder="(not evolved yet - the base prompt is in use)",
                info=help("Rewritten between topics from what this character "
                          "actually said.",
                          "While it has anything in it, this is what the "
                          "model is told to be."))
            with gr.Row():
                s_reset_dynamic = gr.Button("Reset dynamic to base", size="sm")
                s_sharpen = gr.Button("Sharpen base for comedy", size="sm")
            s_samples = gr.Textbox(
                label="Lines in their voice", lines=5,
                placeholder="I paid four hundred dollars for that sandwich and "
                            "I would do it again.\nDon't talk to me about Gary.",
                info=help("Things this character would actually say, one per "
                          "line.",
                          "A few are shown to the model each turn as the sound "
                          "of the voice; at this model size that does more "
                          "than any description. Never rewritten by "
                          "evolution. *Sharpen* has the model restate the base "
                          "prompt as a want, a flaw and a wrong belief instead "
                          "of adjectives. It only fills the box, so read it "
                          "before you save."))
            with gr.Accordion("Vocal stims", open=False):
                s_stims = gr.Textbox(
                    label="Phrases", lines=3,
                    placeholder="jellyfishing\nI'm ready\nbarnacles",
                    info=help("Catchphrases this character blurts out. One "
                              "per line.",
                              "Commas work too. One is picked at random when "
                              "the roll succeeds."))
                s_stim_pct = gr.Slider(
                    0, 100, value=0, step=5, label="Stim chance (%)",
                    info=help("Share of this speaker's turns that carry a "
                              "stim. 0 disables.",
                              "The model is asked to work it in; if it "
                              "ignores that, the phrase is appended so a "
                              "successful roll always lands."))
            with gr.Accordion("Build a persona from Discord history", open=False):
                with gr.Row():
                    s_handle = gr.Textbox(
                        label="Discord handle", scale=3,
                        placeholder="username, display name, or user ID",
                        info=help(f"Reads their last {PERSONA_YEARS:g} years "
                                  f"of messages, samples {PERSONA_SAMPLES}, "
                                  "and writes a base prompt from how they "
                                  "talk.",
                                  "The sampled messages are quoted in the "
                                  "prompt too. Overwrites the base prompt "
                                  "above; nothing is saved until you press "
                                  "**Save speaker**."))
                    s_mine = gr.Button("Build persona", scale=1)
            with gr.Row():
                s_save = gr.Button("Save speaker", variant="primary")
                s_delete = gr.Button("Delete", variant="stop", scale=0,
                                     min_width=110)

    return SpeakersPanel(
        s_roster=s_roster,
        s_prev=s_prev,
        s_next=s_next,
        s_refresh=s_refresh,
        s_name=s_name,
        s_clip=s_clip,
        s_reftext=s_reftext,
        s_transcribe=s_transcribe,
        s_server_voice=s_server_voice,
        s_persona=s_persona,
        s_dynamic=s_dynamic,
        s_reset_dynamic=s_reset_dynamic,
        s_sharpen=s_sharpen,
        s_samples=s_samples,
        s_stims=s_stims,
        s_stim_pct=s_stim_pct,
        s_save=s_save,
        s_delete=s_delete,
        s_export=s_export,
        s_export_file=s_export_file,
        s_handle=s_handle,
        s_mine=s_mine,
    )


def build_topic(app):
    """Inputs: where topics come from. Two columns so the source tabs sit
    beside the current topic and rotation rules instead of below them."""
    state = app.state
    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            gr.Markdown(
                "What they talk about. The **Topic** box is live - rotation "
                "overwrites it, so it always shows what is actually being "
                "discussed. Press Enter or click away to save an edit."
            )
            m_topic = gr.Textbox(value=state.settings.topic, label="Topic", lines=2)
            with gr.Row():
                m_topic_now = gr.Button("Switch to this now", variant="primary")
                m_topic_queue = gr.Button("Queue as next topic")
            m_topic_from = gr.Textbox(
                value=state.settings.topic_author, label="Pinned by",
                interactive=False, max_lines=1,
                info="Who posted the pin this topic came from, and where. Blank once "
                     "you edit the topic by hand - there is no sender to credit then.")

            gr.Markdown("**Up next**")
            topic_queue_md = gr.Markdown(
                "_(no queue yet)_", height=200, container=True,
                elem_classes=["queue-box"])

            gr.Markdown("**Rotation**")
            with gr.Row():
                m_rotate = gr.Checkbox(value=state.settings.topic_rotation,
                                       label="Rotate topic automatically")
                m_rot_mins = gr.Slider(1, 60, value=state.settings.topic_interval_minutes,
                                       step=1, label="Every N minutes")
            m_rot_clear = gr.Checkbox(value=state.settings.topic_clears_context,
                                      label="Clear context on switch")
            m_rot_say = gr.Checkbox(value=state.settings.topic_announce,
                                    label="Announce the switch out loud")
            m_rot_instant = gr.Checkbox(
                value=state.settings.topic_switch_instant,
                label="Switch instantly",
                info="Cut the current speaker off mid-sentence instead of "
                     "waiting for the utterance to finish.")

        with gr.Column(scale=1):
            gr.Markdown(
                "### Sources\n"
                "Each switch picks a source at random, weighted by the sliders below. "
                "A source with weight 0, or with nothing in it, is never drawn - so "
                "50/0/0 and 5/0/0 behave identically."
            )
            with gr.Tabs():
                with gr.Tab("Discord (pins)"):
                    w_pins = gr.Slider(
                        0, 100, value=state.settings.source_pins_weight, step=5,
                        label="Weight", info="How often the topic comes from a pinned message.")
                    m_rot_chans = gr.CheckboxGroup(
                        choices=[], value=[], label="Pin channels",
                        info="Every ticked channel feeds one shared pool. Pins are drawn "
                             "from a shuffled bag, so all get used before any repeats. "
                             "Connect the bot to populate this list.")
                    m_images = gr.Checkbox(
                        value=state.settings.topic_images,
                        label="Send pinned images to the model",
                        info="When a pin is an image, the announcer describes it out loud "
                             "and that description becomes everyone else's context - one "
                             "vision call per topic, not one per turn. Needs a multimodal "
                             "model.")
                    with gr.Row():
                        m_seed = gr.Number(
                            value=state.settings.rng_seed, precision=0, label="RNG seed",
                            info="Drives pin order, speaker choice and stim rolls. 0 = "
                                 "fresh seed each start. Applies on reconnect.")
                        m_reseed = gr.Button("Reshuffle now")
                        m_rot_next = gr.Button("Skip to next pin")

                with gr.Tab("Web search"):
                    w_web = gr.Slider(
                        0, 100, value=state.settings.source_web_weight, step=5,
                        label="Weight", info="How often the topic comes from a web search.")
                    web_subjects = gr.Textbox(
                        value=state.settings.web_subjects, lines=6,
                        label="Subjects to search",
                        placeholder="deep sea creatures\nweird food history\nunsolved mysteries",
                        info="One per line. The director works through them in order, and "
                             "through the results of each in random order, so a subject is "
                             "not used up before the next one gets a turn.")
                    web_n = gr.Slider(
                        3, 20, value=state.settings.web_results_per_search, step=1,
                        label="Results per search",
                        info="Fetched once per subject and cached, then handed out one at "
                             "a time.")
                    web_read = gr.Checkbox(
                        value=state.settings.web_read_articles,
                        label="Read the article",
                        info="Opens the result and has the model brief what it actually "
                             "says - that brief is the topic. Off uses the search "
                             "result's title and snippet, which is a headline with "
                             "nothing in it to discuss. Happens while the previous "
                             "topic is still running, so it costs no air time.")
                    gr.Markdown(
                        "_Looks for news stories first (Bing News feed), then falls back "
                        "to DuckDuckGo; no API key for either, and sponsored results are "
                        "dropped. Pages with no real prose - shops, video players, "
                        "paywalls - are skipped. Everything it does is in the app log "
                        "under `[web]`._")

                with gr.Tab("Discord (crowd-sourced)"):
                    w_crowd = gr.Slider(
                        0, 100, value=state.settings.source_crowd_weight, step=5,
                        label="Weight", info="How often the topic comes from a submission.")
                    gr.Markdown(
                        "Anyone in the server runs **`/topics`** in any channel and it "
                        "lands in the queue. Discord confirms it privately, so the channel "
                        "does not fill with acknowledgements. Submissions are used "
                        "oldest-first and are announced with credit to whoever sent them.\n\n"
                        "A real slash command, registered when the bot connects - it "
                        "appears in Discord's own picker with the description and the "
                        "argument prompt. There is also **`/sayas`**, which puts a line "
                        "straight into a chosen host's mouth."
                    )
                    crowd_pending = gr.Markdown(
                        "_(none submitted yet)_", height=200, container=True,
                        elem_classes=["queue-box"])
                    with gr.Row():
                        crowd_clear = gr.Button("Clear submissions")
                        crowd_max = gr.Slider(
                            10, 500, value=state.settings.crowd_max, step=10,
                            label="Keep at most",
                            info="Oldest are dropped past this.")
    return TopicPanel(
        m_topic_from=m_topic_from,
        topic_queue_md=topic_queue_md,
        m_topic=m_topic,
        m_rotate=m_rotate,
        m_rot_mins=m_rot_mins,
        m_rot_clear=m_rot_clear,
        m_rot_say=m_rot_say,
        m_rot_instant=m_rot_instant,
        m_topic_now=m_topic_now,
        m_topic_queue=m_topic_queue,
        w_pins=w_pins,
        m_rot_chans=m_rot_chans,
        m_images=m_images,
        w_web=w_web,
        web_subjects=web_subjects,
        web_n=web_n,
        web_read=web_read,
        w_crowd=w_crowd,
        crowd_pending=crowd_pending,
        m_seed=m_seed,
        m_reseed=m_reseed,
        m_rot_next=m_rot_next,
        crowd_clear=crowd_clear,
        crowd_max=crowd_max,
    )


def build_behaviour(app):
    """Six sections, one open. Language model is what you come here to
    change; the rest are closed accordions in the order a show uses them."""
    state = app.state
    with gr.Accordion("Language model", open=True):
        with gr.Row():
            m_provider = gr.Radio(
                choices=PROVIDERS, value=state.settings.provider,
                label="LLM provider",
                info=help("ollama runs here and shares the GPU with speech; "
                          "openai is remote and costs money.",
                          "Keep an Ollama model small: it competes with "
                          "tts-server for the card. OpenAI adds latency but "
                          "leaves the whole card free for speech."))
            m_refresh_models = gr.Button("Refresh model list")
        with gr.Row():
            m_model = gr.Dropdown(
                choices=app.ollama.models() or [state.settings.ollama_model],
                value=state.settings.ollama_model, label="Ollama model")
            m_oa_model = gr.Dropdown(
                choices=[state.settings.openai_model],
                value=state.settings.openai_model,
                label="OpenAI model", allow_custom_value=True)
        gr.Markdown(
            "Secrets come from `.env` at the repo root; they are never "
            f"entered or stored here. `{app.env_summary()}`",
            elem_classes=["qp-note"])
        with gr.Row():
            m_think = gr.Checkbox(
                value=state.settings.thinking,
                label="Let the model think first",
                info=help("Ollama only. Seconds of extra latency per line.",
                          "Sets `think` on the request; a model with no "
                          "reasoning mode ignores it. Every line gets 8x the "
                          "token budget, because the reasoning has to fit "
                          "inside the same budget as the answer. In a live "
                          "call that latency is dead air. The interrupt "
                          "router never thinks either way."))
            m_pipe = gr.Checkbox(
                value=state.settings.pipeline_view,
                label="Show the stage strip",
                info=help("The live row above the transcript on Run.",
                          "Says what each part is doing and for how long, "
                          "which is the difference between the model being "
                          "slow, tts-server being queued, and the gap simply "
                          "being set long. Timings are collected either way; "
                          "this only draws them."))
            m_raw = gr.Checkbox(
                value=state.settings.raw_feed,
                label="Show raw model output",
                info=help("Streams every generation into the box on Run.",
                          "Reasoning, false starts, the router's JSON, and "
                          "the empty completions behind a turn that never "
                          "happened. Off puts both providers back on a "
                          "single buffered request."))

    with gr.Accordion("Conversation tuning", open=False):
        with gr.Row():
            m_gap = gr.Slider(0, 3, value=state.settings.gap_seconds, step=0.1,
                              label="Gap between turns (s)",
                              info=help(*HELP["gap_seconds"]))
            m_temp = gr.Slider(0.1, 1.8, value=state.settings.temperature, step=0.05,
                               label="LLM temperature",
                               info=help(*HELP["temperature"]))
        with gr.Row():
            m_pred = gr.Slider(20, 300, value=state.settings.num_predict, step=10,
                               label="Max tokens per line",
                               info=help(*HELP["num_predict"]))
            m_hist = gr.Slider(4, 40, value=state.settings.max_history, step=1,
                               label="Context turns",
                               info=help(*HELP["max_history"]))
        m_reset = gr.Button(
            "Reset the four above to recommended "
            f"(gap {RECOMMENDED['gap_seconds']}s, temp {RECOMMENDED['temperature']}, "
            f"{RECOMMENDED['num_predict']} tokens, {RECOMMENDED['max_history']} turns)",
            size="sm")

    with gr.Accordion("Comedy", open=False):
        with gr.Row():
            m_moves = gr.Checkbox(
                value=state.settings.moves_enabled,
                label="Deal a comedic move each turn",
                info=help("One concrete instruction per line, from a "
                          "shuffled deck.",
                          "A small model told to be funny holds a panel "
                          "discussion. These settings make the decisions in "
                          "code and leave the model to carry them out; see "
                          "*Making it funny* in DEADINTERNET.md. A move is "
                          "one sentence in the prompt, so it is free, and "
                          "none repeats until all have been used. Numbers "
                          "for each segment are on Diagnostics."))
            m_move_pct = gr.Slider(
                0, 100, value=state.settings.move_chance, step=5,
                label="Turns that get a move (%)",
                info=help("Not 100.",
                          "A cast that is always doing a bit has nobody left "
                          "to react to it."))
        m_moves_text = gr.Textbox(
            value=state.settings.moves_text, lines=6, label="The deck",
            placeholder=DEFAULT_MOVES,
            info=help("One move per line. Empty uses the default deck shown "
                      "greyed out.",
                      "Write things to DO, never things to BE. Start a line "
                      "with `short:` to hold the reply to a few words, "
                      "`duo:` to skip it when only one speaker is on. A line "
                      "containing {bit} is a callback, dealt only once "
                      "something has been remembered."))
        with gr.Row():
            m_premises = gr.Checkbox(
                value=state.settings.premises_enabled,
                label="Write premises for each topic",
                info=help("Each character gets something petty to want out "
                          "of the topic.",
                          "Chosen to collide with what the others want. One "
                          "call per topic, made while the previous topic is "
                          "still playing; logged when it lands."))
            m_bits = gr.Checkbox(
                value=state.settings.bits_enabled,
                label="Remember things to call back to",
                info=help("The oddest specifics are noted every few lines.",
                          "So a callback move can reach something that "
                          "scrolled out of the context long ago. Survives "
                          "topic switches. One small call, queued behind the "
                          "next line."))
            m_script = gr.Checkbox(
                value=state.settings.script_framing,
                label="Show the model a script, not a chat",
                info=help("The conversation laid out as dialogue to continue.",
                          "Chat turns are what an assistant is trained to be "
                          "helpful inside; the same lines as a script read "
                          "as fiction. Try it per model."))
        with gr.Row():
            m_takes = gr.Slider(
                1, 5, value=state.settings.line_takes, step=1,
                label="Takes per line",
                info=help("Rewrites a weak line up to this many times. 1 "
                          "turns the filter off.",
                          "A take that opens by agreeing, explains itself, "
                          "echoes a recent line or asks yet another question "
                          "is written again; the least bad one plays. A "
                          "clean first take costs nothing extra."))
            m_judge = gr.Checkbox(
                value=state.settings.line_judge,
                label="Have the model pick the take",
                info=help("Always writes every take, then asks which is "
                          "best.",
                          "Better lines for several times the LLM work per "
                          "line. Watch the stage strip before leaving this "
                          "on."))
        with gr.Row():
            m_min_p = gr.Slider(
                0.0, 0.3, value=state.settings.min_p, step=0.01,
                label="min-p (Ollama)",
                info=help("0 leaves Ollama's own top-p and top-k in charge.",
                          "Drops any token less likely than this fraction of "
                          "the most likely one, which is what lets "
                          "temperature go past 1.0 without lines falling "
                          "apart. Try 0.05-0.1 with temperature 1.1-1.3."))
            m_rep_pen = gr.Slider(
                1.0, 1.5, value=state.settings.repeat_penalty, step=0.01,
                label="Repeat penalty (Ollama)",
                info=help("1.0 is off.",
                          "Makes words already in the context less likely. "
                          "Much past 1.2 it starts avoiding words it needs."))

    with gr.Accordion("Between segments", open=False):
        with gr.Row():
            m_evolve = gr.Checkbox(
                value=state.settings.evolve_enabled,
                label="Evolve characters between segments",
                info=help("Rewrites each speaker's dynamic prompt from what "
                          "they actually said.",
                          "Anchored to the base prompt you wrote. One model "
                          "call per character, competing with tts-server "
                          "for the card. Rewriting takes tens of seconds, "
                          "and the ad break below is what makes that "
                          "inaudible instead of dead air."))
            m_evolve_max = gr.Slider(
                1, 12, value=state.settings.evolve_max_per_break, step=1,
                label="Characters rewritten per break",
                info=help("Only speakers who actually spoke are candidates.",
                          "Longest-unevolved first, so a quiet character "
                          "still comes round."))
            m_evolve_wait = gr.Slider(
                10, 300, value=state.settings.evolve_timeout_seconds, step=5,
                label="Seconds to hold the next topic",
                info=help("Past this the show carries on.",
                          "The rewrites land whenever they finish and apply "
                          "on the next turn either way."))
        with gr.Row():
            m_evolve_chars = gr.Slider(
                300, 1200, value=state.settings.evolve_max_chars, step=50,
                label="Evolved prompt size (characters)",
                info=help("Each rewrite rebuilds the prompt inside this "
                          "budget.",
                          "One that is already over gets condensed the next "
                          "time that character is rewritten. Never below the "
                          "length of the base prompt you wrote."))
            m_evolve_think = gr.Checkbox(
                value=state.settings.evolve_think,
                label="Think before rewriting",
                info=help("About a minute per character instead of ten "
                          "seconds.",
                          "Separate from the thinking switch above, which is "
                          "for spoken lines. A model that reasons at length "
                          "often runs out of room and is retried without it "
                          "anyway. Leave off unless you can see it helping."))
        with gr.Row():
            m_adbreak = gr.Checkbox(
                value=state.settings.adbreak_enabled,
                label="Play an ad break",
                info=help("A random speaker reads an invented sponsor spot "
                          "over a music bed.",
                          "About something the segment actually covered."))
            m_ad_gain = gr.Slider(
                0.0, 1.0, value=state.settings.adbreak_music_gain, step=0.02,
                label="Music level under the read")
        m_ad_prompt = gr.Textbox(
            value=state.settings.adbreak_prompt, lines=6,
            label="Ad brief", placeholder=DEFAULT_AD_PROMPT,
            info=help("How the sponsor read is written. Empty uses the "
                      "default shown greyed out.",
                      "The segment's topic and transcript are always "
                      "appended underneath, so a brief can never have "
                      "nothing to go on. Ask for a jingle, a public "
                      "information film, a threat."))
        with gr.Row():
            m_ad_intro = gr.Checkbox(
                value=state.settings.adbreak_intro_enabled,
                label="Hand over to the break out loud",
                info=help("The reader says a line before the spot.",
                          "Spoken while the ad is still being written, so "
                          "the break opens with someone talking instead of "
                          "with however long the model takes."))
            m_ad_intro_tpl = gr.Textbox(
                value=state.settings.adbreak_intro_template, lines=4,
                label="Hand-off lines", scale=2,
                info=help("One per line, picked at random.",
                          "{name} is whoever is reading, {topic} is the "
                          "segment that just ended."))
        with gr.Row():
            m_music_up = gr.File(
                label="Background music", file_count="multiple", scale=1,
                file_types=["audio"])
            with gr.Column(scale=1):
                # A bounded box, not a bullet list: with forty tracks the list
                # was the tallest thing on the tab.
                m_music_list = gr.Markdown(
                    t_behaviour_music(app), height=140, container=True,
                    elem_classes=["music-box"])
                m_music_clear = gr.Button("Remove all tracks", size="sm",
                                          variant="stop", scale=0, min_width=150)

    with gr.Accordion("Connection and interruption", open=False):
        with gr.Row():
            m_overlap = gr.Checkbox(
                value=state.settings.sayas_overlap,
                label="Let /sayas talk over whatever is playing",
                info=help("Spamming the command puts several voices on at "
                          "once.",
                          "Only /sayas. The Say box and the microphone stay "
                          "strictly in order."))
            m_overlap_max = gr.Slider(
                1, 6, value=state.settings.sayas_overlap_max, step=1,
                label="Voices at once",
                info=help("Counting whoever was already talking.",
                          "Past three or so it stops being an argument and "
                          "becomes noise."))
        with gr.Row():
            m_barge = gr.Checkbox(
                value=state.settings.barge_in,
                label="Interrupt the current line when someone types",
                info=help("Only when nothing is already waiting.",
                          "A rapid handful queues behind the first instead "
                          "of cutting off every sentence in a row."))
            m_ack = gr.Checkbox(
                value=state.settings.ack_sound,
                label="Play a cue when a message is taken",
                info=help("A short two-note blip over whoever is talking.",
                          "Silence after typing means the message was "
                          "refused, not that it was missed."))
            m_queue_max = gr.Slider(
                1, 30, value=state.settings.user_queue_max, step=1,
                label="Messages held at once",
                info=help("Past this, new messages are refused and no cue "
                          "plays.",
                          "Everything held is answered, oldest first."))
        with gr.Row():
            m_rejoin = gr.Checkbox(
                value=state.settings.auto_rejoin, label="Rejoin automatically",
                info=help("A watchdog rejoins as soon as somebody returns.",
                          "Discord ends the call when the last person "
                          "leaves, and discord.py never comes back on its "
                          "own."))
            m_pause_empty = gr.Checkbox(
                value=state.settings.pause_when_empty,
                label="Pause while the channel is empty",
                info=help("Stop generating speech nobody can hear.",
                          "After five minutes empty the conversation "
                          "restarts instead of resuming."))

    with gr.Accordion("Spoken lines", open=False):
        with gr.Row():
            m_open_on = gr.Checkbox(
                value=state.settings.opening_enabled,
                label="Speak an opening when the conversation starts",
                info=help("One sentence per line in each box below; one is "
                          "picked at random each time.",
                          "So the same event does not sound identical every "
                          "occurrence. Leave a box empty to say nothing."))
            m_bye_on = gr.Checkbox(value=state.settings.goodbye_enabled,
                                   label="Say goodbye when you press Stop")
        with gr.Row():
            m_open_tpl = gr.Textbox(
                value=state.settings.opening_template, lines=4, label="Opening",
                info=help("Spoken once when you press Start.",
                          tpl_vars("opening_template")))
            m_bye_tpl = gr.Textbox(
                value=state.settings.goodbye_template, lines=4, label="Goodbye",
                info=help("Spoken once after the current line is cut off.",
                          tpl_vars("goodbye_template")))
        with gr.Row():
            m_topic_tpl = gr.Textbox(
                value=state.settings.topic_template, lines=4,
                label="Topic switch announcement",
                info=help("Read out on every topic change.",
                          tpl_vars("topic_template")))
            m_topic_img_tpl = gr.Textbox(
                value=state.settings.topic_image_template, lines=4,
                label="Topic switch - image pins",
                info=help("Added after the announcement when the pin is a "
                          "picture.",
                          "So the channel knows why the subject changed to "
                          "something with no words in it. "
                          + tpl_vars("topic_image_template")))
    return BehaviourPanel(
        m_reset=m_reset,
        m_open_on=m_open_on,
        m_open_tpl=m_open_tpl,
        m_topic_tpl=m_topic_tpl,
        m_topic_img_tpl=m_topic_img_tpl,
        m_bye_on=m_bye_on,
        m_bye_tpl=m_bye_tpl,
        m_provider=m_provider,
        m_think=m_think,
        m_raw=m_raw,
        m_pipe=m_pipe,
        m_refresh_models=m_refresh_models,
        m_model=m_model,
        m_oa_model=m_oa_model,
        m_gap=m_gap,
        m_temp=m_temp,
        m_pred=m_pred,
        m_hist=m_hist,
        m_moves=m_moves,
        m_move_pct=m_move_pct,
        m_moves_text=m_moves_text,
        m_premises=m_premises,
        m_bits=m_bits,
        m_script=m_script,
        m_takes=m_takes,
        m_judge=m_judge,
        m_min_p=m_min_p,
        m_rep_pen=m_rep_pen,
        m_evolve=m_evolve,
        m_evolve_max=m_evolve_max,
        m_evolve_wait=m_evolve_wait,
        m_evolve_chars=m_evolve_chars,
        m_evolve_think=m_evolve_think,
        m_adbreak=m_adbreak,
        m_ad_gain=m_ad_gain,
        m_ad_prompt=m_ad_prompt,
        m_ad_intro=m_ad_intro,
        m_ad_intro_tpl=m_ad_intro_tpl,
        m_music_up=m_music_up,
        m_music_list=m_music_list,
        m_music_clear=m_music_clear,
        m_barge=m_barge,
        m_overlap=m_overlap,
        m_overlap_max=m_overlap_max,
        m_ack=m_ack,
        m_queue_max=m_queue_max,
        m_rejoin=m_rejoin,
        m_pause_empty=m_pause_empty,
    )
