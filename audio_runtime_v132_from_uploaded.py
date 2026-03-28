from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import tempfile
import time
import wave

import numpy as np
import streamlit as st
import streamlit.components.v1 as components

from app_state_v132_from_uploaded import reset_webrtc_turn_state

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except Exception:
    EDGE_TTS_AVAILABLE = False

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except Exception:
    SR_AVAILABLE = False

try:
    from streamlit_webrtc import webrtc_streamer, WebRtcMode
    WEBRTC_AVAILABLE = True
except Exception:
    webrtc_streamer = None
    WebRtcMode = None
    WEBRTC_AVAILABLE = False


@st.cache_data(show_spinner=False)
def synthesize_tts(text, voice="ja-JP-NanamiNeural", rate="+15%"):
    if not EDGE_TTS_AVAILABLE:
        return None
    text = (text or '').strip()
    if not text:
        return None

    async def _speak():
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp_path = tmp.name
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            await communicate.save(tmp_path)
            with open(tmp_path, "rb") as f:
                data = f.read()
            return data
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    try:
        return asyncio.run(_speak())
    except RuntimeError:
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_speak())
            finally:
                loop.close()
        except Exception:
            return None
    except Exception:
        return None


def play_audio_immediately(audio_bytes: bytes):
    if not audio_bytes:
        return
    st.session_state.audio_render_nonce += 1
    nonce = st.session_state.audio_render_nonce
    b64 = base64.b64encode(audio_bytes).decode()
    components.html(
        f"""
        <audio id="audio-{nonce}" autoplay controls style="width:100%;">
            <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
        </audio>
        <script>
        const audio = document.getElementById("audio-{nonce}");
        if (audio) {{
            const p = audio.play();
            if (p !== undefined) {{
                p.catch((err) => {{
                    console.log("audio play failed:", err);
                }});
            }}
        }}
        </script>
        """,
        height=70,
    )


def play_audio_and_click_next(audio_bytes: bytes, trigger_label: str):
    if not audio_bytes:
        return
    st.session_state.audio_render_nonce += 1
    nonce = st.session_state.audio_render_nonce
    b64 = base64.b64encode(audio_bytes).decode()
    safe_label = trigger_label.replace('"', '\"')
    components.html(
        f"""
        <audio id="auto-audio-{nonce}" autoplay controls style="width:100%;">
            <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
        </audio>
        <script>
        const audio = document.getElementById("auto-audio-{nonce}");
        const triggerLabel = "{safe_label}";
        const clickNext = () => {{
            const doc = window.parent.document;
            const buttons = Array.from(doc.querySelectorAll("button"));
            const target = buttons.find(btn => btn.innerText && btn.innerText.trim() === triggerLabel);
            if (target) {{
                target.click();
                return true;
            }}
            return false;
        }};
        if (audio) {{
            const p = audio.play();
            if (p !== undefined) {{
                p.catch((err) => console.log("auto audio play failed:", err));
            }}
            audio.onended = () => {{
                let tries = 0;
                const timer = setInterval(() => {{
                    if (clickNext() || tries > 20) {{
                        clearInterval(timer);
                    }}
                    tries += 1;
                }}, 150);
            }};
        }}
        </script>
        """,
        height=70,
    )


_PAUSE_ONLY_RE = re.compile(r'^[\s・･･･…‥\.。]+$')


def is_pause_only_text(text: str) -> bool:
    text = (text or '').strip()
    if not text:
        return False
    return bool(_PAUSE_ONLY_RE.fullmatch(text))


def estimate_pause_ms(text: str) -> int:
    text = (text or '').strip()
    if not text:
        return 900
    units = sum(1 for ch in text if ch in '・.。')
    units += sum(2 for ch in text if ch in '…‥')
    if units <= 0:
        units = max(1, len(text))
    return max(900, min(1800, 350 * units))


