"""Dormant shadow-mode bookkeeping primitives — Phase 5 §8.3.

These functions preserve the intended future lifecycle: a candidate policy
scores incoming decisions beside a live policy, comparisons are recorded, and
an eligible run can be concluded for the promotion gate. They are deliberately
not called by the production scheduler or decision path today. The current JSON
history has no durable decision identity/outcome-backfill contract, so wiring it
automatically would be unsafe; fitted candidates therefore remain fail-closed.

After N decisions OR M days (D3 — tiered by role volume), the shadow
run concludes. The promotion gate compares:
  - disagreement rate (candidate vs live verdict)
  - confidence-distribution shift
  - For decisions with realised outcomes during the window: which
    policy was closer to ground truth.

This module owns only manual/test bookkeeping. A production rollout requires a
durable idempotent per-decision store, outcome linkage, concurrency controls,
and an explicit operator activation step. Do not add a token import merely to
make these functions appear wired.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.policy_version import PolicyVersion
from ..models.promotion_gate import ShadowRun


logger = logging.getLogger("taali.decision_policy.shadow_mode")


# D3: tiered shadow trigger by role volume (decisions per week).
SHADOW_THRESHOLD_HIGH_VOL = {"decisions": 200, "days": 7}
SHADOW_THRESHOLD_MED_VOL = {"decisions": 100, "days": 14}


def open_shadow_run(
    db: Session,
    *,
    candidate: PolicyVersion,
    live: PolicyVersion | None,
) -> ShadowRun:
    """Explicitly open a run; no scheduler invokes this automatically."""
    row = ShadowRun(
        candidate_policy_version_id=int(candidate.id),
        live_policy_version_id=int(live.id) if live else None,
        status="comparing",
    )
    # Flip the candidate's status to ``shadow`` so the engine knows
    # not to use it for real verdicts.
    candidate.status = "shadow"
    db.add(row)
    db.flush()
    return row


def record_shadow_decision(
    db: Session,
    *,
    shadow_run: ShadowRun,
    live_prediction: float,
    candidate_prediction: float,
    threshold: float = 0.5,
    realised_label: float | None = None,
) -> None:
    """Increment counters and append to ``metrics_json.history``.

    Pre-pilot scale (<200 rows per shadow run) means stuffing the
    history into JSON is fine. At larger scale this should move to a
    child table.
    """
    shadow_run.decisions_compared = int(shadow_run.decisions_compared or 0) + 1
    live_yes = live_prediction >= threshold
    cand_yes = candidate_prediction >= threshold
    if live_yes != cand_yes:
        shadow_run.disagreements = int(shadow_run.disagreements or 0) + 1
    metrics = dict(shadow_run.metrics_json or {})
    history: list[dict] = list(metrics.get("history") or [])
    history.append({
        "live_p": float(live_prediction),
        "candidate_p": float(candidate_prediction),
        "realised": realised_label,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    metrics["history"] = history[-500:]  # cap, just in case
    shadow_run.metrics_json = metrics
    db.flush()


def is_eligible_for_conclusion(
    shadow_run: ShadowRun,
    *,
    role_volume: str = "high",  # high | medium | low
    now: datetime | None = None,
) -> bool:
    """Has the shadow run hit its tier's N-decisions OR M-days bound?"""
    now = now or datetime.now(timezone.utc)
    started = shadow_run.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (now - started).days if started else 0
    if role_volume == "high":
        return (shadow_run.decisions_compared or 0) >= SHADOW_THRESHOLD_HIGH_VOL["decisions"] or elapsed >= SHADOW_THRESHOLD_HIGH_VOL["days"]
    if role_volume == "medium":
        return (shadow_run.decisions_compared or 0) >= SHADOW_THRESHOLD_MED_VOL["decisions"] or elapsed >= SHADOW_THRESHOLD_MED_VOL["days"]
    # Low-volume roles never auto-conclude shadow — they inherit the
    # org-level policy and ride along.
    return False


def conclude_shadow_run(
    db: Session, *, shadow_run: ShadowRun
) -> dict:
    """Finalise the run and compute the summary metrics.

    Returns a dict the promotion gate inspects. A summary with zero realised
    outcomes remains ineligible for promotion; conclusion is not activation.
    """
    metrics = dict(shadow_run.metrics_json or {})
    history: list[dict] = list(metrics.get("history") or [])
    decisions_with_outcome = [h for h in history if h.get("realised") is not None]

    summary: dict = {
        "decisions_compared": int(shadow_run.decisions_compared or 0),
        "disagreements": int(shadow_run.disagreements or 0),
        "disagreement_rate": (
            (shadow_run.disagreements / shadow_run.decisions_compared)
            if shadow_run.decisions_compared
            else 0.0
        ),
        "outcomes_observed": len(decisions_with_outcome),
    }
    if decisions_with_outcome:
        live_correct = 0
        cand_correct = 0
        for h in decisions_with_outcome:
            real = float(h["realised"])
            live_yes = (h["live_p"] or 0.0) >= 0.5
            cand_yes = (h["candidate_p"] or 0.0) >= 0.5
            target = real >= 0.5
            if live_yes == target:
                live_correct += 1
            if cand_yes == target:
                cand_correct += 1
        summary["live_correct"] = live_correct
        summary["candidate_correct"] = cand_correct
        summary["candidate_accuracy_delta"] = (
            (cand_correct - live_correct) / max(1, len(decisions_with_outcome))
        )
    shadow_run.status = "concluded"
    shadow_run.ended_at = datetime.now(timezone.utc)
    shadow_run.metrics_json = {**metrics, "summary": summary}
    db.flush()
    return summary


__all__ = [
    "SHADOW_THRESHOLD_HIGH_VOL",
    "SHADOW_THRESHOLD_MED_VOL",
    "conclude_shadow_run",
    "is_eligible_for_conclusion",
    "open_shadow_run",
    "record_shadow_decision",
]
