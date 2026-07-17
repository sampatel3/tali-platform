"""Relock, validate, and atomically project a confirmed decision result."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from .decision_provider_authority import lock_decision_provider_authority
from .decision_provider_call import resolve_decision_provider_authority
from .decision_provider_checkpoint import (
    lock_claim_application,
    mark_claim_reconciliation,
)
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_drift import decision_provider_drift_reason
from .decision_provider_operation import confirm_decision_provider_receipt


def _stamp_provider_confirmation(
    db: Session,
    *,
    app: CandidateApplication,
    claim: DecisionProviderClaim,
    provider_result: dict[str, Any],
    actor_type: str,
    actor_id: int | None,
    reason: str | None,
) -> None:
    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
        normalize_pipeline_key,
    )

    snap = claim.snapshot
    now = datetime.now(timezone.utc)
    if snap.provider == "workable":
        if snap.operation_action == "advance":
            app.workable_stage = snap.target_stage
            app.workable_stage_local_write_at = now
            event_type = "workable_moved"
            metadata = {"target_stage": snap.target_stage}
        else:
            app.workable_disqualified = True
            app.workable_disqualified_at = now
            event_type = "workable_disqualified"
            metadata = {"target_outcome": "rejected"}
    else:
        remote = str(
            provider_result.get("provider_remote_stage")
            or snap.provider_remote_stage
            or ""
        ).strip()
        app.bullhorn_status = remote or None
        app.external_stage_raw = remote or None
        app.external_stage_normalized = normalize_pipeline_key(
            "advanced" if snap.operation_action == "advance" else "rejected"
        ) or None
        app.bullhorn_status_local_write_at = now
        event_type = (
            "bullhorn_moved"
            if snap.operation_action == "advance"
            else "bullhorn_rejected"
        )
        metadata = {"bullhorn_status": remote}
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or "Decision provider update confirmed",
        metadata={
            **metadata,
            "operation_id": claim.operation_id,
            "decision_id": snap.decision_id,
            "provider_target_id": snap.provider_target_id,
            "source": "decision_provider_lifecycle",
        },
        idempotency_key=f"{claim.operation_id}:provider-confirmed"[:200],
    )


def _build_post_operation(
    db: Session,
    *,
    app: CandidateApplication,
    decision: AgentDecision,
    claim: DecisionProviderClaim,
    actor_id: int | None,
    note: str | None,
) -> dict[str, Any] | None:
    if claim.snapshot.provider not in {"workable", "bullhorn"}:
        return None
    from ..actions._decision_side_effects import verdict_for
    from ..actions._workable_decision_summary import (
        _mint_30d_share_link,
        compose_decision_summary_note,
    )

    verdict = verdict_for(
        disposition=claim.snapshot.disposition,
        decision_type=str(decision.decision_type),
        override_action=claim.snapshot.override_action,
    )
    if not verdict:
        return None
    share_url = _mint_30d_share_link(db, app=app, created_by_user_id=actor_id)
    body = compose_decision_summary_note(
        decision,
        app,
        verdict=verdict,
        override_action=claim.snapshot.override_action,
        reason=note,
        share_url=share_url,
    )
    operation_id = f"{claim.operation_id}:summary-note"[:200]
    return {
        "status": "pending",
        "operation_id": operation_id,
        "body": body,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "provider": claim.snapshot.provider,
        "provider_target_id": claim.snapshot.provider_target_id,
        "candidate_provider_id": claim.snapshot.candidate_provider_id,
        "actor_id": actor_id,
    }


def apply_local_decision(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    actor,
    note: str | None,
    target_stage: str | None,
    override_action: str | None,
) -> AgentDecision:
    sink: dict[str, Any] = {}
    if claim.snapshot.disposition == "approved":
        from ..actions import approve_decision

        return approve_decision.run(
            db,
            actor,
            organization_id=claim.snapshot.organization_id,
            decision_id=claim.snapshot.decision_id,
            note=note,
            workable_target_stage=target_stage,
            collect_side_effects=sink,
            expected_decision_type=claim.snapshot.expected_decision_type,
            expected_role_family=claim.expected_role_family,
            provider_operation_id=claim.operation_id,
        )
    from ..actions import override_decision

    return override_decision.run(
        db,
        actor,
        organization_id=claim.snapshot.organization_id,
        decision_id=claim.snapshot.decision_id,
        override_action=override_action,
        note=note,
        workable_target_stage=target_stage,
        collect_side_effects=sink,
        expected_decision_type=claim.snapshot.expected_decision_type,
        expected_role_family=claim.expected_role_family,
        provider_operation_id=claim.operation_id,
    )


def _authority_unavailable_result(claim: DecisionProviderClaim) -> dict[str, Any]:
    return {
        "status": "reconciliation_required",
        "decision_id": claim.snapshot.decision_id,
        "operation_id": claim.operation_id,
        "reconciliation_reason": "decision_authority_unavailable",
        "failed": True,
    }


def finalize_decision_provider_success(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    provider_result: dict[str, Any],
    actor,
    note: str | None,
    target_stage: str | None,
    override_action: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        current = lock_decision_provider_authority(
            db,
            organization_id=claim.snapshot.organization_id,
            decision_id=claim.snapshot.decision_id,
            expected_decision_type=claim.snapshot.expected_decision_type,
            expected_role_family=claim.expected_role_family,
            disposition=claim.snapshot.disposition,
            override_action=override_action,
        )
    except HTTPException:
        db.rollback()
        app = lock_claim_application(db, claim)
        if app is None:
            db.rollback()
            return _authority_unavailable_result(claim), None
        return (
            mark_claim_reconciliation(
                db,
                claim=claim,
                app=app,
                reason="decision_authority_unavailable",
                provider_succeeded=True,
            ),
            None,
        )
    provider = resolve_decision_provider_authority(
        db,
        app=current.app,
        candidate=current.candidate,
        organization=current.organization,
        owner_role=current.owner_role,
        operation_action=claim.snapshot.operation_action,
        target_stage=target_stage,
        reason=note,
    )
    drift = decision_provider_drift_reason(
        claim=claim, current=current, provider=provider
    )
    if drift is not None:
        return (
            mark_claim_reconciliation(
                db,
                claim=claim,
                app=current.app,
                reason=drift,
                provider_succeeded=True,
            ),
            None,
        )
    try:
        resolved = apply_local_decision(
            db,
            claim=claim,
            actor=actor,
            note=note,
            target_stage=target_stage,
            override_action=override_action,
        )
        # Project the provider-specific mirror only after the local action's
        # own actionability gate has passed. For a confirmed rejection,
        # stamping ``workable_disqualified`` first would make the same atomic
        # local rejection incorrectly reject itself as already inactionable.
        _stamp_provider_confirmation(
            db,
            app=current.app,
            claim=claim,
            provider_result=provider_result,
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=note,
        )
        post = _build_post_operation(
            db,
            app=current.app,
            decision=resolved,
            claim=claim,
            actor_id=actor.user_id,
            note=note,
        )
        confirmed = confirm_decision_provider_receipt(
            current.app,
            operation_id=claim.operation_id,
            provider_result=provider_result,
            post_operation=post,
            expected_snapshot_fingerprint=claim.snapshot.fingerprint(),
        )
        if confirmed is None:
            raise RuntimeError("decision provider receipt changed during finalization")
        db.commit()
    except Exception:
        db.rollback()
        relocked = lock_claim_application(db, claim)
        if relocked is None:
            db.rollback()
            raise
        return (
            mark_claim_reconciliation(
                db,
                claim=claim,
                app=relocked,
                reason="local_projection_failed",
                provider_succeeded=True,
            ),
            None,
        )
    return (
        {
            "status": "ok",
            "decision_id": claim.snapshot.decision_id,
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
        },
        post,
    )


__all__ = ["apply_local_decision", "finalize_decision_provider_success"]
