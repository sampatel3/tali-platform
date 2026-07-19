"""Authenticated provenance for cross-process shadow reservations."""

from __future__ import annotations

import hashlib
import hmac
import json

from ..platform.config import settings
from .credit_reservation_contract import CreditReservation


def shadow_reservation_proof(
    *,
    organization_id: int,
    feature: str,
    amount: int,
    external_ref: str,
    role_id: int | None,
    user_id: int | None,
    entity_id: str | None,
    candidate_id: int | None,
    provider: str | None,
    model: str | None,
    request_sha256: str | None,
) -> str:
    """Sign the immutable shadow identity with the deployment secret."""

    message = json.dumps(
        {
            "version": 2,
            "organization_id": organization_id,
            "feature": feature,
            "amount": amount,
            "external_ref": external_ref,
            "role_id": role_id,
            "user_id": user_id,
            "entity_id": entity_id,
            "candidate_id": candidate_id,
            "provider": provider,
            "model": model,
            "request_sha256": request_sha256,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def shadow_reservation_proof_is_valid(
    *,
    proof: str | None,
    organization_id: int,
    feature: str,
    amount: int,
    external_ref: str,
    role_id: int | None,
    user_id: int | None,
    entity_id: str | None,
    candidate_id: int | None,
    provider: str | None,
    model: str | None,
    request_sha256: str | None,
) -> bool:
    if not proof:
        return False
    expected = shadow_reservation_proof(
        organization_id=organization_id,
        feature=feature,
        amount=amount,
        external_ref=external_ref,
        role_id=role_id,
        user_id=user_id,
        entity_id=entity_id,
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        request_sha256=request_sha256,
    )
    return hmac.compare_digest(proof, expected)


def reservation_with_shadow_proof(
    *,
    organization_id: int,
    feature: str,
    amount: int,
    external_ref: str,
    live: bool,
    role_id: int | None,
    user_id: int | None,
    entity_id: str | None,
    candidate_id: int | None,
    provider: str | None,
    model: str | None,
    request_sha256: str | None,
) -> CreditReservation:
    return CreditReservation(
        organization_id=organization_id,
        feature=feature,
        amount=amount,
        external_ref=external_ref,
        live=live,
        role_id=role_id,
        version=2,
        user_id=user_id,
        entity_id=entity_id,
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        request_sha256=request_sha256,
        shadow_proof=(
            None
            if live
            else shadow_reservation_proof(
                organization_id=organization_id,
                feature=feature,
                amount=amount,
                external_ref=external_ref,
                role_id=role_id,
                user_id=user_id,
                entity_id=entity_id,
                candidate_id=candidate_id,
                provider=provider,
                model=model,
                request_sha256=request_sha256,
            )
        ),
    )


__all__ = [
    "reservation_with_shadow_proof",
    "shadow_reservation_proof",
    "shadow_reservation_proof_is_valid",
]
