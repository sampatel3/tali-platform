"""Glue: aggregate → retroactive eval → retuner → write inactive revision.

Public entry: ``run_for_org(db, organization_id) -> NightlyResult``.
The Celery beat task in ``app.tasks.decision_policy_tasks`` calls this
once per active org per day. Auto-apply opt-in lives in
``Organization.workspace_settings.decision_policy_auto_apply``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.orm import Session

from ..models.decision_policy import DecisionPolicy
from ..models.organization import Organization
from ..models.policy_version import PolicyVersion
from ..models.rubric_revision import RubricRevision
from .audit_examples import load_audit_examples
from .bias_audit import AuditExample
from .engine import load_active_policy
from .feedback_aggregator import aggregate_signals
from .promotion_gate import evaluate_auto_apply
from .retuner import HeuristicRetuner, RetuneProposal, Retuner
from .schema import PolicyJson


logger = logging.getLogger("taali.decision_policy.nightly_retune")


@dataclass
class NightlyResult:
    organization_id: int
    skipped_reason: str | None
    proposal: RetuneProposal | None
    revision_id: int | None
    policy_id: int | None
    activated: bool
    # When auto-apply was requested but the safety gate withheld
    # activation, this records why (bias-audit / gold-set failure, or a
    # cold-start vacuum). None when not applicable.
    gate_blocked_reason: str | None = None


def _resolve_min_signals(org: Organization) -> int | None:
    """Per-org override on the retuner's MIN_SIGNALS_FOR_RETUNE."""
    settings = (
        org.workspace_settings if isinstance(org.workspace_settings, dict) else None
    )
    val = (settings or {}).get("decision_policy_min_signals_for_retune")
    if isinstance(val, int) and val >= 0:
        return val
    return None


def _auto_apply_enabled(org: Organization) -> bool:
    settings = (
        org.workspace_settings if isinstance(org.workspace_settings, dict) else None
    )
    return bool((settings or {}).get("decision_policy_auto_apply", False))


def _latest_fitted_policy_version(
    db: Session, *, organization_id: int, role_id: int | None, statuses: tuple[str, ...]
) -> PolicyVersion | None:
    """Most recent fitted ``PolicyVersion`` for (org, role) in ``statuses``.

    The auto-apply gate judges the org's *current learned signal* via the
    nightly fitter's output. ``candidate``/``shadow`` rows are the model
    we'd audit; ``live`` is the baseline to compare gold-set log-loss
    against.
    """
    return (
        db.query(PolicyVersion)
        .filter(
            PolicyVersion.organization_id == organization_id,
            (
                PolicyVersion.role_id == role_id
                if role_id is not None
                else PolicyVersion.role_id.is_(None)
            ),
            PolicyVersion.status.in_(statuses),
        )
        .order_by(PolicyVersion.trained_at.desc())
        .first()
    )


def _had_recent_run(db: Session, *, organization_id: int) -> bool:
    """Avoid retuning orgs with zero recent agent activity."""
    from datetime import timedelta

    from ..models.agent_run import AgentRun

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    return (
        db.query(AgentRun)
        .filter(
            AgentRun.organization_id == organization_id,
            AgentRun.started_at >= cutoff,
        )
        .first()
        is not None
    )


