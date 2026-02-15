from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..platform.config import settings


def lemon_pack_catalog() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(settings.LEMON_PACKS_JSON or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for pack_id, pack in raw.items():
        if not isinstance(pack, dict):
            continue
        variant_id = str(pack.get("variant_id") or "").strip()
        credits = int(pack.get("credits") or 0)
        if not variant_id or credits <= 0:
            continue
        output[str(pack_id)] = {
            "variant_id": variant_id,
            "credits": credits,
            "label": str(pack.get("label") or pack_id),
        }
    return output


def resolve_pack(pack_id: str) -> dict[str, Any] | None:
    catalog = lemon_pack_catalog()
    key = str(pack_id)
    pack = catalog.get(key)
    if pack:
        return pack

    # Backwards-compat: legacy pack ids used before we standardized on 5/10/20.
    legacy_aliases = {
        "growth_15": "growth_10",
        "scale_50": "scale_20",
    }
    alias = legacy_aliases.get(key)
    if alias:
        return catalog.get(alias)
    return None


def resolve_pack_by_variant(variant_id: str) -> tuple[str, dict[str, Any]] | None:
    target = str(variant_id or "").strip()
    for pack_id, pack in lemon_pack_catalog().items():
        if str(pack.get("variant_id")) == target:
            return pack_id, pack
    return None


def append_credit_ledger_entry(
    db: Session,
    *,
    organization: Organization,
    delta: int,
    reason: str,
    external_ref: str | None = None,
    assessment_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[BillingCreditLedger, bool]:
    if external_ref:
        existing = (
            db.query(BillingCreditLedger)
            .filter(BillingCreditLedger.external_ref == external_ref)
            .first()
        )
        if existing:
            return existing, False

    current = int(organization.credits_balance or 0)
    next_balance = current + int(delta)
    if next_balance < 0:
        raise ValueError("insufficient_credits")
    organization.credits_balance = next_balance

    entry = BillingCreditLedger(
        organization_id=organization.id,
        delta=int(delta),
        balance_after=next_balance,
        reason=reason,
        external_ref=external_ref,
        assessment_id=assessment_id,
        entry_metadata=metadata or {},
    )
    db.add(entry)
    db.flush()
    return entry, True
