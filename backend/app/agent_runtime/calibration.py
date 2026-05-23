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
    # Agent-authored breadcrumbs for the next cycle. Each entry:
    # {note, kind, recorded_at (ISO), agent_run_id}. Written via the
    # record_observation tool; rendered in the system prompt so the
    # agent picks up where it left off instead of re-deriving context.
    "notes": [],
    # Summary of the most recent cycle (whether it completed cleanly or
    # aborted). Written by the orchestrator at every terminal path. Read
    # next cycle so the agent knows what just happened.
    "last_cycle": {},
}

_MAX_OBSERVATIONS = 50
_MAX_RECENT_DECISIONS = 20
_MAX_OVERRIDE_PATTERNS = 10
_MAX_OUTCOMES = 50
_MAX_NOTES = 10


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
        elif key == "notes" and isinstance(value, list):
            buf = deque(existing.get(key, []), maxlen=_MAX_NOTES)
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

    last_cycle_line = _render_last_cycle(calibration.get("last_cycle") or {})
    notes_block = _render_notes(calibration.get("notes") or [])

    parts = [agreement, score_line, pattern_line, track_record_line, last_cycle_line]
    if notes_block:
        parts.append(notes_block)
    return "\n".join(parts)


def _render_last_cycle(last_cycle: dict[str, Any]) -> str:
    """One line describing the previous cycle's outcome so the agent knows
    whether to resume vs. start fresh."""
    if not last_cycle:
        return "last cycle: none on record"
    status = last_cycle.get("status") or "unknown"
    rounds = last_cycle.get("rounds_used")
    decisions = last_cycle.get("decisions_emitted", 0)
    finished_via_complete = last_cycle.get("finished_via_complete")
    bits = [f"status={status}"]
    if rounds is not None:
        bits.append(f"rounds={rounds}")
    bits.append(f"decisions={decisions}")
    if finished_via_complete is False:
        bits.append("did NOT call agent_run_complete")
    err = last_cycle.get("error")
    if err:
        bits.append(f"error={err[:80]}")
    return "last cycle: " + ", ".join(bits)


def _render_notes(notes: Any) -> str:
    """Render agent-authored breadcrumbs from prior cycles. Capped to 10
    entries already; rendered most-recent first so the freshest context
    is at the top of the section.

    ``notes`` is persisted JSON whose shape has drifted across versions:
    it may arrive as a non-list scalar (a bare string was seen in prod on
    role 31, which iterated char-by-char and crashed run_cycle on
    ``str.get``), or as a list containing bare-string entries instead of
    ``{note, kind, recorded_at}`` dicts. Coerce defensively — a malformed
    breadcrumb must never break the agent cycle."""
    if not isinstance(notes, list) or not notes:
        return ""
    lines = ["NOTES FROM PRIOR CYCLES (most recent first):"]
    for entry in reversed(notes[-10:]):
        if isinstance(entry, dict):
            text = str(entry.get("note") or "").strip()
            kind = str(entry.get("kind") or "context")
            recorded_at = str(entry.get("recorded_at") or "")[:10]  # YYYY-MM-DD
        elif isinstance(entry, str):
            text = entry.strip()
            kind = "context"
            recorded_at = ""
        else:
            continue
        if not text:
            continue
        lines.append(f"- [{kind} @ {recorded_at}] {text}")
    return "\n".join(lines) if len(lines) > 1 else ""


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
        # outcome_learning appends SEPARATE "interviewed" and "hired" entries
        # for the same advance (same decision_id), so a single advance would
        # otherwise be counted twice. ``n`` is the number of *distinct*
        # advances; entries without a decision_id (legacy rows) count
        # individually. The per-outcome tallies stay label-scoped, which is
        # already double-count-free since "interviewed" and "hired" are
        # distinct labels.
        distinct_ids = {o.get("decision_id") for o in advances if o.get("decision_id") is not None}
        loose = sum(1 for o in advances if o.get("decision_id") is None)
        n = len(distinct_ids) + loose
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
