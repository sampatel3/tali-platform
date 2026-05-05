"""Persistent cross-cycle agent state, stored on ``role.agent_calibration``.

The agent rebuilds its conversation from scratch each cycle. Anything it
should "remember" between cycles — score distributions, recruiter
override patterns, recent decisions — lives in this JSON blob and is
rendered into the system prompt.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role


_DEFAULT: dict[str, Any] = {
    "decisions_total": 0,
    "decisions_approved": 0,
    "decisions_overridden": 0,
    "score_observations": [],   # rolling list of last N CV-match scores seen
    "recent_decisions": [],     # rolling list of last N decisions (type, reasoning_summary, status)
    "override_patterns": [],    # surfaced patterns from recruiter overrides
}

_MAX_OBSERVATIONS = 50
_MAX_RECENT_DECISIONS = 20
_MAX_OVERRIDE_PATTERNS = 10


def load(role: Role) -> dict[str, Any]:
    raw = role.agent_calibration or {}
    merged: dict[str, Any] = dict(_DEFAULT)
    merged.update(raw)
    return merged


def save(db: Session, *, role: Role, updates: dict[str, Any]) -> None:
    """Merge updates into role.agent_calibration with bounded growth.

    Numeric counters are summed; list-typed entries get appended and
    capped (FIFO) so the blob stays small enough to fit comfortably in
    one cached system-prompt section.
    """
    if not updates:
        return
    existing = load(role)
    for key, value in updates.items():
        if key in {"decisions_total", "decisions_approved", "decisions_overridden"}:
            existing[key] = int(existing.get(key, 0)) + int(value)
        elif key == "score_observations" and isinstance(value, list):
            buf = deque(existing.get(key, []), maxlen=_MAX_OBSERVATIONS)
            buf.extend(value)
            existing[key] = list(buf)
        elif key == "recent_decisions" and isinstance(value, list):
            buf = deque(existing.get(key, []), maxlen=_MAX_RECENT_DECISIONS)
            buf.extend(value)
            existing[key] = list(buf)
        elif key == "override_patterns" and isinstance(value, list):
            buf = deque(existing.get(key, []), maxlen=_MAX_OVERRIDE_PATTERNS)
            buf.extend(value)
            existing[key] = list(buf)
        else:
            existing[key] = value
    role.agent_calibration = existing
    db.add(role)


def render_summary(calibration: dict[str, Any]) -> str:
    """Format the calibration blob as a few lines for the system prompt."""
    total = int(calibration.get("decisions_total", 0))
    approved = int(calibration.get("decisions_approved", 0))
    overridden = int(calibration.get("decisions_overridden", 0))
    if total > 0:
        approval_rate = (approved / total) * 100
        agreement = f"{approval_rate:.0f}% recruiter agreement ({approved}/{total} decisions)"
    else:
        agreement = "no decisions yet — calibrate cautiously"

    obs = calibration.get("score_observations") or []
    if obs:
        sample = obs[-min(len(obs), 20):]
        avg = sum(float(x) for x in sample) / len(sample)
        score_line = f"recent CV-match scores avg {avg:.1f} over last {len(sample)}"
    else:
        score_line = "no score observations yet"

    patterns = calibration.get("override_patterns") or []
    pattern_line = (
        "recruiter overrides noted: " + "; ".join(str(p) for p in patterns[-3:])
        if patterns
        else "no recruiter overrides on record"
    )

    return f"{agreement}\n{score_line}\n{pattern_line}"
