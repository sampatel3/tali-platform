"""Bullhorn siblings of the shared op_runner's ATS-write handlers.

``services/workable_op_runner`` owns the op dispatch, the shared retry / requeue /
surface machinery, and the Workable-shaped handler bodies. For a Bullhorn-connected
org those handlers early-delegate here (build plan §6 line 89 — "op_runner resolves
provider through the PR-1 seam") so the ATS write goes to :class:`BullhornProvider`
while everything cross-cutting stays in the ONE shared shell — **no new op types, no
new Celery task, no change to gated/ungated semantics or retry policy.**

Each handler is the Bullhorn analogue of one Workable handler and returns the same
shell-compatible ``{"status": ...}`` dict. Under ``strict_workable_writes()`` the
provider's write raises the shared :class:`WorkableWritebackError`, which the shell
already turns into a retry (retriable) or a terminal ``surface_op_failure`` — the
SAME terminal-failure surface Workable ops use — so a server-side workflow-validation
rejection on a status write lands in the Decision Hub exactly like a Workable one.

Gating: reached only when ``resolve_ats_provider(org, db)`` returns a
:class:`BullhornProvider`, i.e. ``BULLHORN_ENABLED`` on AND the org is
Bullhorn-connected. A no-op otherwise (the Workable handler body runs instead).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....services.workable_actions_service import WorkableWritebackError
from .provider import BullhornProvider

logger = logging.getLogger("taali.bullhorn.op_handlers")


def _bullhorn_provider(
    db: Session, org: Organization, app: CandidateApplication
) -> BullhornProvider | None:
    """Resolve Bullhorn from this application's durable ATS linkage."""
    from ..resolver import resolve_application_ats_provider

    provider = resolve_application_ats_provider(org, db, app)
    return provider if isinstance(provider, BullhornProvider) else None


def _raise_if_failed(result: dict, *, default_action: str) -> None:
    """Turn a non-strict failure dict into a WorkableWritebackError.

    Under strict mode the provider already raised; this covers the ungated path
    (e.g. the free-form note op, which is not strict-gated) so a failed write is
    surfaced/retried by the shell instead of silently succeeding.
    """
    if result.get("success"):
        return
    code = str(result.get("code") or "api_error")
    raise WorkableWritebackError(
        action=str(result.get("action") or default_action),
        code=code,
        message=str(result.get("message") or "Bullhorn write failed"),
        retriable=(code == "api_error"),
    )


def run_move_stage(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Compatibility seam routed through the canonical receipt lifecycle.

    Provider-neutral stage moves superseded the historical Bullhorn-only
    implementation. Retaining this callable avoids breaking older imports,
    while exactly-once claims, drift reconciliation, and durable notes now use
    the same path as every production ``move_stage`` job.
    """
    from ....services.ats_stage_move_lifecycle import execute_stage_move_lifecycle

    canonical_payload = {
        **payload,
        "application_id": int(app.id),
        "provider": "bullhorn",
        "provider_target_id": str(app.bullhorn_job_submission_id or ""),
        "target_stage": str(
            payload.get("target_stage") or payload.get("target_intent") or "advanced"
        ),
    }
    return execute_stage_move_lifecycle(
        db,
        organization_id=int(org.id),
        payload=canonical_payload,
    )


def run_manual_outcome(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Bullhorn outcome sync — analogue of ``_op_manual_outcome``.

    Reject writes the org's rejected-category status; re-open writes a non-reject
    status back (Bullhorn has no first-class un-reject). Local outcome already
    committed in the route; this is the (retried) remote write only.
    """
    from ....domains.assessments_runtime.pipeline_service import append_application_event
    from ....services.workable_actions_service import strict_workable_writes

    application_id = int(app.id)
    provider_target_id = str(payload.get("provider_target_id") or "").strip()
    if (
        not provider_target_id
        or str(app.bullhorn_job_submission_id or "").strip() != provider_target_id
        or app.workable_candidate_id
    ):
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    provider = _bullhorn_provider(db, org, app)
    if provider is None:
        return {"status": "skipped", "reason": "not_connected", "application_id": application_id}

    target_outcome = payload.get("target_outcome")
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    actor_type = str(payload.get("actor_type") or "recruiter")
    actor_id = payload.get("actor_id", user_id)
    with strict_workable_writes():
        result = provider.move_application(
            candidate_id=provider_target_id,
            target_stage=("advanced" if target_outcome == "open" else "rejected"),
            role=None,
        )
        event_type = (
            "bullhorn_reverted" if target_outcome == "open" else "bullhorn_rejected"
        )
    _raise_if_failed(result, default_action="move")
    from ....services.manual_outcome_lifecycle import (
        finalize_manual_outcome_success,
    )

    reconciliation = finalize_manual_outcome_success(
        db,
        app,
        payload,
        provider="bullhorn",
        remote_status=result.get("config", {}).get("remote_status"),
    )
    if reconciliation is not None:
        return reconciliation
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or "Bullhorn outcome synced",
        metadata={
            "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
            "target_outcome": target_outcome,
            "bullhorn_status": result.get("config", {}).get("remote_status"),
        },
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def run_post_note(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Deprecated import seam delegated to the canonical note runtime."""

    from ....services.ats_note_dispatch import prepare_application_ats_note_payload
    from ....services.ats_note_runtime import execute_ats_note

    candidate = getattr(app, "candidate", None)
    canonical = prepare_application_ats_note_payload(
        db,
        organization_id=int(org.id),
        application_id=int(app.id),
        body=str(payload.get("body") or ""),
        provider="bullhorn",
        actor_type=str(payload.get("actor_type") or "recruiter"),
        actor_id=payload.get("actor_id", payload.get("user_id")),
        expected_provider_target_id=str(app.bullhorn_job_submission_id or ""),
        expected_candidate_provider_id=str(
            getattr(candidate, "bullhorn_candidate_id", None) or ""
        ),
    )
    canonical["note_operation_id"] = str(
        payload.get("note_operation_id")
        or f"bullhorn-compat-note:{int(org.id)}:{int(app.id)}"
    )
    return execute_ats_note(
        db,
        organization_id=int(org.id),
        payload=canonical,
    )
