"""Fail-closed compatibility API for the retired generic ledger writer.

Credit mutation is now coupled to provider reservations, metered events, or
explicit grants in the canonical usage services.  A generic balance-and-ledger
write cannot safely delegate because it lacks the feature, reservation, and
usage-event invariants those services require.  The import and call signature
remain available so older integrations receive a deterministic error rather
than mutating balances through the superseded non-locking path.
"""

from __future__ import annotations

from typing import Any, NoReturn

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization


class DeprecatedCreditLedgerAPIError(RuntimeError):
    """The caller must select a canonical, transaction-aware credit flow."""


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
    """Reject the superseded generic mutation before touching the session.

    Use ``usage_credit_reservations`` for paid calls and
    ``usage_metering_service.grant_credits`` for explicit grants.
    """

    _fail_closed(
        db,
        organization,
        delta,
        reason,
        external_ref,
        assessment_id,
        metadata,
    )


def _fail_closed(*_unused: object) -> NoReturn:
    raise DeprecatedCreditLedgerAPIError(
        "append_credit_ledger_entry is retired; use usage_credit_reservations "
        "for paid calls or usage_metering_service.grant_credits for grants"
    )


__all__ = ["DeprecatedCreditLedgerAPIError", "append_credit_ledger_entry"]
