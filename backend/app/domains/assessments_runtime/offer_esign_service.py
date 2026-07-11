"""P2 e-sign handoff: a vendor-neutral signature-request *shape* for an offer.

Read-only. Produces the payload an e-sign provider (Dropbox Sign, DocuSign, …)
would consume to create a signature request — signer, document reference, and
the fields to prefill. The actual create-signature-request call is a later,
credentialed integration; this is the contract it maps from.
"""
from __future__ import annotations

from typing import Any, Dict

from ...models.offer import (
    OFFER_STATUS_APPROVED,
    OFFER_STATUS_SENT,
    Offer,
)


def build_esign_request(offer: Offer) -> Dict[str, Any]:
    """Map an offer to a provider-neutral signature-request shape.

    ``ready_to_send`` is True once the offer is approved (or already sent) — an
    offer still in draft/pending-approval should not be dispatched for signature.
    """
    application = offer.application
    candidate = getattr(application, "candidate", None)
    role = getattr(application, "role", None)

    role_name = getattr(role, "name", None) or "the role"
    return {
        "offer": {
            "id": offer.id,
            "version": offer.version,
            "status": offer.status,
        },
        "document": {
            "title": f"Offer of Employment — {role_name}",
            "reference": f"offer-{offer.id}-v{offer.version}",
        },
        "signers": [
            {
                "role": "candidate",
                "name": getattr(candidate, "full_name", None),
                "email": getattr(candidate, "email", None),
            }
        ],
        "prefill_fields": {
            "base_salary_amount": offer.base_salary_amount,
            "currency": offer.currency,
            "pay_frequency": offer.pay_frequency,
            "signing_bonus": offer.signing_bonus,
            "start_date": offer.starts_at.isoformat() if offer.starts_at else None,
        },
        "ready_to_send": offer.status in (OFFER_STATUS_APPROVED, OFFER_STATUS_SENT),
    }
