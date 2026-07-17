"""Per-result Anthropic batch metering within caller-owned savepoints."""

from __future__ import annotations

from typing import Any, Optional

from ..models.anthropic_batch_job import AnthropicBatchJob
from ..models.anthropic_batch_result_receipt import (
    AnthropicBatchResultReceipt,
)
from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent
from ..services.usage_credit_reservations import reservation_from_payload
from ..services.usage_metering_service import record_event
from .result_receipts import (
    LegacyFallbackClaim,
    existing_usage_event,
    normalized_receipt_payload,
)


def add_receipt(
    session: Any,
    *,
    batch_row: AnthropicBatchJob,
    custom_id: str,
    state: str,
    result_type: str,
    usage_event_id: Optional[int] = None,
    call_log_id: Optional[int] = None,
    provider_message_id: Optional[str] = None,
) -> dict[str, Any]:
    row = AnthropicBatchResultReceipt(
        batch_job_id=int(batch_row.id),
        custom_id=custom_id,
        state=state,
        result_type=result_type,
        usage_event_id=usage_event_id,
        call_log_id=call_log_id,
        provider_message_id=provider_message_id,
    )
    session.add(row)
    session.flush()
    return normalized_receipt_payload(row)


def result_details(
    batch_row: AnthropicBatchJob,
    context: dict,
    entry: Any,
) -> dict[str, Any]:
    result = getattr(entry, "result", None)
    custom_id = str(getattr(entry, "custom_id", "") or "")
    per = context.get(custom_id)
    per = per if isinstance(per, dict) else {}
    reservation = per.get("credit_reservation")
    message = getattr(result, "message", None)
    usage = getattr(message, "usage", None)
    model = str(getattr(message, "model", None) or batch_row.model or "")
    org_id = per.get("organization_id")
    if org_id is None:
        org_id = batch_row.organization_id
    parsed_reservation = reservation_from_payload(reservation)
    if org_id is None and parsed_reservation is not None:
        org_id = int(parsed_reservation.organization_id)
    entity_id = str(per.get("entity_id") or custom_id)
    provider_message_id = (
        str(getattr(message, "id", None)) if getattr(message, "id", None) else None
    )
    metering = {
        "feature": batch_row.feature,
        "organization_id": org_id,
        "user_id": per.get("user_id"),
        "role_id": per.get("role_id"),
        "entity_id": entity_id,
        "metadata": {
            "batch_id": batch_row.batch_id,
            "batch_custom_id": custom_id,
        },
        "credit_reservation": reservation,
    }
    return {
        "custom_id": custom_id,
        "reservation": reservation,
        "result_type": str(getattr(result, "type", None) or "not_succeeded"),
        "usage": usage,
        "model": model,
        "organization_id": org_id,
        "entity_id": entity_id,
        "provider_message_id": provider_message_id,
        "metering": metering,
    }


def prepare_provider_outcome(
    messages: Any,
    details: dict[str, Any],
    *,
    receipts: dict[str, dict[str, Any]],
) -> None:
    """Persist provider outcome evidence before the local batch transaction."""

    existing = receipts.get(details["custom_id"])
    if isinstance(existing, dict) and str(existing.get("state") or "") in {
        "metered",
        "skipped",
    }:
        return
    if details["result_type"] != "succeeded":
        messages._release_credit_reservation_safe(
            {"credit_reservation": details["reservation"]},
            reason=f"batch_result:{details['result_type']}",
            allow_started=True,
        )
        return
    messages._mark_provider_success(
        usage=details["usage"],
        model=details["model"],
        metering=details["metering"],
        provider_request_id=details["provider_message_id"],
        service_tier="batch",
    )


