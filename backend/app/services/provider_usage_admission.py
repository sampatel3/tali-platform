"""Shared hard-admission helpers for one metered provider call.

The metered Anthropic wrapper owns actual-usage settlement.  Callers use this
module immediately before a provider request to atomically hold the estimated
charge against both the organization balance and the role's monthly ceiling,
then thread ``credit_reservation`` into the wrapper's metering payload.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from ..llm.core import ProviderAuthorityError
from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..models.role import Role
from ..platform.database import SessionLocal
from .pricing_service import Feature
from .usage_credit_reservations import (
    CreditReservation,
    release_credit_reservation,
    reservation_from_payload,
    reserve_credits,
)

logger = logging.getLogger("taali.provider_usage_admission")

PROVIDER_ATTEMPT_STARTED_STATE = "provider_attempt_started"
PROVIDER_SUCCEEDED_PENDING_STATE = "provider_succeeded_metering_pending"
PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE = "provider_succeeded_usage_unknown"


class AutomaticProviderAuthorityError(ProviderAuthorityError):
    """A live role/workspace control no longer authorizes provider spend."""


def _lock_and_require_automatic_role_authority(
    db: Any,
    *,
    organization_id: int,
    role_id: int | None,
) -> None:
    """Linearize an autonomous provider attempt with both control switches.

    Graphiti can make several Anthropic and Voyage calls during one admitted
    episode.  A recruiter may pause the workspace or role between those calls,
    so the initial task/outbox gate is not sufficient.  Every real provider
    reservation takes the workspace row first and the role row second, then
    evaluates their current values in that same transaction.
    """

    if role_id is None:
        raise AutomaticProviderAuthorityError(
            "autonomous provider admission requires role attribution"
        )
    organization = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .with_for_update(of=Organization)
        .populate_existing()
        .one_or_none()
    )
    if organization is None:
        raise AutomaticProviderAuthorityError("workspace is unavailable")
    if organization.agent_workspace_paused_at is not None:
        raise AutomaticProviderAuthorityError("workspace agent is paused")

    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .populate_existing()
        .one_or_none()
    )
    if role is None:
        raise AutomaticProviderAuthorityError("role is unavailable")
    if not bool(role.agentic_mode_enabled):
        raise AutomaticProviderAuthorityError("role agent is disabled")
    if role.agent_paused_at is not None:
        raise AutomaticProviderAuthorityError("role agent is paused")


def serialize_provider_work(
    db: Any,
    *,
    scope: str,
    entity_id: int,
) -> None:
    """Serialize duplicate provider jobs for one entity on Postgres.

    Celery is at-least-once, so a publish/update burst can deliver the same
    role-artifact task to two workers before either has populated its cache.
    The transaction-scoped advisory lock makes the second worker wait, then
    re-read the cache written by the first. SQLite remains a no-op for tests
    and local development.
    """

    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    if bind is None or getattr(bind.dialect, "name", None) != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope), :entity_id)"),
        {"scope": str(scope), "entity_id": int(entity_id)},
    )


def reserve_provider_usage(
    *,
    organization_id: int,
    role_id: int | None,
    feature: Feature | str,
    trace_id: str,
    entity_id: str | None = None,
    sub_feature: str | None = None,
    amount: int | None = None,
    metadata: dict[str, Any] | None = None,
    require_role_authority: bool = False,
) -> CreditReservation:
    """Commit one durable hold before an attributed provider call.

    The random suffix deliberately gives each actual SDK attempt its own hold.
    Reusing one reservation for two provider calls would make the second
    settlement look like a duplicate and undercharge it.  Ledger settlement and
    release remain idempotent on the returned reservation's external ref.
    ``role_id=None`` is reserved for genuine workspace-level work (for example,
    a user graph search); organization credit admission still applies, while no
    role attribution is invented.
    """

    feature_value = feature.value if isinstance(feature, Feature) else str(feature)
    ref = (
        f"usage-hold:{feature_value}:{str(trace_id).strip() or 'untraced'}:"
        f"{uuid.uuid4().hex}"
    )
    with SessionLocal() as meter_db:
        if require_role_authority:
            _lock_and_require_automatic_role_authority(
                meter_db,
                organization_id=int(organization_id),
                role_id=int(role_id) if role_id is not None else None,
            )
        reservation = reserve_credits(
            meter_db,
            organization_id=int(organization_id),
            feature=feature,
            external_ref=ref,
            amount=amount,
            metadata={
                **dict(metadata or {}),
                "sub_feature": sub_feature or feature_value,
                "role_id": int(role_id) if role_id is not None else None,
                "entity_id": entity_id,
                "trace_id": str(trace_id),
            },
            role_id=int(role_id) if role_id is not None else None,
            enforce_role_budget=role_id is not None,
        )
        meter_db.commit()
        return reservation


def with_credit_reservation(
    metering: dict[str, Any] | None,
    reservation: CreditReservation,
) -> dict[str, Any]:
    """Return a copy of ``metering`` carrying the wrapper settlement payload."""

    return {
        **dict(metering or {}),
        "credit_reservation": reservation.as_metering_payload(),
    }


def release_provider_usage(
    reservation: CreditReservation | dict[str, Any] | None,
    *,
    reason: str,
    allow_started: bool = False,
) -> None:
    """Best-effort compensation; started attempts are protected by default."""

    if reservation is None:
        return
    try:
        with SessionLocal() as meter_db:
            release_credit_reservation(
                meter_db,
                reservation=reservation,
                reason=reason,
                allow_started=allow_started,
            )
            meter_db.commit()
    except Exception:
        # Conservatively retain the traceable hold. Pre-call holds can be
        # refunded by stale recovery; attempt-started holds remain protected
        # because an ambiguous provider outcome cannot safely be made free.
        logger.exception("failed to release provider usage reservation")


def provider_error_is_definitely_nonbillable(error: BaseException) -> bool:
    """Return true only for an explicit provider rejection with no result.

    Connection failures, read timeouts, 5xx responses, and unknown exception
    types are outcome-ambiguous: the provider may have accepted and billed the
    request before the client lost the response. Those must retain the durable
    attempt hold. Safe evidence is either an internal transport-boundary error
    explicitly marked ``provider_not_called`` or an allowlisted provider
    rejection status that cannot carry billable output.
    """

    # Internal adapter/transport-boundary contract failures are raised only
    # while the underlying SDK is still untouched.  Routed adapters may have
    # already persisted their conservative "attempt started" marker by then,
    # so this explicit evidence must be eligible for allow_started release.
    if bool(getattr(error, "provider_not_called", False)):
        return True

    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    return code in {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}


def release_provider_usage_if_definitely_nonbillable(
    reservation: CreditReservation | dict[str, Any] | None,
    *,
    error: BaseException,
    reason: str,
) -> bool:
    """Release a hold only when provider rejection is unambiguous."""

    parsed = reservation_from_payload(reservation)
    if parsed is None or not parsed.live:
        return False
    if not provider_error_is_definitely_nonbillable(error):
        logger.warning(
            "retaining ambiguous provider attempt ref=%s error=%s",
            parsed.external_ref,
            type(error).__name__,
        )
        return False
    release_provider_usage(
        reservation,
        reason=reason,
        allow_started=True,
    )
    return True


def mark_provider_attempt_started(
    reservation: CreditReservation | dict[str, Any] | None,
    *,
    provider: str,
    attempt_ref: str | None = None,
) -> bool:
    """Durably record the last safe point before a paid SDK invocation.

    The marker closes the otherwise unavoidable window where the provider
    returns successfully, both the success-receipt and UsageEvent writes fail,
    and the generic stale-hold sweep later mistakes real spend for an abandoned
    pre-call hold.  Explicit provider errors settle/release this hold; an
    ambiguous worker/network failure remains protected for reconciliation.
    """

    parsed = reservation_from_payload(reservation)
    if parsed is None or not parsed.live:
        return True
    try:
        with SessionLocal() as meter_db:
            hold = (
                meter_db.query(BillingCreditLedger)
                .filter(
                    BillingCreditLedger.organization_id == int(parsed.organization_id),
                    BillingCreditLedger.external_ref == parsed.external_ref,
                    BillingCreditLedger.reason.like("reservation:%"),
                )
                .with_for_update()
                .one_or_none()
            )
            if hold is None:
                return False
            if (
                meter_db.query(BillingCreditLedger.id)
                .filter(
                    BillingCreditLedger.external_ref == f"{parsed.external_ref}:settled"
                )
                .first()
                is not None
            ):
                # One reservation represents exactly one provider attempt.
                # Reusing a settled hold would make a second call free.
                return False
            metadata = (
                dict(hold.entry_metadata)
                if isinstance(hold.entry_metadata, dict)
                else {}
            )
            state = str(metadata.get("state") or "")
            if state == PROVIDER_ATTEMPT_STARTED_STATE:
                # A live reservation funds exactly one physical attempt even
                # across processes or newly-created adapter instances. Legacy
                # callers omit attempt_ref and retain their old idempotency;
                # routed callers always bind the durable invocation/ordinal.
                if attempt_ref is None:
                    return True
                return metadata.get("provider_attempt_ref") == str(attempt_ref)
            if state in {
                PROVIDER_SUCCEEDED_PENDING_STATE,
                PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
            }:
                return False
            metadata.update(
                {
                    "state": PROVIDER_ATTEMPT_STARTED_STATE,
                    "provider": str(provider),
                    "provider_attempt_ref": (
                        str(attempt_ref) if attempt_ref is not None else None
                    ),
                    "provider_attempt_started_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            )
            hold.entry_metadata = metadata
            meter_db.add(hold)
            meter_db.commit()
            return True
    except Exception:
        logger.exception(
            "failed to mark provider attempt for reservation ref=%s",
            getattr(parsed, "external_ref", None),
        )
        return False


def mark_provider_usage_succeeded(
    reservation: CreditReservation | dict[str, Any] | None,
    *,
    deferred_usage_event: dict[str, Any] | None,
    provider: str,
    provider_request_id: str | None = None,
) -> bool:
    """Mark a committed hold as known-billable before normal settlement.

    If the following UsageEvent write fails, the recovery sweep can rebuild
    the exact event from this minimal receipt instead of refunding real
    provider spend as though the worker died before making the call.
    """
    parsed = reservation_from_payload(reservation)
    if parsed is None or not parsed.live:
        return False
    try:
        with SessionLocal() as meter_db:
            hold = (
                meter_db.query(BillingCreditLedger)
                .filter(
                    BillingCreditLedger.organization_id == int(parsed.organization_id),
                    BillingCreditLedger.external_ref == parsed.external_ref,
                    BillingCreditLedger.reason.like("reservation:%"),
                )
                .with_for_update()
                .one_or_none()
            )
            if hold is None:
                return False
            if (
                meter_db.query(BillingCreditLedger.id)
                .filter(
                    BillingCreditLedger.external_ref == f"{parsed.external_ref}:settled"
                )
                .first()
                is not None
            ):
                return True
            metadata = (
                dict(hold.entry_metadata)
                if isinstance(hold.entry_metadata, dict)
                else {}
            )
            metadata.update(
                {
                    "state": (
                        PROVIDER_SUCCEEDED_PENDING_STATE
                        if deferred_usage_event is not None
                        else PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
                    ),
                    "provider": str(provider),
                    "provider_request_id": provider_request_id,
                    "provider_succeeded_at": datetime.now(timezone.utc).isoformat(),
                    "deferred_usage_event": (
                        dict(deferred_usage_event)
                        if deferred_usage_event is not None
                        else None
                    ),
                }
            )
            # Assign a fresh dict so SQLAlchemy always notices the JSON change.
            hold.entry_metadata = metadata
            meter_db.add(hold)
            meter_db.commit()
            return True
    except Exception:
        # Normal settlement still gets a chance. Never release here: the
        # provider already returned a potentially billable result.
        logger.exception(
            "failed to mark provider success for reservation ref=%s",
            getattr(parsed, "external_ref", None),
        )
        return False


__all__ = [
    "AutomaticProviderAuthorityError",
    "PROVIDER_ATTEMPT_STARTED_STATE",
    "PROVIDER_SUCCEEDED_PENDING_STATE",
    "PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE",
    "mark_provider_attempt_started",
    "mark_provider_usage_succeeded",
    "provider_error_is_definitely_nonbillable",
    "release_provider_usage",
    "release_provider_usage_if_definitely_nonbillable",
    "reserve_provider_usage",
    "serialize_provider_work",
    "with_credit_reservation",
]