def run_for_org(
    db: Session,
    *,
    organization_id: int,
    retuner: Retuner | None = None,
    audit_examples: Sequence[AuditExample] | None = None,
) -> NightlyResult:
    """Run the nightly retune for one org.

    Auto-apply (flipping a learned proposal live without a human approval
    click) is **operator-opt-in** via
    ``Organization.workspace_settings.decision_policy_auto_apply`` and is
    OFF by default. When it is off, the proposal is always written inactive
    for human review and ``audit_examples`` is irrelevant.

    ``audit_examples`` is the protected-attribute holdout the bias audit
    runs against when auto-apply IS enabled. Protected attributes are
    deliberately kept out of production data (see
    ``graph_writeback.sensitivity``), so this is a *curated compliance
    set* supplied by the caller rather than something we can derive from
    the warehouse. ``run_for_all_orgs`` now resolves it per-org via
    ``audit_examples.load_audit_examples`` (TAA-28) so the EEOC bias audit
    runs on real data when a holdout is configured. When it's absent the
    auto-apply gate fails closed (cold start) and the proposal is written
    inactive for human review — the same safe behaviour as before.
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .one_or_none()
    )
    if org is None:
        return NightlyResult(
            organization_id=organization_id,
            skipped_reason="organization not found",
            proposal=None,
            revision_id=None,
            policy_id=None,
            activated=False,
        )

    if not _had_recent_run(db, organization_id=organization_id):
        return NightlyResult(
            organization_id=organization_id,
            skipped_reason="no agent runs in the last 7 days",
            proposal=None,
            revision_id=None,
            policy_id=None,
            activated=False,
        )

    try:
        policy_row = load_active_policy(
            db, organization_id=organization_id, role_id=None
        )
        current = PolicyJson.model_validate(policy_row.policy_json or {})
    except Exception as exc:
        return NightlyResult(
            organization_id=organization_id,
            skipped_reason=f"no active policy to retune: {exc}",
            proposal=None,
            revision_id=None,
            policy_id=None,
            activated=False,
        )

    signals = aggregate_signals(db, organization_id=organization_id)

    if retuner is None:
        kwargs: dict[str, Any] = {}
        min_signals = _resolve_min_signals(org)
        if min_signals is not None:
            kwargs["min_signals"] = min_signals
        retuner = HeuristicRetuner(**kwargs)

    proposal = retuner.propose(current, signals)
    if proposal is None or not proposal.has_changes:
        return NightlyResult(
            organization_id=organization_id,
            skipped_reason=(
                "no actionable signals" if proposal is None else "no shifts proposed"
            ),
            proposal=proposal,
            revision_id=None,
            policy_id=None,
            activated=False,
        )

    revision = RubricRevision(
        organization_id=organization_id,
        role_id=None,
        parent_revision_id=int(policy_row.revision_id),
        cause="feedback_retune",
        feedback_ids=signals.feedback_ids,
        weights_diff=None,  # full diff lives on policy_json metadata
        threshold_diff=None,
        notes=(proposal.new_policy_json.get("metadata") or {}).get("notes"),
    )
    db.add(revision)
    db.flush()

    # Auto-apply removes the human approval click — but NOT the safety
    # checks. Before flipping a proposal live we run the promotion gate's
    # synchronous checks (non-bypassable bias audit + gold-set log-loss)
    # against the org's latest fitted candidate. A failing or cold-start
    # gate leaves the proposal inactive, identical to the default path.
    activate = False
    gate_blocked_reason: str | None = None
    if _auto_apply_enabled(org):
        candidate_pv = _latest_fitted_policy_version(
            db,
            organization_id=organization_id,
            role_id=None,
            statuses=("candidate", "shadow"),
        )
        live_pv = _latest_fitted_policy_version(
            db,
            organization_id=organization_id,
            role_id=None,
            statuses=("live",),
        )
        gate = evaluate_auto_apply(
            db,
            candidate=candidate_pv,
            live=live_pv,
            audit_examples=audit_examples or [],
        )
        activate = gate.passed
        if not gate.passed:
            detail = "; ".join(gate.reasons) or "blocked"
            prefix = "cold start: " if gate.cold_start else ""
            gate_blocked_reason = f"auto-apply gate withheld activation ({prefix}{detail})"
            logger.info(
                "org=%s auto-apply gate withheld activation: cold_start=%s reasons=%s",
                organization_id, gate.cold_start, gate.reasons,
            )

    activated_at = datetime.now(timezone.utc) if activate else None
    new_policy = DecisionPolicy(
        organization_id=organization_id,
        role_id=None,
        revision_id=int(revision.id),
        policy_json=proposal.new_policy_json,
        activated_at=activated_at,
        deactivated_at=None,
    )
    db.add(new_policy)
    db.flush()

    if activate:
        # Deactivate the prior policy in the same transaction.
        policy_row.deactivated_at = datetime.now(timezone.utc)
        db.add(policy_row)
        db.flush()

    return NightlyResult(
        organization_id=organization_id,
        skipped_reason=None,
        proposal=proposal,
        revision_id=int(revision.id),
        policy_id=int(new_policy.id),
        activated=activate,
        gate_blocked_reason=gate_blocked_reason,
    )


def run_for_all_orgs(db: Session) -> list[NightlyResult]:
    org_ids = [int(row[0]) for row in db.query(Organization.id).all()]
    results: list[NightlyResult] = []
    for oid in org_ids:
        try:
            # TAA-28: resolve the org's compliance-curated bias-audit
            # holdout and thread it through so ``evaluate_auto_apply``'s
            # EEOC bias audit runs on REAL data when auto-apply is enabled.
            # Unconfigured orgs resolve to ``[]`` → the gate fails closed
            # (cold start), which is the safe default. Auto-apply itself
            # stays opt-in per ``decision_policy_auto_apply`` (off by
            # default), so this never activates a policy on its own.
            org = (
                db.query(Organization)
                .filter(Organization.id == oid)
                .one_or_none()
            )
            audit_examples = (
                load_audit_examples(org) if org is not None else []
            )
            results.append(
                run_for_org(
                    db, organization_id=oid, audit_examples=audit_examples
                )
            )
        except Exception as exc:
            logger.warning(
                "nightly retune crashed org_id=%s error_type=%s",
                oid,
                type(exc).__name__,
            )
            # run_for_org adds/flushes rows; a mid-flight failure leaves
            # the session in a failed state. Roll back before the next org
            # so one org's crash doesn't poison every subsequent retune.
            db.rollback()
            results.append(
                NightlyResult(
                    organization_id=oid,
                    skipped_reason="retune_crashed",
                    proposal=None,
                    revision_id=None,
                    policy_id=None,
                    activated=False,
                )
            )
    return results


__all__ = ["NightlyResult", "run_for_all_orgs", "run_for_org"]
