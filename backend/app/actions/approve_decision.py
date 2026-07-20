"""Recruiter approves a queued ``AgentDecision``.

Resolves the queue row to ``approved`` and dispatches the underlying
action with ``actor=recruiter`` so the audit row records *the recruiter*
as the one who made the change — with metadata pointing back to the
agent's reasoning and run id.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..services.decision_membership import lock_resolution_roles
from ..services.decision_approval_guard import enforce_decision_approval_eligibility
from . import advance_stage, reject_application, resend_assessment_invite, send_assessment
from ._decision_side_effects import (
    apply_decision_side_effects,
    lock_organization_for_decision_resolution,
)
from .types import ACTOR_RECRUITER, Actor


_REJECT_DECISION_TYPES = ("reject", "skip_assessment_reject")


class ApprovalOutcomeUnknownError(RuntimeError):
    """Acceptance may be durable, so the recruiter must not retry blindly."""


def rollback_preserving_unknown_outcome(db: Session) -> None:
    """Best-effort cleanup that cannot hide an ambiguous acceptance result."""

    with suppress(Exception):
        db.rollback()


def _accept_for_processing(
    db: Session,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str],
    allow_engine_outdated: bool = False,
) -> AgentDecision:
    """Lock a pending decision and flip it to ``processing``. Raises 404/409.

    No commit — the caller commits the whole batch's flips at once.
    """
    q = db.query(AgentDecision).filter(
        AgentDecision.id == decision_id,
        AgentDecision.organization_id == organization_id,
    ).populate_existing()
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    decision = q.first()
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status not in ("pending", "reverted_for_feedback"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"agent_decision {decision_id} is {decision.status}, not "
                "pending or awaiting a post-teach resolution"
            ),
        )
    enforce_decision_approval_eligibility(
        db,
        decision,
        allow_engine_outdated=allow_engine_outdated,
    )
    decision.status = "processing"
    if note is not None:
        decision.resolution_note = note
    return decision


def enqueue_batch(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_ids: list[int],
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    workable_target_stages: Optional[dict[str, str]] = None,
    allow_engine_outdated_decision_ids: Optional[set[int]] = None,
) -> dict:
    """Accept a batch of approvals for background processing.

    A whole request — single approve or a 100-row bulk approve — becomes ONE
    background job. Each valid pending decision is flipped to ``processing``
    (visible but read-only in the recruiter's queue), a single
    ``BackgroundJobRun`` (kind ``decision_batch``) is recorded for Settings →
    Background jobs, and ONE ``process_decision_batch`` task drains the
    Workable writebacks sequentially (serialized per org, so a big batch can't
    breach the rate limit). A decision whose writeback fails is returned to the
    queue by the task — never lost.

    The only synchronous work is the status flip + bookkeeping (no Workable
    calls), so approving 100 decisions returns immediately.

    ``workable_target_stages`` is the per-role advance-stage map (``role_id``
    string → Workable stage) for a multi-role bulk approve; ``workable_target_stage``
    is the single-stage fallback used by ``enqueue_one``. The batch handler
    resolves each advance decision's stage from the map first, then the
    fallback. Roles in neither advance on Tali's internal stage only.

    Returns ``{"job_run_id", "accepted": [ids], "failures": [{decision_id, error}]}``.
    """
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="approve is recruiter-only")

    requested = list(dict.fromkeys(int(x) for x in decision_ids))
    accepted: list[int] = []
    failures: list[dict] = []
    engine_force_ids = {
        int(value) for value in (allow_engine_outdated_decision_ids or set())
    }
    for decision_id in requested:
        try:
            _accept_for_processing(
                db,
                organization_id=int(organization_id),
                decision_id=decision_id,
                note=note,
                allow_engine_outdated=decision_id in engine_force_ids,
            )
            accepted.append(decision_id)
        except HTTPException as exc:
            failures.append(
                {
                    "decision_id": decision_id,
                    "status_code": exc.status_code,
                    "error": str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
                }
            )
    job_run_id = None
    if accepted:
        from ..services.workable_op_runner import (
            OP_APPROVE_DECISIONS,
            AtsJobRunPersistenceError,
            persist_workable_op_run,
            publish_workable_op,
        )

        payload = {
            "decision_ids": accepted,
            "user_id": int(actor.user_id) if actor.user_id else None,
            "note": note,
            "workable_target_stage": workable_target_stage,
            "workable_target_stages": workable_target_stages or None,
            "allow_engine_outdated_decision_ids": [
                decision_id for decision_id in accepted if decision_id in engine_force_ids
            ],
        }
        counters = {
            "total": len(accepted),
            "succeeded": 0,
            "requeued": 0,
            "failed": 0,
            "decision_ids": accepted,
        }
        try:
            # The processing flips and their watchdog/recovery row become
            # visible in one commit. A process death can therefore never leave
            # a hidden processing decision without durable tracking.
            job_run_id = persist_workable_op_run(
                db,
                organization_id=int(organization_id),
                op_type=OP_APPROVE_DECISIONS,
                payload=payload,
                # ``decision_ids`` lets the watchdog (expire_stuck_decision_batches)
                # return exactly this batch's rows to the queue if the worker is
                # killed mid-run. Overwritten by result counters on completion, so
                # it only persists while the run is in-flight — which is all the
                # watchdog needs.
                counters=counters,
            )
            db.commit()
        except AtsJobRunPersistenceError:
            db.rollback()
            raise
        except Exception as exc:
            # COMMIT failures are outcome-ambiguous: PostgreSQL may have made
            # both the processing row and recovery run durable before the
            # connection dropped. Do not translate that into the safe-to-retry
            # persistence error used for a failed pre-commit insert.
            rollback_preserving_unknown_outcome(db)
            raise ApprovalOutcomeUnknownError(str(exc)) from exc

        # Publish only after the atomic state+tracking commit. The durable run
        # lets the watchdog recover a broker/process failure from this point.
        try:
            publish_workable_op(
                job_run_id=int(job_run_id),
                organization_id=int(organization_id),
                op_type=OP_APPROVE_DECISIONS,
                payload=payload,
            )
        except Exception as exc:
            # The processing rows + recovery run are already committed. Broker
            # acknowledgement can be lost after delivery, so a retry could race
            # the live worker even though this request surfaced an error.
            raise ApprovalOutcomeUnknownError(str(exc)) from exc
    else:
        # Release any locks taken while classifying request failures.
        db.commit()
    return {"job_run_id": job_run_id, "accepted": accepted, "failures": failures}


def enqueue_one(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    allow_engine_outdated: bool = False,
) -> dict:
    """Accept one decision without querying again after durable acceptance."""
    result = enqueue_batch(
        db,
        actor,
        organization_id=organization_id,
        decision_ids=[decision_id],
        note=note,
        workable_target_stage=workable_target_stage,
        allow_engine_outdated_decision_ids=(
            {int(decision_id)} if allow_engine_outdated else None
        ),
    )
    if int(decision_id) not in result["accepted"]:
        failure = next(
            (f for f in result["failures"] if f["decision_id"] == int(decision_id)),
            None,
        )
        raise HTTPException(
            status_code=(failure or {}).get("status_code", 409),
            detail=(failure or {}).get("error", "could not accept decision"),
        )
    return {
        "decision_id": int(decision_id),
        "accepted": True,
        "job_run_id": result["job_run_id"],
    }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    collect_side_effects: Optional[dict] = None,
    allow_engine_outdated: bool = False,
    commit_after_confirmed_movement: bool = False,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="approve is recruiter-only")

    identity = (
        db.query(
            AgentDecision.application_id,
            AgentDecision.role_id,
            AgentDecision.decision_type,
        )
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .one_or_none()
    )
    if identity is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    application_identity = db.query(CandidateApplication.role_id).filter(
        CandidateApplication.id == int(identity[0]),
        CandidateApplication.organization_id == int(organization_id),
    ).one_or_none()
    if application_identity is None:
        raise HTTPException(status_code=404, detail="decision application not found")
    if identity[1] is None or application_identity[0] is None:
        raise HTTPException(status_code=409, detail="decision role membership is unavailable")
    expected_decision_role_id = int(identity[1])
    expected_application_role_id = int(application_identity[0])
    requires_exclusive_organization_lock = str(identity[2] or "") == "send_assessment"
    # Lock Organization, then every acting/owner Role for shared-pool decisions.
    # Assessment creation later reserves capacity under an Organization UPDATE
    # lock, so take that exclusive mode up front instead of upgrading KEY SHARE.
    lock_organization_for_decision_resolution(
        db,
        organization_id=int(organization_id),
        exclusive=requires_exclusive_organization_lock,
    )
    locked_roles = lock_resolution_roles(
        db,
        organization_id=organization_id,
        role_ids=(expected_decision_role_id, expected_application_role_id),
    )
    if len(locked_roles) != len(
        {expected_decision_role_id, expected_application_role_id}
    ):
        raise HTTPException(status_code=409, detail="decision role is unavailable")
    # Then Application→Decision serializes sibling terminal actions.
    application_lock = db.query(CandidateApplication).filter(
        CandidateApplication.id == int(identity[0]),
        CandidateApplication.organization_id == int(organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        application_lock = application_lock.with_for_update()
    app = application_lock.populate_existing().one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="decision application not found")

    # C2: row-level lock on the decision. Two recruiters approving the
    # same pending decision in the same second would otherwise both pass
    # the ``status != "pending"`` check and both dispatch the underlying
    # action — double Workable POST, double pipeline-stage event, double
    # candidate email. ``with_for_update`` blocks the second request
    # until the first commits; it then sees ``status='approved'`` and
    # 409s. SQLite tests ignore the row lock (no NOWAIT support) which
    # is fine since the race only matters in real production traffic.
    decision_query = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        decision_query = decision_query.with_for_update()
    decision = decision_query.populate_existing().first()
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if (
        int(decision.application_id) != int(app.id)
        or decision.role_id is None
        or int(decision.role_id) != expected_decision_role_id
        or app.role_id is None
        or int(app.role_id) != expected_application_role_id
    ):
        raise HTTPException(status_code=409, detail="Decision job membership changed; refresh and try again.")
    if (
        decision.decision_type == "send_assessment"
        and not requires_exclusive_organization_lock
    ):
        # The action changed after the pre-lock identity read. Never enter the
        # assessment path under the weaker organization guard.
        raise HTTPException(
            status_code=409,
            detail="Decision action changed; refresh and try again.",
        )
    # ``reverted_for_feedback`` is a taught-but-not-yet-resolved decision — the
    # corrected row can then be approved/overridden, so it stays actionable
    # alongside ``pending``. ``processing`` is accepted because the async
    # dispatch path flips the row pending→processing before enqueuing the
    # background task that calls run().
    if decision.status not in ("pending", "reverted_for_feedback", "processing"):
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not actionable",
        )

    # Re-check at the locked execution boundary. Acceptance and worker
    # execution are separate transactions; a score/criteria/CV change in
    # between must return the card to HITL without any candidate side effect.
    enforce_decision_approval_eligibility(
        db,
        decision,
        application=app,
        allow_engine_outdated=allow_engine_outdated,
    )

    metadata = {
        "agent_decision_id": int(decision.id),
        "agent_run_id": int(decision.agent_run_id) if decision.agent_run_id else None,
        "agent_reasoning": decision.reasoning,
        "model_version": decision.model_version,
        "prompt_version": decision.prompt_version,
        "acting_role_id": int(decision.role_id),
    }
    reason = (note or "").strip() or f"Approved agent recommendation #{decision.id}"
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    role = locked_roles[expected_decision_role_id]

    # "Did this approval freshly reject the candidate?" — gates the background
    # Workable disqualify so an already-rejected candidate isn't re-processed.
    # (Taali never emails the candidate; the ATS owns job comms.) Set in the
    # reject branch below.
    reject_notify = False

    if decision.decision_type == "advance_to_interview":
        advance_stage.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            to_stage="advanced",
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata=metadata,
        )
    elif decision.decision_type in _REJECT_DECISION_TYPES:
        prev_outcome = (
            getattr(app, "application_outcome", None) if app is not None else None
        )
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata={**metadata, "decision_type": decision.decision_type},
            defer_notify=True,
        )
        reject_notify = bool(
            app is not None
            and prev_outcome != "rejected"
            and getattr(app, "application_outcome", None) == "rejected"
        )
    elif decision.decision_type == "send_assessment":
        # Evidence (set when the agent queued the decision) may carry the
        # task_id / duration_minutes the agent picked. Fall back to the
        # send_assessment defaults when absent.
        ev = decision.evidence or {}
        send_result = send_assessment.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            role_id=int(decision.role_id),
            task_id=int(ev["task_id"]) if ev.get("task_id") is not None else None,
            duration_minutes=int(ev.get("duration_minutes") or 90),
        )
        # send_assessment can no-op (misconfigured / insufficient_credits /
        # blocked / already_exists). "queued" means a durable delivery intent
        # exists; provider success will perform the invited transition. Anything
        # else must NOT close the
        # decision as approved (it never sent), so raise a clear, actionable
        # error. The approve runner returns the decision to the queue with
        # this message instead of silently looping (mirrors the override path).
        send_status = getattr(send_result, "status", None)
        if send_status not in ("queued", "sent", "already_exists"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Couldn't send the assessment (status={send_status!r}): "
                    f"{getattr(send_result, 'detail', None) or 'no assessment was sent'}. "
                    "Link an assessment task to this role, or use Skip & advance instead."
                ),
            )
    elif decision.decision_type == "resend_assessment_invite":
        ev = decision.evidence or {}
        assessment_id = ev.get("assessment_id")
        if assessment_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"resend_assessment_invite decision {decision.id} is missing "
                    "evidence.assessment_id — cannot dispatch."
                ),
            )
        resend_assessment_invite.run(
            db,
            actor,
            organization_id=organization_id,
            assessment_id=int(assessment_id),
        )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"unknown decision_type={decision.decision_type!r}",
        )

    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = actor.user_id
    decision.resolution_note = note
    decision.human_disposition = "approved"

    if role is not None:
        try:
            from ..agent_runtime import calibration

            calibration.save(db, role=role, updates={"decisions_approved": 1})
        except Exception:
            import logging

            logging.getLogger("taali.actions.approve_decision").exception(
                "approval calibration counter failed (decision_id=%s)", decision.id
            )

    # Realised-outcome learning. The pipeline transition fired by the action
    # dispatch above already ran the outcome_learning hooks, but at that point
    # this decision was still ``processing`` — so the hooks' approved-decision
    # lookup found nothing. Now that it's stamped ``approved`` we record the
    # outcome against it directly. Best-effort: calibration bookkeeping must
    # never block an approval.
    if app is not None:
        try:
            from ..agent_runtime import outcome_learning

            outcome_learning.record_outcome_for_approved_decision(
                db, decision=decision, application=app,
            )
        except Exception:
            import logging

            logging.getLogger("taali.actions.approve_decision").exception(
                "realised-outcome recording failed (decision_id=%s)", decision.id,
            )

    # Best-effort side effects (Workable writeback + recruiter-action graph
    # episode). By default they run inline (agent runs, tests). When the
    # caller passes ``collect_side_effects`` — the approve / bulk-approve
    # routes do — we skip the inline work and hand the route what it needs to
    # enqueue the deferred Celery task post-commit, so the recruiter's click
    # returns immediately instead of waiting on slow Workable / LLM calls.
    if collect_side_effects is None:
        apply_decision_side_effects(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            note=note,
            workable_target_stage=workable_target_stage,
            reject_notify=reject_notify,
            commit_after_confirmed_movement=commit_after_confirmed_movement,
        )
    else:
        collect_side_effects["reject_notify"] = reject_notify

    return decision
