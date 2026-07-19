"""Ledger-backed validation for caller-supplied provider reservations."""

from __future__ import annotations

from typing import Any

from ..models.billing_credit_ledger import BillingCreditLedger
from ..platform.config import settings
from ..platform.database import SessionLocal
from .credit_reservation_shadow_proof import shadow_reservation_proof_is_valid
from .credit_reservation_ledger import hold_matches_reservation
from .pricing_service import Feature
from .usage_credit_reservations import CreditReservation, reservation_from_payload


def reservation_matches(
    reservation: CreditReservation | dict[str, Any] | None,
    *,
    organization_id: int,
    feature: Feature | str,
    role_id: int | None,
    user_id: int | None,
    entity_id: str | None,
    candidate_id: int | None,
    provider: str | None,
    model: str | None,
    request_sha256: str | None,
) -> bool:
    parsed = reservation_from_payload(reservation)
    feature_value = feature.value if isinstance(feature, Feature) else str(feature)
    if (
        parsed is None
        or parsed.version != 2
        or int(parsed.organization_id) != int(organization_id)
        or str(parsed.feature) != feature_value
        or not str(parsed.external_ref).strip()
        or parsed.role_id != role_id
        or parsed.user_id != user_id
        or parsed.entity_id != entity_id
        or parsed.candidate_id != candidate_id
        or parsed.provider != provider
        or parsed.model != model
        or parsed.request_sha256 != request_sha256
    ):
        return False

    live_mode = bool(getattr(settings, "USAGE_METER_LIVE", False))
    if not parsed.live:
        return (
            not live_mode
            and parsed.role_id
            == (int(role_id) if role_id is not None else None)
            and shadow_reservation_proof_is_valid(
                proof=parsed.shadow_proof,
                organization_id=parsed.organization_id,
                feature=parsed.feature,
                amount=parsed.amount,
                external_ref=parsed.external_ref,
                role_id=parsed.role_id,
                user_id=parsed.user_id,
                entity_id=parsed.entity_id,
                candidate_id=parsed.candidate_id,
                provider=parsed.provider,
                model=parsed.model,
                request_sha256=parsed.request_sha256,
            )
        )

    try:
        with SessionLocal() as meter_db:
            hold = (
                meter_db.query(BillingCreditLedger)
                .filter(
                    BillingCreditLedger.organization_id == int(organization_id),
                    BillingCreditLedger.external_ref == parsed.external_ref,
                    BillingCreditLedger.reason == f"reservation:{feature_value}",
                )
                .one_or_none()
            )
            if hold is None:
                return False
            if (
                meter_db.query(BillingCreditLedger.id)
                .filter(
                    BillingCreditLedger.organization_id == int(organization_id),
                    BillingCreditLedger.external_ref
                    == f"{parsed.external_ref}:settled"
                )
                .first()
                is not None
            ):
                return False
            return (
                parsed.live
                and hold_matches_reservation(
                    hold,
                    parsed,
                    allowed_states={"held"},
                )
            )
    except Exception:
        return False


def reservation_self_authenticates(reservation: CreditReservation) -> bool:
    """Validate a parsed reservation against its own immutable identity."""

    return reservation_matches(
        reservation,
        organization_id=reservation.organization_id,
        feature=reservation.feature,
        role_id=reservation.role_id,
        user_id=reservation.user_id,
        entity_id=reservation.entity_id,
        candidate_id=reservation.candidate_id,
        provider=reservation.provider,
        model=reservation.model,
        request_sha256=reservation.request_sha256,
    )


__all__ = ["reservation_matches", "reservation_self_authenticates"]
