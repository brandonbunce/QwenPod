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

from .components import (BehaviourPanel, DiagnosticsPanel, GeneratePanel,
                         HeaderPanel, OutputsPanel, RunPanel, SpeakersPanel,
                         TopicPanel)
from .feedback import tpl_vars
from .tabs.behaviour import music_report as t_behaviour_music
from .mic import MIC_HTML
from .selectors import NEW, roster_choices
from ..config import HELP, MODES, PERSONA_SAMPLES, PERSONA_YEARS, RECOMMENDED
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

    return DiagnosticsPanel(voice=voice, chat=chat, norm=norm, topic=topic,
                            services=services, log=log, log_clear=log_clear)


def build_outputs(app):
    """Where the audio goes. Discord is the only output today; this is its own
    tab so adding another does not have to squeeze into the header."""
    gr.Markdown(
        "Where the conversation is played. The bot must be connected **and** in "
        "a voice channel before anything is audible."
    )
    with gr.Group():
        gr.Markdown("**Discord**")
        with gr.Row():
            d_connect = gr.Button("Connect bot", variant="primary", scale=1)
            d_channel = gr.Dropdown(choices=[], label="Voice channel", scale=3,
                                    container=True)
            d_join = gr.Button("Join", scale=1)
            d_leave = gr.Button("Leave", scale=1)
    gr.Markdown(app.discord_hint())
    return OutputsPanel(
        d_connect=d_connect,
        d_channel=d_channel,
        d_join=d_join,
        d_leave=d_leave,
    )


def build_generate(init_voices):
    with gr.Row():
        with gr.Column():
            g_text = gr.Textbox(label="Text", lines=4, value="Hello, this is a test.")
            with gr.Row():
                g_voice = gr.Dropdown(
                    choices=init_voices, label="Voice", scale=4,
                    value=init_voices[0] if init_voices else None)
                g_refresh = gr.Button("Refresh voices", scale=1)
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
        g_refresh=g_refresh,
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
    with gr.Row(elem_classes=["masthead"]):
        gr.Markdown("# QwenPod", elem_classes=["brand"])
        sb_status = gr.Markdown("_starting..._", elem_classes=["status-strip"])
        hdr_refresh = gr.Button("Refresh", scale=0, min_width=90)
    sb_action = gr.Markdown(app.banner(), elem_classes=["action-line"])
    return HeaderPanel(
        sb_status=sb_status,
        sb_action=sb_action,
        hdr_refresh=hdr_refresh,
    )


