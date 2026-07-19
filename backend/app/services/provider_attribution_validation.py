"""Exact scalar validation shared by paid provider admission paths."""

from __future__ import annotations

from typing import Any

from .credit_reservation_contract import CreditReservation


def require_provider_attribution(
    *,
    organization_id: Any,
    role_id: Any,
    user_id: Any,
    entity_id: Any,
    candidate_id: Any,
    provider: Any,
    model: Any,
    request_sha256: Any,
) -> None:
    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("organization_id must be a positive integer")
    for field, value in (
        ("role_id", role_id),
        ("user_id", user_id),
        ("candidate_id", candidate_id),
    ):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"{field} must be a positive integer")
    for field, value in (
        ("entity_id", entity_id),
        ("provider", provider),
        ("model", model),
    ):
        if value is not None and (
            type(value) is not str
            or not value.strip()
            or value != value.strip()
        ):
            raise ValueError(f"{field} must be a non-empty string")
    if request_sha256 is not None and (
        type(request_sha256) is not str
        or len(request_sha256) != 64
        or any(char not in "0123456789abcdef" for char in request_sha256)
    ):
        raise ValueError("request_sha256 must be a lowercase SHA-256 digest")


def provider_is_exact_v2_authority(
    reservation: CreditReservation | None,
    provider: Any,
) -> bool:
    """Require an exact provider-bound v2 identity before attempt state."""

    return bool(
        reservation is not None
        and reservation.version == 2
        and type(provider) is str
        and provider.strip()
        and reservation.provider == provider
    )


__all__ = ["provider_is_exact_v2_authority", "require_provider_attribution"]
