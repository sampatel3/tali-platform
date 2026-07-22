"""Generic serialized runner for ALL Workable write-backs.

Every recruiter/system action that writes to Workable (decision approve / bulk
/ override, hand-back stage move, and manual outcome change) routes through
here instead of calling Workable inline on the request thread. Legacy note jobs
are retained only as fail-closed tombstones. The goals are uniform:

- **Serialized per org** — one Workable conversation per org at a time (shared
  ``_acquire_workable_org_mutex``), so a burst of actions can't breach the rate
  limit.
- **Background + tracked** — each request becomes a ``BackgroundJobRun`` (kind
  ``decision_batch`` for Hub batches, ``workable_op`` for single ops) visible
  in Settings → Background jobs.
- **Retried + never dropped** — a transient failure (429/5xx → ``api_error``)
  retries with backoff; on exhaustion the op surfaces (re-queues the decision /
  records a ``workable_*_failed`` event) instead of silently vanishing.

This module holds the op handlers + the dispatch (``execute_op`` /
``surface_op_failure``). The Celery shell that owns the mutex, the job-run
bookkeeping and the retry/backoff lives in
``app.tasks.workable_tasks.run_workable_op_task``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from .workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)
from .workable_stage_matching import same_workable_stage, workable_stage_aliases

logger = logging.getLogger("taali.workable_op_runner")


class AtsJobRunPersistenceError(RuntimeError):
    """Raised when an ATS operation cannot be durably tracked before publish."""

    def __init__(self, op_type: str):
        self.op_type = str(op_type or "unknown")
        super().__init__(
            f"could not persist BackgroundJobRun for ATS operation {self.op_type!r}"
        )


# Op type constants — also the dispatch keys.
OP_APPROVE_DECISIONS = "approve_decisions"
OP_OVERRIDE_DECISION = "override_decision"
OP_MOVE_STAGE = "move_stage"
OP_MANUAL_OUTCOME = "manual_outcome"
OP_POST_NOTE = "post_note"

# ``post_note`` is retained only as a tombstone for durable jobs queued before
# standalone ATS notes were retired.  It always fails closed.  Related-role
# movement notes use a private helper that is called only after a confirmed
# outbound movement, so a forged queue payload cannot turn this legacy op into
# a write primitive.
NOTE_PURPOSE_RELATED_ROLE_MOVEMENT = "related_role_movement"


# Override actions whose Workable write is a safely-replayable state change
# (disqualify / stage move) — gated so a failure re-queues. send_assessment /
# hold are NOT gated (email side-effect / no-op).
_GATED_OVERRIDE_ACTIONS = frozenset({"reject", "advance", "skip_assessment_advance"})
# Decision types whose approval Workable write is safely replayable (gated).
_GATED_DECISION_TYPES = frozenset({"reject", "skip_assessment_reject", "advance_to_interview"})


def _recruiter_actor(user_id: int | None):
    from ..actions.types import ACTOR_RECRUITER, Actor

    return Actor(type=ACTOR_RECRUITER, user_id=int(user_id) if user_id else None)


def _active_ats_label(
    db: Session, organization_id: int, payload: dict | None = None
) -> tuple[str, str]:
    """Return ``(slug, label)`` for provider-aware audit/error wording."""
    from ..components.integrations.resolver import (
        resolve_application_ats_provider,
        resolve_ats_provider,
    )
    from ..models.organization import Organization

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    provider = None
    application_id = (payload or {}).get("application_id")
    if application_id is not None:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
            )
            .first()
        )
        provider = resolve_application_ats_provider(org, db, app)
        if provider is None and app is not None and app.bullhorn_job_submission_id:
            return "bullhorn", "Bullhorn"
    if provider is None:
        provider = resolve_ats_provider(org, db)
    slug = str(getattr(provider, "ats", "") or "").lower()
    if slug == "bullhorn":
        return "bullhorn", "Bullhorn"
    if slug == "workable":
        return "workable", "Workable"
    # This runner predates provider routing and disconnected/local fixtures can
    # still inject its legacy Workable errors. Bullhorn is always explicit via
    # the resolver; preserve Workable wording for the fallback contract.
    return "workable", "Workable"


def _route_bullhorn_op(
    db: Session, organization_id: int, payload: dict, *, handler_name: str
) -> dict | None:
    """Delegate an ATS-write op to the Bullhorn handler when the org routes to
    Bullhorn; return ``None`` to fall through to the Workable body.

    This is the "op_runner resolves provider through the PR-1 seam" hook (build
    plan §6): the shared shell (mutex, retry, bookkeeping, surface-on-failure) is
    unchanged — only the ATS-write body differs by provider. A no-op (returns
    None) when ``BULLHORN_ENABLED`` is off or the org isn't Bullhorn-connected, so
    the Workable path is untouched for every non-Bullhorn org.
    """
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider
    from ..models.organization import Organization

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        return None
    application_id = int(payload["application_id"])
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    provider = resolve_application_ats_provider(org, db, app)
    if not isinstance(provider, BullhornProvider):
        # A Bullhorn-linked application must never fall through to the Workable
        # handler merely because Bullhorn was disabled/disconnected after the
        # op was queued. Surface the divergence through the shared failure rail.
        if app.bullhorn_job_submission_id and not app.workable_candidate_id:
            raise WorkableWritebackError(
                action=handler_name,
                code="not_configured",
                message=(
                    "Bullhorn is disabled or disconnected for this linked "
                    "application"
                ),
                retriable=False,
            )
        return None
    from ..components.integrations.bullhorn import op_handlers

    handler = getattr(op_handlers, handler_name)
    return handler(db, org, app, payload)


# ---------------------------------------------------------------------------
# Op handlers. Each takes (db, organization_id, payload) and returns a result
# dict. Single-op handlers may raise WorkableWritebackError (the Celery shell
# turns a retriable one into a retry, and a terminal one into
# ``surface_op_failure``). The batch handler is self-contained: it commits per
# decision and never raises, so one bad row can't fail the whole batch.
# ---------------------------------------------------------------------------


def _requeue_decision(db: Session, decision_id: int, organization_id: int, *, note: str) -> None:
    """Return a processing decision to the Hub queue (status → pending)."""
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None or decision.status != "processing":
        return
    decision.status = "pending"
    decision.resolution_note = (note or "")[:500] or None
    db.commit()


def compensate_override_delivery_loss(
    *,
    organization_id: int,
    decision_id: int,
    job_run_id: int | None,
    reason: str,
    error_code: str,
    allowed_run_statuses: tuple[str, ...] = ("queued",),
    stale_before: datetime | None = None,
) -> dict:
    """Fail a non-replayable override delivery and return its decision to HITL.

    Override operations can contain email or other non-idempotent side effects,
    so a lost broker delivery must never be replayed from a stored payload.  The
    ``BackgroundJobRun`` is instead the coordination row: lock it, prove it is
    still in an eligible non-terminal state, fail it, and requeue only a decision
    that is still ``processing``.  A worker that already terminalized the run is
    left untouched; a worker that won the ``queued -> running`` race is likewise
    left alone by the immediate (queued-only) compensator.

    ``stale_before`` is used by the Beat watchdog.  For a running retry chain its
    latest ``last_started_at`` receipt is authoritative, so a healthy delayed
    retry is not reaped merely because the run row itself is old.
    """
    from ..models.background_job_run import JOB_KIND_WORKABLE_OP, BackgroundJobRun
    from ..platform.database import SessionLocal

    def _aware(value: object) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    db = SessionLocal()
    try:
        run = None
        if job_run_id is not None:
            run = (
                db.query(BackgroundJobRun)
                .filter(
                    BackgroundJobRun.id == int(job_run_id),
                    BackgroundJobRun.organization_id == int(organization_id),
                    BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
                )
                .with_for_update()
                .one_or_none()
            )
            if run is not None:
                counters = run.counters if isinstance(run.counters, dict) else {}
                if str(counters.get("op_type") or "") != OP_OVERRIDE_DECISION:
                    return {
                        "status": "wrong_op_type",
                        "job_run_id": int(run.id),
                        "requeued": False,
                    }
                if run.finished_at is not None or run.status not in allowed_run_statuses:
                    return {
                        "status": "already_terminal_or_active",
                        "job_run_id": int(run.id),
                        "run_status": run.status,
                        "requeued": False,
                    }
                if stale_before is not None:
                    reference = _aware(run.started_at)
                    if run.status == "running":
                        reference = _aware(counters.get("last_started_at")) or reference
                    if reference is not None and reference > stale_before:
                        return {
                            "status": "not_stale",
                            "job_run_id": int(run.id),
                            "run_status": run.status,
                            "requeued": False,
                        }

        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id == int(decision_id),
                AgentDecision.organization_id == int(organization_id),
            )
            .with_for_update()
            .one_or_none()
        )
        requeued = bool(decision is not None and decision.status == "processing")
        if requeued:
            decision.status = "pending"
            decision.resolution_note = (reason or "")[:500] or None

        now = datetime.now(timezone.utc)
        if run is not None:
            counters = dict(run.counters or {})
            counters.update(
                {
                    "op_type": OP_OVERRIDE_DECISION,
                    "decision_id": int(decision_id),
                    "failure_code": str(error_code or "delivery_lost")[:100],
                }
            )
            run.counters = counters
            run.status = "failed"
            run.finished_at = now
            run.error = str(reason or "ATS override delivery was lost")[:2000]

        db.commit()
        return {
            "status": "compensated",
            "job_run_id": int(run.id) if run is not None else None,
            "decision_id": int(decision_id),
            "requeued": requeued,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _op_approve_decisions(db: Session, organization_id: int, payload: dict) -> dict:
    """Drain a batch of approved decisions sequentially (self-contained).

    Each decision's local change commits only after its Workable write
    confirms (gated types); a decision whose writeback fails is returned to the
    queue and the batch keeps going.
    """
    from ..actions import approve_decision as approve_decision_action
    from .decision_resolution_provenance import requested_target_stage

    ids = [int(x) for x in (payload.get("decision_ids") or [])]
    note = payload.get("note")
    workable_target_stage = payload.get("workable_target_stage")
    # Per-role advance-stage map (role_id string → Workable stage). A bulk
    # approve spanning roles carries one stage per role; the single fallback
    # above covers enqueue_one / single approve.
    workable_target_stages = payload.get("workable_target_stages") or {}
    engine_force_ids = {
        int(value)
        for value in (payload.get("allow_engine_outdated_decision_ids") or [])
    }
    actor = _recruiter_actor(payload.get("user_id"))
    _provider_slug, provider_label = _active_ats_label(db, organization_id)

    counters = {"total": len(ids), "succeeded": 0, "requeued": 0, "failed": 0, "skipped": 0}
    for decision_id in ids:
        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id == decision_id,
                AgentDecision.organization_id == organization_id,
            )
            .first()
        )
        if decision is None or decision.status != "processing":
            # Already resolved / requeued elsewhere (e.g. approved by an earlier
            # overlapping batch) — idempotent skip. Counted separately so a run
            # with succeeded < total reads as "X approved, Y already resolved"
            # instead of looking like a partial failure.
            counters["skipped"] += 1
            continue
        payload_stage = (
            workable_target_stages.get(str(decision.role_id))
            if decision.role_id is not None
            else None
        ) or workable_target_stage
        stage = requested_target_stage(decision, payload_stage)
        gated = decision.decision_type in _GATED_DECISION_TYPES
        try:
            if gated:
                with strict_workable_writes():
                    approve_decision_action.run(
                        db,
                        actor,
                        organization_id=int(organization_id),
                        decision_id=int(decision_id),
                        note=note,
                        workable_target_stage=stage,
                        allow_engine_outdated=decision_id in engine_force_ids,
                        commit_after_confirmed_movement=True,
                    )
            else:
                approve_decision_action.run(
                    db,
                    actor,
                    organization_id=int(organization_id),
                    decision_id=int(decision_id),
                    note=note,
                    workable_target_stage=stage,
                    allow_engine_outdated=decision_id in engine_force_ids,
                    commit_after_confirmed_movement=True,
                )
            db.commit()
            counters["succeeded"] += 1
        except WorkableWritebackError as exc:
            db.rollback()
            _requeue_decision(
                db,
                decision_id,
                organization_id,
                note=(
                    f"Returned to queue: {provider_label} didn't accept the "
                    f"update. {exc.message}"
                ),
            )
            counters["requeued"] += 1
        except HTTPException as exc:
            # A deterministic, expected action failure (e.g. send_assessment on a
            # role with no linked task, missing resend evidence). Re-queue with
            # the clear message so the recruiter sees *why* on the card and can
            # act, rather than a generic "unexpected error".
            db.rollback()
            _requeue_decision(
                db,
                decision_id,
                organization_id,
                note=f"Returned to queue: {exc.detail}",
            )
            counters["requeued"] += 1
        except Exception:  # noqa: BLE001 — one bad row must not halt the batch
            db.rollback()
            logger.exception("approve_decisions: unexpected error decision_id=%s", decision_id)
            _requeue_decision(
                db,
                decision_id,
                organization_id,
                note="Returned to queue after an unexpected error. Please try approving it again.",
            )
            counters["failed"] += 1
    return counters


def _op_override_decision(db: Session, organization_id: int, payload: dict) -> dict:
    """Apply a single recruiter override, gated on Workable for state-change
    actions (reject / advance / skip-advance). Raises on Workable failure so
    the shell retries / re-queues."""
    from ..actions import override_decision as override_decision_action
    from .decision_resolution_provenance import requested_target_stage

    decision_id = int(payload["decision_id"])
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None or decision.status != "processing":
        return {"status": "skipped", "reason": "not_processing", "decision_id": decision_id}

    actor = _recruiter_actor(payload.get("user_id"))
    override_action = payload.get("override_action")
    target_stage = requested_target_stage(
        decision, payload.get("workable_target_stage")
    )
    gated = override_action in _GATED_OVERRIDE_ACTIONS

    def _run():
        override_decision_action.run(
            db,
            actor,
            organization_id=int(organization_id),
            decision_id=decision_id,
            override_action=override_action,
            note=payload.get("note"),
            workable_target_stage=target_stage,
            commit_after_confirmed_movement=True,
        )

    if gated:
        with strict_workable_writes():
            _run()
    else:
        _run()
    db.commit()
    return {"status": "ok", "decision_id": decision_id}


def _record_workable_movement_note_failure(
    db: Session,
    *,
    app: CandidateApplication,
    application_id: int,
    role_id: int | None = None,
    ats_application_id: int | None = None,
) -> None:
    """Best-effort audit after a confirmed move; never invalidates the move.

    The movement transaction is committed before this helper is called.  Clear
    any failed note transaction first, then make the audit event its own
    best-effort transaction.  A broken audit write must not bubble into the op
    runner and replay an already-confirmed provider movement.
    """
    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
    )

    try:
        db.rollback()
    except Exception:
        logger.exception(
            "could not reset session after related-role Workable note failure "
            "application_id=%s",
            application_id,
        )
        return
    try:
        append_application_event(
            db,
            app=app,
            role_id=role_id,
            event_type="workable_movement_note_failed",
            actor_type="system",
            reason=(
                "The related-role movement was confirmed, but its Workable "
                "summary was not posted."
            ),
            metadata={
                "ats": "workable",
                "action": "related_role_movement_note",
                **(
                    {"acting_role_id": int(role_id)}
                    if role_id is not None
                    else {}
                ),
                **(
                    {"ats_application_id": int(ats_application_id)}
                    if ats_application_id is not None
                    else {}
                ),
            },
        )
        db.commit()
    except Exception:
        logger.exception(
            "could not persist related-role Workable note failure "
            "application_id=%s",
            application_id,
        )
        try:
            db.rollback()
        except Exception:
            logger.exception(
                "could not roll back related-role Workable note failure "
                "application_id=%s",
                application_id,
            )


def _post_confirmed_related_role_workable_note(
    db: Session,
    organization_id: int,
    *,
    app: CandidateApplication,
    event_app: CandidateApplication | None = None,
    owner_role: Any,
    acting_role: Any,
    user_id: int | None,
) -> dict:
    """Post the fixed related-role summary after a confirmed outbound move.

    This intentionally is not an op-runner handler and accepts no caller-owned
    body or purpose flag.  Its only call site is ``_op_move_stage`` after the
    provider movement has succeeded and been committed.
    """
    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
    )
    from ..domains.integrations_notifications.adapters import (
        build_workable_adapter,
    )
    from .sister_role_service import related_role_advance_note
    from .workable_actions_service import (
        resolve_workable_actor_member_id,
        workable_writeback_enabled,
    )

    if (
        acting_role is None
        or int(getattr(acting_role, "ats_owner_role_id", 0) or 0)
        != int(app.role_id or 0)
    ):
        return {
            "status": "skipped",
            "reason": "invalid_related_role",
            "application_id": int(app.id),
        }
    org = app.organization
    if org is None or int(org.id) != int(organization_id):
        return {
            "status": "skipped",
            "reason": "not_linked",
            "application_id": int(app.id),
        }
    if not app.workable_candidate_id or not workable_writeback_enabled(org):
        return {
            "status": "skipped",
            "reason": "not_linked_or_writeback_disabled",
            "application_id": int(app.id),
        }
    member_id = resolve_workable_actor_member_id(org, role=owner_role)
    if not member_id or not getattr(org, "workable_access_token", None):
        return {
            "status": "skipped",
            "reason": "not_configured",
            "application_id": int(app.id),
        }

    adapter = build_workable_adapter(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result = adapter.post_candidate_comment(
        candidate_id=str(app.workable_candidate_id),
        member_id=member_id,
        body=related_role_advance_note(acting_role, owner_role),
        trusted_role_values=tuple(
            value
            for value in (
                str(getattr(acting_role, "name", None) or "").strip(),
                str(getattr(owner_role, "name", None) or "").strip(),
            )
            if value
        ),
    )
    if not result.get("success"):
        raise WorkableWritebackError(
            action="note",
            code="api_error",
            message=str(result.get("error") or "note post failed"),
            retriable=True,
        )
    append_application_event(
        db,
        app=event_app or app,
        role_id=int(acting_role.id),
        event_type="workable_note_posted",
        actor_type="recruiter",
        actor_id=user_id,
        reason="Related-role movement summary posted to Workable",
        metadata={
            "acting_role_id": int(acting_role.id),
            "ats_application_id": int(app.id),
            "workable_candidate_id": app.workable_candidate_id,
        },
    )
    db.commit()
    return {"status": "ok", "application_id": int(app.id)}


def _is_workable_outbound_stage(role: Any, value: str | None) -> bool:
    """Whether a target is a cached/legacy post-Taali hand-off stage."""
    from ..domains.assessments_runtime.pipeline_service import (
        is_post_handover_workable_stage,
        map_legacy_status_to_pipeline,
    )

    aliases = workable_stage_aliases(role, value)
    if any(
        map_legacy_status_to_pipeline(alias)[0] == "advanced"
        and is_post_handover_workable_stage(alias)
        for alias in aliases
    ):
        return True
    stages = getattr(role, "workable_stages", None)
    for stage in stages if isinstance(stages, list) else []:
        if not isinstance(stage, dict):
            continue
        stage_aliases = {
            str(stage.get(key) or "").strip().casefold()
            for key in ("id", "slug", "name")
        }
        if aliases.intersection(stage_aliases) and str(
            stage.get("kind") or ""
        ).strip().casefold() in {"interview", "offer", "hired"}:
            return True
    return False


def _op_move_stage(db: Session, organization_id: int, payload: dict) -> dict:
    """Hand a candidate back to a Workable stage. Gated: Tali's stage advances
    only after the Workable move confirms."""
    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
        transition_stage,
    )
    from ..models.organization import Organization
    from ..models.role import Role
    from .related_role_action_service import (
        RelatedRoleActionContractError,
        related_role_ats_action_state,
        resolve_related_role_ats_action_context,
        transition_related_role_stage_action,
    )
    from .workable_actions_service import move_candidate_in_workable

    routed = _route_bullhorn_op(db, organization_id, payload, handler_name="run_move_stage")
    if routed is not None:
        return routed

    application_id = int(payload["application_id"])
    target_stage = str(payload.get("target_stage") or "").strip()
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None or not app.workable_candidate_id:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    role = db.query(Role).filter(Role.id == app.role_id).first() if app.role_id else None
    try:
        related_context = resolve_related_role_ats_action_context(
            db,
            organization_id=int(organization_id),
            ats_application=app,
            acting_role_id=payload.get("acting_role_id"),
            source_application_id=payload.get("role_application_id"),
        )
    except RelatedRoleActionContractError as exc:
        raise WorkableWritebackError(
            action="move",
            code="related_role_context_invalid",
            message=str(exc),
            retriable=False,
        ) from exc
    event_app = (
        related_context.source_application if related_context is not None else app
    )
    logical_role_id = (
        int(related_context.role.id)
        if related_context is not None
        else int(app.role_id)
    )

    def blocked_related_move(codes: list[str], *, reason_code: str) -> dict:
        append_application_event(
            db,
            app=event_app,
            role_id=logical_role_id,
            event_type="ats_writeback_restricted",
            actor_type="recruiter",
            actor_id=user_id,
            target_stage=target_stage,
            effect_status="blocked",
            reason="Related-role ATS move was blocked at execution time",
            metadata={
                "acting_role_id": logical_role_id,
                "ats_application_id": int(app.id),
                "provider": "workable",
                "op_type": OP_MOVE_STAGE,
                "restriction_codes": codes,
                "target_stage": target_stage,
            },
            idempotency_key=(
                f"ats_move_blocked:{payload.get('job_run_id')}"
                if payload.get("job_run_id") is not None
                else (
                    f"ats_move_blocked:workable:{event_app.id}:"
                    f"{logical_role_id}:{target_stage}:{reason_code}"
                )
            ),
        )
        db.commit()
        return {
            "status": "skipped",
            "reason": reason_code,
            "application_id": application_id,
        }

    def reconcile_confirmed_advance(*, already_at_target: bool) -> None:
        event_reason = (
            f"Already at the Workable hand-off stage: {target_stage}"
            if already_at_target
            else f"Handed back to Workable: {target_stage}"
        )
        event_key = (
            f"workable_handback_reconcile:{event_app.id}:{logical_role_id}:{target_stage}"
            if already_at_target
            else f"workable_handback:{event_app.id}:{logical_role_id}:{target_stage}"
        )
        if related_context is not None:
            transition_related_role_stage_action(
                db,
                application=event_app,
                acting_role_id=logical_role_id,
                to_stage="advanced",
                source="recruiter",
                actor_type="recruiter",
                actor_id=user_id,
                reason=event_reason,
                metadata={
                    "acting_role_id": logical_role_id,
                    "ats_application_id": int(app.id),
                    "workable_target_stage": target_stage,
                },
                idempotency_key=event_key,
            )
            return
        transition_stage(
            db,
            app=app,
            to_stage="advanced",
            source="recruiter",
            actor_type="recruiter",
            actor_id=user_id,
            reason=event_reason,
            metadata={"workable_target_stage": target_stage},
            idempotency_key=event_key,
        )

    exact_target = same_workable_stage(role, app.workable_stage, target_stage)
    if related_context is not None:
        action_state = related_role_ats_action_state(related_context)
        local_codes = list(action_state["local_codes"])
        if local_codes == ["role_pipeline_stage_advanced"]:
            return {
                "status": "skipped",
                "reason": "role_already_advanced",
                "application_id": application_id,
            }
        if local_codes:
            return blocked_related_move(
                local_codes,
                reason_code="related_role_application_resolved",
            )
        hard_codes = list(action_state["hard_restriction_codes"])
        if hard_codes:
            return blocked_related_move(
                hard_codes,
                reason_code="related_role_ats_write_restricted",
            )
        if bool(action_state["post_handover"]) and not exact_target:
            # The external ATS has already passed Taali's hand-off boundary.
            # Reconcile only this role's local stage; never replay a provider
            # write or mutate the owner role.
            reconcile_confirmed_advance(already_at_target=True)
            db.commit()
            return {
                "status": "skipped",
                "reason": "already_at_target",
                "application_id": application_id,
            }

    if exact_target:
        if _is_workable_outbound_stage(role, target_stage):
            reconcile_confirmed_advance(already_at_target=True)
            db.commit()
        return {
            "status": "skipped",
            "reason": "already_at_target",
            "application_id": application_id,
        }

    with strict_workable_writes():
        move_result = move_candidate_in_workable(
            org=org,
            candidate_id=str(app.workable_candidate_id),
            target_stage=target_stage,
            role=role,
        )
    if not move_result.get("success"):
        return {
            "status": "skipped",
            "reason": str(move_result.get("code") or "move_not_confirmed"),
            "application_id": application_id,
        }
    app.workable_stage = target_stage
    # Local-write-wins: stamp so the candidate sync won't revert this fresh move.
    app.workable_stage_local_write_at = datetime.now(timezone.utc)
    append_application_event(
        db,
        app=event_app,
        role_id=logical_role_id,
        event_type="workable_moved",
        actor_type="recruiter",
        actor_id=user_id,
        reason=reason or "Recruiter handed candidate back to Workable",
        target_stage=target_stage,
        effect_status="confirmed",
        metadata={
            "target_stage": target_stage,
            "workable_candidate_id": app.workable_candidate_id,
            "ats_application_id": int(app.id),
            **(
                {"acting_role_id": logical_role_id}
                if related_context is not None
                else {}
            ),
        },
    )
    confirmed_outbound_advance = _is_workable_outbound_stage(role, target_stage)
    if confirmed_outbound_advance:
        reconcile_confirmed_advance(already_at_target=False)
    # The confirmed provider movement is the critical operation. Persist it
    # before attempting the optional related-role attribution so a note error
    # can never replay or roll back an already-completed ATS stage move.
    db.commit()
    acting_role_id = payload.get("acting_role_id")
    if confirmed_outbound_advance and acting_role_id is not None:
        acting_role = db.get(Role, int(acting_role_id))
        if (
            acting_role is not None
            and int(acting_role.ats_owner_role_id or 0) == int(app.role_id)
        ):
            try:
                note_result = _post_confirmed_related_role_workable_note(
                    db,
                    organization_id,
                    app=app,
                    event_app=event_app,
                    owner_role=role,
                    acting_role=acting_role,
                    user_id=user_id,
                )
                if note_result.get("status") != "ok":
                    raise WorkableWritebackError(
                        action="note",
                        code=str(note_result.get("reason") or "note_skipped"),
                        message="Related-role movement note was not posted",
                        retriable=False,
                    )
            except Exception as exc:
                # This message is deliberately best-effort. Record the miss for
                # operators, but return success for the confirmed stage move.
                logger.warning(
                    "related-role Workable movement note failed after confirmed move "
                    "application_id=%s error_type=%s",
                    application_id,
                    type(exc).__name__,
                )
                _record_workable_movement_note_failure(
                    db,
                    app=event_app,
                    application_id=application_id,
                    role_id=logical_role_id,
                    ats_application_id=int(app.id),
                )
    return {"status": "ok", "application_id": application_id}


def _op_manual_outcome(db: Session, organization_id: int, payload: dict) -> dict:
    """Mirror a recruiter's manual outcome change to Workable (disqualify on
    reject, revert on re-open). The local outcome already committed in the
    route — this is the (retried) Workable writeback only."""
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from ..models.organization import Organization
    from ..models.role import Role
    from .workable_actions_service import (
        disqualify_candidate_in_workable,
        revert_candidate_disqualification_in_workable,
    )

    routed = _route_bullhorn_op(db, organization_id, payload, handler_name="run_manual_outcome")
    if routed is not None:
        return routed

    application_id = int(payload["application_id"])
    target_outcome = payload.get("target_outcome")
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None or not app.workable_candidate_id:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    role = db.query(Role).filter(Role.id == app.role_id).first() if app.role_id else None

    with strict_workable_writes():
        if target_outcome == "open":
            revert_candidate_disqualification_in_workable(org=org, app=app, role=role)
            event_type = "workable_reverted"
        else:
            disqualify_candidate_in_workable(org=org, app=app, role=role, reason=reason)
            event_type = "workable_disqualified"
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type="recruiter",
        actor_id=user_id,
        reason=reason or "Workable outcome synced",
        metadata={"workable_candidate_id": app.workable_candidate_id, "target_outcome": target_outcome},
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def _op_post_note(db: Session, organization_id: int, payload: dict) -> dict:
    """Fail closed for every standalone/legacy ATS-note payload.

    Even a payload forged with the old related-role purpose cannot write.  The
    real movement summary is composed and posted only inside the confirmed move
    handler via ``_post_confirmed_related_role_workable_note``.
    """
    application_id = int(payload["application_id"])
    return {
        "status": "skipped",
        "reason": "standalone_ats_notes_disabled",
        "application_id": application_id,
    }


_HANDLERS: dict[str, Callable[[Session, int, dict], dict]] = {
    OP_APPROVE_DECISIONS: _op_approve_decisions,
    OP_OVERRIDE_DECISION: _op_override_decision,
    OP_MOVE_STAGE: _op_move_stage,
    OP_MANUAL_OUTCOME: _op_manual_outcome,
    OP_POST_NOTE: _op_post_note,
}


def _workable_op_run_spec(
    *,
    organization_id: int,
    op_type: str,
    payload: dict,
    scope_id: int | None = None,
    job_kind: str | None = None,
    counters: dict | None = None,
) -> tuple[str, int, dict, str]:
    """Build the durable tracking-row fields for one ATS operation."""
    import json

    from ..models.background_job_run import JOB_KIND_DECISION_BATCH, JOB_KIND_WORKABLE_OP
    from ..platform.config import settings
    from ..platform.secrets import encrypt_text

    kind = job_kind or (
        JOB_KIND_DECISION_BATCH if op_type == OP_APPROVE_DECISIONS else JOB_KIND_WORKABLE_OP
    )
    replay_safe = op_type in {OP_MOVE_STAGE, OP_MANUAL_OUTCOME}
    run_counters = dict(counters or {"op_type": op_type})
    run_counters["op_type"] = op_type
    if op_type == OP_OVERRIDE_DECISION:
        # Deliberately persist only the non-secret coordination key, never the
        # override payload.  A watchdog can return the decision to HITL, but it
        # cannot replay a potentially non-idempotent recruiter action.
        run_counters["decision_id"] = int(payload["decision_id"])
    if replay_safe:
        run_counters["recovery_payload"] = encrypt_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            settings.SECRET_KEY,
        )
    return (
        kind,
        int(scope_id if scope_id is not None else organization_id),
        run_counters,
        "dispatching" if replay_safe else "queued",
    )


def persist_workable_op_run(
    db: Session,
    *,
    organization_id: int,
    op_type: str,
    payload: dict,
    scope_id: int | None = None,
    job_kind: str | None = None,
    counters: dict | None = None,
) -> int:
    """Add durable ATS tracking to the caller's current transaction.

    This deliberately does not commit. Callers that also mutate tracked state
    can commit both changes atomically before publishing the broker task.
    """
    from .background_job_runs import SCOPE_KIND_ORG, add_run

    kind, resolved_scope_id, run_counters, run_status = _workable_op_run_spec(
        organization_id=int(organization_id),
        op_type=op_type,
        payload=payload,
        scope_id=scope_id,
        job_kind=job_kind,
        counters=counters,
    )
    try:
        return add_run(
            db,
            kind=kind,
            scope_kind=SCOPE_KIND_ORG,
            scope_id=resolved_scope_id,
            organization_id=int(organization_id),
            counters=run_counters,
            status=run_status,
        )
    except Exception as exc:
        raise AtsJobRunPersistenceError(op_type) from exc


def publish_workable_op(
    *,
    job_run_id: int,
    organization_id: int,
    op_type: str,
    payload: dict,
) -> int:
    """Publish an ATS operation only after its tracking row is committed."""
    from .background_job_runs import mark_dispatched
    from ..tasks.assessment_tasks import mark_workable_op_pending
    from ..tasks.workable_tasks import run_workable_op_task

    replay_safe = op_type in {OP_MOVE_STAGE, OP_MANUAL_OUTCOME}
    delivery_payload = {**payload, "job_run_id": int(job_run_id)}

    # Tell the periodic Workable syncs to yield the per-org mutex so this
    # user-facing write isn't starved behind a long candidate sync.
    mark_workable_op_pending(int(organization_id))
    try:
        run_workable_op_task.apply_async(
            kwargs={
                "job_run_id": job_run_id,
                "organization_id": int(organization_id),
                "op_type": op_type,
                "payload": delivery_payload,
            }
        )
    except Exception as exc:
        if op_type == OP_OVERRIDE_DECISION:
            reason = (
                "Returned to queue: the ATS override could not be delivered to "
                "the background worker. No ATS side effect was replayed; review "
                "the decision and try again."
            )
            outcome = compensate_override_delivery_loss(
                organization_id=int(organization_id),
                decision_id=int(payload["decision_id"]),
                job_run_id=job_run_id,
                reason=reason,
                error_code="initial_queue_unavailable",
                # If an ambiguous broker response already reached a worker and
                # it won the running claim, do not race or undo that live task.
                allowed_run_statuses=("queued",),
            )
            logger.error(
                "ATS override broker kick failed; compensation status=%s "
                "run_id=%s decision_id=%s error_type=%s",
                outcome.get("status"),
                job_run_id,
                payload.get("decision_id"),
                type(exc).__name__,
            )
            if outcome.get("status") in {
                "compensated",
                "already_terminal_or_active",
            }:
                return job_run_id
        if not replay_safe:
            raise
        # The durable dispatching row is the outbox. Beat will replay this
        # idempotent status operation; the request can return the already-
        # committed local state without losing the remote update.
        logger.error(
            "ATS op broker kick failed; durable recovery will replay "
            "run_id=%s error_type=%s",
            job_run_id,
            type(exc).__name__,
        )
    else:
        if replay_safe:
            mark_dispatched(job_run_id)
    return job_run_id


def enqueue_workable_op(
    *,
    organization_id: int,
    op_type: str,
    payload: dict,
    scope_id: int | None = None,
    job_kind: str | None = None,
    counters: dict | None = None,
) -> int:
    """Record a BackgroundJobRun and enqueue the serialized runner task.

    Returns the durable job_run_id. No ATS task is published unless that row was
    persisted first, so every accepted operation has a meter and poll handle.
    Callers that need their own state change to be atomic with this row should
    use :func:`persist_workable_op_run`, commit, then call
    :func:`publish_workable_op`.
    """
    from .background_job_runs import SCOPE_KIND_ORG, create_run

    kind, resolved_scope_id, run_counters, run_status = _workable_op_run_spec(
        organization_id=int(organization_id),
        op_type=op_type,
        payload=payload,
        scope_id=scope_id,
        job_kind=job_kind,
        counters=counters,
    )
    job_run_id = create_run(
        kind=kind,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=resolved_scope_id,
        organization_id=int(organization_id),
        counters=run_counters,
        status=run_status,
    )
    if (
        isinstance(job_run_id, bool)
        or not isinstance(job_run_id, int)
        or job_run_id <= 0
    ):
        # ``create_run`` is intentionally best-effort for ordinary background
        # bookkeeping, but ATS writes require durable tracking. Fail before the
        # broker publish so a provider side effect can never run unmetered.
        raise AtsJobRunPersistenceError(op_type)
    return publish_workable_op(
        job_run_id=job_run_id,
        organization_id=int(organization_id),
        op_type=op_type,
        payload=payload,
    )


def execute_op(db: Session, *, organization_id: int, op_type: str, payload: dict) -> dict:
    handler = _HANDLERS.get(op_type)
    if handler is None:
        raise ValueError(f"unknown workable op_type={op_type!r}")
    return handler(db, int(organization_id), payload)


def surface_op_failure(
    db: Session, *, organization_id: int, op_type: str, payload: dict, error: WorkableWritebackError
) -> None:
    """Op-specific terminal-failure surfacing after retries are exhausted (or a
    non-retriable failure). Best-effort; never raises. Each op leaves a visible
    trail so a dropped Workable write is never silent."""
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    provider_slug, provider_label = _active_ats_label(
        db, int(organization_id), payload
    )
    note = (
        f"{provider_label} didn't accept the update after several tries. "
        f"{error.message}"
    )
    try:
        if op_type == OP_OVERRIDE_DECISION:
            _requeue_decision(db, int(payload["decision_id"]), int(organization_id), note=note)
            return
        if op_type == OP_APPROVE_DECISIONS:
            # The approve batch never ran (e.g. lock timeout) — return every
            # decision to the queue. Its payload carries ``decision_ids``, not
            # an ``application_id``, so without this the rows were stranded in
            # 'processing' forever (no approver, never completed).
            for d_id in (payload.get("decision_ids") or []):
                _requeue_decision(db, int(d_id), int(organization_id), note=note)
            return
        application_id = payload.get("application_id")
        if application_id is None:
            return
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
            )
            .first()
        )
        if app is None:
            return
        event_app = app
        logical_role_id = int(app.role_id)
        acting_role_id = payload.get("acting_role_id")
        role_application_id = payload.get("role_application_id")
        if op_type == OP_MOVE_STAGE and acting_role_id is not None:
            from ..models.role import ROLE_KIND_SISTER, Role

            acting_role = (
                db.query(Role)
                .filter(
                    Role.id == int(acting_role_id),
                    Role.organization_id == int(organization_id),
                    Role.role_kind == ROLE_KIND_SISTER,
                )
                .one_or_none()
            )
            source_app = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.id
                    == int(role_application_id or application_id),
                    CandidateApplication.organization_id == int(organization_id),
                    CandidateApplication.candidate_id == int(app.candidate_id),
                )
                .one_or_none()
            )
            if acting_role is not None and source_app is not None:
                event_app = source_app
                logical_role_id = int(acting_role.id)
        if op_type == OP_MANUAL_OUTCOME and provider_slug == "bullhorn":
            from .ats_writeback_state import set_outcome_writeback_state

            set_outcome_writeback_state(
                app,
                provider="bullhorn",
                status="failed",
                target_outcome=str(payload.get("target_outcome") or ""),
                error_code=error.code,
            )
        event_prefix = provider_slug if provider_slug in {"workable", "bullhorn"} else "ats"
        event_type = {
            OP_MOVE_STAGE: f"{event_prefix}_move_stage_failed",
            OP_MANUAL_OUTCOME: f"{event_prefix}_writeback_failed",
            OP_POST_NOTE: f"{event_prefix}_writeback_failed",
        }.get(op_type, f"{event_prefix}_writeback_failed")
        append_application_event(
            db,
            app=event_app,
            role_id=logical_role_id,
            event_type=event_type,
            actor_type="system",
            reason=note,
            target_stage=(
                str(
                    payload.get("target_stage")
                    or payload.get("target_intent")
                    or ""
                ).strip()
                or None
            ),
            effect_status="failed",
            idempotency_key=(
                f"ats_op_failure:{payload.get('job_run_id')}"
                if payload.get("job_run_id") is not None
                else (
                    f"ats_op_failure:{op_type}:{event_app.id}:"
                    f"{logical_role_id}:{error.code}"
                )
            ),
            metadata={
                "op_type": op_type,
                "code": error.code,
                "source": "workable_op_runner",
                "ats": provider_slug,
                "target_stage": (
                    payload.get("target_stage") or payload.get("target_intent")
                ),
                **(
                    {
                        "acting_role_id": int(acting_role_id),
                        "ats_application_id": int(app.id),
                    }
                    if acting_role_id is not None
                    else {}
                ),
            },
        )
        db.commit()
    except Exception:  # pragma: no cover — surfacing must never raise
        logger.exception("surface_op_failure raised for op_type=%s", op_type)
        try:
            db.rollback()
        except Exception:
            pass