def meter_one_result(
    session: Any,
    messages: Any,
    details: dict[str, Any],
    *,
    batch_row: AnthropicBatchJob,
    receipts: dict[str, dict[str, Any]],
    existing_logs: dict[str, ClaudeCallLog],
    existing_log_matches: dict[
        str,
        tuple[ClaudeCallLog, UsageEvent, Optional[LegacyFallbackClaim]],
    ],
    exact_usage_events: dict[tuple, UsageEvent],
    usage_events_without_custom_id: dict[tuple, list[UsageEvent]],
) -> tuple[
    str,
    Optional[dict[str, Any]],
    Optional[ClaudeCallLog],
    Optional[LegacyFallbackClaim],
]:
    """Stage one atomic result receipt inside the caller's savepoint."""

    custom_id = str(details["custom_id"])
    existing_receipt = receipts.get(custom_id)
    if isinstance(existing_receipt, dict):
        state = str(existing_receipt.get("state") or "")
        if state in {"metered", "skipped"}:
            return state, None, None, None

    reservation = details["reservation"]
    result_type = str(details["result_type"])
    if result_type != "succeeded":
        receipt = add_receipt(
            session,
            batch_row=batch_row,
            custom_id=custom_id,
            state="skipped",
            result_type=result_type,
        )
        return "skipped", receipt, None, None

    usage = details["usage"]
    model = str(details["model"])
    org_id = details["organization_id"]
    entity_id = str(details["entity_id"])
    provider_message_id = details["provider_message_id"]
    result_metering = details["metering"]
    if usage is None:
        if reservation:
            return "failed", None, None, None
        receipt = add_receipt(
            session,
            batch_row=batch_row,
            custom_id=custom_id,
            state="skipped",
            result_type="missing_usage",
            provider_message_id=provider_message_id,
        )
        return "skipped", receipt, None, None

    existing_match = existing_log_matches.get(custom_id)
    existing_log = existing_match[0] if existing_match is not None else None
    usage_event = existing_match[1] if existing_match is not None else None
    legacy_fallback_claim = existing_match[2] if existing_match is not None else None
    if (
        existing_match is None
        and provider_message_id is not None
        and provider_message_id in existing_logs
    ):
        # Every pre-existing provider identity must have been proven by the
        # caller before provider-success or billing work began.
        return "failed", None, None, None
    if usage_event is None and org_id is not None:
        (
            usage_event,
            legacy_fallback_claim,
            legacy_usage_conflict,
        ) = existing_usage_event(
            custom_id=custom_id,
            organization_id=int(org_id),
            feature=str(batch_row.feature),
            entity_id=entity_id,
            model=model,
            usage=usage,
            exact=exact_usage_events,
            without_custom_id=usage_events_without_custom_id,
        )
        if legacy_usage_conflict:
            return "failed", None, None, None
    if usage_event is None and org_id is not None:
        payload = messages._usage_event_payload(
            usage=usage,
            model=model,
            metering=result_metering,
            service_tier="batch",
        )
        if payload is None:
            return "failed", None, None, None
        usage_event = record_event(
            session,
            **payload,
            credit_reservation=reservation,
        )

    usage_event_id = int(usage_event.id) if usage_event is not None else None
    if existing_log is None:
        existing_log = messages._build_call_log_row(
            organization_id=int(org_id) if org_id is not None else None,
            model=model,
            usage=usage,
            feature_hint=str(batch_row.feature),
            status="ok",
            error_reason=None,
            anthropic_request_id=provider_message_id,
            usage_event_id=usage_event_id,
            service_tier="batch",
        )
        session.add(existing_log)
    elif existing_log.usage_event_id is None and usage_event_id is not None:
        existing_log.usage_event_id = usage_event_id
    session.flush()

    receipt = add_receipt(
        session,
        batch_row=batch_row,
        custom_id=custom_id,
        state="metered",
        result_type="succeeded",
        usage_event_id=usage_event_id,
        call_log_id=int(existing_log.id),
        provider_message_id=provider_message_id,
    )
    return "metered", receipt, existing_log, legacy_fallback_claim


__all__ = [
    "add_receipt",
    "meter_one_result",
    "prepare_provider_outcome",
    "result_details",
]
