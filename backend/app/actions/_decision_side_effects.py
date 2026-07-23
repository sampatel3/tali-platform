"""Best-effort side effects after a recruiter resolves an ``AgentDecision``.

Centralizes the three slow, best-effort effects that fire *after* the
decision's actual state change (advance / reject / send) has committed:

1. Workable stage move (advance) or disqualify (reject).
2. The ATS activity-feed movement summary note.
3. A durable recruiter-action graph intent for later indexing.

These ran inline on the approve / override request and added 20-30s to
every click. The synchronous path (agent runs, tests) and the deferred
Celery task (``app.tasks.decision_tasks.apply_decision_side_effects``)
both call ``apply_decision_side_effects`` so the logic stays identical no
matter where it runs. Every step is wrapped: a failure in one never
aborts the others or raises to the caller.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import append_application_event
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from ..services.decision_resolution_provenance import record_resolution_effect
from ..services.related_role_application_runtime import (
    related_role_evaluation_for_application,
)
from ..services.workable_actions_service import WorkableWritebackError
from ._workable_decision_summary import (
    post_decision_summary_to_workable,
    try_workable_advance,
)
from .types import Actor

logger = logging.getLogger("taali.actions.decision_side_effects")


def _discard_checkpointed_optional_work(db: Session) -> None:
    """Reset optional work without touching the already-durable movement."""
    try:
        db.rollback()
    except Exception:  # pragma: no cover - broken-session defensive fallback
        logger.warning("could not reset session after optional decision side effect")


def _organization_resolution_guard_statement(
    organization_id: int,
    *,
    exclusive: bool = False,
):
    """Lock the workspace key before any decision-resolution row locks.

    Graph provider admission takes ``Organization`` then ``Role``. Holding a
    key-share lock first gives approval/override the same order while still
    allowing ordinary foreign-key inserts, including the graph outbox row.

    Assessment creation later locks the same organization ``FOR UPDATE`` while
    reserving capacity. Those paths must take the exclusive lock here instead
    of upgrading a held ``KEY SHARE`` lock: two concurrent send resolutions
    could otherwise each hold the weak lock while waiting to upgrade past the
    other.
    """
    statement = select(Organization.id).where(
        Organization.id == int(organization_id)
    )
    if exclusive:
        return statement.with_for_update()
    return statement.with_for_update(read=True, key_share=True)


def lock_organization_for_decision_resolution(
    db: Session,
    *,
    organization_id: int,
    exclusive: bool = False,
) -> None:
    """Acquire the organization-first lock-order guard for a resolution."""
    organization = db.execute(
        _organization_resolution_guard_statement(
            int(organization_id),
            exclusive=exclusive,
        )
    ).scalar_one_or_none()
    if organization is None:
        raise RuntimeError(f"organization {int(organization_id)} not found")


# Plain-English verdict for the resolved decision — drives which Workable
# writeback runs and the headline on the activity note.
VERDICT_BY_DECISION_TYPE = {
    "advance_to_interview": "advanced",
    "reject": "rejected",
    "skip_assessment_reject": "rejected",
    # Invite decisions commit a durable *delivery intent*, not an external ATS
    # hand-back. Their provider-success outbox may synchronize an ATS stage but
    # assessment lifecycle messaging remains entirely inside Taali.
}
VERDICT_BY_OVERRIDE_ACTION = {
    "reject": "rejected",
    "advance": "advanced",
    "skip_assessment_advance": "skip_advanced",
}


def verdict_for(
    *,
    disposition: str,
    decision_type: Optional[str],
    override_action: Optional[str],
) -> Optional[str]:
    """Resolve the verdict for an approved / overridden decision."""
    if disposition == "overridden":
        return VERDICT_BY_OVERRIDE_ACTION.get(override_action or "")
    return VERDICT_BY_DECISION_TYPE.get(decision_type or "")


def _related_ats_transport(
    db: Session,
    *,
    app: CandidateApplication,
    decision_role: Optional[Role],
) -> tuple[
    CandidateApplication | None,
    SisterRoleEvaluation | None,
    list[str],
]:
    """Resolve the explicit ATS transport for one logical role application.

    Ordinary roles write through their own application. Related roles may use
    a different evidence/application row, so only the membership's first-class
    ``ats_application`` relationship can authorize transport. No owner-role or
    role-id inference is permitted here.
    """

    if (
        decision_role is None
        or str(decision_role.role_kind or "") != ROLE_KIND_SISTER
    ):
        return app, None, []
    evaluation = related_role_evaluation_for_application(
        db,
        role_id=int(decision_role.id),
        application=app,
    )
    if evaluation is None:
        return None, None, ["related_role_membership_missing"]
    transport = evaluation.ats_application
    if transport is None:
        return None, evaluation, ["ats_application_unlinked"]
    errors: list[str] = []
    if int(transport.organization_id) != int(app.organization_id):
        errors.append("ats_application_wrong_organization")
    if int(transport.candidate_id) != int(app.candidate_id):
        errors.append("ats_application_wrong_candidate")
    if getattr(transport, "deleted_at", None) is not None:
        errors.append("ats_application_deleted")
    return (None if errors else transport), evaluation, errors


def _workable_stage_display_name(role: Optional[Role], value: Optional[str]) -> Optional[str]:
    target = str(value or "").strip()
    if not target:
        return None
    stages = getattr(role, "workable_stages", None)
    for stage in stages if isinstance(stages, list) else []:
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id") or "").strip()
        slug = str(stage.get("slug") or "").strip()
        name = str(stage.get("name") or "").strip()
        if target.casefold() in {
            stage_id.casefold(),
            slug.casefold(),
            name.casefold(),
        }:
            return name or target
    return target.replace("_", " ").replace("-", " ").strip().title()


def _confirmed_movement_destination(
    db: Session,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    role: Optional[Role],
    requested_workable_stage: Optional[str],
) -> Optional[str]:
    """Return the human provider destination after a confirmed movement."""
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider

    provider = resolve_application_ats_provider(org, db, app)
    if isinstance(provider, BullhornProvider):
        return str(getattr(app, "bullhorn_status", None) or "").strip() or "Advanced"
    return _workable_stage_display_name(
        role,
        (requested_workable_stage or "").strip()
        or str(getattr(app, "workable_stage", None) or "").strip(),
    )


def apply_decision_side_effects(
    db: Session,
    actor: Actor,
    *,
    decision: AgentDecision,
    app: Optional[CandidateApplication],
    org: Optional[Organization],
    role: Optional[Role],
    disposition: str,
    override_action: Optional[str] = None,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    reject_notify: bool = True,
    commit_after_confirmed_movement: bool = False,
) -> None:
    """Run all best-effort side effects for a resolved decision.

    Best-effort and never raises EXCEPT under ``strict_workable_writes`` (the
    decision-batch path), where a failed critical Workable writeback (stage
    move / disqualify) raises ``WorkableWritebackError`` so the batch task can
    abort + re-queue the decision instead of committing a Taali-only change.
    The activity note (step 2) and graph episode (step 3) stay best-effort
    regardless.

    ``disposition`` is ``"approved"`` or ``"overridden"``. ``reject_notify``
    is the caller's "this resolution is what freshly rejected the candidate"
    signal — guards against re-disqualifying / re-emailing a candidate who
    was already rejected by another path (mirrors the inline freshness check
    that used to live in ``reject_application.run``).

    ``commit_after_confirmed_movement`` is reserved for the generic acks-late
    ATS runner. Once the provider confirms a movement, that runner must make
    the decision and local movement receipt durable before attempting the
    optional ATS summary or graph intent. Direct and legacy synchronous callers
    keep the historical caller-owned transaction boundary (the default).
    """
    verdict = verdict_for(
        disposition=disposition,
        decision_type=decision.decision_type,
        override_action=override_action,
    )
    movement_confirmed = False
    moved_to: Optional[str] = None
    transport_app: Optional[CandidateApplication] = app
    writeback_role: Optional[Role] = None
    action_event_metadata = {
        "acting_role_id": int(decision.role_id),
        "agent_decision_id": int(decision.id),
        "workable_target_stage": workable_target_stage,
    }

    # 1. Workable stage move (advance) or disqualify (reject).
    if app is not None:
        if verdict in ("advanced", "skip_advanced"):
            transport_app, evaluation, transport_errors = _related_ats_transport(
                db,
                app=app,
                decision_role=role,
            )
            if transport_app is not None:
                action_event_metadata["ats_application_id"] = int(transport_app.id)
                writeback_role = (
                    db.get(Role, int(transport_app.role_id))
                    if transport_app.role_id is not None
                    else None
                )
            elif role is None or str(role.role_kind or "") != ROLE_KIND_SISTER:
                writeback_role = role
            ats_write_allowed = True
            if role is not None and str(role.role_kind or "") == ROLE_KIND_SISTER:
                if evaluation is not None:
                    from ..services.sister_role_service import (
                        related_role_action_restrictions,
                    )

                    restrictions = related_role_action_restrictions(
                        role=role,
                        evaluation=evaluation,
                        source_application=app,
                    )
                    restriction_codes = list(restrictions.get("codes") or [])
                    restriction_codes.extend(transport_errors)
                    ats_write_allowed = bool(
                        transport_app is not None
                        and restrictions.get("can_advance_in_ats")
                        and not transport_errors
                    )
                else:
                    restriction_codes = transport_errors or [
                        "related_role_membership_missing"
                    ]
                    ats_write_allowed = False
                if not ats_write_allowed:
                    append_application_event(
                        db,
                        app=app,
                        role_id=int(role.id),
                        agent_decision_id=int(decision.id),
                        event_type="ats_writeback_restricted",
                        actor_type=actor.type,
                        actor_id=actor.event_actor_id,
                        reason=(
                            "Related-role advance kept local because its explicit "
                            "ATS transport is unavailable or restricted"
                        ),
                        metadata={
                            **action_event_metadata,
                            "restriction_codes": sorted(set(restriction_codes)),
                            "action": "advance",
                        },
                        target_stage=workable_target_stage,
                        effect_status="skipped",
                    )
            if ats_write_allowed:
                assert transport_app is not None
                try:
                    moved = try_workable_advance(
                        db,
                        actor,
                        app=transport_app,
                        event_app=app,
                        event_role_id=int(decision.role_id),
                        org=org,
                        role=writeback_role,
                        target_stage=workable_target_stage,
                        reason=(note or "").strip()
                        or "Advanced by recruiter (decision resolution)",
                        event_metadata=action_event_metadata,
                    )
                    movement_confirmed = moved
                except WorkableWritebackError:
                    # strict (batch) path — propagate so the batch can re-queue.
                    raise
                except Exception:  # pragma: no cover — defensive
                    logger.warning(
                        "workable advance raised for decision_id=%s",
                        getattr(decision, "id", None),
                    )
        elif (
            verdict == "rejected"
            and reject_notify
            and not (
                role is not None
                and str(role.role_kind or "") == ROLE_KIND_SISTER
            )
        ):
            try:
                from .reject_application import notify_rejection

                # The canonical movement summary below owns the only
                # human-readable Decision Hub message. Do not also attach the
                # recruiter note to Workable's disqualify request or the same
                # text appears twice in its activity feed. Direct/manual
                # rejection paths still pass their reason to the notifier.
                movement_confirmed = notify_rejection(
                    db,
                    app=app,
                    actor=actor,
                    reason=None,
                    event_metadata=action_event_metadata,
                )
            except WorkableWritebackError:
                # strict (batch) path — propagate so the batch can re-queue.
                raise
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "rejection notify raised for decision_id=%s",
                    getattr(decision, "id", None),
                )

    related_local_reject = bool(
        verdict == "rejected"
        and role is not None
        and str(role.role_kind or "") == ROLE_KIND_SISTER
    )
    if related_local_reject:
        record_resolution_effect(
            decision,
            effect_status="local_confirmed",
            provider_movement_confirmed=False,
        )
    elif verdict in ("advanced", "skip_advanced", "rejected"):
        record_resolution_effect(
            decision,
            effect_status=(
                "confirmed"
                if movement_confirmed
                else (
                    "local_confirmed"
                    if role is not None
                    and str(role.role_kind or "") == ROLE_KIND_SISTER
                    else "unconfirmed"
                )
            ),
            provider_movement_confirmed=movement_confirmed,
        )
    else:
        record_resolution_effect(decision, effect_status="confirmed")

    movement_checkpointed = bool(
        movement_confirmed and commit_after_confirmed_movement
    )
    if movement_checkpointed:
        # The provider write has happened and cannot be rolled back. Commit the
        # decision plus local movement receipt now, before the optional note.
        # If the acks-late worker dies after this point, redelivery sees the
        # resolved decision and skips instead of replaying the provider write.
        db.commit()

    # Destination formatting is optional summary context, not part of the
    # movement. Resolve it only after the durable checkpoint and never let a
    # resolver/read failure turn a confirmed provider update into a retry.
    if app is not None and movement_confirmed and verdict in (
        "advanced",
        "skip_advanced",
    ):
        try:
            if movement_checkpointed:
                with db.begin_nested():
                    moved_to = _confirmed_movement_destination(
                        db,
                        app=transport_app or app,
                        org=org,
                        role=writeback_role,
                        requested_workable_stage=workable_target_stage,
                    )
            else:
                moved_to = _confirmed_movement_destination(
                    db,
                    app=transport_app or app,
                    org=org,
                    role=writeback_role,
                    requested_workable_stage=workable_target_stage,
                )
        except Exception:  # pragma: no cover - defensive optional context
            logger.warning(
                "movement destination resolution raised for decision_id=%s",
                getattr(decision, "id", None),
            )
            if movement_checkpointed:
                _discard_checkpointed_optional_work(db)

    # 2. Provider-neutral ATS movement summary note.
    if app is not None and transport_app is not None and verdict and movement_confirmed:
        try:
            if movement_checkpointed:
                # Keep database bookkeeping for the optional provider note in
                # a savepoint. A post-provider flush failure can then discard
                # only this optional receipt without poisoning the checkpointed
                # decision transaction.
                with db.begin_nested():
                    post_decision_summary_to_workable(
                        db,
                        actor,
                        app=transport_app,
                        event_app=app,
                        event_role_id=int(decision.role_id),
                        org=org,
                        decision=decision,
                        verdict=verdict,
                        override_action=override_action,
                        reason=note,
                        moved_to=moved_to,
                    )
            else:
                post_decision_summary_to_workable(
                    db,
                    actor,
                    app=transport_app,
                    event_app=app,
                    event_role_id=int(decision.role_id),
                    org=org,
                    decision=decision,
                    verdict=verdict,
                    override_action=override_action,
                    reason=note,
                    moved_to=moved_to,
                )
        except Exception:  # pragma: no cover — defensive
            logger.warning(
                "decision-summary post raised for decision_id=%s",
                getattr(decision, "id", None),
            )
            if movement_checkpointed:
                _discard_checkpointed_optional_work(db)

    # Flush the decision/Workable state before opening the optional savepoint.
    # SQLAlchemy otherwise performs this flush while entering begin_nested();
    # a core-state failure there could be mistaken for a disposable graph
    # failure and leave the outer transaction unusable.
    if not movement_checkpointed:
        db.flush()

    # 3. Recruiter-action graph episode (Phase 2 §6.7) — persist a durable
    # outbox row in this transaction. Graphiti and its provider metering run
    # later in the graph-outbox worker, after the approval/Workable transaction
    # commits, so optional graph work can never hold up a recruiter action.
    try:
        from ..candidate_graph import episode_outbox

        recruiter_id = int(actor.user_id) if actor.user_id else 0
        if disposition == "overridden":
            reason_parts: list[str] = []
            if override_action:
                reason_parts.append(f"override_action={override_action}")
            if note:
                reason_parts.append(note)
            action = "override"
            reason = " | ".join(reason_parts) if reason_parts else None
        else:
            action = "approve"
            reason = note
        # The savepoint makes this optional write truly best-effort: a rollout
        # mismatch or dedup race can roll back only the outbox insert, leaving
        # the already-confirmed Workable/decision transaction committable.
        with db.begin_nested():
            episode_outbox.enqueue_recruiter_action(
                db,
                organization_id=int(decision.organization_id),
                role_id=int(decision.role_id),
                decision_id=int(decision.id),
                recruiter_id=recruiter_id,
                action=action,
                reason=reason,
                happened_at=decision.resolved_at,
            )
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "recruiter-action episode enqueue failed for decision_id=%s",
            getattr(decision, "id", None),
            exc_info=True,
        )
        if movement_checkpointed:
            _discard_checkpointed_optional_work(db)
