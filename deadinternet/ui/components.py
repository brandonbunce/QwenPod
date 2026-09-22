"""Typed handles on the components each panel builds.

These replace a bare namespace object that panel builders populated by
attribute assignment and the wiring block read back by name -- a contract
nothing declared and nothing checked, where a typo on either side surfaced a
thousand lines away as an AttributeError.

Every field is required. A panel that forgets a component is a TypeError at
construction, naming the field it missed, before the page is ever served.
frozen=True because a component handle is set once when the panel is built and
read for the rest of the page's life; rebinding one always means a mistake.

The prefixes the old namespace used (g_, c_, s_, m_...) are gone -- the
dataclass does that namespacing now, so a field is `ui.topic.rotate` rather
than `u.m_rot`.
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiagnosticsPanel:
    voice: Any
    chat: Any
    norm: Any
    topic: Any
    comedy: Any
    services: Any
    log: Any
    log_clear: Any


@dataclass(frozen=True)
class OutputsPanel:
    """Where the audio goes. Two independent outputs, one at a time."""
    d_connect: Any
    d_channel: Any
    d_join: Any
    d_leave: Any
    d_disconnect: Any
    l_start: Any
    l_stop: Any
    l_sink: Any
    l_refresh: Any
    v_create: Any
    v_remove: Any
    v_mon_on: Any
    v_mon_off: Any
    v_volume: Any
    v_status: Any
    m_norm: Any
    m_dbfs: Any
    m_cap_on: Any
    m_cap_sec: Any
    t_restart: Any
    t_stop: Any
    t_device: Any
    t_where: Any
    t_url: Any
    t_use: Any
    t_local_box: Any
    t_remote_note: Any


@dataclass(frozen=True)
class GeneratePanel:
    g_text: Any
    g_instruct: Any
    g_go: Any
    g_audio: Any
    g_status: Any
    g_voice: Any
    g_temp: Any
    g_topk: Any
    g_topp: Any
    g_rep: Any
    g_seed: Any
    g_max: Any


@dataclass(frozen=True)
class HeaderPanel:
    sb_status: Any
    sb_action: Any
    hdr_refresh: Any


@dataclass(frozen=True)
class RunPanel:
    m_mode: Any
    run_topic: Any
    r_enabled: Any
    run_queue: Any
    run_inject: Any
    run_inject_now: Any
    run_inject_queue: Any
    r_pipe: Any
    r_raw: Any
    r_transcript: Any
    r_start: Any
    r_stop: Any
    r_clear: Any
    r_rotate: Any
    man_speaker: Any
    man_text: Any
    man_go: Any
    man_mic: Any
    man_mic_session: Any


@dataclass(frozen=True)
class SpeakersPanel:
    s_roster: Any
    s_prev: Any
    s_next: Any
    s_refresh: Any
    s_name: Any
    s_clip: Any
    s_reftext: Any
    s_transcribe: Any
    s_server_voice: Any
    s_persona: Any
    s_dynamic: Any
    s_reset_dynamic: Any
    s_sharpen: Any
    s_samples: Any
    s_stims: Any
    s_stim_pct: Any
    s_save: Any
    s_delete: Any
    s_export: Any
    s_export_file: Any
    s_handle: Any
    s_mine: Any


@dataclass(frozen=True)
class TopicPanel:
    m_topic_from: Any
    topic_queue_md: Any
    m_topic: Any
    m_rotate: Any
    m_rot_mins: Any
    m_rot_clear: Any
    m_rot_say: Any
    m_rot_instant: Any
    m_topic_now: Any
    m_topic_queue: Any
    w_pins: Any
    m_rot_chans: Any
    m_images: Any
    w_web: Any
    web_subjects: Any
    web_n: Any
    web_read: Any
    w_crowd: Any
    crowd_pending: Any
    m_seed: Any
    m_reseed: Any
    m_rot_next: Any
    crowd_clear: Any
    crowd_max: Any


@dataclass(frozen=True)
class BehaviourPanel:
    m_reset: Any
    m_open_on: Any
    m_open_tpl: Any
    m_topic_tpl: Any
    m_topic_img_tpl: Any
    m_bye_on: Any
    m_bye_tpl: Any
    m_provider: Any
    m_think: Any
    m_raw: Any
    m_pipe: Any
    m_refresh_models: Any
    m_model: Any
    m_oa_model: Any
    m_gap: Any
    m_temp: Any
    m_pred: Any
    m_hist: Any
    m_evolve: Any
    m_evolve_max: Any
    m_evolve_wait: Any
    m_evolve_chars: Any
    m_evolve_think: Any
    m_moves: Any
    m_move_pct: Any
    m_moves_text: Any
    m_premises: Any
    m_bits: Any
    m_script: Any
    m_takes: Any
    m_judge: Any
    m_min_p: Any
    m_rep_pen: Any
    m_adbreak: Any
    m_ad_gain: Any
    m_ad_prompt: Any
    m_ad_intro: Any
    m_ad_intro_tpl: Any
    m_music_up: Any
    m_music_list: Any
    m_music_clear: Any
    m_barge: Any
    m_overlap: Any
    m_overlap_max: Any
    m_ack: Any
    m_queue_max: Any
    m_rejoin: Any
    m_pause_empty: Any
