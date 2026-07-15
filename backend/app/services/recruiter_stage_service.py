"""Provider-neutral hiring-stage axis after Tali evaluation handoff.

``pipeline_stage`` remains Tali's evaluation lifecycle.  ``recruiter_stage``
tracks the downstream hiring milestone regardless of whether Workable,
Bullhorn, or Tali's native ATS owns logistics.  Keeping these axes separate
prevents an external interview/offer update from silently becoming a Tali
evaluation decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from .document_service import sanitize_json_for_storage


RECRUITER_STAGES = ("screening", "interviewing", "offer", "hired")
RECRUITER_STAGE_SOURCES = ("system", "recruiter", "sync", "agent")
EXTERNAL_STAGE_SYNC_KEY = "hiring_stage_sync"

# Workable stage names are tenant-customizable display text. ``stage_kind`` is
# the provider's stable semantic contract and is therefore the only safe input
# to the downstream hiring axis. See Workable's /stages contract.
_WORKABLE_STAGE_KIND_TO_RECRUITER_STAGE = {
    "sourced": "screening",
    "applied": "screening",
    "shortlisted": "screening",
    "assessment": "interviewing",
    "phone_screen": "screening",
    "interview": "interviewing",
    "offer": "offer",
    "hired": "hired",
}

# Exact compatibility mappings for old Workable rows written before we stored
# ``stage_kind``. This is deliberately a closed allowlist (not fuzzy matching):
# tenant-specific labels outside it must go through the provider stage map.
_LEGACY_WORKABLE_STAGE_LABEL_TO_RECRUITER_STAGE = {
    "interview": "interviewing",
    "technical_interview": "interviewing",
    "final_interview": "interviewing",
    "offer": "offer",
    "offer_extended": "offer",
    "hired": "hired",
}

_SCREENING = {
    "screening", "screen", "phone_screen", "phone_interview", "first_stage",
}
_INTERVIEWING = {
    "interview", "interviewing", "technical", "technical_interview",
    "final_interview", "onsite", "presentation", "assessment",
    "interview_scheduled", "interviewscheduled",
}
_OFFER = {"offer", "offer_extended", "offer_accepted"}
_HIRED = {"hired", "placed", "placement", "confirmed"}


def _key(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_recruiter_stage(value: str | None) -> str:
    key = _key(value)
    if key not in RECRUITER_STAGES:
        raise HTTPException(status_code=422, detail=f"Unsupported recruiter_stage={value!r}")
    return key


def recruiter_stage_from_external(value: str | None) -> str | None:
    key = _key(value)
    if key in _SCREENING:
        return "screening"
    if key in _INTERVIEWING:
        return "interviewing"
    if key in _OFFER:
        return "offer"
    if key in _HIRED:
        return "hired"
    return None


def recruiter_stage_from_workable_kind(value: str | None) -> str | None:
    """Map Workable's documented semantic stage kind, never its display label."""

    return _WORKABLE_STAGE_KIND_TO_RECRUITER_STAGE.get(_key(value))