def click_next_after_delay(trigger_label: str, delay_ms: int):
    st.session_state.audio_render_nonce += 1
    nonce = st.session_state.audio_render_nonce
    safe_label = trigger_label.replace('"', '\"')
    delay_ms = max(0, int(delay_ms))
    components.html(
        f"""
        <div id="pause-next-{nonce}" style="height:1px;"></div>
        <script>
        const triggerLabel = "{safe_label}";
        const clickNext = () => {{
            const doc = window.parent.document;
            const buttons = Array.from(doc.querySelectorAll("button"));
            const target = buttons.find(btn => btn.innerText && btn.innerText.trim() === triggerLabel);
            if (target) {{
                target.click();
                return true;
            }}
            return false;
        }};
        setTimeout(() => {{
            let tries = 0;
            const timer = setInterval(() => {{
                if (clickNext() || tries > 20) {{
                    clearInterval(timer);
                }}
                tries += 1;
            }}, 150);
        }}, {delay_ms});
        </script>
        """,
        height=1,
    )


def prefetch_next_tts(script, current_idx: int, voice: str, rate: str):
    next_idx = current_idx + 1
    if next_idx >= len(script):
        return
    next_line = script[next_idx]
    next_text = next_line.get("text", "")
    prefetch_key = f"{next_idx}:{voice}:{rate}:{next_text}"
    if st.session_state.get("ai_prefetched_key") == prefetch_key:
        return
    try:
        synthesize_tts(next_text, voice=voice, rate=rate)
        st.session_state.ai_prefetched_key = prefetch_key
    except Exception:
        pass


def pcm_bytes_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = 48000, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def audio_frame_to_mono_int16(frame):
    arr = frame.to_ndarray()
    arr = np.array(arr)
    if arr.ndim == 2:
        if arr.shape[0] <= 2:
            arr = arr.mean(axis=0)
        else:
            arr = arr.mean(axis=1)
    arr = np.squeeze(arr)
    if arr.dtype != np.int16:
        arr = arr.astype(np.int16)
    return arr


def collect_webrtc_audio(ctx, rms_threshold: int = 350):
    if not WEBRTC_AVAILABLE or ctx is None or not getattr(ctx.state, "playing", False):
        return False
    receiver = getattr(ctx, "audio_receiver", None)
    if receiver is None:
        return False
    try:
        frames = receiver.get_frames(timeout=0.2)
    except Exception:
        return False
    got_any = False
    now = time.time()
    for frame in frames:
        got_any = True
        mono = audio_frame_to_mono_int16(frame)
        if mono.size == 0:
            continue
        st.session_state.webrtc_sample_rate = getattr(frame, "sample_rate", 48000) or 48000
        st.session_state.webrtc_pcm_buffer += mono.tobytes()
        rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float32)))))
        if rms >= rms_threshold:
            st.session_state.webrtc_speech_started = True
            st.session_state.webrtc_last_voice_ts = now
    return got_any


def maybe_finalize_webrtc_recording(expected: str, role: str, practice_mode: str, confirmed_script, user_role: str, apply_judgment_result):
    if not st.session_state.webrtc_speech_started:
        return False
    last_voice = st.session_state.webrtc_last_voice_ts
    if last_voice is None or time.time() - last_voice < 1.0:
        return False
    pcm_bytes = st.session_state.webrtc_pcm_buffer
    if not pcm_bytes:
        return False
    turn_key = f"{st.session_state.idx}:{len(pcm_bytes)}:{st.session_state.webrtc_last_voice_ts}"
    if st.session_state.webrtc_last_processed_turn == turn_key:
        return False
    wav_bytes = pcm_bytes_to_wav_bytes(
        pcm_bytes,
        sample_rate=st.session_state.webrtc_sample_rate or 48000,
        channels=1,
        sample_width=2,
    )
    transcript = transcribe_audio_bytes(wav_bytes)
    apply_judgment_result(expected, transcript, role, practice_mode, confirmed_script, user_role)
    st.session_state.webrtc_last_processed_turn = turn_key
    reset_webrtc_turn_state(st.session_state)
    return True


def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    if not SR_AVAILABLE:
        raise RuntimeError("SpeechRecognition が未インストールです。")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
        f.write(audio_bytes)
        wav_path = f.name
    try:
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio, language="ja-JP")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


__all__ = [
    "EDGE_TTS_AVAILABLE",
    "SR_AVAILABLE",
    "WEBRTC_AVAILABLE",
    "webrtc_streamer",
    "WebRtcMode",
    "synthesize_tts",
    "play_audio_immediately",
    "play_audio_and_click_next",
    "is_pause_only_text",
    "estimate_pause_ms",
    "click_next_after_delay",
    "prefetch_next_tts",
    "collect_webrtc_audio",
    "maybe_finalize_webrtc_recording",
    "transcribe_audio_bytes",
]
