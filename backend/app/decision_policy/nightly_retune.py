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
from typing import Any

from sqlalchemy.orm import Session

from ..models.decision_policy import DecisionPolicy
from ..models.organization import Organization
from ..models.rubric_revision import RubricRevision
from ..models.role import Role
from .engine import load_active_policy
from .feedback_aggregator import aggregate_signals
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
) -> NightlyResult:
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

    auto_apply = _auto_apply_enabled(org)
    activated_at = datetime.now(timezone.utc) if auto_apply else None
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

    if auto_apply:
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
        activated=auto_apply,
    )


def run_for_all_orgs(db: Session) -> list[NightlyResult]:
    org_ids = [int(row[0]) for row in db.query(Organization.id).all()]
    results: list[NightlyResult] = []
    for oid in org_ids:
        try:
            results.append(run_for_org(db, organization_id=oid))
        except Exception as exc:
            logger.exception("nightly retune crashed for org_id=%s", oid)
            # run_for_org adds/flushes rows; a mid-flight failure leaves
            # the session in a failed state. Roll back before the next org
            # so one org's crash doesn't poison every subsequent retune.
            db.rollback()
            results.append(
                NightlyResult(
                    organization_id=oid,
                    skipped_reason=f"crashed: {exc}",
                    proposal=None,
                    revision_id=None,
                    policy_id=None,
                    activated=False,
                )
            )
    return results


__all__ = ["NightlyResult", "run_for_all_orgs", "run_for_org"]