def _external_stage_sync_state(app: CandidateApplication) -> dict[str, Any]:
    state = (
        app.integration_sync_state
        if isinstance(getattr(app, "integration_sync_state", None), dict)
        else {}
    )
    value = state.get(EXTERNAL_STAGE_SYNC_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _set_external_stage_sync_state(
    app: CandidateApplication,
    *,
    provider: str,
    raw_stage: str | None,
    status: str,
    recruiter_stage: str | None = None,
    provider_stage_kind: str | None = None,
) -> None:
    """Persist an honest, machine-readable provider-stage mapping receipt."""

    now = datetime.now(timezone.utc).isoformat()
    state = (
        dict(app.integration_sync_state)
        if isinstance(getattr(app, "integration_sync_state", None), dict)
        else {}
    )
    receipt: dict[str, Any] = {
        "provider": _key(provider) or "ats",
        "status": status,
        "raw_stage": str(raw_stage or "")[:200],
        "updated_at": now,
    }
    if provider_stage_kind:
        receipt["provider_stage_kind"] = str(provider_stage_kind)[:100]
    if recruiter_stage:
        receipt["recruiter_stage"] = recruiter_stage
    if status == "needs_mapping":
        receipt["error_code"] = "needs_mapping"
        state["sync_status"] = "needs_mapping"
        state["sync_exception"] = {
            "code": "needs_mapping",
            "scope": "recruiter_stage",
            "provider": receipt["provider"],
            "raw_stage": receipt["raw_stage"],
        }
    else:
        previous_exception = state.get("sync_exception")
        if (
            isinstance(previous_exception, dict)
            and previous_exception.get("scope") == "recruiter_stage"
        ):
            state.pop("sync_exception", None)
        if state.get("sync_status") == "needs_mapping":
            state["sync_status"] = "success"
    state[EXTERNAL_STAGE_SYNC_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)


def mark_external_stage_mapping_resolved(
    app: CandidateApplication,
    *,
    provider: str,
    raw_stage: str | None,
    recruiter_stage: str | None = None,
    provider_stage_kind: str | None = None,
) -> None:
    """Clear a previous mapping exception for an explicitly mapped status."""

    _set_external_stage_sync_state(
        app,
        provider=provider,
        raw_stage=raw_stage,
        status="mapped",
        recruiter_stage=recruiter_stage,
        provider_stage_kind=provider_stage_kind,
    )


def _clear_stale_stage_for_mapping_exception(
    db: Session,
    *,
    app: CandidateApplication,
    provider: str,
    raw_stage: str | None,
) -> None:
    """Clear a formerly mapped value when the provider status is now unknown."""

    before = _key(getattr(app, "recruiter_stage", None))
    if before not in RECRUITER_STAGES:
        return
    app.recruiter_stage = None
    app.recruiter_stage_source = "sync"
    app.recruiter_stage_updated_at = datetime.now(timezone.utc)
    app.version = int(app.version or 1) + 1
    db.add(
        CandidateApplicationEvent(
            application_id=int(app.id),
            organization_id=int(app.organization_id),
            event_type="recruiter_stage_mapping_required",
            actor_type="sync",
            reason=f"{provider.title()} status requires an explicit hiring-stage mapping",
            event_metadata={
                "from_recruiter_stage": before,
                "provider": _key(provider),
                "raw_stage": str(raw_stage or "")[:200],
                "error_code": "needs_mapping",
            },
        )
    )


def current_recruiter_stage(app: CandidateApplication) -> str | None:
    if str(getattr(app, "application_outcome", "") or "").lower() == "hired":
        return "hired"
    if _external_stage_sync_state(app).get("status") == "needs_mapping":
        # Do not present the last successfully mapped stage as current after the
        # provider has moved to a custom/unknown status.
        return None
    stored = _key(getattr(app, "recruiter_stage", None))
    stored_source = _key(getattr(app, "recruiter_stage_source", None))
    # Migration seeded Advanced rows conservatively as screening. Prefer a
    # recognizable provider milestone over that fallback so an existing Offer
    # does not regress visually until the next successful sync.
    if stored in RECRUITER_STAGES and stored_source != "migration":
        return stored
    external = recruiter_stage_from_external(
        getattr(app, "external_stage_raw", None)
        or getattr(app, "workable_stage", None)
        or getattr(app, "bullhorn_status", None)
        or getattr(app, "external_stage_normalized", None)
    )
    if external:
        return external
    if stored in RECRUITER_STAGES:
        return stored
    if _key(getattr(app, "pipeline_stage", None)) == "advanced":
        return "screening"
    return None


def recruiter_stage_context(app: CandidateApplication) -> dict[str, Any]:
    linked_workable = bool(getattr(app, "workable_candidate_id", None))
    linked_bullhorn = bool(getattr(app, "bullhorn_job_submission_id", None))
    provider = "workable" if linked_workable else "bullhorn" if linked_bullhorn else "native"
    stage = current_recruiter_stage(app)
    native_needs_calendar = provider == "native" and stage not in (None, "hired")
    sync_state = (
        app.integration_sync_state
        if isinstance(getattr(app, "integration_sync_state", None), dict)
        else {}
    )
    last_synced_at = getattr(app, "last_synced_at", None)
    hiring_stage_sync = _external_stage_sync_state(app)
    needs_mapping = hiring_stage_sync.get("status") == "needs_mapping"
    return {
        "stage": stage,
        "source": getattr(app, "recruiter_stage_source", None),
        "updated_at": (
            app.recruiter_stage_updated_at.isoformat()
            if getattr(app, "recruiter_stage_updated_at", None)
            else None
        ),
        "provider": provider,
        "evaluation_handed_off": _key(getattr(app, "pipeline_stage", None)) == "advanced",
        # The workflow is agent/connector-driven. Humans own only the genuine
        # consequential decision boundary (offer/hire) on the native path.
        "workflow_owner": "external_ats" if provider != "native" else "agent",
        "decision_owner": "external_ats" if provider != "native" else "human_hitl",
        "stage_sync": hiring_stage_sync or None,
        "logistics_automation": {
            "status": (
                "integration_required"
                if native_needs_calendar
                else "needs_mapping"
                if provider != "native" and needs_mapping
                else "external_ats_owned"
                if provider != "native"
                else "not_applicable"
            ),
            "required_integration": "calendar" if native_needs_calendar else None,
            "manual_coordination_is_default": False,
            "last_sync_status": sync_state.get("sync_status"),
            "last_synced_at": (
                last_synced_at.isoformat() if last_synced_at else None
            ),
        },
    }


def set_recruiter_stage(
    db: Session,
    *,
    app: CandidateApplication,
    to_stage: str,
    source: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
    authorization_basis: str | None = None,
    bump_version: bool = True,
) -> bool:
    target = normalize_recruiter_stage(to_stage)
    source_key = _key(source)
    if source_key not in RECRUITER_STAGE_SOURCES:
        raise HTTPException(status_code=422, detail=f"Unsupported recruiter_stage_source={source!r}")
    if expected_version is not None and int(expected_version) != int(app.version or 0):
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected={expected_version}, current={app.version}",
        )
    if source_key == "agent" and target in {"offer", "hired"}:
        raise HTTPException(
            status_code=403,
            detail="Offer and hire require a human decision or signed inbound ATS event",
        )
    if idempotency_key:
        existing = (
            db.query(CandidateApplicationEvent.id)
            .filter(
                CandidateApplicationEvent.application_id == int(app.id),
                CandidateApplicationEvent.idempotency_key == str(idempotency_key),
            )
            .first()
        )
        if existing is not None:
            return False

    before = current_recruiter_stage(app)
    if before == target and _key(getattr(app, "recruiter_stage", None)) == target:
        return False
    now = datetime.now(timezone.utc)
    app.recruiter_stage = target
    app.recruiter_stage_source = source_key
    app.recruiter_stage_updated_at = now
    if bump_version:
        app.version = int(app.version or 1) + 1
    db.add(
        CandidateApplicationEvent(
            application_id=int(app.id),
            organization_id=int(app.organization_id),
            event_type="recruiter_stage_changed",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or f"Hiring stage changed to {target}",
            event_metadata={
                "from_recruiter_stage": before,
                "to_recruiter_stage": target,
                "source": source_key,
                "authorization_basis": authorization_basis,
            },
            idempotency_key=idempotency_key,
        )
    )

    if target in {"interviewing", "offer", "hired"}:
        try:
            from ..agent_runtime.outcome_learning import (
                record_interview_outcome_on_recruiter_stage,
            )

            record_interview_outcome_on_recruiter_stage(
                db, application=app, new_stage=target
            )
        except Exception:  # pragma: no cover - never block stage synchronization
            pass

    if target == "hired" and str(app.application_outcome or "open").lower() != "hired":
        # Outcome remains the canonical terminal result. Keep the update in the
        # same transaction and emit its normal learning/audit event.
        from ..domains.assessments_runtime.pipeline_service import transition_outcome

        transition_outcome(
            db,
            app=app,
            to_outcome="hired",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or "Hiring stage reached hired",
            metadata={"recruiter_stage": target, "source": source_key},
            idempotency_key=(f"{idempotency_key}:outcome" if idempotency_key else None),
        )
    return True


