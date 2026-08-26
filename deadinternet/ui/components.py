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
    services: Any
    log: Any
    log_clear: Any


@dataclass(frozen=True)
class OutputsPanel:
    """The Discord connection row, moved out of the header into its own tab."""
    d_connect: Any
    d_channel: Any
    d_join: Any
    d_leave: Any


@dataclass(frozen=True)
class GeneratePanel:
    g_text: Any
    g_instruct: Any
    g_go: Any
    g_audio: Any
    g_status: Any
    g_voice: Any
    g_refresh: Any
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


@dataclass(frozen=True)
class SpeakersPanel:
    s_roster: Any
    s_prev: Any
    s_next: Any
    s_name: Any
    s_clip: Any
    s_reftext: Any
    s_transcribe: Any
    s_persona: Any
    s_dynamic: Any
    s_reset_dynamic: Any
    s_stims: Any
    s_stim_pct: Any
    s_save: Any
    s_delete: Any
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
    m_refresh_models: Any
    m_model: Any
    m_oa_model: Any
    m_gap: Any
    m_temp: Any
    m_pred: Any
    m_hist: Any
    m_norm: Any
    m_dbfs: Any
    m_cap_on: Any
    m_cap_sec: Any
    m_evolve: Any
    m_evolve_max: Any
    m_evolve_wait: Any
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
