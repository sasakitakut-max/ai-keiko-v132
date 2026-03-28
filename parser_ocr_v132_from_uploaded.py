from __future__ import annotations
from typing import Sequence, List

from parser_shared_v132_from_uploaded import (
    Entry,
    _rescue_stage_entries,
    _split_inline_role_switches,
    _join_broken_plain_lines,
    _collapse_stage_runs,
    _rescue_dialogue_from_stage,
    _split_embedded_role_switches,
    _split_stage_sentences,
    _merge_consecutive_same_role,
    _remove_page_number_entries,
)

def postprocess_ocr(entries: Sequence[Entry], *, route_family: str) -> List[Entry]:
    entries = _rescue_stage_entries(entries)
    entries = _split_inline_role_switches(entries, route_family=route_family)
    entries = _join_broken_plain_lines(entries, route_family=route_family)
    entries = _collapse_stage_runs(entries, route_family=route_family)
    entries = _rescue_dialogue_from_stage(entries)
    entries = _split_embedded_role_switches(entries)
    entries = _split_stage_sentences(entries)
    entries = _merge_consecutive_same_role(entries)
    entries = _remove_page_number_entries(entries)
    return list(entries)
