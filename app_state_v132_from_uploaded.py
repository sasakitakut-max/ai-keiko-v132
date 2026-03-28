from __future__ import annotations

from copy import deepcopy

DEFAULT_APP_STATE = {
    "last_file_id": None,
    "raw_text": None,
    "script": None,
    "confirmed_script": None,
    "idx": 0,
    "is_playing": False,
    "retry_count": 0,
    "last_feedback_html": None,
    "last_spoken_text": "",
    "last_audio_key": None,
    "webrtc_pcm_buffer": b"",
    "webrtc_last_voice_ts": None,
    "webrtc_speech_started": False,
    "webrtc_last_processed_turn": None,
    "webrtc_sample_rate": 48000,
    "webrtc_turn_idx": None,
    "run_results": [],
    "ai_read_played_idx": None,
    "ai_read_autoplay_idx": None,
    "audio_render_nonce": 0,
    "ai_prefetched_key": None,
    "auto_mode_played_idx": None,
    "auto_mode_busy": False,
    "auto_role_candidates": [],
    "role_editor_text": "",
    "first_page_mode": "自動判定（おすすめ）",
    "ocr_structured_text": "",
    "ocr_copy_text": "",
    "ocr_extras": {},
    "ocr_cast_text": "",
    "layout_mode": "自動判定（おすすめ）",
    "source_mode": "自動判定（おすすめ）",
    "role_mode": "自動抽出（おすすめ）",
    "tesseract_cmd": "",
}


def _clone_default(value):
    if isinstance(value, (dict, list, set, bytearray)):
        return deepcopy(value)
    return value


def ensure_app_state(state):
    for key, value in DEFAULT_APP_STATE.items():
        if key not in state:
            state[key] = _clone_default(value)


def reset_webrtc_turn_state(state):
    state["webrtc_pcm_buffer"] = b""
    state["webrtc_last_voice_ts"] = None
    state["webrtc_speech_started"] = False
    state["webrtc_last_processed_turn"] = None


def reset_auto_mode_state(state=None):
    if state is None:
        import streamlit as st
        state = st.session_state
    state["auto_mode_played_idx"] = None
    state["auto_mode_busy"] = False


def clear_auto_state(state):
    state["last_audio_key"] = None
    reset_webrtc_turn_state(state)


def reset_run_state(state):
    state["retry_count"] = 0
    state["last_feedback_html"] = None
    state["last_spoken_text"] = ""
    state["run_results"] = []
    state["last_audio_key"] = None
    state["ai_read_played_idx"] = None
    state["ai_read_autoplay_idx"] = None
    state["audio_render_nonce"] = 0
    state["ai_prefetched_key"] = None
    reset_auto_mode_state(state)
    reset_webrtc_turn_state(state)


def move_next(state, script_length: int):
    if state["idx"] < script_length - 1:
        state["idx"] += 1
    else:
        state["idx"] = script_length
        state["is_playing"] = False
    clear_auto_state(state)


def reset_for_new_file(state, file_id: str):
    state["raw_text"] = None
    state["script"] = None
    state["confirmed_script"] = None
    state["last_file_id"] = file_id
    state["ocr_structured_text"] = ""
    state["ocr_copy_text"] = ""
    state["ocr_extras"] = {}
    state["idx"] = 0
    state["is_playing"] = False
    reset_run_state(state)
