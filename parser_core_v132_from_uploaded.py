from __future__ import annotations

import re
import unicodedata
from typing import Dict, List

from parser_shared_v132_from_uploaded import (
    BUILD_ID,
    VERSION_LABEL,
    DEFAULT_CAST_TEXT,
    OCRConfigError,
    OCRProcessingError,
    _base_process_pdf,
    _extract_route_family,
    _extract_source_family,
    _postprocess_structured_text,
    _rewrite_copy_report,
    parse_structured_text as _parse_structured_text,
)

APP_TITLE = "AI稽古 OCR 分割版 V132"


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _strip_page_number_noise(text: str) -> str:
    text = _normalize_text(text)
    text = re.sub(r"^[\-‐‑‒–—―ー一]+\s*\d+\s*[\-‐‑‒–—―ー一]+$", "", text)
    text = re.sub(r"^\d+$", "", text)
    return text.strip()


def process_pdf(*args, **kwargs):
    structured_text, extras = _base_process_pdf(*args, **kwargs)
    route_family = _extract_route_family(dict(extras or {}))
    source_family = _extract_source_family(dict(extras or {}))
    new_structured_text = _postprocess_structured_text(
        structured_text, route_family=route_family, source_family=source_family
    )
    new_entries = _parse_structured_text(new_structured_text)
    new_structured_count = len(new_entries)

    new_extras: Dict[str, object] = dict(extras or {})
    new_extras["structured_count"] = new_structured_count
    new_extras["route_family"] = route_family
    new_extras["source_family"] = source_family

    if "copy_report_text" in new_extras:
        new_extras["copy_report_text"] = _rewrite_copy_report(
            str(new_extras.get("copy_report_text", "")), new_structured_text, new_structured_count
        )
    if "report_text" in new_extras:
        text = str(new_extras.get("report_text", ""))
        text = re.sub(r"座標OCR（分割版・[^）]+）", f"座標OCR（分割版・{VERSION_LABEL}）", text, count=1)
        new_extras["report_text"] = text
    return new_structured_text, new_extras


def parse_structured_text(structured_text: str):
    return _parse_structured_text(structured_text)


def structured_text_to_script(structured_text: str) -> List[Dict[str, str]]:
    script: List[Dict[str, str]] = []
    for entry in _parse_structured_text(structured_text):
        role = _normalize_text(getattr(entry, "role", "")).strip()
        text = _strip_page_number_noise(_normalize_text(getattr(entry, "text", "")).strip())
        if not role or not text:
            continue
        script.append({"role": role, "text": text})
    return script


def collect_role_candidates(script: List[Dict[str, str]]) -> List[str]:
    roles: List[str] = []
    for item in script:
        role = _normalize_text(str(item.get("role", ""))).replace(" ", "")
        if role and role not in {"不明", "ト書き"} and role not in roles:
            roles.append(role)
    return roles


__all__ = [
    "BUILD_ID", "VERSION_LABEL", "APP_TITLE", "DEFAULT_CAST_TEXT",
    "OCRConfigError", "OCRProcessingError", "process_pdf",
    "parse_structured_text", "structured_text_to_script", "collect_role_candidates",
]
