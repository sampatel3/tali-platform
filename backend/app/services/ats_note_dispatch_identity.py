"""Non-secret durable identity for ATS-note producer retries."""

from __future__ import annotations

import hashlib
import json

from .ats_note_claim import ensure_note_operation_payload


def prepare_note_dispatch_identity(
    payload: dict,
    *,
    organization_id: int,
    dispatch_key: str | None,
) -> tuple[dict, str, dict[str, str]]:
    """Normalize a note and return its operation plus body-hash contract."""

    prepared = ensure_note_operation_payload(
        payload,
        organization_id=int(organization_id),
        stable_key=dispatch_key,
    )
    provider = str(prepared.get("provider") or "").strip().lower()
    provider_target_id = str(prepared.get("provider_target_id") or "").strip()
    candidate_provider_id = str(
        prepared.get("candidate_provider_id") or ""
    ).strip()
    actor_type = (
        str(prepared.get("actor_type") or "recruiter").strip()[:32] or "recruiter"
    )
    raw_application_id = prepared.get("application_id")
    actor_id = prepared.get("actor_id", prepared.get("user_id"))
    try:
        application_id = int(raw_application_id)
        if isinstance(raw_application_id, bool) or application_id <= 0:
            raise ValueError
        if actor_id is not None:
            if isinstance(actor_id, bool):
                raise ValueError
            actor_id = int(actor_id)
    except (TypeError, ValueError):
        from .ats_note_provider import note_provider_failure

        raise note_provider_failure(
            "invalid_note_operation", "Exact ATS note identity is required"
        ) from None
    provider_actor_member_id = (
        str(prepared.get("provider_actor_member_id") or "").strip() or None
    )
    provider_job_order_id = (
        str(prepared.get("provider_job_order_id") or "").strip() or None
    )
    provider_note_action = (
        str(prepared.get("provider_note_action") or "").strip() or None
    )
    prepared.update(
        provider=provider,
        provider_target_id=provider_target_id,
        candidate_provider_id=candidate_provider_id,
        actor_type=actor_type,
        actor_id=actor_id,
    )
    for key, value in (
        ("provider_actor_member_id", provider_actor_member_id),
        ("provider_job_order_id", provider_job_order_id),
        ("provider_note_action", provider_note_action),
    ):
        if key in payload:
            prepared[key] = value
    canonical_intent = {
        "organization_id": int(organization_id),
        "application_id": application_id,
        "provider": provider,
        "provider_target_id": provider_target_id,
        "candidate_provider_id": candidate_provider_id,
        "body": str(prepared["body"]),
        "actor_type": actor_type,
        "actor_id": actor_id,
    }
    encoded = json.dumps(canonical_intent, sort_keys=True, separators=(",", ":"))
    intent_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    canonical_dispatch = {
        **canonical_intent,
        "provider_actor_member_id": provider_actor_member_id,
        "provider_job_order_id": provider_job_order_id,
        "provider_note_action": provider_note_action,
    }
    dispatch_encoded = json.dumps(
        canonical_dispatch,
        sort_keys=True,
        separators=(",", ":"),
    )
    dispatch_sha256 = hashlib.sha256(dispatch_encoded.encode("utf-8")).hexdigest()
    queued_intent_sha256 = str(payload.get("note_intent_sha256") or "").strip()
    queued_dispatch_sha256 = str(payload.get("note_dispatch_sha256") or "").strip()
    if (
        queued_intent_sha256
        and queued_intent_sha256 != intent_sha256
    ) or (
        queued_dispatch_sha256
        and queued_dispatch_sha256 != dispatch_sha256
    ):
        from .ats_note_provider import note_provider_failure

        raise note_provider_failure(
            "invalid_note_operation", "Exact ATS note intent is required"
        )
    prepared.update(
        note_intent_sha256=intent_sha256,
        note_dispatch_sha256=dispatch_sha256,
    )
    return (
        prepared,
        str(prepared["note_operation_id"]),
        {
            "note_body_sha256": str(prepared["note_body_sha256"]),
            "note_intent_sha256": intent_sha256,
            "note_dispatch_sha256": dispatch_sha256,
        },
    )


__all__ = ["prepare_note_dispatch_identity"]
