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
import gradio as gr

from .autoscroll import AUTOSCROLL_JS
from .autosave import Autosave
from .binding import bound
from .feed import FeedUpdate
from .feed import poll as _poll
from .feed import stream_status as _stream_status
from .feed import stream_voice_boot as _stream_voice_boot
from .help import HELP_JS
from .mic import MIC_JS
from .rosterscroll import ROSTER_SCROLL_JS
from .panels import (build_behaviour, build_diagnostics, build_generate,
                     build_header, build_outputs, build_run, build_speakers,
                     build_topic)
from .selectors import SelectorUpdates
from .selectors import roster_choices as _roster_choices
from .selectors import selector_updates as _selector_updates
from .selectors import speaker_names as _speaker_names
from .selectors import voice_choices as _voice_choices
from .tabs import behaviour as t_behaviour
from .tabs import diagnostics as t_diagnostics
from .tabs import local as t_local
from .tabs import discord as t_discord
from .tabs import run as t_run
from .tabs import speakers as t_speakers
from .tabs import topic as t_topic
from .tabs import voices as t_voices


class _Bag:
    """Somewhere to hang components so the panel builders can stay separate
    functions while the wiring below still reads as plain names."""


def build(app):
    """app is a DeadInternetApp -- see app.py."""
    state, tts = app.state, app.tts
    u = _Bag()

    # ---- shared helpers -------------------------------------------------
    # Thin adapters over ui/selectors.py, so the ~30 call sites below keep
    # reading as plain no-arg names while the logic lives at module level.
    def voice_choices():
        return _voice_choices(app)

    def speaker_names():
        return _speaker_names(app)

    def roster_choices():
        return _roster_choices(app)

    def selector_updates(voice=None, sel=None, man=None):
        return _selector_updates(app, voice, sel, man)

    # ---- live feed --------------------------------------------------------
    def poll(last_topic=None):
        return _poll(app, last_topic)

    # `yield from`, not `return`: gradio decides whether to stream a handler by
    # calling inspect.isgeneratorfunction on it, and a plain function that
    # returns a generator fails that check -- the client would get one opaque
    # object instead of a live feed.
    def stream_voice_boot():
        yield from _stream_voice_boot(app, selector_updates, voice_choices)

    def stream_status():
        yield from _stream_status(app)

    # ---- panels -------------------------------------------------------------
    init_voices = voice_choices()
    init_names = speaker_names()








    # ---- layout -------------------------------------------------------------
    with gr.Blocks(title="QwenPod") as demo:
        header = build_header(app)
        u.sb_status = header.sb_status
        u.sb_action = header.sb_action
        u.hdr_refresh = header.hdr_refresh

        with gr.Tabs() as main_tabs:
            u.main_tabs = main_tabs
            with gr.Tab("Run", id="run"):
                run = build_run(app, init_names)
                u.m_mode = run.m_mode
                u.run_topic = run.run_topic
                u.r_enabled = run.r_enabled
                u.run_queue = run.run_queue
                u.run_inject = run.run_inject
                u.run_inject_now = run.run_inject_now
                u.run_inject_queue = run.run_inject_queue
                u.r_pipe = run.r_pipe
                u.r_raw = run.r_raw
                u.r_transcript = run.r_transcript
                u.r_start = run.r_start
                u.r_stop = run.r_stop
                u.r_clear = run.r_clear
                u.r_rotate = run.r_rotate
                u.man_speaker = run.man_speaker
                u.man_text = run.man_text
                u.man_go = run.man_go
                u.man_mic = run.man_mic
                u.man_mic_session = run.man_mic_session
            with gr.Tab("Speakers", id="speakers"):
                speakers = build_speakers(app)
                u.s_roster = speakers.s_roster
                u.s_prev = speakers.s_prev
                u.s_next = speakers.s_next
                u.s_refresh = speakers.s_refresh
                u.s_name = speakers.s_name
                u.s_clip = speakers.s_clip
                u.s_reftext = speakers.s_reftext
                u.s_transcribe = speakers.s_transcribe
                u.s_server_voice = speakers.s_server_voice
                u.s_persona = speakers.s_persona
                u.s_dynamic = speakers.s_dynamic
                u.s_reset_dynamic = speakers.s_reset_dynamic
                u.s_sharpen = speakers.s_sharpen
                u.s_samples = speakers.s_samples
                u.s_stims = speakers.s_stims
                u.s_stim_pct = speakers.s_stim_pct
                u.s_save = speakers.s_save
                u.s_delete = speakers.s_delete
                u.s_export = speakers.s_export
                u.s_export_file = speakers.s_export_file
                u.s_handle = speakers.s_handle
                u.s_mine = speakers.s_mine
            with gr.Tab("Inputs", id="inputs"):
                topic = build_topic(app)
                u.m_topic_from = topic.m_topic_from
                u.topic_queue_md = topic.topic_queue_md
                u.m_topic = topic.m_topic
                u.m_rotate = topic.m_rotate
                u.m_rot_mins = topic.m_rot_mins
                u.m_rot_clear = topic.m_rot_clear
                u.m_rot_say = topic.m_rot_say
                u.m_rot_instant = topic.m_rot_instant
                u.m_topic_now = topic.m_topic_now
                u.m_topic_queue = topic.m_topic_queue
                u.w_pins = topic.w_pins
                u.m_rot_chans = topic.m_rot_chans
                u.m_images = topic.m_images
                u.w_web = topic.w_web
                u.web_subjects = topic.web_subjects
                u.web_n = topic.web_n
                u.web_read = topic.web_read
                u.w_crowd = topic.w_crowd
                u.crowd_pending = topic.crowd_pending
                u.m_seed = topic.m_seed
                u.m_reseed = topic.m_reseed
                u.m_rot_next = topic.m_rot_next
                u.crowd_clear = topic.crowd_clear
                u.crowd_max = topic.crowd_max
            with gr.Tab("Outputs", id="outputs"):
                outputs = build_outputs(app)
                u.d_connect = outputs.d_connect
                u.d_channel = outputs.d_channel
                u.d_join = outputs.d_join
                u.d_leave = outputs.d_leave
                u.d_disconnect = outputs.d_disconnect
                u.l_start = outputs.l_start
                u.l_stop = outputs.l_stop
                u.l_sink = outputs.l_sink
                u.l_refresh = outputs.l_refresh
                u.v_create = outputs.v_create
                u.v_remove = outputs.v_remove
                u.v_mon_on = outputs.v_mon_on
                u.v_mon_off = outputs.v_mon_off
                u.v_volume = outputs.v_volume
                u.v_status = outputs.v_status
                u.m_norm = outputs.m_norm
                u.m_dbfs = outputs.m_dbfs
                u.m_cap_on = outputs.m_cap_on
                u.m_cap_sec = outputs.m_cap_sec
                u.t_restart = outputs.t_restart
                u.t_stop = outputs.t_stop
                u.t_device = outputs.t_device
                u.t_where = outputs.t_where
                u.t_url = outputs.t_url
                u.t_use = outputs.t_use
                u.t_local_box = outputs.t_local_box
                u.t_remote_note = outputs.t_remote_note
            with gr.Tab("Behaviour", id="behaviour"):
                behaviour = build_behaviour(app)
                u.m_reset = behaviour.m_reset
                u.m_open_on = behaviour.m_open_on
                u.m_open_tpl = behaviour.m_open_tpl
                u.m_topic_tpl = behaviour.m_topic_tpl
                u.m_topic_img_tpl = behaviour.m_topic_img_tpl
                u.m_bye_on = behaviour.m_bye_on
                u.m_bye_tpl = behaviour.m_bye_tpl
                u.m_provider = behaviour.m_provider
                u.m_think = behaviour.m_think
                u.m_raw = behaviour.m_raw
                u.m_pipe = behaviour.m_pipe
                u.m_refresh_models = behaviour.m_refresh_models
                u.m_model = behaviour.m_model
                u.m_oa_model = behaviour.m_oa_model
                u.m_gap = behaviour.m_gap
                u.m_temp = behaviour.m_temp
                u.m_pred = behaviour.m_pred
                u.m_hist = behaviour.m_hist
                u.m_evolve = behaviour.m_evolve
                u.m_evolve_max = behaviour.m_evolve_max
                u.m_evolve_wait = behaviour.m_evolve_wait
                u.m_evolve_chars = behaviour.m_evolve_chars
                u.m_evolve_think = behaviour.m_evolve_think
                u.m_moves = behaviour.m_moves
                u.m_move_pct = behaviour.m_move_pct
                u.m_moves_text = behaviour.m_moves_text
                u.m_premises = behaviour.m_premises
                u.m_bits = behaviour.m_bits
                u.m_script = behaviour.m_script
                u.m_takes = behaviour.m_takes
                u.m_judge = behaviour.m_judge
                u.m_min_p = behaviour.m_min_p
                u.m_rep_pen = behaviour.m_rep_pen
                u.m_adbreak = behaviour.m_adbreak
                u.m_ad_gain = behaviour.m_ad_gain
                u.m_ad_prompt = behaviour.m_ad_prompt
                u.m_ad_intro = behaviour.m_ad_intro
                u.m_ad_intro_tpl = behaviour.m_ad_intro_tpl
                u.m_music_up = behaviour.m_music_up
                u.m_music_list = behaviour.m_music_list
                u.m_music_clear = behaviour.m_music_clear
                u.m_barge = behaviour.m_barge
                u.m_overlap = behaviour.m_overlap
                u.m_overlap_max = behaviour.m_overlap_max
                u.m_ack = behaviour.m_ack
                u.m_queue_max = behaviour.m_queue_max
                u.m_rejoin = behaviour.m_rejoin
                u.m_pause_empty = behaviour.m_pause_empty
            # Hidden while speech comes from a remote server: everything on
            # it speaks through tts-server on this machine.
            with gr.Tab("Testing", id="testing",
                        visible=t_local.local_visible(app)) as tab_testing:
                u.tab_testing = tab_testing
                gen = build_generate(init_voices)
                u.g_text = gen.g_text
                u.g_instruct = gen.g_instruct
                u.g_go = gen.g_go
                u.g_audio = gen.g_audio
                u.g_status = gen.g_status
                u.g_voice = gen.g_voice
                u.g_temp = gen.g_temp
                u.g_topk = gen.g_topk
                u.g_topp = gen.g_topp
                u.g_rep = gen.g_rep
                u.g_seed = gen.g_seed
                u.g_max = gen.g_max
            with gr.Tab("Diagnostics", id="diagnostics"):
                diag = build_diagnostics(app)
                u.dbg_voice = diag.voice
                u.dbg_chat = diag.chat
                u.dbg_norm = diag.norm
                u.dbg_topic = diag.topic
                u.dbg_comedy = diag.comedy
                u.dbg_services = diag.services
                u.dbg_log = diag.log
                u.dbg_log_clear = diag.log_clear

        # ---- wiring ---------------------------------------------------------
        # Constructed here, not at the top of build(): it needs the action line,
        # which does not exist until panel_header() has run above.
        saver = Autosave(app, u.sb_action)
        bind = saver.bind
        # `after=` callbacks take no arguments, so they need the same app
        # binding the event handlers get.
        _rebuild_note = bound(t_behaviour.rebuild_note, app)

        u.g_go.click(bound(t_voices.do_synth, app),
                     [u.g_text, u.g_voice, u.g_instruct,
                      u.g_temp, u.g_topk, u.g_topp,
                      u.g_rep, u.g_seed, u.g_max],
                     [u.g_audio, u.g_status])

        # Every mutation refreshes all four selectors, so nothing on another
        # tab goes stale until the app restarts.
        # Order here is the contract with SelectorUpdates: same arity but the
        # wrong order raises nothing, it just lands each update in the wrong
        # control. Assert what can be asserted; the field names carry the rest.
        sel_out = [u.g_voice, u.s_roster, u.man_speaker, u.r_enabled]
        assert len(sel_out) == len(SelectorUpdates._fields)
        u.s_refresh.click(bound(t_voices.refresh_voices, app), None, sel_out + [u.sb_action])
        u.d_connect.click(bound(t_discord.connect_bot, app), None, [u.d_channel, u.m_rot_chans, u.sb_action])
        u.d_join.click(bound(t_discord.join_channel, app), u.d_channel, u.sb_action)
        u.d_leave.click(bound(t_discord.leave_channel, app), None, u.sb_action)
        u.d_disconnect.click(bound(t_discord.disconnect_bot, app), None,
                             [u.d_channel, u.sb_action])

        # Local output. Each output refuses to start while the other is live,
        # so these write the action line the same way the Discord buttons do.
        u.l_start.click(bound(t_local.start_local, app), None, u.sb_action)
        u.l_stop.click(bound(t_local.stop_local, app), None, u.sb_action)
        u.l_refresh.click(bound(t_local.refresh_sinks, app), None,
                          [u.l_sink, u.sb_action])
        u.l_sink.change(bound(t_local.set_sink, app), u.l_sink, u.sb_action)
        # Virtual microphone. Each button re-lists the output devices, since
        # making or removing the sink is exactly what changes that list.
        mic_out = [u.l_sink, u.v_status, u.sb_action]
        u.v_create.click(bound(t_local.create_mic, app), None, mic_out)
        u.v_remove.click(bound(t_local.remove_mic, app), None, mic_out)
        u.v_mon_on.click(bound(t_local.monitor_on, app), None, mic_out)
        u.v_mon_off.click(bound(t_local.monitor_off, app), None, mic_out)
        u.v_volume.release(bound(t_local.set_monitor_volume, app), u.v_volume,
                           u.sb_action)
        u.t_restart.click(bound(t_local.restart_tts, app), u.t_device,
                          u.sb_action)
        u.t_stop.click(bound(t_local.stop_tts, app), None, u.sb_action)
        u.t_use.click(bound(t_local.set_tts_server, app), [u.t_where, u.t_url],
                      [u.t_url, u.sb_action, u.t_local_box, u.t_remote_note,
                       u.tab_testing, u.main_tabs])

        # Speakers
        u.s_roster.change(bound(t_speakers.load_speaker, app), u.s_roster,
                          [u.s_name, u.s_clip, u.s_reftext, u.s_persona,
                           u.s_dynamic, u.s_samples, u.s_stims, u.s_stim_pct,
                           u.s_server_voice, u.sb_action])
        u.s_save.click(bound(t_speakers.save_speaker, app),
                       [u.s_name, u.s_clip, u.s_reftext, u.s_persona,
                        u.s_samples, u.s_stims, u.s_stim_pct, u.s_server_voice],
                       sel_out + [u.sb_action])
        u.s_sharpen.click(bound(t_speakers.sharpen_persona, app),
                          [u.s_name, u.s_persona], [u.s_persona, u.sb_action])
        u.s_delete.click(bound(t_speakers.delete_speaker, app), u.s_roster, sel_out + [u.sb_action])
        u.s_export.click(bound(t_speakers.export_voices, app), None,
                         [u.s_export_file, u.sb_action], concurrency_limit=1)
        u.s_reset_dynamic.click(bound(t_speakers.reset_dynamic, app), u.s_roster,
                                [u.s_dynamic, u.sb_action])
        u.s_mine.click(bound(t_speakers.build_persona, app), [u.s_handle, u.s_name],
                       [u.s_name, u.s_persona, u.sb_action])
        # upload and stop_recording, deliberately not change: change also fires
        # when the roster loads a saved clip, which would re-transcribe on
        # every click and overwrite a transcript that was already right.
        for ev in (u.s_clip.upload, u.s_clip.stop_recording, u.s_transcribe.click):
            ev(bound(t_speakers.transcribe_clip, app), u.s_clip,
               [u.s_reftext, u.sb_action], concurrency_limit=1)

        u.s_prev.click(bound(t_speakers.roster_prev, app), u.s_roster, u.s_roster)
        u.s_next.click(bound(t_speakers.roster_next, app), u.s_roster, u.s_roster)

        # Run
        u.r_start.click(bound(t_run.start_run, app), None, u.sb_action)
        u.r_stop.click(bound(t_run.stop_run, app), None, u.sb_action)
        u.r_clear.click(bound(t_run.clear_context, app), None, u.sb_action)
        u.r_rotate.click(bound(t_topic.rotate_now, app), None, u.sb_action)
        u.man_go.click(bound(t_run.manual_say, app), [u.man_speaker, u.man_text], u.sb_action)
        u.r_enabled.change(bound(t_run.set_enabled, app), u.r_enabled, u.sb_action)
        u.man_text.submit(bound(t_run.manual_say, app), [u.man_speaker, u.man_text], u.sb_action)
        # The recorder in ui/mic.py writes the uploaded file's server path into
        # this hidden textbox and fires an input event; that is what gets us
        # back into gradio without a gr.Audio anywhere near the clip.
        #
        # concurrency_limit=1 because the recorder now cuts a continuous take
        # into one clip per sentence: with the app-wide limit of 4, four of them
        # would transcribe in parallel and be spoken in whatever order whisper
        # happened to finish. The recorder also waits for this handler to clear
        # the textbox before releasing the next clip, so this is the second of
        # two locks -- the one that still holds if that handshake times out.
        u.man_mic.change(bound(t_run.say_from_mic, app),
                         [u.man_speaker, u.man_mic],
                         [u.sb_action, u.man_mic],
                         concurrency_limit=1)
        # Record and Stop in the recorder: whisper-server comes up for the
        # session and goes after it has been idle.
        u.man_mic_session.change(bound(t_run.mic_session, app), u.man_mic_session,
                                 u.sb_action)
        u.m_mode.change(bound(t_behaviour.set_mode, app), u.m_mode, u.sb_action)
        # Same two handlers the Inputs tab uses; they take the text as an
        # argument, so a second box on this tab needs nothing else.
        u.run_inject_now.click(bound(t_topic.switch_to_typed, app),
                               u.run_inject, u.sb_action)
        u.run_inject_queue.click(bound(t_topic.queue_typed, app),
                                 u.run_inject, u.sb_action)

        # Diagnostics
        u.dbg_log_clear.click(bound(t_diagnostics.clear_log, app), None,
                              [u.dbg_log, u.sb_action])

        # Topic. The topic box has its own handler because an edit also drops
        # the "pinned by" credit; the rest are plain autosaves.
        u.m_topic.blur(bound(t_topic.set_topic, app), u.m_topic, [u.m_topic_from, u.sb_action])
        u.m_topic.submit(bound(t_topic.set_topic, app), u.m_topic, [u.m_topic_from, u.sb_action])
        u.m_rot_chans.change(bound(t_topic.set_pin_channels, app), u.m_rot_chans, u.sb_action)
        u.m_reseed.click(bound(t_topic.reseed_now, app), None, u.sb_action)
        u.m_rot_next.click(bound(t_topic.rotate_now, app), None, u.sb_action)
        bind(u.w_pins, "source_pins_weight", "pins weight", float, "release")
        bind(u.w_web, "source_web_weight", "web weight", float, "release")
        bind(u.w_crowd, "source_crowd_weight", "crowd weight", float, "release")
        bind(u.web_subjects, "web_subjects", "web subjects", None, "blur")
        bind(u.web_n, "web_results_per_search", "results per search", int, "release")
        bind(u.web_read, "web_read_articles", "reading web articles", bool)
        bind(u.crowd_max, "crowd_max", "crowd queue cap", int, "release")
        u.crowd_clear.click(bound(t_topic.clear_crowd, app), None, [u.sb_action, u.crowd_pending])
        u.m_topic_now.click(bound(t_topic.switch_to_typed, app), u.m_topic, u.sb_action)
        u.m_topic_queue.click(bound(t_topic.queue_typed, app), u.m_topic, u.sb_action)
        bind(u.m_rotate, "topic_rotation", "topic rotation", bool)
        bind(u.m_rot_mins, "topic_interval_minutes", "rotation interval", float, "release")
        bind(u.m_images, "topic_images", "pinned images", bool)
        bind(u.m_rot_clear, "topic_clears_context", "clear context on switch", bool)
        bind(u.m_rot_say, "topic_announce", "announce switches", bool)
        bind(u.m_rot_instant, "topic_switch_instant", "instant switching", bool)
        bind(u.m_seed, "rng_seed", "RNG seed", lambda v: int(v or 0), "blur")

        # Behaviour. No Apply button: everything persists as you change it.
        bind(u.m_provider, "provider", "LLM provider", after=_rebuild_note)
        # after= is load-bearing: the Ollama client is shared and
        # long-lived, and rebuild_llm() is what copies this onto it.
        bind(u.m_think, "thinking", "thinking", bool, after=_rebuild_note)
        # after= for the same reason: rebuild_llm() is what attaches or drops
        # the tap, and the tap is what decides whether the request streams.
        bind(u.m_raw, "raw_feed", "raw output", bool, after=_rebuild_note)
        # No after=: timings are always collected, so this only decides
        # whether the strip is drawn -- nothing needs rebuilding.
        bind(u.m_pipe, "pipeline_view", "stage strip", bool)
        bind(u.m_model, "ollama_model", "Ollama model", after=_rebuild_note)
        bind(u.m_oa_model, "openai_model", "OpenAI model",
             lambda v: (v or "").strip(), after=_rebuild_note)
        bind(u.m_gap, "gap_seconds", "gap between turns", float, "release")
        bind(u.m_temp, "temperature", "LLM temperature", float, "release")
        bind(u.m_pred, "num_predict", "max tokens per line", int, "release")
        bind(u.m_hist, "max_history", "context turns", int, "release")
        bind(u.m_moves, "moves_enabled", "comedic moves", bool)
        bind(u.m_move_pct, "move_chance", "turns that get a move", float,
             "release")
        bind(u.m_moves_text, "moves_text", "the move deck", None, "blur")
        bind(u.m_premises, "premises_enabled", "topic premises", bool)
        bind(u.m_bits, "bits_enabled", "callback memory", bool)
        bind(u.m_script, "script_framing", "script framing", bool)
        bind(u.m_takes, "line_takes", "takes per line", int, "release")
        bind(u.m_judge, "line_judge", "model picks the take", bool)
        # after= for the same reason as thinking: the sampler options live on
        # the shared Ollama client, and rebuild_llm() is what copies them on.
        bind(u.m_min_p, "min_p", "min-p", float, "release", after=_rebuild_note)
        bind(u.m_rep_pen, "repeat_penalty", "repeat penalty", float, "release",
             after=_rebuild_note)
        bind(u.m_norm, "normalize_audio", "loudness normalisation", bool)
        bind(u.m_dbfs, "target_dbfs", "target loudness", float, "release")
        bind(u.m_cap_on, "speech_limit_enabled", "speech cap", bool)
        bind(u.m_cap_sec, "max_speech_seconds", "max seconds per utterance",
             float, "release")
        bind(u.m_evolve, "evolve_enabled", "character evolution", bool)
        bind(u.m_evolve_max, "evolve_max_per_break",
             "characters rewritten per break", int, "release")
        bind(u.m_evolve_wait, "evolve_timeout_seconds",
             "how long to hold the next topic", float, "release")
        bind(u.m_evolve_chars, "evolve_max_chars",
             "evolved prompt size", int, "release")
        bind(u.m_evolve_think, "evolve_think", "thinking during rewrites", bool)
        bind(u.m_adbreak, "adbreak_enabled", "ad break", bool)
        bind(u.m_ad_gain, "adbreak_music_gain", "music level", float, "release")
        bind(u.m_ad_prompt, "adbreak_prompt", "ad brief", None, "blur")
        bind(u.m_ad_intro, "adbreak_intro_enabled", "spoken hand-off", bool)
        bind(u.m_ad_intro_tpl, "adbreak_intro_template", "hand-off lines",
             None, "blur")
        u.m_music_up.upload(bound(t_behaviour.upload_music, app), u.m_music_up,
                            [u.m_music_list, u.sb_action])
        u.m_music_clear.click(bound(t_behaviour.clear_music, app), None,
                              [u.m_music_list, u.sb_action])
        bind(u.m_barge, "barge_in", "interrupt on message", bool)
        bind(u.m_overlap, "sayas_overlap", "/sayas overlap", bool)
        # after= is load-bearing: the cap lives on the runtime, and this is
        # what pushes a change onto a bot that is already connected.
        bind(u.m_overlap_max, "sayas_overlap_max", "voices at once", int,
             "release", after=lambda: app.sync_voice_settings())
        bind(u.m_ack, "ack_sound", "message cue", bool)
        bind(u.m_queue_max, "user_queue_max", "messages held at once",
             int, "release")
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
        u.m_refresh_models.click(bound(t_behaviour.refresh_models, app), u.m_provider,
                                 [u.m_model, u.m_oa_model, u.sb_action])
        u.m_reset.click(bound(t_behaviour.reset_tuning, app), None,
                        [u.m_gap, u.m_temp, u.m_pred, u.m_hist, u.sb_action])

        # Live feed. sb_action is deliberately absent: it is written only by
        # the handlers above, so a confirmation survives longer than 1.5s.
        outs = [u.sb_status, u.r_pipe, u.r_raw, u.r_transcript, u.run_topic, u.run_queue,
                u.topic_queue_md, u.crowd_pending,
                u.dbg_voice, u.dbg_chat, u.dbg_norm, u.dbg_topic, u.dbg_comedy,
                u.m_topic, u.m_topic_from,
                u.dbg_services, u.dbg_log]
        assert len(outs) == len(FeedUpdate._fields)
        assert u.sb_action not in outs, "the feed must never write the action line"
        # Wrapped so gradio sees a zero-argument callable: an explicit refresh
        # always pushes the topic, while the streaming feed passes the last
        # value it sent so it can skip an unchanged one.
        u.hdr_refresh.click(lambda: poll(), None, outs)
        # Restore the channel lists on page load when the bot is already
        # connected, so a reload or an app restart that left the connection
        # up does not present an empty pin-channel checklist.
        demo.load(bound(t_discord.resync_connection, app), None,
                  [u.d_channel, u.m_rot_chans, u.sb_action])
        u.hdr_refresh.click(bound(t_discord.resync_connection, app), None,
                            [u.d_channel, u.m_rot_chans, u.sb_action])
        # show_progress hidden: this stream never ends, so gradio's default
        # would keep every fed box wearing its orange "generating" top edge
        # for the life of the page -- eleven boxes across four tabs, all
        # permanently "busy".
        demo.load(stream_status, None, outs, show_progress="hidden")
        # Voice lists are read from the tts-server registry when the page is
        # built, which at startup is before the server has finished booting.
        demo.load(stream_voice_boot, None, sel_out)
        demo.load(None, None, None, js=AUTOSCROLL_JS)
        demo.load(None, None, None, js=MIC_JS)
        demo.load(None, None, None, js=ROSTER_SCROLL_JS)
        demo.load(None, None, None, js=HELP_JS)

    return demo
