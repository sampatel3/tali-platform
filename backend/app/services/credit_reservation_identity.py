"""Versioned immutable identity helpers for provider credit holds."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from .credit_reservation_contract import CreditReservation, reservation_from_payload
from .credit_reservation_ledger import hold_matches_reservation
from .credit_reservation_shadow_proof import reservation_with_shadow_proof
from .pricing_service import Feature, estimate_reservation


def normalize_new_reservation_inputs(
    *,
    feature: Feature | str,
    amount: int | None,
    external_ref: str,
) -> tuple[Feature, int, str]:
    """Validate billing scalars without coercing caller mistakes."""

    if isinstance(feature, Feature):
        feature_enum = feature
    elif type(feature) is str:
        feature_enum = Feature(feature)
    else:
        raise ValueError("feature must be a supported feature string")
    if amount is None:
        held = int(estimate_reservation(feature_enum))
    elif type(amount) is not int or amount < 0:
        raise ValueError("amount must be a non-negative integer")
    else:
        held = amount
    if type(external_ref) is not str:
        raise ValueError("external_ref must be a string")
    ref = external_ref.strip()
    if not ref:
        raise ValueError("external_ref is required for a credit reservation")
    if ref != external_ref:
        raise ValueError("external_ref must not contain surrounding whitespace")
    return feature_enum, held, ref


def build_v2_reservation(
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
    reservation = reservation_with_shadow_proof(
        organization_id=organization_id,
        feature=feature,
        amount=amount,
        external_ref=external_ref,
        live=live,
        role_id=role_id,
        user_id=user_id,
        entity_id=entity_id,
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        request_sha256=request_sha256,
    )
    parsed = reservation_from_payload(reservation)
    if parsed is None:
        raise ValueError("credit reservation identity is malformed")
    return parsed


def v2_identity_metadata(reservation: CreditReservation) -> dict[str, Any]:
    return {
        "reservation_version": 2,
        "reservation_user_id": reservation.user_id,
        "reservation_entity_id": reservation.entity_id,
        "reservation_candidate_id": reservation.candidate_id,
        "reservation_provider": reservation.provider,
        "reservation_model": reservation.model,
        "reservation_request_sha256": reservation.request_sha256,
    }


def reuse_exact_v2_hold(
    db: Session,
    *,
    hold: BillingCreditLedger,
    expected: CreditReservation,
) -> CreditReservation:
    if not hold_matches_reservation(hold, expected, allowed_states={"held"}):
        raise ValueError(
            f"credit reservation ref already used: {expected.external_ref}"
        )
    if (
        db.query(BillingCreditLedger.id)
        .filter(
            BillingCreditLedger.external_ref
            == f"{expected.external_ref}:settled"
        )
        .first()
        is not None
    ):
        raise ValueError(
            f"credit reservation already settled: {expected.external_ref}"
        )
    return expected


def reservation_from_ledger_hold(
    hold: BillingCreditLedger,
    *,
    feature: str,
    amount: int,
) -> CreditReservation:
    """Rebuild a settleable v1/v2 identity from immutable ledger metadata."""

    metadata = hold.entry_metadata if isinstance(hold.entry_metadata, dict) else {}
    version = metadata.get("reservation_version", 1)
    if version not in {1, 2} or type(version) is not int:
        raise ValueError("reservation version metadata is malformed")
    payload: dict[str, Any] = {
        "organization_id": int(hold.organization_id),
        "feature": feature,
        "amount": amount,
        "external_ref": str(hold.external_ref),
        "live": True,
        "role_id": metadata.get("role_id"),
        "shadow_proof": None,
    }
    if version == 2:
        payload.update(
            {
                "version": 2,
                "user_id": metadata.get("reservation_user_id"),
                "entity_id": metadata.get("reservation_entity_id"),
                "candidate_id": metadata.get("reservation_candidate_id"),
                "provider": metadata.get("reservation_provider"),
                "model": metadata.get("reservation_model"),
                "request_sha256": metadata.get(
                    "reservation_request_sha256"
                ),
            }
        )
    reservation = reservation_from_payload(payload)
    if reservation is None:
        raise ValueError("reservation identity metadata is malformed")
    return reservation


__all__ = [
    "build_v2_reservation",
    "normalize_new_reservation_inputs",
    "reservation_from_ledger_hold",
    "reuse_exact_v2_hold",
    "v2_identity_metadata",
]
