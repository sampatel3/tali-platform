"""Atomic upgrade of a pre-provider credit hold to a request-specific bound."""

from __future__ import annotations

import uuid
from typing import Any

from ..platform.database import SessionLocal
from .pricing_service import Feature
from .provider_attribution_validation import require_provider_attribution
from .usage_credit_reservations import (
    CreditReservation,
    release_credit_reservation,
    reservation_from_payload,
    reserve_credits,
)


def replace_reservation(
    reservation: CreditReservation | dict[str, Any],
    *,
    organization_id: int,
    role_id: int | None,
    feature: Feature | str,
    trace_id: str,
    amount: int,
    user_id: int | None,
    entity_id: str | None,
    candidate_id: int | None,
    provider: str | None,
    model: str | None,
    request_sha256: str | None,
    metadata: dict[str, Any] | None,
    require_role_authority: bool,
) -> CreditReservation:
    from .provider_usage_admission import (
        ProviderReservationReplacementError,
        _lock_and_require_automatic_role_authority,
    )

    parsed = reservation_from_payload(reservation)
    require_provider_attribution(
        organization_id=organization_id,
        role_id=role_id,
        user_id=user_id,
        entity_id=entity_id,
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        request_sha256=request_sha256,
    )
    feature_value = feature.value if isinstance(feature, Feature) else str(feature)
    if (
        parsed is None
        or parsed.version != 2
        or parsed.organization_id != organization_id
        or str(parsed.feature) != feature_value
        or parsed.role_id != role_id
        or parsed.user_id != user_id
        or parsed.entity_id != entity_id
        or parsed.candidate_id != candidate_id
        or parsed.provider != provider
        or parsed.model != model
        or parsed.request_sha256 != request_sha256
    ):
        raise ProviderReservationReplacementError(
            "provider reservation does not match the admitted request"
        )
    if int(parsed.amount) >= int(amount):
        return parsed
    replacement_ref = (
        f"usage-hold:{feature_value}:{str(trace_id).strip() or 'untraced'}:"
        f"{uuid.uuid4().hex}"
    )
    try:
        with SessionLocal() as meter_db:
            if require_role_authority:
                _lock_and_require_automatic_role_authority(
                    meter_db,
                    organization_id=organization_id,
                    role_id=role_id,
                )
            if parsed.live and release_credit_reservation(
                meter_db,
                reservation=parsed,
                reason="provider_request_bound_upgrade",
            ) <= 0:
                raise ProviderReservationReplacementError(
                    "existing provider reservation is not safely replaceable"
                )
            replacement = reserve_credits(
                meter_db,
                organization_id=organization_id,
                feature=feature,
                external_ref=replacement_ref,
                amount=int(amount),
                metadata={
                    **dict(metadata or {}),
                    "replaces_reservation_ref": parsed.external_ref,
                    "role_id": role_id,
                    "entity_id": entity_id,
                    "trace_id": str(trace_id),
                },
                role_id=role_id,
                user_id=user_id,
                entity_id=entity_id,
                candidate_id=candidate_id,
                provider=provider,
                model=model,
                request_sha256=request_sha256,
                enforce_role_budget=role_id is not None,
            )
            meter_db.commit()
            return replacement
    except ProviderReservationReplacementError:
        raise
    except Exception as exc:
        raise ProviderReservationReplacementError(
            "provider reservation bound upgrade failed"
        ) from exc


__all__ = ["replace_reservation"]
