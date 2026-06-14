"""Proactive role-health review for the agent chat.

A free, read-only scan that surfaces the issues most likely to be *hurting a
role's decisions* — so the conversational agent can lead with a steer
("this requirement is removing 94% of your pool — soften it?") instead of
waiting to be asked. Pure reads + deterministic thresholds: no LLM, no
mutation. The agent narrates the single top finding as a question and the
recruiter decides (advise, never act — see feedback_agent_warns_not_blocks).

Findings, by family:

* **calibration** — the recruiter keeps overriding the agent in one
  direction, so the cut-off / a requirement is mis-tuned. The strongest
  signal, and it's already recorded on ``AgentDecision`` (status
  ``overridden`` / ``human_disposition`` ``overridden``/``taught``); the chat
  was simply blind to it.
* **requirements** — a must-have almost nobody meets (quietly killing the
  pool), one that can't be evaluated from the CVs (filtering on missing
  data), or one everyone meets (no filtering signal / redundant).
* **threshold** — a cut-off set so strict almost nobody passes, or so loose
  it rubber-stamps everyone.
* **data** — stale (old-engine) scores the decisions still rest on, or a
  decision backlog waiting on the recruiter.

Every finding carries the structured handles (``criterion_id``, counts) the
agent needs to *offer* the fix with its existing tools
(``simulate_threshold`` / ``rescreen_scoped`` / ``remove_constraint`` /
``rescore_candidates`` …). Conservative thresholds throughout — we would
rather stay quiet than invent a problem.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.role_criterion import RoleCriterion
from . import impact as _impact
from . import rescore as _rescore
from .assessments import _entry_for, _req_id


# --- tuning knobs (kept conservative so we never invent problems) -----------
_MIN_POOL = 8           # need a real open pool before commenting on pool health
_MIN_ASSESSED = 8       # ...and a real assessed sample before judging a criterion
_DEAD_MET_FRAC = 0.15   # a must-have met by <15% of the assessed is killing the pool
_UNIVERSAL_MET_FRAC = 0.97  # met by ~everyone → no filtering signal
_UNVERIFIABLE_FRAC = 0.5    # >half of the assessed come back 'unknown' → can't evaluate
_STRICT_PASS_FRAC = 0.08
_LOOSE_PASS_FRAC = 0.92
_SMALL_QUALIFIED_ABS = 3
_BACKLOG_MIN = 10
_OVERRIDE_MIN = 3
_OVERRIDE_DOMINANCE = 0.65
_OVERRIDE_WINDOW_DAYS = 30

# Reject- vs advance-family decision types: which way an override leans tells us
# whether the agent is screening harder (rejects rescued) or softer (advances
# blocked) than the recruiter would.
_REJECT_FAMILY = frozenset({"reject", "skip_assessment_reject"})
_ADVANCE_FAMILY = frozenset(
    {"advance_to_interview", "send_assessment", "resend_assessment_invite"}
)

# Base rank by finding type; the specific instance adds a small magnitude bump
# so the worst instance of a type sorts first.
_BASE_RANK = {
    "calibration_drift": 100.0,
    "threshold_too_strict": 85.0,
    "dead_requirement": 80.0,
    "threshold_too_loose": 55.0,
    "unverifiable_requirement": 50.0,
    "stale_scores": 45.0,
    "decision_backlog": 35.0,
    "redundant_requirement": 20.0,
}

_CHECKS = (
    "calibration",
    "threshold",
    "requirements",
    "stale_scores",
    "backlog",
)


def _f(
    ftype: str,
    *,
    severity: str,
    title: str,
    detail: str,
    suggestion: str,
    mag: float = 0.0,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "type": ftype,
        "severity": severity,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
        "_mag": float(mag),
        **fields,
    }


# ---------------------------------------------------------------------------
# Calibration — the recruiter's overrides reveal a mis-tuned agent
# ---------------------------------------------------------------------------


def _calibration_finding(db: Session, role: Role) -> dict[str, Any] | None:
    since = datetime.now(timezone.utc) - timedelta(days=_OVERRIDE_WINDOW_DAYS)
    rows = (
        db.query(AgentDecision.decision_type)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.created_at >= since,
            (
                (AgentDecision.status == "overridden")
                | (AgentDecision.human_disposition.in_(("overridden", "taught")))
            ),
        )
        .all()
    )
    rescued = sum(1 for (dt,) in rows if dt in _REJECT_FAMILY)   # agent → reject, human kept
    blocked = sum(1 for (dt,) in rows if dt in _ADVANCE_FAMILY)  # agent → advance, human declined
    total = rescued + blocked
    if total < _OVERRIDE_MIN:
        return None

    mag = min(float(total), 20.0)
    window = _OVERRIDE_WINDOW_DAYS
    if rescued / total >= _OVERRIDE_DOMINANCE:
        return _f(
            "calibration_drift",
            severity="high",
            title="You keep rescuing candidates I'd reject",
            detail=(
                f"In the last {window} days you overrode {rescued} of my reject "
                f"calls (and {blocked} of my advances) — I'm screening harder than "
                "you'd actually decide."
            ),
            suggestion=(
                "My cut-off or a must-have is likely too strict. Want me to "
                "simulate a lower threshold, or look at which requirement is "
                "doing the rejecting?"
            ),
            mag=mag,
            direction="too_strict",
            overridden_rejects=rescued,
            overridden_advances=blocked,
            window_days=window,
        )
    if blocked / total >= _OVERRIDE_DOMINANCE:
        return _f(
            "calibration_drift",
            severity="high",
            title="You keep declining candidates I'd advance",
            detail=(
                f"In the last {window} days you overrode {blocked} of my advance "
                f"calls (and {rescued} of my rejects) — I'm clearing people you "
                "wouldn't."
            ),
            suggestion=(
                "My cut-off or a requirement is likely too loose. Want me to "
                "simulate a higher threshold, or tighten a requirement?"
            ),
            mag=mag,
            direction="too_loose",
            overridden_rejects=rescued,
            overridden_advances=blocked,
            window_days=window,
        )
    return _f(
        "calibration_drift",
        severity="medium",
        title="You've corrected me several times lately",
        detail=(
            f"In the last {window} days you overrode {total} of my recommendations "
            f"with no clear lean ({rescued} rejects rescued, {blocked} advances "
            "blocked)."
        ),
        suggestion=(
            "Worth a quick look at the spec and cut-off together so I match how "
            "you decide — want to walk through it?"
        ),
        mag=mag,
        direction="mixed",
        overridden_rejects=rescued,
        overridden_advances=blocked,
        window_days=window,
    )


# ---------------------------------------------------------------------------
# Threshold — pass-rate pathologies
# ---------------------------------------------------------------------------


def _threshold_findings(
    rows: list[Any], above: list[Any], threshold: float | None
) -> list[dict[str, Any]]:
    total = len(rows)
    if total < _MIN_POOL or threshold is None:
        return []
    n_above = len(above)
    frac = n_above / total
    if n_above <= _SMALL_QUALIFIED_ABS or frac <= _STRICT_PASS_FRAC:
        return [
            _f(
                "threshold_too_strict",
                severity="high",
                title="Your cut-off is barely producing a shortlist",
                detail=(
                    f"Only {n_above} of {total} open candidates clear your cut-off "
                    f"of {threshold:.0f} ({frac:.0%})."
                ),
                suggestion=(
                    "Want me to recommend a cut-off that clears a few more, or is "
                    "the pool genuinely this thin?"
                ),
                mag=(1.0 - frac) * 20.0,
                threshold=threshold,
                qualified=n_above,
                total_open=total,
            )
        ]
    if frac >= _LOOSE_PASS_FRAC:
        return [
            _f(
                "threshold_too_loose",
                severity="medium",
                title="Your cut-off is letting almost everyone through",
                detail=(
                    f"{n_above} of {total} ({frac:.0%}) clear your cut-off of "
                    f"{threshold:.0f}, so it isn't really filtering."
                ),
                suggestion=(
                    "Raise it to focus on the strongest, or are you intentionally "
                    "casting wide?"
                ),
                mag=frac * 10.0,
                threshold=threshold,
                qualified=n_above,
                total_open=total,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Requirements — a criterion that's killing the pool / unverifiable / redundant
# ---------------------------------------------------------------------------


def _criterion_counts(details_list: list[Any], criterion_id: int) -> tuple[int, int, int, int]:
    """(met, missing, unknown, not_assessed) for one criterion across the pool."""
    rid = _req_id(criterion_id)
    met = missing = unknown = not_assessed = 0
    for details in details_list:
        entry = _entry_for(details, rid)
        if entry is None:
            not_assessed += 1
            continue
        status = str(entry.get("status") or "unknown").lower()
        if status == "met":
            met += 1
        elif status == "missing":
            missing += 1
        else:
            unknown += 1
    return met, missing, unknown, not_assessed


def _requirement_findings(db: Session, role: Role) -> list[dict[str, Any]]:
    crits = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == int(role.id),
            RoleCriterion.deleted_at.is_(None),
            RoleCriterion.bucket.in_(("must", "constraint", "preferred")),
        )
        .all()
    )
    if not crits:
        return []

    details_list = [
        d
        for (d,) in db.query(CandidateApplication.cv_match_details)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    ]
    if len(details_list) < _MIN_POOL:
        return []

    findings: list[dict[str, Any]] = []
    for c in crits:
        met, missing, unknown, _not_assessed = _criterion_counts(details_list, int(c.id))
        assessed = met + missing + unknown
        if assessed < _MIN_ASSESSED:
            continue
        met_frac = met / assessed
        unknown_frac = unknown / assessed
        text = c.text or f"criterion #{c.id}"

        if c.bucket in ("must", "constraint"):
            if met_frac <= _DEAD_MET_FRAC:
                findings.append(
                    _f(
                        "dead_requirement",
                        severity="high",
                        title="A must-have almost nobody meets",
                        detail=(
                            f"'{text}' is met by only {met} of {assessed} assessed "
                            f"candidates ({met_frac:.0%}). If that isn't deliberate, "
                            "it's quietly removing most of your pool."
                        ),
                        suggestion=(
                            "Soften or drop it? I can re-screen just the affected "
                            "group cheaply rather than the whole pool."
                        ),
                        mag=(1.0 - met_frac) * 15.0,
                        criterion_id=int(c.id),
                        bucket=c.bucket,
                        met=met,
                        assessed=assessed,
                    )
                )
                continue
            if unknown_frac >= _UNVERIFIABLE_FRAC:
                findings.append(
                    _f(
                        "unverifiable_requirement",
                        severity="medium",
                        title="A requirement I often can't verify",
                        detail=(
                            f"For {unknown} of {assessed} candidates I can't tell "
                            f"whether they meet '{text}' — the CVs don't say. So "
                            "it's filtering on missing data."
                        ),
                        suggestion=(
                            "Decide how unstated should count (pass or hold), "
                            "reword it to something CVs actually show, or drop it."
                        ),
                        mag=unknown_frac * 8.0,
                        criterion_id=int(c.id),
                        bucket=c.bucket,
                        unknown=unknown,
                        assessed=assessed,
                    )
                )
                continue
            if met_frac >= _UNIVERSAL_MET_FRAC:
                findings.append(
                    _f(
                        "redundant_requirement",
                        severity="low",
                        title="A requirement everyone meets",
                        detail=(
                            f"'{text}' is met by {met} of {assessed} "
                            f"({met_frac:.0%}) — it isn't separating candidates."
                        ),
                        suggestion=(
                            "Fine to keep, but it isn't filtering anything — drop "
                            "it to simplify, or tighten it if you meant something "
                            "stricter."
                        ),
                        mag=met_frac,
                        criterion_id=int(c.id),
                        bucket=c.bucket,
                        met=met,
                        assessed=assessed,
                    )
                )
                continue
        elif c.bucket == "preferred":
            if met == 0:
                findings.append(
                    _f(
                        "redundant_requirement",
                        severity="low",
                        title="A preference no one has",
                        detail=(
                            f"No candidate has the nice-to-have '{text}', so it "
                            "never boosts anyone in the ranking."
                        ),
                        suggestion="Drop it, or is it a deliberate future-looking signal?",
                        mag=0.0,
                        criterion_id=int(c.id),
                        bucket=c.bucket,
                        met=met,
                        assessed=assessed,
                    )
                )
            elif met_frac >= _UNIVERSAL_MET_FRAC:
                findings.append(
                    _f(
                        "redundant_requirement",
                        severity="low",
                        title="A preference everyone has",
                        detail=(
                            f"Every assessed candidate has '{text}' ({met} of "
                            f"{assessed}), so as a preference it boosts everyone "
                            "equally — i.e. not at all."
                        ),
                        suggestion="Drop it to simplify, or promote it to a must-have if it's actually required.",
                        mag=met_frac,
                        criterion_id=int(c.id),
                        bucket=c.bucket,
                        met=met,
                        assessed=assessed,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Data quality — stale scores + decision backlog
# ---------------------------------------------------------------------------


def _data_quality_findings(db: Session, role: Role) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    stale = _rescore.stale_scores_summary(db, role)
    if stale and int(stale.get("stale_count") or 0) > 0:
        n = int(stale["stale_count"])
        findings.append(
            _f(
                "stale_scores",
                severity="medium",
                title="Some scores are from an old engine",
                detail=(
                    f"{n} candidate(s) still carry old-engine scores "
                    f"(v{', v'.join(stale.get('engine_versions') or ['?'])}), so "
                    "decisions on them rest on stale judgments."
                ),
                suggestion=(
                    f"Re-score them to the current engine (~${stale.get('est_cost_all_usd')})? "
                    "You pick the scope — all, the top by score, or just those below a cut-off."
                ),
                mag=min(float(n), 30.0) / 30.0 * 5.0,
                stale_count=n,
                est_cost_usd=stale.get("est_cost_all_usd"),
            )
        )

    pending = (
        db.query(func.count(AgentDecision.id))
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
        .scalar()
    ) or 0
    if int(pending) >= _BACKLOG_MIN:
        n = int(pending)
        findings.append(
            _f(
                "decision_backlog",
                severity="medium",
                title="Decisions are waiting on you",
                detail=f"{n} decisions are queued for your review.",
                suggestion=(
                    "Want to work through them? I can summarise the reject/advance "
                    "split first so you know what's there."
                ),
                mag=min(float(n), 60.0) / 60.0 * 10.0,
                pending=n,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def role_health_check(db: Session, role: Role) -> dict[str, Any]:
    """A ranked, read-only scan of what's most likely hurting this role's
    decisions. The agent leads with ``top_finding`` as a question; an empty
    ``findings`` (``all_clear``) means the role looks healthy — say so in a
    line, don't invent problems."""
    rows = _impact.load_open_candidates(db, role)
    threshold = _impact.effective_threshold(db, role)
    above, _below = _impact.split_by_threshold(rows, threshold)

    findings: list[dict[str, Any]] = []
    cal = _calibration_finding(db, role)
    if cal:
        findings.append(cal)
    findings.extend(_threshold_findings(rows, above, threshold))
    findings.extend(_requirement_findings(db, role))
    findings.extend(_data_quality_findings(db, role))

    for fnd in findings:
        fnd["rank_score"] = round(
            _BASE_RANK.get(fnd["type"], 0.0) + float(fnd.pop("_mag", 0.0)), 2
        )
    findings.sort(key=lambda x: x["rank_score"], reverse=True)

    return {
        "type": "role_health",
        "role_id": int(role.id),
        "role_name": role.name,
        "open_candidates": len(rows),
        "checks_run": list(_CHECKS),
        "finding_count": len(findings),
        "findings": findings,
        "top_finding": findings[0] if findings else None,
        "all_clear": not findings,
    }


__all__ = ["role_health_check"]
