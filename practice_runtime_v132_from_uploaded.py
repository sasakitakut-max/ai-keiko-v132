from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import streamlit as st

from app_state_v132_from_uploaded import clear_auto_state, move_next


def normalize_for_score(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("…", "").replace("⋯", "")
    text = re.sub(r"[、。,.!！?？「」『』（）()\s　]", "", text)
    return text.strip()


def tokenize_japanese_for_diff(text: str):
    text = unicodedata.normalize("NFKC", text)
    return re.findall(r"[一-龯々ぁ-んァ-ヶーa-zA-Z0-9]+|.", text)


def build_missing_highlight_html(expected: str, spoken: str) -> str:
    exp_tokens = tokenize_japanese_for_diff(expected)
    spk_tokens = tokenize_japanese_for_diff(spoken)
    sm = SequenceMatcher(None, exp_tokens, spk_tokens)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        seg = "".join(exp_tokens[i1:i2])
        if tag == "equal":
            out.append(seg)
        elif tag == "delete":
            out.append(f"<span style='color:#d32f2f;font-weight:700;'>{seg}</span>")
        elif tag == "replace":
            out.append(f"<span style='color:#d32f2f;font-weight:700;'>{seg}</span>")
        elif tag == "insert":
            pass
    return "".join(out)


def is_perfect_match(expected: str, spoken: str) -> bool:
    return normalize_for_score(expected) == normalize_for_score(spoken)


def is_soft_match(expected: str, spoken: str) -> bool:
    expected_n = normalize_for_score(expected)
    spoken_n = normalize_for_score(spoken)
    if expected_n == spoken_n:
        return True
    ratio = SequenceMatcher(None, expected_n, spoken_n).ratio()
    return ratio >= 0.82


def append_run_result(role: str, expected: str, spoken: str):
    st.session_state.run_results.append(
        {
            "role": role,
            "expected": expected,
            "spoken": spoken,
            "feedback_html": build_missing_highlight_html(expected, spoken),
            "perfect": is_perfect_match(expected, spoken),
            "soft": is_soft_match(expected, spoken),
        }
    )


def find_retry_index(script, current_idx, user_role):
    i = current_idx - 1
    while i >= 0:
        if script[i]["role"] != user_role:
            return i
        i -= 1
    return current_idx


def apply_judgment_result(expected: str, spoken: str, role: str, practice_mode: str, confirmed_script, user_role: str):
    feedback_html = build_missing_highlight_html(expected, spoken)
    st.session_state.last_feedback_html = feedback_html
    st.session_state.last_spoken_text = spoken
    append_run_result(role, expected, spoken)
    perfect_ok = is_perfect_match(expected, spoken)
    soft_ok = is_soft_match(expected, spoken)
    if practice_mode == "反復モード":
        if perfect_ok:
            st.session_state.retry_count = 0
            move_next(st.session_state, len(confirmed_script))
        else:
            st.session_state.retry_count += 1
            retry_idx = find_retry_index(confirmed_script, st.session_state.idx, user_role)
            st.session_state.idx = retry_idx
            clear_auto_state(st.session_state)
    elif practice_mode == "やさしいモード":
        if soft_ok:
            st.session_state.retry_count = 0
            move_next(st.session_state, len(confirmed_script))
        else:
            st.session_state.retry_count += 1
            retry_idx = find_retry_index(confirmed_script, st.session_state.idx, user_role)
            st.session_state.idx = retry_idx
            clear_auto_state(st.session_state)
    elif practice_mode == "通し稽古モード":
        st.session_state.retry_count = 0
        move_next(st.session_state, len(confirmed_script))
    st.session_state.is_playing = True
