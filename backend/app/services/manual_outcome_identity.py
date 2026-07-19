"""Exact identity contract for replayable manual ATS outcome writes."""

from __future__ import annotations

import hashlib


def build_manual_outcome_operation_id(
    *,
    organization_id: int,
    application_id: int,
    application_version: int,
    target_outcome: str,
    idempotency_key: str | None,
) -> str:
    seed = str(idempotency_key or "").strip() or (
        f"v{int(application_version)}:{str(target_outcome).strip().lower()}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"manual-outcome:{int(organization_id)}:{int(application_id)}:{digest}"


def validate_manual_outcome_payload(payload: dict) -> tuple[int, str, str, str]:
    expected_version = payload.get("expected_application_version")
    target_outcome = str(payload.get("target_outcome") or "").strip().lower()
    expected_outcome = str(payload.get("expected_local_outcome") or "").strip().lower()
    operation_id = str(payload.get("operation_id") or "").strip()
    provider, provider_target_id = manual_outcome_provider_snapshot(payload)
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
        or not target_outcome
        or expected_outcome != target_outcome
        or not operation_id
        or len(operation_id) > 200
        or provider not in {"bullhorn", "workable"}
        or not provider_target_id
        or len(provider_target_id) > 200
    ):
        raise ValueError("manual outcome writes require an exact lifecycle snapshot")
    return expected_version, target_outcome, expected_outcome, operation_id


def manual_outcome_provider_snapshot(payload: dict) -> tuple[str, str]:
    return (
        str(payload.get("provider") or "").strip().lower(),
        str(payload.get("provider_target_id") or "").strip(),
    )


__all__ = [
    "build_manual_outcome_operation_id",
    "manual_outcome_provider_snapshot",
    "validate_manual_outcome_payload",
]