def build_run(app, init_names):
    """Two columns: what the conversation *is* on the left, what it is doing
    and what you can do to it on the right.

    The transport controls span both, because Start/Stop apply to the whole
    thing rather than to either column.
    """
    state = app.state
    with gr.Row():
        r_start = gr.Button("Start", variant="primary", scale=1)
        r_stop = gr.Button("Stop", scale=1)
        r_clear = gr.Button("Clear LLM context", scale=1)
        r_rotate = gr.Button("Switch topic now", scale=1)

    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            m_mode = gr.Radio(
                choices=MODES, value=state.settings.mode, label="Mode",
                info="podcast - bots talk among themselves, chat ignored. "
                     "interactive - someone typing in the server interrupts "
                     "and a router picks who answers. "
                     "manual - nothing automatic; you pick a speaker and a line.")
            gr.Markdown("**Current topic**")
            run_topic = gr.Markdown(
                f"{state.settings.topic}", height=110, container=True,
                elem_classes=["topic-box"])
            # Enabling/disabling a speaker is the most common mid-conversation
            # change, so it lives here rather than one per speaker on another
            # tab.
            r_enabled = gr.CheckboxGroup(
                choices=[s.name for s in state.restorable()],
                value=[s.name for s in state.active()],
                label="Who's talking",
                info="Tick to let someone join the conversation. Saves immediately.")

        with gr.Column(scale=1):
            gr.Markdown("**Up next**")
            run_queue = gr.Markdown(
                "_(no queue yet)_", height=150, container=True,
                elem_classes=["queue-box"])
            # A second way into switch_to_typed/queue_typed, so a topic can be
            # injected without leaving the tab you are watching. Its own box
            # rather than a mirror of the one on Inputs: two components bound
            # to one setting would fight each other on every feed tick.
            run_inject = gr.Textbox(
                label="Inject a topic", lines=2,
                placeholder="something for them to talk about")
            with gr.Row():
                run_inject_now = gr.Button("Switch to this now", scale=1,
                                           variant="primary")
                run_inject_queue = gr.Button("Queue it next", scale=1)
            gr.Markdown("**Transcript**")
            r_transcript = gr.Markdown(
                "_(nothing yet)_", height=300, container=True,
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

    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            s_name = gr.Textbox(label="Name")
            s_clip = gr.Audio(label="Reference clip", type="filepath")
            gr.Markdown(
                "_To trim: click the scissors icon to select a range, drag on the "
                "waveform to highlight it, then click the small **Trim** button that "
                "appears. Dragging alone does nothing - clicking Trim without a "
                "highlighted range also does nothing, silently._")
            s_reftext = gr.Textbox(
                label="Reference transcript (optional)", lines=3,
                info="Filled in by whisper.cpp when you add a clip. Check it "
                     "before saving - a wrong word makes the clone worse and "
                     "nothing else reports it.")
            # Also a button, because the automatic pass only fires on a clip
            # you just added: selecting a speaker off the roster loads their
            # saved clip without re-transcribing it.
            s_transcribe = gr.Button("Transcribe clip", size="sm")

        with gr.Column(scale=1):
            s_persona = gr.Textbox(
                label="Base system prompt", lines=6,
                placeholder="You are Dave. You are relentlessly upbeat and "
                            "derail every topic into cycling.",
                info="Yours. The app never rewrites this - it is the anchor "
                     "every evolution starts from, and what Reset returns to.")
            # Display-only. Written by the evolution pass between topics, and
            # deliberately not an input to Save: a rewrite can land between
            # this box rendering and you pressing Save, and taking the value
            # from the form would quietly undo it.
            s_dynamic = gr.Textbox(
                label="Dynamic system prompt", lines=6, interactive=False,
                placeholder="(not evolved yet - the base prompt is in use)",
                info="Rewritten between topics from what this character "
                     "actually said. While it has anything in it, this is what "
                     "the model is told to be.")
            s_reset_dynamic = gr.Button("Reset dynamic to base", size="sm")
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
                    s_handle = gr.Textbox(
                        label="Discord handle", scale=3,
                        placeholder="username, display name, or user ID")
                    s_mine = gr.Button("Build persona", scale=1)
            s_stims = gr.Textbox(
                label="Vocal stims", lines=3,
                placeholder="jellyfishing\nI'm ready\nbarnacles",
                info="Catchphrases this character blurts out. One per line "
                     "(commas work too); one is picked at random when the roll "
                     "succeeds.")
            s_stim_pct = gr.Slider(
                0, 100, value=0, step=5, label="Stim chance (%)",
                info="Share of this speaker's turns that carry a stim. 0 disables. "
                     "The model is asked to work it in; if it ignores that, the "
                     "phrase is appended so a successful roll always lands.")
            with gr.Row():
                s_save = gr.Button("Save speaker", variant="primary")
                s_delete = gr.Button("Delete")

    return SpeakersPanel(
        s_roster=s_roster,
        s_prev=s_prev,
        s_next=s_next,
        s_name=s_name,
        s_clip=s_clip,
        s_reftext=s_reftext,
        s_transcribe=s_transcribe,
        s_persona=s_persona,
        s_dynamic=s_dynamic,
        s_reset_dynamic=s_reset_dynamic,
        s_stims=s_stims,
        s_stim_pct=s_stim_pct,
        s_save=s_save,
        s_delete=s_delete,
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
                    gr.Markdown(
                        "_Searches DuckDuckGo with no API key. If it ever stops returning "
                        "anything, the layout changed - check the app log for `[web]`._")

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
        w_crowd=w_crowd,
        crowd_pending=crowd_pending,
        m_seed=m_seed,
        m_reseed=m_reseed,
        m_rot_next=m_rot_next,
        crowd_clear=crowd_clear,
        crowd_max=crowd_max,
    )


def build_behaviour(app):
    state = app.state
    gr.Markdown("### Language model")
    with gr.Row():
        m_provider = gr.Radio(choices=PROVIDERS, value=state.settings.provider,
                              label="LLM provider")
        m_refresh_models = gr.Button("Refresh model list")
    gr.Markdown(
        "`ollama` runs locally but shares the GPU with the TTS server - keep the "
        "model small. `openai` is remote: it costs money and adds latency, but "
        "leaves the whole card free for speech.\n\n"
        "Secrets come from `.env` at the repo root (copy `.env.example`); they are "
        f"never entered or stored in this UI. Current state - `{app.env_summary()}`"
    )
    with gr.Row():
        m_model = gr.Dropdown(
            choices=app.ollama.models() or [state.settings.ollama_model],
            value=state.settings.ollama_model, label="Ollama model")
        m_oa_model = gr.Dropdown(
            choices=[state.settings.openai_model], value=state.settings.openai_model,
            label="OpenAI model", allow_custom_value=True)
    m_think = gr.Checkbox(
        value=state.settings.thinking, label="Let the model think first",
        info="Ollama only - it sets `think` on the request, and a model with no "
             "reasoning mode ignores it. Every line gets 8x the token budget, "
             "because the reasoning has to fit inside the same budget as the "
             "answer. Expect seconds of extra latency per line, which in a live "
             "call is dead air. The interrupt router never thinks either way.")

    gr.Markdown("### Conversation tuning")
    with gr.Row():
        m_gap = gr.Slider(0, 3, value=state.settings.gap_seconds, step=0.1,
                          label="Gap between turns (s)", info=HELP["gap_seconds"])
        m_temp = gr.Slider(0.1, 1.5, value=state.settings.temperature, step=0.05,
                           label="LLM temperature", info=HELP["temperature"])
    with gr.Row():
        m_pred = gr.Slider(20, 300, value=state.settings.num_predict, step=10,
                           label="Max tokens per line", info=HELP["num_predict"])
        m_hist = gr.Slider(4, 40, value=state.settings.max_history, step=1,
                           label="Context turns", info=HELP["max_history"])
    m_reset = gr.Button(
        "Reset the four above to recommended "
        f"(gap {RECOMMENDED['gap_seconds']}s, temp {RECOMMENDED['temperature']}, "
        f"{RECOMMENDED['num_predict']} tokens, {RECOMMENDED['max_history']} turns)")

    gr.Markdown("### Audio")
    with gr.Row():
        m_norm = gr.Checkbox(value=state.settings.normalize_audio,
                             label="Normalise output loudness")
        m_dbfs = gr.Slider(-30, -12, value=state.settings.target_dbfs, step=0.5,
                           label="Target loudness (dBFS RMS)")
    with gr.Row():
        m_cap_on = gr.Checkbox(value=state.settings.speech_limit_enabled,
                               label="Hard limit on speech length")
        m_cap_sec = gr.Slider(
            3, 120, value=state.settings.max_speech_seconds, step=1,
            label="Max seconds per utterance",
            info="Ceiling on how long one line can hold the channel. Generation "
                 "is capped in frames so nothing is rendered and then discarded; "
                 "anything still over is cut with a short fade.")

    gr.Markdown(
        "### Between segments\n"
        "What happens in the gap when the topic rotates. These are one feature: "
        "rewriting characters takes tens of seconds, and the ad break is what "
        "makes that inaudible instead of dead air."
    )
    with gr.Row():
        m_evolve = gr.Checkbox(
            value=state.settings.evolve_enabled,
            label="Evolve characters between segments",
            info="Re-reads what each speaker actually said and rewrites their "
                 "dynamic system prompt from it, anchored to the base prompt you "
                 "wrote. Uses thinking mode, so it competes with tts-server for "
                 "the card - see the VRAM note in DEADINTERNET.md.")
        m_evolve_max = gr.Slider(
            1, 12, value=state.settings.evolve_max_per_break, step=1,
            label="Characters rewritten per break",
            info="Only speakers who actually spoke are candidates, "
                 "longest-unevolved first so a quiet character still comes round.")
        m_evolve_wait = gr.Slider(
            10, 300, value=state.settings.evolve_timeout_seconds, step=5,
            label="Seconds to hold the next topic",
            info="Past this the show carries on and the rewrites land whenever "
                 "they finish - they apply on the next turn either way.")
    with gr.Row():
        m_adbreak = gr.Checkbox(
            value=state.settings.adbreak_enabled,
            label="Play an ad break",
            info="A random speaker reads an invented sponsor spot about "
                 "something the segment actually covered, over a music bed.")
        m_ad_gain = gr.Slider(
            0.0, 1.0, value=state.settings.adbreak_music_gain, step=0.02,
            label="Music level under the read")
    m_ad_prompt = gr.Textbox(
        value=state.settings.adbreak_prompt, lines=8,
        label="Ad brief", placeholder=DEFAULT_AD_PROMPT,
        info="How the sponsor read is written. Leave it empty to use the "
             "default shown greyed out above - the segment's topic and "
             "transcript are always appended underneath whatever you put here, "
             "so you cannot accidentally write a brief that has nothing to go "
             "on. Ask for a jingle, a public information film, a threat.")
    with gr.Group():
        gr.Markdown("**Background music** - one track picked at random each break.")
        with gr.Row():
            m_music_up = gr.File(
                label="Add tracks", file_count="multiple", scale=3,
                file_types=["audio"])
            with gr.Column(scale=1):
                m_music_list = gr.Markdown(t_behaviour_music(app))
                m_music_clear = gr.Button("Remove all", size="sm")

    gr.Markdown("### Connection and interruption")
    with gr.Row():
        m_barge = gr.Checkbox(
            value=state.settings.barge_in,
            label="Interrupt the current line when someone types",
            info="Only for a message that arrives with nothing already "
                 "waiting. A rapid handful queues behind it instead of cutting "
                 "off every sentence in a row.")
        m_ack = gr.Checkbox(
            value=state.settings.ack_sound,
            label="Play a cue when a message is taken",
            info="A short two-note blip, mixed over whoever is talking rather "
                 "than interrupting them. Silence after typing means the "
                 "message was refused, not that it was missed.")
        m_queue_max = gr.Slider(
            1, 30, value=state.settings.user_queue_max, step=1,
            label="Messages held at once",
            info="Everything held is answered, oldest first. Past this, new "
                 "messages are refused and no cue plays.")
        m_rejoin = gr.Checkbox(
            value=state.settings.auto_rejoin, label="Rejoin automatically",
            info="Discord ends the call when the last person leaves, and "
                 "discord.py never comes back on its own. A watchdog rejoins as "
                 "soon as somebody returns.")
        m_pause_empty = gr.Checkbox(
            value=state.settings.pause_when_empty,
            label="Pause while the channel is empty",
            info="Stop generating speech nobody can hear. After five minutes "
                 "empty the conversation restarts instead of resuming.")

    gr.Markdown(
        "### Spoken lines\n"
        "One sentence **per line** in each box - a line is picked at random "
        "each time, so the same event doesn't sound identical every occurrence. "
        "Leave a box empty to say nothing.")

    m_open_on = gr.Checkbox(value=state.settings.opening_enabled,
                            label="Speak an opening when the conversation starts")
    m_open_tpl = gr.Textbox(
        value=state.settings.opening_template, lines=4, label="Opening",
        info="Spoken once when you press Start. " + tpl_vars("opening_template"))

    m_topic_tpl = gr.Textbox(
        value=state.settings.topic_template, lines=4,
        label="Topic switch announcement",
        info="Read out on every topic change. " + tpl_vars("topic_template"))
    m_topic_img_tpl = gr.Textbox(
        value=state.settings.topic_image_template, lines=3,
        label="Topic switch - image pins",
        info="Added after the announcement when the pin is a picture, so "
             "the channel knows why the subject changed to something with "
             "no words in it. " + tpl_vars("topic_image_template"))

    m_bye_on = gr.Checkbox(value=state.settings.goodbye_enabled,
                           label="Say goodbye when you press Stop")
    m_bye_tpl = gr.Textbox(
        value=state.settings.goodbye_template, lines=4, label="Goodbye",
        info="Spoken once after the current line is cut off, before the "
             "bots go quiet. " + tpl_vars("goodbye_template"))
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
        m_refresh_models=m_refresh_models,
        m_model=m_model,
        m_oa_model=m_oa_model,
        m_gap=m_gap,
        m_temp=m_temp,
        m_pred=m_pred,
        m_hist=m_hist,
        m_norm=m_norm,
        m_dbfs=m_dbfs,
        m_cap_on=m_cap_on,
        m_cap_sec=m_cap_sec,
        m_evolve=m_evolve,
        m_evolve_max=m_evolve_max,
        m_evolve_wait=m_evolve_wait,
        m_adbreak=m_adbreak,
        m_ad_gain=m_ad_gain,
        m_ad_prompt=m_ad_prompt,
        m_music_up=m_music_up,
        m_music_list=m_music_list,
        m_music_clear=m_music_clear,
        m_barge=m_barge,
        m_ack=m_ack,
        m_queue_max=m_queue_max,
        m_rejoin=m_rejoin,
        m_pause_empty=m_pause_empty,
    )
