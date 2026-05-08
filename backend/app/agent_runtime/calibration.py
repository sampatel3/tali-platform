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
    # Realized outcomes for previously-approved decisions. Closes the
    # feedback loop: not "did the recruiter approve" (which only measures
    # whether the recruiter agreed at queue time) but "what actually
    # happened to the candidate after approval." See outcome_learning.py.
    # Each entry: {decision_type, outcome, observed_at (ISO), application_id, decision_id?}
    "outcomes": [],
}

_MAX_OBSERVATIONS = 50
_MAX_RECENT_DECISIONS = 20
_MAX_OVERRIDE_PATTERNS = 10
_MAX_OUTCOMES = 50


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
        elif key == "outcomes" and isinstance(value, list):
            buf = deque(existing.get(key, []), maxlen=_MAX_OUTCOMES)
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

    # Track record: realized outcomes for previously-approved decisions.
    # The agent's job is to advance the right people; this measures what
    # happened *after* recruiters approved the recommendations, not just
    # whether they approved at queue time. See outcome_learning.py.
    outcomes = calibration.get("outcomes") or []
    track_record_line = _render_track_record(outcomes)

    return f"{agreement}\n{score_line}\n{pattern_line}\n{track_record_line}"


def _render_track_record(outcomes: list[dict[str, Any]]) -> str:
    """Render a one-line 'of your last N advances, M reached interview, K
    were hired' summary so the agent can update its priors on whether
    its advance recommendations are predictive."""
    if not outcomes:
        return "track record: no realized outcomes yet"

    advances = [o for o in outcomes if o.get("decision_type") == "advance_to_interview"]
    rejects = [
        o for o in outcomes
        if o.get("decision_type") in ("reject", "skip_assessment_reject")
    ]

    parts: list[str] = []

    if advances:
        n = len(advances)
        interviewed = sum(1 for o in advances if o.get("outcome") == "interviewed")
        hired = sum(1 for o in advances if o.get("outcome") == "hired")
        parts.append(
            f"of last {n} advance recommendation{'s' if n != 1 else ''}, "
            f"{interviewed} reached interview, {hired} hired"
        )

    if rejects:
        n = len(rejects)
        confirmed = sum(1 for o in rejects if o.get("outcome") == "rejected_confirmed")
        parts.append(
            f"of last {n} reject recommendation{'s' if n != 1 else ''}, "
            f"{confirmed} confirmed by recruiter"
        )

    if not parts:
        return "track record: no realized outcomes yet"
    return "track record: " + "; ".join(parts)
