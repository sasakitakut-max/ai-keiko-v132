import json
import re
import unicodedata
import asyncio
import tempfile
import os
import base64
import time
import hashlib
import io
import wave
from difflib import SequenceMatcher
import numpy as np
import streamlit as st
import streamlit.components.v1 as components
from parser_core_v132_from_uploaded import (
    BUILD_ID as OCR_BUILD_ID,
    DEFAULT_CAST_TEXT as OCR_DEFAULT_CAST_TEXT,
    OCRConfigError,
    OCRProcessingError,
    process_pdf as process_current_ocr_pdf,
    structured_text_to_script,
    collect_role_candidates,
)
from app_state_v132_from_uploaded import (
    ensure_app_state,
    reset_webrtc_turn_state,
    reset_run_state,
    clear_auto_state,
    reset_auto_mode_state,
    move_next,
    reset_for_new_file,
)
from audio_runtime_v132_from_uploaded import (
    EDGE_TTS_AVAILABLE,
    SR_AVAILABLE,
    WEBRTC_AVAILABLE,
    webrtc_streamer,
    WebRtcMode,
    synthesize_tts,
    play_audio_immediately,
    play_audio_and_click_next,
    is_pause_only_text,
    estimate_pause_ms,
    click_next_after_delay,
    prefetch_next_tts,
    collect_webrtc_audio,
    maybe_finalize_webrtc_recording,
    transcribe_audio_bytes,
)

