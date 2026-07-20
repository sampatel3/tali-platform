"""Pure helpers for comparing cached Workable stage identifiers.

Workable surfaces the same stage as an id, slug, or display name depending on
the API boundary. Keep that normalization independent of writeback services so
Decision Hub and the generic ATS runner can share it without importing each
other.
"""

from __future__ import annotations

from typing import Any


def workable_stage_aliases(role: Any, value: str | None) -> set[str]:
    """Return cached id/slug/name aliases for one Workable stage value."""
    token = str(value or "").strip().casefold()
    if not token:
        return set()
    aliases = {token}
    stages = getattr(role, "workable_stages", None)
    for stage in stages if isinstance(stages, list) else []:
        if not isinstance(stage, dict):
            continue
        stage_aliases = {
            str(stage.get(key) or "").strip().casefold()
            for key in ("id", "slug", "name")
        }
        stage_aliases.discard("")
        if token in stage_aliases:
            aliases.update(stage_aliases)
    return aliases


def same_workable_stage(role: Any, left: str | None, right: str | None) -> bool:
    """Match exact values or one unambiguous cached id/slug/name record."""
    left_token = str(left or "").strip().casefold()
    right_token = str(right or "").strip().casefold()
    if not left_token or not right_token:
        return False
    if left_token == right_token:
        return True
    left_matches: list[int] = []
    right_matches: list[int] = []
    stages = getattr(role, "workable_stages", None)
    for index, stage in enumerate(stages if isinstance(stages, list) else []):
        if not isinstance(stage, dict):
            continue
        aliases = {
            str(stage.get(key) or "").strip().casefold()
            for key in ("id", "slug", "name")
        }
        aliases.discard("")
        if left_token in aliases:
            left_matches.append(index)
        if right_token in aliases:
            right_matches.append(index)
    return (
        len(left_matches) == 1
        and len(right_matches) == 1
        and left_matches[0] == right_matches[0]
    )
