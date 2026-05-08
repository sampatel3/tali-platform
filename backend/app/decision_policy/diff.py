"""Human-readable diff between two ``policy_json`` blobs.

Used by:
  - The Hub's "pending retune review" view (Phase 6) to render
    "what's about to change?" annotations.
  - The retune log notes field, so admins can audit historical
    changes at a glance.

Output shape:

    {
        "send_assessment.thresholds.role_fit_min": {
            "old": 65, "new": 63,
            "cause_summary": "3 manual sends below threshold",
        },
        ...
    }

When a retuner-produced ``RetuneProposal`` is supplied, its
``shifts`` annotations populate ``cause_summary`` automatically.
Otherwise the raw diff is returned without annotations.
"""

from __future__ import annotations

from typing import Any

from .retuner import RetuneProposal


def policy_diff(
    old: dict[str, Any], new: dict[str, Any], *, proposal: RetuneProposal | None = None
) -> dict[str, dict[str, Any]]:
    flat_old = _flatten(old)
    flat_new = _flatten(new)
    annotations: dict[str, str] = {}
    sources: dict[str, list[int]] = {}
    if proposal is not None:
        for shift in proposal.shifts:
            annotations[shift.field_path] = shift.cause_summary
            sources[shift.field_path] = list(shift.contributing_source_ids)

    keys = sorted(set(flat_old) | set(flat_new))
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        if _is_metadata_key(key):
            continue
        old_val = flat_old.get(key, None)
        new_val = flat_new.get(key, None)
        if _values_equal(old_val, new_val):
            continue
        entry: dict[str, Any] = {"old": old_val, "new": new_val}
        if key in annotations:
            entry["cause_summary"] = annotations[key]
        if key in sources:
            entry["contributing_source_ids"] = sources[key]
        out[key] = entry
    return out


def _flatten(d: Any, *, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten(v, prefix=path))
        return out
    if isinstance(d, list):
        # Lists become dotted-index keys so a rule reorder shows up
        # cleanly. For v1 we don't try to detect insertions vs
        # rearrangements — a swap looks like two changes.
        out = {}
        for i, item in enumerate(d):
            out.update(_flatten(item, prefix=f"{prefix}[{i}]"))
        if not d:
            out[prefix] = []
        return out
    return {prefix: d} if prefix else {}


def _is_metadata_key(key: str) -> bool:
    return key.startswith("metadata.")


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


__all__ = ["policy_diff"]
