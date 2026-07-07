"""P2 HRIS handoff: a vendor-neutral export *shape* for an offer.

Read-only. Produces the structured payload an HRIS import would consume
(employee + position + typed compensation). Deliberately vendor-agnostic — the
actual push to a specific HRIS (BambooHR, Workday, …) is a later, credentialed
integration; this is the contract it will map from. Carrying currency +
pay_frequency here is the point: the Workable->HRIS path drops them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from ...models.offer import OFFER_STATUS_ACCEPTED, Offer


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def build_hris_payload(offer: Offer) -> Dict[str, Any]:
    """Map an offer (+ its application/candidate/role) to the HRIS import shape.

    ``hris_ready`` is True only once the offer is accepted — a caller can build
    the payload early for preview, but should not hand it off before then.
    """
    application = offer.application
    candidate = getattr(application, "candidate", None)
    role = getattr(application, "role", None)

    return {
        "offer": {
            "id": offer.id,
            "version": offer.version,
            "status": offer.status,
            "hris_ready": offer.status == OFFER_STATUS_ACCEPTED,
        },
        "employee": {
            "full_name": getattr(candidate, "full_name", None),
            "email": getattr(candidate, "email", None),
            "phone": getattr(candidate, "phone", None),
        },
        "position": {
            "title": getattr(role, "name", None),
            "department": getattr(role, "department", None),
            "employment_type": getattr(role, "employment_type", None),
            "workplace_type": getattr(role, "workplace_type", None),
            "location": {
                "city": getattr(role, "location_city", None),
                "country": getattr(role, "location_country", None),
            },
        },
        "compensation": {
            "base_salary_amount": offer.base_salary_amount,
            "currency": offer.currency,
            "pay_frequency": offer.pay_frequency,
            "signing_bonus": offer.signing_bonus,
            "equity_units": offer.equity_units,
            "custom_fields": offer.custom_fields,
        },
        "dates": {
            "starts_at": _iso(offer.starts_at),
            "accepted_at": _iso(offer.accepted_at),
        },
        "source": {
            "application_id": offer.application_id,
            "role_id": getattr(role, "id", None),
            "candidate_id": getattr(candidate, "id", None),
        },
    }
