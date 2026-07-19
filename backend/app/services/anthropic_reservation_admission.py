"""Immutable reservation admission for one synchronous Anthropic request."""

from __future__ import annotations

import uuid
from typing import Any

from . import anthropic_metering_identity as identity
from .anthropic_request_admission import anthropic_request_credit_upper_bound
from .pricing_service import Feature
from .provider_request_identity import provider_request_sha256
from .provider_reservation_contract import (
    reservation_matches as provider_reservation_matches_attribution,
)
from .provider_usage_admission import (
    mark_provider_attempt_started,
    release_provider_usage,
    replace_provider_usage_reservation,
    reserve_provider_usage,
)
from .usage_credit_reservations import reservation_from_payload


class ProviderAttemptMarkerError(RuntimeError):
    """Raised before the SDK when a paid attempt cannot be durably marked."""


def ensure_anthropic_provider_reservation(
    *,
    metering: dict[str, Any],
    request: dict[str, Any],
    organization_id: int,
) -> None:
    """Bind, validate, and mark one exact v2 paid-request reservation."""

    if type(organization_id) is not int or organization_id <= 0:
        raise ProviderAttemptMarkerError(
            "Anthropic call requires organization attribution"
        )
    feature = Feature(metering["feature"])
    try:
        role_id, user_id, entity_id, candidate_id, metadata = (
            identity.require_role_and_metadata_types(metering)
        )
        request_hash = provider_request_sha256(request)
    except ValueError as exc:
        raise ProviderAttemptMarkerError(str(exc)) from exc
    model = request.get("model")
    if type(model) is not str or not model.strip():
        raise ProviderAttemptMarkerError("Anthropic model attribution is required")
    model = model.strip()
    provider = "anthropic"
    require_role_authority = metering.get("require_role_authority", False)
    if type(require_role_authority) is not bool:
        raise ProviderAttemptMarkerError(
            "Anthropic role-authority admission flag must be boolean"
        )
    required_amount = anthropic_request_credit_upper_bound(request, feature=feature)
    reservation_payload = metering.get("credit_reservation")
    if not reservation_payload:
        trace_id = str(metering.get("trace_id") or uuid.uuid4().hex)
        metering["trace_id"] = trace_id
        reservation = reserve_provider_usage(
            organization_id=organization_id,
            role_id=role_id,
            feature=feature,
            trace_id=trace_id,
            user_id=user_id,
            entity_id=entity_id,
            candidate_id=candidate_id,
            provider=provider,
            model=model,
            request_sha256=request_hash,
            metadata={
                **dict(metadata or {}),
                "admission_source": "metered_anthropic_fallback",
            },
            amount=required_amount,
            require_role_authority=require_role_authority,
        )
        reservation_payload = reservation.as_metering_payload()
        metering["credit_reservation"] = reservation_payload
    else:
        parsed = reservation_from_payload(reservation_payload)
        if not provider_reservation_matches_attribution(
            reservation_payload,
            organization_id=organization_id,
            feature=feature,
            role_id=role_id,
            user_id=user_id,
            entity_id=entity_id,
            candidate_id=candidate_id,
            provider=provider,
            model=model,
            request_sha256=request_hash,
        ):
            raise ProviderAttemptMarkerError(
                "provider credit reservation does not match request attribution"
            )
        assert parsed is not None
        if parsed.amount < required_amount:
            trace_id = str(metering.get("trace_id") or uuid.uuid4().hex)
            metering["trace_id"] = trace_id
            replacement = replace_provider_usage_reservation(
                reservation_payload,
                organization_id=organization_id,
                role_id=role_id,
                feature=feature,
                trace_id=trace_id,
                amount=required_amount,
                user_id=user_id,
                entity_id=entity_id,
                candidate_id=candidate_id,
                provider=provider,
                model=model,
                request_sha256=request_hash,
                metadata={
                    **dict(metadata or {}),
                    "admission_source": "metered_anthropic_bound_upgrade",
                },
                require_role_authority=require_role_authority,
            )
            reservation_payload = replacement.as_metering_payload()
            metering["credit_reservation"] = reservation_payload

    if not mark_provider_attempt_started(reservation_payload, provider=provider):
        release_provider_usage(
            reservation_payload,
            reason="anthropic_attempt_marker_failed",
        )
        raise ProviderAttemptMarkerError(
            "could not durably mark Anthropic provider attempt"
        )


__all__ = ["ProviderAttemptMarkerError", "ensure_anthropic_provider_reservation"]
