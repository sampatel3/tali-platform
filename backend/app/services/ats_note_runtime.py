"""Serialized worker runtime for one exact ATS activity note."""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import ats_note_writeback


def execute_ats_note(
    db: Session,
    *,
    organization_id: int,
    payload: dict,
) -> dict:
    """Claim, call, checkpoint, and terminalize one serialized note."""

    from .ats_note_dispatch_identity import prepare_note_dispatch_identity

    try:
        payload, _operation_id, _identity = prepare_note_dispatch_identity(
            payload,
            organization_id=int(organization_id),
            dispatch_key=str(payload.get("note_operation_id") or "") or None,
        )
        plan, terminal = ats_note_writeback.prepare_ats_note_delivery(
            db,
            organization_id=int(organization_id),
            payload=payload,
        )
    except ats_note_writeback.AtsNoteProviderFailure as exc:
        db.rollback()
        try:
            application_id = int(payload.get("application_id") or 0)
            if isinstance(payload.get("application_id"), bool):
                application_id = 0
        except (TypeError, ValueError):
            application_id = 0
        return {
            "status": "failed",
            "application_id": application_id,
            "failed": 1,
            "provider_called": False,
            "retriable": exc.retriable,
            "code": exc.code,
        }
    if terminal is not None:
        return terminal
    assert plan is not None and not db.in_transaction()
    actor_type = str(payload.get("actor_type") or "recruiter")[:32]
    actor_id = payload.get("actor_id", payload.get("user_id"))
    if plan.provider_call_required:
        assert not db.in_transaction()
        with Session(
            bind=db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        ) as authority_db:
            try:
                locked_plan = ats_note_writeback.lock_ats_note_provider_scope(
                    authority_db,
                    plan=plan,
                )
                provider_result = ats_note_writeback.perform_ats_note_provider_call(
                    locked_plan,
                    should_yield=payload.get("_should_yield"),
                )
            except ats_note_writeback.AtsNoteProviderFailure as exc:
                result = ats_note_writeback.finish_ats_note_delivery(
                    authority_db,
                    plan=plan,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    failure=exc,
                )
                if exc.code == "mutex_lease_lost" and exc.provider_called is False:
                    result["mutex_lease_lost"] = True
                if exc.retriable and exc.provider_called is False:
                    result.update(retriable=True, code=exc.code)
                return result
            plan = locked_plan
            checkpoint = ats_note_writeback.checkpoint_ats_note_provider_success(
                authority_db,
                plan=plan,
                provider_result=provider_result,
            )
            if checkpoint is not None:
                return checkpoint
    return ats_note_writeback.finish_ats_note_delivery(
        db,
        plan=plan,
        actor_type=actor_type,
        actor_id=actor_id,
    )


__all__ = ["execute_ats_note"]