from practice_runtime_v132_from_uploaded import (
    build_missing_highlight_html,
    append_run_result,
    apply_judgment_result,
)
# ========= 基本設定 =========
st.set_page_config(page_title="AI稽古 V132", layout="wide")
st.title("AI稽古 V132")
st.caption("V132 OCR ＋ 読み合わせモード統合版（開始番号ジャンプ対応版）")
st.markdown(
    """
    <style>
    button[kind="secondary"] p {
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.info("V132では、V129の安定性を維持したまま、各行の通し番号表示と開始番号ジャンプを追加しています。")
# ========= 共通関数 =========
def normalize_ocr_line(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    replace_map = {
        "〜": "~",
        "～": "~",
        "⋯": "…",
        "•": "・",
        "｜": "|",
    }
    for src, dst in replace_map.items():
        text = text.replace(src, dst)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _make_uploaded_like(pdf_bytes: bytes, filename: str = "script.pdf"):
    buf = io.BytesIO(pdf_bytes)
    buf.name = filename
    return buf


def _run_current_ocr(pdf_bytes: bytes, filename: str, *, first_page_mode: str, layout_mode: str, source_mode: str, role_mode: str, cast_text: str, tesseract_cmd: str):
    uploaded_like = _make_uploaded_like(pdf_bytes, filename=filename)
    structured_text, extras = process_current_ocr_pdf(
        uploaded_like,
        first_page_mode=first_page_mode,
        layout_mode=layout_mode,
        cast_text=cast_text,
        tesseract_cmd=tesseract_cmd,
        role_mode=role_mode,
        source_mode=source_mode,
    )
    script = structured_text_to_script(structured_text)
    copy_text = str((extras or {}).get("copy_report_text") or structured_text)
    return structured_text, copy_text, dict(extras or {}), script

def build_numbered_script_text(script):
    lines = []
    for i, line in enumerate(script or [], 1):
        role = str(line.get("role", ""))
        text = str(line.get("text", ""))
        lines.append(f"{i:04d} {role} {text}")
    return "\n".join(lines)


def jump_to_line(state, script_length: int, target_number: int, *, autoplay: bool = False, practice_mode: str = ""):
    if script_length <= 0:
        return
    try:
        target_idx = int(target_number) - 1
    except Exception:
        target_idx = 0
    target_idx = max(0, min(script_length - 1, target_idx))
    state["idx"] = target_idx
    state["is_playing"] = False
    reset_run_state(state)
    if autoplay:
        if practice_mode == "AI全読み確認モード":
            state["ai_read_autoplay_idx"] = target_idx
        elif practice_mode in {"AI全読みモード", "通し稽古モード"}:
            state["is_playing"] = True


def render_line_number_badge(line_no: int, total_lines: int):
    st.markdown(
        f"<div style='display:inline-block; padding:4px 10px; margin:4px 0 14px 0; border-radius:999px; background:#eef2ff; color:#3730a3; font-weight:700; font-size:0.95rem;'>No. {line_no:04d} / {total_lines:04d}</div>",
        unsafe_allow_html=True,
    )

# ========= state =========
ensure_app_state(st.session_state)
# ========= 画面1 =========
st.header("1. 台本を入れる")
pdf_file = st.file_uploader("台本PDFをアップロードしてください", type=["pdf"], key="main_pdf_uploader")
if pdf_file is None:
    st.info("PDFを入れるとOCR→役名抽出→読み合わせまで進められます。")
    st.stop()
pdf_bytes = pdf_file.read()
file_id = f"{pdf_file.name}_{len(pdf_bytes)}"
if st.session_state.last_file_id != file_id:
    reset_for_new_file(st.session_state, file_id)
# ========= 画面2 =========
st.header("2. OCRを実行する")
st.caption("OCRは V132 の8ファイル純化安定版を使い、読み合わせは旧完全版のモードを引き継ぎます。")

col_ocr1, col_ocr2 = st.columns(2)
with col_ocr1:
    first_page_mode = st.selectbox(
        "1ページ目の扱い",
        ["除外する", "含める"],
        index=["除外する", "含める"].index(st.session_state.get("first_page_mode", "除外する") if st.session_state.get("first_page_mode", "除外する") in ["除外する", "含める"] else "除外する"),
        key="v113_first_page_mode",
    )
with col_ocr2:
    layout_mode = st.selectbox(
        "ページレイアウト",
        ["自動判定（おすすめ）", "見開き（左右に分割）", "単ページ"],
        index=["自動判定（おすすめ）", "見開き（左右に分割）", "単ページ"].index(st.session_state.get("layout_mode", "自動判定（おすすめ）")),
        key="v113_layout_mode",
    )
st.session_state.first_page_mode = first_page_mode
st.session_state.layout_mode = layout_mode

source_mode = st.radio(
    "ソース判定",
    ["自動判定（おすすめ）", "文字レイヤーを優先", "常にOCR"],
    horizontal=True,
    index=["自動判定（おすすめ）", "文字レイヤーを優先", "常にOCR"].index(st.session_state.get("source_mode", "自動判定（おすすめ）")),
    key="v113_source_mode",
)
st.session_state.source_mode = source_mode

role_mode = st.radio(
    "役名抽出モード",
    ["自動抽出（おすすめ）", "手入力辞書を使う"],
    horizontal=True,
    index=["自動抽出（おすすめ）", "手入力辞書を使う"].index(st.session_state.get("role_mode", "自動抽出（おすすめ）")),
    key="v113_role_mode",
)
st.session_state.role_mode = role_mode

if role_mode == "自動抽出（おすすめ）":
    cast_default = st.session_state.get("ocr_cast_text", "")
    cast_text = st.text_area(
        "補助用の役名辞書（任意・空欄OK）",
        value=cast_default,
        height=180,
        help="自動抽出で足りない役名だけ、ここに追記できます。",
        key="v113_cast_text_auto",
    )
else:
    cast_default = st.session_state.get("ocr_cast_text", "") or OCR_DEFAULT_CAST_TEXT
    cast_text = st.text_area(
        "1ページ目の役名辞書（名字/名前の対応）",
        value=cast_default,
        height=220,
        key="v113_cast_text_manual",
    )
st.session_state.ocr_cast_text = cast_text

tesseract_cmd = st.text_input(
    "Tesseract実行ファイルの場所（空欄なら自動検出）",
    value=st.session_state.get("tesseract_cmd", ""),
    placeholder=r"例: C:\Program Files\Tesseract-OCR\tesseract.exe",
    key="v113_tesseract_cmd",
)
st.session_state.tesseract_cmd = tesseract_cmd

ocr_key = f"{file_id}|{first_page_mode}|{layout_mode}|{source_mode}|{role_mode}|{cast_text}|{tesseract_cmd}"
c_ocr1, c_ocr2 = st.columns([1, 1])
with c_ocr1:
    run_ocr_clicked = st.button("OCRを実行 / 再実行", type="primary")
with c_ocr2:
    reparse_clicked = st.button("確定役名で台本を再解析する")

need_initial_ocr = not st.session_state.get("ocr_structured_text")
need_ocr = need_initial_ocr or run_ocr_clicked
if need_ocr:
    try:
        with st.spinner("OCR中... 少し待ってください"):
            structured_text, copy_text, extras, script = _run_current_ocr(
                pdf_bytes,
                pdf_file.name,
                first_page_mode=first_page_mode,
                layout_mode=layout_mode,
                source_mode=source_mode,
                role_mode=role_mode,
                cast_text=cast_text,
                tesseract_cmd=tesseract_cmd,
            )
        auto_roles = collect_role_candidates(script)
        st.session_state.raw_text = copy_text
        st.session_state.ocr_structured_text = structured_text
        st.session_state.ocr_copy_text = copy_text
        st.session_state.ocr_extras = extras
        st.session_state.auto_role_candidates = auto_roles
        st.session_state.role_editor_text = ",".join(auto_roles)
        st.session_state.script = script
        st.session_state.confirmed_script = None
        st.session_state.idx = 0
        st.session_state.is_playing = False
        reset_run_state(st.session_state)
    except OCRConfigError as e:
        st.error(str(e))
    except OCRProcessingError as e:
        st.error(str(e))
    except Exception as e:
        st.exception(e)

raw_text = st.session_state.raw_text
if raw_text is None:
    st.warning("OCRを実行してください。")
    st.stop()

ocr_extras = st.session_state.get("ocr_extras", {}) or {}
st.write(f"OCR_BUILD_ID: {OCR_BUILD_ID}")
if ocr_extras.get("elapsed") is not None:
    st.caption(f"処理時間: {float(ocr_extras.get('elapsed')):.2f} 秒 / 構造化行数: {ocr_extras.get('structured_count', 0)}")
if ocr_extras.get("route_family") is not None:
    st.caption(f"route_family: {ocr_extras.get('route_family')} / source_family: {ocr_extras.get('source_family')}")
with st.expander("OCR結果を見る", expanded=False):
    st.text(raw_text)
if st.session_state.get("ocr_copy_text"):
    st.download_button(
        "OCR結果をテキストでダウンロード",
        data=st.session_state.get("ocr_copy_text", "").encode("utf-8"),
        file_name="ocr_result_v132.txt",
        mime="text/plain",
        use_container_width=True,
    )
if ocr_extras.get("dev_log_text"):
    with st.expander("開発ログ（v113）", expanded=False):
        st.code(str(ocr_extras.get("dev_log_text", "")), language="text")

# ========= 画面3 =========
st.header("3. 役名を確認する")
auto_role_candidates = st.session_state.get("auto_role_candidates", [])
if auto_role_candidates:
    st.caption("OCRで拾った役名候補です。不要な名前を消したり、必要な役名を足してから再解析できます。")
else:
    st.caption("役名候補がまだ見つかっていません。必要なら手で追加して再解析してください。")
role_editor_text = st.text_input(
    "役名一覧（カンマ区切り）",
    value=st.session_state.get("role_editor_text", ""),
    key="role_editor_text_input",
)
final_role_candidates = [normalize_ocr_line(r).replace(" ", "") for r in role_editor_text.split(",") if normalize_ocr_line(r)]
st.session_state.role_editor_text = ",".join(final_role_candidates)
if reparse_clicked:
    if not final_role_candidates:
        st.warning("再解析する役名がありません。")
    else:
        try:
            with st.spinner("確定役名で再解析中... 少し待ってください"):
                reparsed_structured_text, reparsed_copy_text, reparsed_extras, reparsed_script = _run_current_ocr(
                    pdf_bytes,
                    pdf_file.name,
                    first_page_mode=first_page_mode,
                    layout_mode=layout_mode,
                    source_mode=source_mode,
                    role_mode="手入力辞書を使う",
                    cast_text="\n".join(final_role_candidates),
                    tesseract_cmd=tesseract_cmd,
                )
            reparsed_roles = collect_role_candidates(reparsed_script)
            st.session_state.raw_text = reparsed_copy_text
            st.session_state.ocr_structured_text = reparsed_structured_text
            st.session_state.ocr_copy_text = reparsed_copy_text
            st.session_state.ocr_extras = reparsed_extras
            st.session_state.auto_role_candidates = reparsed_roles
            st.session_state.role_editor_text = ",".join(reparsed_roles or final_role_candidates)
            st.session_state.script = reparsed_script
            st.session_state.confirmed_script = None
            st.session_state.idx = 0
            st.session_state.is_playing = False
            reset_run_state(st.session_state)
            st.success("確定役名で台本を再解析しました。")
        except OCRConfigError as e:
            st.error(str(e))
        except OCRProcessingError as e:
            st.error(str(e))
        except Exception as e:
            st.exception(e)

script = st.session_state.script
if script is None:
    st.warning("OCR結果を確認してください。")
    st.stop()

# ========= 画面4 =========
st.header("4. 台本を整える")
script_json_text = st.text_area(
    "必要なら role / text を修正してください（JSON形式）",
    value=json.dumps(script, ensure_ascii=False, indent=2),
    height=320,
)
c1, c2 = st.columns(2)
with c1:
    if st.button("この内容で確定する"):
        try:
            edited_script = json.loads(script_json_text)
            st.session_state.confirmed_script = edited_script
            st.session_state.idx = 0
            st.session_state.is_playing = False
            reset_run_state(st.session_state)
            st.success("台本データを確定しました。")
        except Exception as e:
            st.error(f"JSONの形式が正しくありません: {e}")
with c2:
    if st.button("修正を反映して再表示"):
        try:
            edited_script = json.loads(script_json_text)
            st.session_state.script = edited_script
            st.success("修正内容を反映しました。")
        except Exception as e:
            st.error(f"JSONの形式が正しくありません: {e}")
if st.session_state.confirmed_script is None:
    st.info("役名一覧と台本を確認して『この内容で確定する』を押すと読み合わせに進みます。")
    st.stop()
confirmed_script = st.session_state.confirmed_script
total_lines = len(confirmed_script)
current_line_no = min(st.session_state.idx + 1, total_lines) if total_lines else 0
numbered_script_text = build_numbered_script_text(confirmed_script)

# ========= 画面5 =========
st.header("5. 読み合わせ")

roles = sorted(list(set(line["role"] for line in confirmed_script if line["role"] not in ["不明", "ト書き"])))
if not roles:
    st.error("役名が抽出できませんでした。OCR結果・役名一覧・JSON内容を確認してください。")
    st.stop()
user_role = st.selectbox("あなたの役", roles, key="user_role_select")
voice = st.selectbox(
    "相手役の声",
    ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "ja-JP-AoiNeural", "ja-JP-DaichiNeural"],
    index=0,
    key="partner_voice_select",
)
speed_label = st.selectbox(
    "読み上げ速度",
    ["ゆっくり", "標準", "やや速い", "速い", "かなり速い", "超速い", "最速"],
    index=3,
    key="tts_speed_select",
)
speed_map = {
    "ゆっくり": "-20%",
    "標準": "+0%",
    "やや速い": "+15%",
    "速い": "+30%",
    "かなり速い": "+45%",
    "超速い": "+65%",
    "最速": "+85%",
}
tts_rate = speed_map[speed_label]
practice_mode = st.radio(
    "練習モード",
    ["音声1行テスト", "AI全読み確認モード", "AI全読みモード", "反復モード", "やさしいモード", "通し稽古モード"],
    horizontal=True,
    key="practice_mode_radio",
)
if practice_mode == "AI全読み確認モード":
    st.caption("AI全読み確認モードでは、最初だけ再生ボタンを押し、その後は『次のセリフへ』で次の行を自動再生します。")
elif practice_mode == "AI全読みモード":
    st.caption("AI全読みモードでは、STARTで全役を自動再生します。音声終了後に自動で次へ進みます。")
elif practice_mode == "通し稽古モード":
    st.caption("通し稽古モードでは、途中で止めずに最後まで進み、判定は最後にまとめて表示します。")

with st.expander("番号付き台本一覧を見る", expanded=False):
    st.caption("各構造化行に 1 からの通し番号を付けています。開始番号ジャンプの確認にも使えます。")
    st.text_area("番号付き台本", value=numbered_script_text, height=260, disabled=True, key="numbered_script_preview")
    st.download_button(
        "番号付き台本をテキストでダウンロード",
        data=numbered_script_text.encode("utf-8"),
        file_name="numbered_script_v132.txt",
        mime="text/plain",
        use_container_width=True,
    )

jump_col1, jump_col2, jump_col3 = st.columns([1.2, 1, 1])
with jump_col1:
    jump_line_number = st.number_input(
        "開始番号",
        min_value=1,
        max_value=max(total_lines, 1),
        value=min(current_line_no if current_line_no else 1, max(total_lines, 1)),
        step=1,
        key="jump_line_number",
        help="この番号の行へ移動して、そこから再生や練習を始められます。",
    )
with jump_col2:
    if st.button("この番号へ移動", use_container_width=True):
        jump_to_line(st.session_state, total_lines, jump_line_number, autoplay=False, practice_mode=practice_mode)
        st.rerun()
with jump_col3:
    can_autostart = practice_mode in ["AI全読み確認モード", "AI全読みモード", "通し稽古モード"]
    if st.button("この番号から開始", use_container_width=True, disabled=not can_autostart):
        jump_to_line(st.session_state, total_lines, jump_line_number, autoplay=True, practice_mode=practice_mode)
        st.rerun()
st.caption(f"現在位置: No. {current_line_no:04d} / {total_lines:04d}　START は現在位置から始まります。")

if practice_mode == "AI全読み確認モード":
    top1, top2 = st.columns(2)
    with top1:
        if st.button("最初から"):
            st.session_state.idx = 0
            st.session_state.is_playing = False
            st.session_state.ai_read_played_idx = None
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_prefetched_key = None
            clear_auto_state(st.session_state)
            reset_auto_mode_state(st.session_state)
            st.rerun()
    with top2:
        if st.button("戻る"):
            if st.session_state.idx > 0:
                st.session_state.idx -= 1
            st.session_state.is_playing = False
            st.session_state.ai_read_played_idx = None
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_prefetched_key = None
            clear_auto_state(st.session_state)
            reset_auto_mode_state(st.session_state)
            st.session_state.retry_count = 0
            st.session_state.last_feedback_html = None
            st.session_state.last_spoken_text = ""
            st.rerun()
else:
    top1, top2, top3, top4 = st.columns(4)
    with top1:
        if st.button("最初から"):
            st.session_state.idx = 0
            st.session_state.is_playing = False
            st.session_state.ai_read_played_idx = None
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_prefetched_key = None
            clear_auto_state(st.session_state)
            reset_auto_mode_state(st.session_state)
            st.rerun()
    with top2:
        if st.button("戻る"):
            if st.session_state.idx > 0:
                st.session_state.idx -= 1
            st.session_state.is_playing = False
            st.session_state.ai_read_played_idx = None
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_prefetched_key = None
            clear_auto_state(st.session_state)
            reset_auto_mode_state(st.session_state)
            st.session_state.retry_count = 0
            st.session_state.last_feedback_html = None
            st.session_state.last_spoken_text = ""
            st.rerun()
    with top3:
        if st.button("次へ"):
            if st.session_state.idx < len(confirmed_script) - 1:
                st.session_state.idx += 1
            st.session_state.is_playing = False
            st.session_state.ai_read_played_idx = None
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_prefetched_key = None
            clear_auto_state(st.session_state)
            reset_auto_mode_state(st.session_state)
            st.session_state.retry_count = 0
            st.session_state.last_feedback_html = None
            st.session_state.last_spoken_text = ""
            st.rerun()
    with top4:
        label = "■ STOP" if st.session_state.is_playing else "▶ START"
        if st.button(label):
            st.session_state.is_playing = not st.session_state.is_playing
            clear_auto_state(st.session_state)
            reset_auto_mode_state(st.session_state)
            st.rerun()
progress_value = min((st.session_state.idx + 1) / max(total_lines, 1), 1.0)
st.progress(progress_value)
st.caption(f"{min(st.session_state.idx + 1, total_lines)} / {total_lines}")
if st.session_state.idx >= total_lines:
    st.session_state.is_playing = False
    if practice_mode == "通し稽古モード":
        st.header("通し稽古の結果")
        user_results = [r for r in st.session_state.run_results if r["role"] == user_role]
        if not user_results:
            st.info("まだ通し稽古の記録はありません。")
        else:
            needs_review = [r for r in user_results if not r["perfect"]]
            c1, c2 = st.columns(2)
            with c1:
                st.metric("あなたのセリフ数", len(user_results))
            with c2:
                st.metric("要確認", len(needs_review))
            if not needs_review:
                st.success("全部のセリフを言えました。")
            else:
                st.write("言えなかったところは赤です。")
                for i, r in enumerate(user_results, 1):
                    label_result = "OK" if r["perfect"] else "要確認"
                    with st.expander(f"{i:03d}. {r['role']} / {label_result}"):
                        st.write("本来のセリフ")
                        st.markdown(
                            f"<div style='font-size:1.05rem; line-height:1.9; padding:0.6rem 0;'>{r['feedback_html']}</div>",
                            unsafe_allow_html=True,
                        )
                        st.write("あなたが入力したセリフ")
                        st.markdown(
                            f"<div style='font-size:1.05rem; line-height:1.9; background:#fafafa; border:1px solid #eee; border-radius:8px; padding:12px;'>{r['spoken']}</div>",
                            unsafe_allow_html=True,
                        )
    else:
        st.success("読み合わせ終了")
    st.stop()
line = confirmed_script[st.session_state.idx]
role = line["role"]
text = line["text"]
# ========= 音声1行テスト =========
if practice_mode == "音声1行テスト":
    st.subheader("音声1行テスト")
    render_line_number_badge(current_line_no, total_lines)
    st.write(f"役: {role}")
    st.write(f"セリフ: {text}")
    if st.button("この1行を再生", key="single_line_test_play"):
        audio_bytes = synthesize_tts(text, voice=voice, rate=tts_rate)
        if isinstance(audio_bytes, dict) and "error" in audio_bytes:
            st.error(f"TTS接続エラー: {audio_bytes['error']}")
        elif audio_bytes:
            play_audio_immediately(audio_bytes)
        else:
            st.error("音声生成に失敗しました。")
    st.info("このテストでは自動で次に進みません。1行だけ最後まで聞こえるか確認してください。")
    st.stop()
# ========= AI全読み確認モード =========
if practice_mode == "AI全読み確認モード":
    left, right = st.columns([2, 1])
    with left:
        # 自動再生予約があるときは、この行を再生
        if EDGE_TTS_AVAILABLE and st.session_state.ai_read_autoplay_idx == st.session_state.idx:
            audio_bytes = synthesize_tts(text, voice=voice, rate=tts_rate)
            if isinstance(audio_bytes, dict) and "error" in audio_bytes:
                st.error(f"TTS接続エラー: {audio_bytes['error']}")
            elif audio_bytes:
                play_audio_immediately(audio_bytes)
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_read_played_idx = st.session_state.idx
        if EDGE_TTS_AVAILABLE:
            prefetch_next_tts(confirmed_script, st.session_state.idx, voice, tts_rate)
        already_played_this_line = st.session_state.ai_read_played_idx == st.session_state.idx
        action_box = st.container()
        status_box = st.container()
        with action_box:
            if EDGE_TTS_AVAILABLE:
                if not already_played_this_line:
                    # 次へ用 Enter ハンドラを解除
                    components.html(
                        """
                        <script>
                        (function() {
                            const parentWin = window.parent;
                            const doc = parentWin.document;
                            const key = "__ai_read_enter_handler";
                            if (parentWin[key]) {
                                doc.removeEventListener("keydown", parentWin[key], true);
                                parentWin[key] = null;
                            }
                        })();
                        </script>
                        """,
                        height=0,
                    )
                    if st.button(
                        "▶ このセリフを再生",
                        key=f"ai_read_play_{st.session_state.idx}",
                        use_container_width=True,
                    ):
                        st.session_state.ai_read_played_idx = st.session_state.idx
                        st.session_state.ai_read_autoplay_idx = st.session_state.idx
                        st.rerun()
                else:
                    if st.button(
                        "次のセリフへ",
                        key=f"ai_read_next_{st.session_state.idx}",
                        use_container_width=True,
                    ):
                        next_idx = st.session_state.idx + 1
                        st.session_state.ai_read_played_idx = None
                        if next_idx < len(confirmed_script):
                            st.session_state.idx = next_idx
                            st.session_state.ai_read_autoplay_idx = next_idx
                        else:
                            st.session_state.idx = len(confirmed_script)
                            st.session_state.ai_read_autoplay_idx = None
                            st.session_state.is_playing = False
                        st.rerun()
                    # 画面のどこにフォーカスがあっても Enter / Space で次へ進む
                    components.html(
                        """
                        <script>
                        (function() {
                            const parentWin = window.parent;
                            const doc = parentWin.document;
                            const key = "__ai_read_enter_handler";
                            if (parentWin[key]) {
                                doc.removeEventListener("keydown", parentWin[key], true);
                            }
                            parentWin[key] = function(e) {
                                const isEnter = e.key === "Enter";
                                const isSpace = e.key === " " || e.code === "Space" || e.key === "Spacebar";
                                if (!isEnter && !isSpace) return;
                                const tag = (e.target && e.target.tagName ? e.target.tagName : "").toLowerCase();
                                if (tag === "textarea") return;
                                const editable = e.target && (
                                    e.target.isContentEditable ||
                                    tag === "input" ||
                                    tag === "select"
                                );
                                if (editable) return;
                                e.preventDefault();
                                e.stopPropagation();
                                const buttons = Array.from(doc.querySelectorAll("button"));
                                const target = buttons.find(
                                    (btn) => btn.innerText && btn.innerText.trim() === "次のセリフへ"
                                );
                                if (target) {
                                    target.click();
                                }
                            };
                            doc.addEventListener("keydown", parentWin[key], true);
                        })();
                        </script>
                        """,
                        height=0,
                    )
            else:
                components.html(
                    """
                    <script>
                    (function() {
                        const parentWin = window.parent;
                        const doc = parentWin.document;
                        const key = "__ai_read_enter_handler";
                        if (parentWin[key]) {
                            doc.removeEventListener("keydown", parentWin[key], true);
                            parentWin[key] = null;
                        }
                    })();
                    </script>
                    """,
                    height=0,
                )
                if st.button(
                    "次のセリフへ",
                    key=f"ai_read_next_no_tts_{st.session_state.idx}",
                    use_container_width=True,
                ):
                    next_idx = st.session_state.idx + 1
                    st.session_state.ai_read_played_idx = None
                    st.session_state.ai_read_autoplay_idx = None
                    if next_idx < len(confirmed_script):
                        st.session_state.idx = next_idx
                    else:
                        st.session_state.idx = len(confirmed_script)
                    st.rerun()
        with status_box:
            if EDGE_TTS_AVAILABLE:
                if already_played_this_line:
                    st.success("Enterキー または ボタンで次へ進めます。")
                else:
                    st.info("上のボタンで再生します。")
            else:
                st.info("edge-tts が入っていないため読み上げはオフです。")
        render_line_number_badge(current_line_no, total_lines)
        role_color = "#0f172a"
        if role == "ト書き":
            role_color = "#92400e"
        elif role == "不明":
            role_color = "#b91c1c"
        st.markdown(
            f"""
            <div style="padding: 8px 0 16px 0;">
                <div style="font-size: 2rem; font-weight: 700; color: {role_color}; margin: 20px 0 16px 0;">
                    {role}
                </div>
                <div style="font-size: 1.35rem; line-height: 1.9; white-space: pre-wrap; min-height: 180px;">
                    {text}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if role == "ト書き":
            st.info("ト書きです。必要に応じて読み上げて進んでください。")
        elif role == "不明":
            st.warning("役名不明の行です。内容を確認して進んでください。")
        if st.button(
            "最初からやり直す",
            key=f"ai_read_reset_{st.session_state.idx}",
            use_container_width=True,
        ):
            st.session_state.idx = 0
            st.session_state.is_playing = False
            st.session_state.ai_read_played_idx = None
            st.session_state.ai_read_autoplay_idx = None
            st.session_state.ai_prefetched_key = None
            clear_auto_state(st.session_state)
            st.rerun()
    with right:
        st.subheader("進行")
        st.write(f"No. {st.session_state.idx + 1:04d} / {total_lines:04d}")
        st.write("AIが全役を読みます。")
        st.write("最初だけ再生し、その後は Enter かボタンで次の行へ進みます。")
        st.subheader("台本メモ")
        st.write(f"現在の役: {role}")
    st.stop()
# ========= AI全読みモード =========
if practice_mode == "AI全読みモード":
    left, right = st.columns([2, 1])
    with left:
        render_line_number_badge(current_line_no, total_lines)
        render_line_number_badge(current_line_no, total_lines)
        st.markdown(f"## {role}")
        st.markdown(
            f"<div style='font-size:1.35rem; line-height:1.9; min-height:180px;'>{text}</div>",
            unsafe_allow_html=True,
        )
        if role == "ト書き":
            st.info("ト書きも自動で読みます。")
        elif role == "不明":
            st.warning("役名不明の行を自動で読みます。")
        auto_trigger_label = f"AUTO_NEXT_{st.session_state.idx}"
        if st.session_state.is_playing:
            if st.session_state.auto_mode_played_idx != st.session_state.idx:
                if is_pause_only_text(text):
                    pause_ms = estimate_pause_ms(text)
                    click_next_after_delay(auto_trigger_label, pause_ms)
                    st.session_state.auto_mode_played_idx = st.session_state.idx
                    st.session_state.auto_mode_busy = True
                    if EDGE_TTS_AVAILABLE:
                        prefetch_next_tts(confirmed_script, st.session_state.idx, voice, tts_rate)
                    st.info(f"無音の間を {pause_ms/1000:.1f} 秒入れて次へ進みます。")
                else:
                    audio_bytes = synthesize_tts(text, voice=voice, rate=tts_rate)
                    if isinstance(audio_bytes, dict) and "error" in audio_bytes:
                        st.error(f"TTS接続エラー: {audio_bytes['error']}")
                        st.session_state.is_playing = False
                        reset_auto_mode_state(st.session_state)
                    elif audio_bytes:
                        play_audio_and_click_next(audio_bytes, auto_trigger_label)
                        st.session_state.auto_mode_played_idx = st.session_state.idx
                        st.session_state.auto_mode_busy = True
                        if EDGE_TTS_AVAILABLE:
                            prefetch_next_tts(confirmed_script, st.session_state.idx, voice, tts_rate)
                        st.info("自動再生中です。STOPで停止できます。")
                    else:
                        st.error("音声生成に失敗しました。")
                        st.session_state.is_playing = False
                        reset_auto_mode_state(st.session_state)
            else:
                st.info("再生中です。音声終了後に自動で次へ進みます。")
        else:
            st.info("STARTを押すと、全役を自動で読み進めます。")
        if st.button(auto_trigger_label, key=f"hidden_auto_next_{st.session_state.idx}"):
            next_idx = st.session_state.idx + 1
            st.session_state.auto_mode_played_idx = None
            st.session_state.auto_mode_busy = False
            if next_idx < len(confirmed_script):
                st.session_state.idx = next_idx
            else:
                st.session_state.idx = len(confirmed_script)
                st.session_state.is_playing = False
            st.rerun()
    with right:
        st.subheader("進行")
        st.write(f"No. {st.session_state.idx + 1:04d} / {total_lines:04d}")
        st.write("AIが全役を自動で読みます。")
        st.write("音声終了後に自動で次へ進みます。")
        st.caption("※ 実験モードです。ページやブラウザによって挙動差が出ることがあります。")
    st.stop()
# ========= 通し稽古モード =========
if practice_mode == "通し稽古モード":
    left, right = st.columns([2, 1])
    with left:
        st.markdown(f"## {role}")
        st.markdown(
            f"<div style='font-size:1.35rem; line-height:1.9; min-height:180px;'>{text}</div>",
            unsafe_allow_html=True,
        )
        if role == "ト書き":
            st.info("ト書きも自動で読みます。")
        elif role == "不明":
            st.warning("役名不明の行を自動で読みます。")
        if role != user_role:
            auto_trigger_label = f"TSUKESHI_NEXT_{st.session_state.idx}"
            if st.session_state.is_playing:
                if st.session_state.auto_mode_played_idx != st.session_state.idx:
                    if is_pause_only_text(text):
                        pause_ms = estimate_pause_ms(text)
                        click_next_after_delay(auto_trigger_label, pause_ms)
                        st.session_state.auto_mode_played_idx = st.session_state.idx
                        st.session_state.auto_mode_busy = True
                        if EDGE_TTS_AVAILABLE:
                            prefetch_next_tts(confirmed_script, st.session_state.idx, voice, tts_rate)
                        st.info(f"無音の間を {pause_ms/1000:.1f} 秒入れて次へ進みます。")
                    else:
                        audio_bytes = synthesize_tts(text, voice=voice, rate=tts_rate)
                        if isinstance(audio_bytes, dict) and "error" in audio_bytes:
                            st.error(f"TTS接続エラー: {audio_bytes['error']}")
                            st.session_state.is_playing = False
                            reset_auto_mode_state(st.session_state)
                        elif audio_bytes:
                            play_audio_and_click_next(audio_bytes, auto_trigger_label)
                            st.session_state.auto_mode_played_idx = st.session_state.idx
                            st.session_state.auto_mode_busy = True
                            if EDGE_TTS_AVAILABLE:
                                prefetch_next_tts(confirmed_script, st.session_state.idx, voice, tts_rate)
                            st.info("自動再生中です。音声終了後に次へ進みます。")
                        else:
                            st.error("音声生成に失敗しました。")
                            st.session_state.is_playing = False
                            reset_auto_mode_state(st.session_state)
                else:
                    st.info("再生中です。音声終了後に自動で次へ進みます。")
            else:
                st.info("STARTを押すと通し稽古を始めます。")
            if st.button(auto_trigger_label, key=f"hidden_tsukeshi_next_{st.session_state.idx}"):
                next_idx = st.session_state.idx + 1
                st.session_state.auto_mode_played_idx = None
                st.session_state.auto_mode_busy = False
                if next_idx < len(confirmed_script):
                    st.session_state.idx = next_idx
                else:
                    st.session_state.idx = len(confirmed_script)
                    st.session_state.is_playing = False
                st.rerun()
        else:
            st.warning("あなたの番です。セリフを入力して進みます。")
            spoken_text = st.text_area(
                "あなたのセリフを入力",
                key=f"tsukeshi_text_{st.session_state.idx}",
                height=140,
                placeholder="ここに言ったセリフを入力してください",
            )
            if st.session_state.is_playing:
                st.info("入力したら『この内容で次へ』を押してください。判定は最後にまとめて表示します。")
            else:
                st.caption("STARTを押すと通し稽古を再開できます。")
            if st.button("この内容で次へ", key=f"tsukeshi_next_{st.session_state.idx}", use_container_width=True):
                append_run_result(role, text, spoken_text or "")
                next_idx = st.session_state.idx + 1
                st.session_state.auto_mode_played_idx = None
                st.session_state.auto_mode_busy = False
                if next_idx < len(confirmed_script):
                    st.session_state.idx = next_idx
                else:
                    st.session_state.idx = len(confirmed_script)
                    st.session_state.is_playing = False
                st.rerun()
    with right:
        st.subheader("進行")
        st.write(f"No. {st.session_state.idx + 1:04d} / {total_lines:04d}")
        st.write("相手役は自動で読みます。")
        st.write("あなたの役だけ入力して進みます。")
        st.write("判定は最後にまとめて表示します。")
        st.subheader("あなたのセリフ一覧")
        my_lines = [l["text"] for l in confirmed_script if l["role"] == user_role]
        for i, t in enumerate(my_lines, 1):
            st.write(f"{i:03d}. {t}")
    st.stop()
# ========= 通常モード系 =========
left, right = st.columns([2, 1])
with left:
    render_line_number_badge(current_line_no, total_lines)
    st.write(f"### {role}")
    st.write(text)
    if st.session_state.retry_count > 0:
        st.info(f"リピート中：{st.session_state.retry_count}回目")
    if role == user_role:
        st.warning("あなたの番です")
        if st.session_state.last_feedback_html:
            st.write("前回の判定（言えなかったところは赤）")
            st.markdown(
                f"<div style='font-size:1.05rem; line-height:1.9; padding:0.6rem 0;'>{st.session_state.last_feedback_html}</div>",
                unsafe_allow_html=True,
            )
            if st.session_state.last_spoken_text:
                st.write("前回話したセリフ")
                st.markdown(
                    f"<div style='font-size:1.05rem; line-height:1.9; background:#fafafa; border:1px solid #eee; border-radius:8px; padding:12px;'>{st.session_state.last_spoken_text}</div>",
                    unsafe_allow_html=True,
                )
        spoken_text = st.text_area("文字で確認したい場合はこちら", key=f"spoken_text_{st.session_state.idx}")
        if spoken_text:
            st.write("本来のセリフ")
            feedback_html = build_missing_highlight_html(text, spoken_text)
            st.markdown(
                f"<div style='font-size:1.15rem; line-height:1.9; padding:0.6rem 0;'>{feedback_html}</div>",
                unsafe_allow_html=True,
            )
            st.write("話したセリフ")
            st.markdown(
                f"<div style='font-size:1.15rem; line-height:1.9; background:#fafafa; border:1px solid #eee; border-radius:8px; padding:12px;'>{spoken_text}</div>",
                unsafe_allow_html=True,
            )
            if st.button("この内容で判定して進行", key=f"judge_text_{st.session_state.idx}"):
                apply_judgment_result(text, spoken_text, role, practice_mode, confirmed_script, user_role)
                st.rerun()
        if WEBRTC_AVAILABLE:
            st.caption("最初に一度だけマイク接続を開始してください。以後はあなたの番で自動録音します。")
            webrtc_ctx = webrtc_streamer(
                key="ai-keiko-webrtc",
                mode=WebRtcMode.SENDONLY,
                media_stream_constraints={"video": False, "audio": True},
                rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
                async_processing=True,
            )
            if st.session_state.is_playing:
                if st.session_state.get("webrtc_turn_idx") != st.session_state.idx:
                    reset_webrtc_turn_state(st.session_state)
                    st.session_state.webrtc_turn_idx = st.session_state.idx
                if webrtc_ctx and getattr(webrtc_ctx.state, "playing", False):
                    collect_webrtc_audio(webrtc_ctx)
                    try:
                        if maybe_finalize_webrtc_recording(text, role, practice_mode, confirmed_script, user_role, apply_judgment_result):
                            st.rerun()
                    except Exception as e:
                        st.error(f"音声認識エラー: {e}")
                    if st.session_state.webrtc_speech_started:
                        st.info("録音中です。1秒の無音で自動停止して判定します。")
                    else:
                        st.info("あなたの番です。話し始めると自動録音します。")
                    time.sleep(0.2)
                    st.rerun()
                else:
                    st.warning("マイク未接続です。上の START ボタンでマイク接続を開始してください。")
            else:
                st.caption("停止中です。")
        else:
            st.warning("自動録音には streamlit-webrtc のインストールが必要です。未導入時は従来の手動録音を使います。")
            audio_value = st.audio_input("声で録音して判定（対応ブラウザのみ）", key=f"audio_{st.session_state.idx}")
            if audio_value is not None:
                st.audio(audio_value)
                audio_bytes = audio_value.getvalue()
                audio_hash = hashlib.md5(audio_bytes).hexdigest()
                audio_key = f"{st.session_state.idx}:{audio_hash}:{len(audio_bytes)}"
                if st.session_state.last_audio_key != audio_key:
                    try:
                        with st.spinner("音声を認識して判定中..."):
                            transcript = transcribe_audio_bytes(audio_bytes)
                        apply_judgment_result(text, transcript, role, practice_mode, confirmed_script, user_role)
                        st.session_state.last_audio_key = audio_key
                        st.rerun()
                    except Exception as e:
                        st.error(f"音声認識エラー: {e}")
                else:
                    st.caption("この録音は判定済みです。もう一度録音すると自動で再判定します。")
            if st.session_state.is_playing:
                st.info("あなたの番です。録音が終わると自動で判定して次へ進みます。")
    else:
        st.success("相手役")
        if EDGE_TTS_AVAILABLE:
            if st.button("▶ 相手役を読む", key=f"tts_{st.session_state.idx}"):
                audio_bytes = synthesize_tts(text, voice=voice, rate=tts_rate)
                if audio_bytes:
                    play_audio_immediately(audio_bytes)
                else:
                    st.error("音声生成に失敗しました。")
            if st.session_state.is_playing:
                if st.button("相手役を読んだので次へ", key=f"next_after_tts_{st.session_state.idx}"):
                    move_next(st.session_state, len(confirmed_script))
                    st.rerun()
        else:
            st.info("edge-tts が入っていないため読み上げはオフです。")
with right:
    st.subheader("あなたのセリフ一覧")
    my_lines = [l["text"] for l in confirmed_script if l["role"] == user_role]
    for i, t in enumerate(my_lines, 1):
        st.write(f"{i:03d}. {t}")
