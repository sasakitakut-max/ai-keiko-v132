from __future__ import annotations
from typing import Sequence, List

from parser_shared_v132_from_uploaded import (
    Entry,
    _join_broken_plain_lines,
    _merge_suspicious_role_switches,
    _rescue_multi_role_stage_entries,
    _split_embedded_role_switches,
    _rescue_dialogue_continuations,
    _merge_consecutive_same_role,
    _split_stage_sentences,
    _reject_dialogue_entries_starting_with_comma,
    _remove_page_number_entries,
    _maybe_revert_direct_result,
)

def postprocess_direct_like(entries: Sequence[Entry], *, source_family: str) -> List[Entry]:
    base_entries = list(entries)
    entries = _join_broken_plain_lines(entries, route_family="direct")
    entries = _merge_suspicious_role_switches(entries, route_family="direct")
    if source_family == "mixed":
        # mixed は direct 優先だが、途中で stage→dialogue rescue が入る v114 挙動を維持
        from parser_shared_v132_from_uploaded import _rescue_dialogue_from_stage
        entries = _rescue_dialogue_from_stage(entries)
    entries = _rescue_multi_role_stage_entries(entries, route_family="direct")
    entries = _split_embedded_role_switches(entries, block_comma_boundary=True, prefer_space_boundary=True)
    entries = _rescue_dialogue_continuations(entries, source_family=source_family)
    entries = _merge_consecutive_same_role(entries, joiner="")
    entries = _split_stage_sentences(entries)
    entries = _reject_dialogue_entries_starting_with_comma(entries, source_family=source_family)
    entries = _remove_page_number_entries(entries)
    entries = list(_maybe_revert_direct_result(base_entries, entries, source_family=source_family))
    return list(entries)