def initialize_handoff_stage(
    db: Session,
    *,
    app: CandidateApplication,
    source: str,
    actor_type: str,
    actor_id: int | None = None,
) -> bool:
    if getattr(app, "recruiter_stage", None):
        return False
    return set_recruiter_stage(
        db,
        app=app,
        to_stage="screening",
        source=source,
        actor_type=actor_type,
        actor_id=actor_id,
        reason="Tali evaluation handed off; downstream screening opened",
        idempotency_key=f"recruiter_stage_handoff:{int(app.id)}",
        bump_version=False,
    )


def sync_from_external(
    db: Session,
    *,
    app: CandidateApplication,
    raw_stage: str | None,
    provider: str,
    force_stage: str | None = None,
    provider_stage_kind: str | None = None,
) -> bool:
    provider_key = _key(provider)
    effective_stage_kind = provider_stage_kind
    if provider_key == "workable" and not effective_stage_kind:
        cached_kind = getattr(app, "external_stage_normalized", None)
        if recruiter_stage_from_workable_kind(cached_kind):
            # Compatibility callers can reuse the semantic kind persisted by
            # the Workable sync; arbitrary normalized display text is rejected.
            effective_stage_kind = str(cached_kind)
    if force_stage:
        target = normalize_recruiter_stage(force_stage)
    elif provider_key == "workable":
        target = recruiter_stage_from_workable_kind(effective_stage_kind)
        if target is None and not effective_stage_kind:
            target = _LEGACY_WORKABLE_STAGE_LABEL_TO_RECRUITER_STAGE.get(
                _key(raw_stage)
            )
    elif provider_key == "bullhorn":
        # Bullhorn statuses are per-org free text. The caller must pass the
        # stage forced by AtsStageMap or a configured categorization status.
        target = None
    else:
        target = recruiter_stage_from_external(raw_stage)
    if target is None:
        if str(raw_stage or "").strip():
            _clear_stale_stage_for_mapping_exception(
                db,
                app=app,
                provider=provider_key or provider,
                raw_stage=raw_stage,
            )
            _set_external_stage_sync_state(
                app,
                provider=provider_key or provider,
                raw_stage=raw_stage,
                status="needs_mapping",
                provider_stage_kind=effective_stage_kind,
            )
        return False
    changed = set_recruiter_stage(
        db,
        app=app,
        to_stage=target,
        source="sync",
        actor_type="sync",
        reason=f"{provider.title()} hiring stage synchronized: {raw_stage}",
        # A repeated snapshot of the same live stage is already a no-op in
        # ``set_recruiter_stage``.  Do not assign a permanent key based only on
        # the raw value: real hiring flows can revisit a stage (for example,
        # offer -> screening -> offer), and a forever-key would suppress the
        # second legitimate transition.
        idempotency_key=None,
        authorization_basis=f"{provider}_inbound_status",
    )
    mark_external_stage_mapping_resolved(
        app,
        provider=provider_key or provider,
        raw_stage=raw_stage,
        recruiter_stage=target,
        provider_stage_kind=effective_stage_kind,
    )
    return changed


__all__ = [
    "RECRUITER_STAGES",
    "EXTERNAL_STAGE_SYNC_KEY",
    "current_recruiter_stage",
    "initialize_handoff_stage",
    "normalize_recruiter_stage",
    "mark_external_stage_mapping_resolved",
    "recruiter_stage_context",
    "recruiter_stage_from_external",
    "recruiter_stage_from_workable_kind",
    "set_recruiter_stage",
    "sync_from_external",
]
