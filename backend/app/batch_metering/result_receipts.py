"""Normalized and legacy Anthropic batch receipt lookup helpers."""

from __future__ import annotations

from typing import Any, Optional

from ..models.anthropic_batch_job import AnthropicBatchJob
from ..models.anthropic_batch_result_receipt import (
    AnthropicBatchResultReceipt,
)
from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent

_RESULT_RECEIPTS_KEY = "_metered_results"
_QUERY_CHUNK_SIZE = 500
LegacyFallbackClaim = tuple[tuple[int, str, str, str], int]


def legacy_receipts(context: object) -> dict[str, dict[str, Any]]:
    """Read pre-v188 JSON receipts without ever rewriting them."""

    if not isinstance(context, dict):
        return {}
    receipts = context.get(_RESULT_RECEIPTS_KEY)
    if not isinstance(receipts, dict):
        return {}
    return {
        str(custom_id): dict(receipt)
        for custom_id, receipt in receipts.items()
        if isinstance(receipt, dict)
    }


def normalized_receipt_payload(
    receipt: AnthropicBatchResultReceipt,
) -> dict[str, Any]:
    return {
        "state": str(receipt.state),
        "result_type": str(receipt.result_type),
        "usage_event_id": receipt.usage_event_id,
        "call_log_id": receipt.call_log_id,
        "provider_message_id": receipt.provider_message_id,
    }


def _receipt_outcome(receipt: dict[str, Any]) -> tuple[str, str]:
    return (
        str(receipt.get("state") or ""),
        str(receipt.get("result_type") or ""),
    )


def _receipt_identity_conflicts(
    legacy: dict[str, Any], normalized: dict[str, Any]
) -> bool:
    """Whether two otherwise-equal receipts point at different evidence."""

    for key in ("usage_event_id", "call_log_id", "provider_message_id"):
        legacy_value = legacy.get(key)
        normalized_value = normalized.get(key)
        if legacy_value in {None, ""} or normalized_value in {None, ""}:
            continue
        if str(legacy_value) != str(normalized_value):
            return True
    return False


def load_receipts(
    session: Any,
    batch_row: AnthropicBatchJob,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Load legacy and normalized receipts once for a linear reconciliation."""

    receipts = legacy_receipts(batch_row.context)
    conflicts: set[str] = set()
    normalized = (
        session.query(AnthropicBatchResultReceipt)
        .filter(AnthropicBatchResultReceipt.batch_job_id == int(batch_row.id))
        .all()
    )
    for row in normalized:
        custom_id = str(row.custom_id)
        payload = normalized_receipt_payload(row)
        legacy = receipts.get(custom_id)
        if legacy is not None:
            legacy_state, legacy_type = _receipt_outcome(legacy)
            state, result_type = _receipt_outcome(payload)
            if (
                legacy_state != state
                or (legacy_type and result_type and legacy_type != result_type)
                or _receipt_identity_conflicts(legacy, payload)
            ):
                conflicts.add(custom_id)
        receipts[custom_id] = payload
    return receipts, conflicts


def _chunks(values: set[str]) -> list[list[str]]:
    ordered = sorted(values)
    return [
        ordered[index : index + _QUERY_CHUNK_SIZE]
        for index in range(0, len(ordered), _QUERY_CHUNK_SIZE)
    ]


def load_existing_call_logs(
    session: Any,
    *,
    entries: list,
) -> tuple[dict[str, ClaudeCallLog], set[str]]:
    provider_ids = {
        str(getattr(getattr(entry, "result", None), "message", None).id)
        for entry in entries
        if getattr(getattr(entry, "result", None), "message", None) is not None
        and getattr(
            getattr(getattr(entry, "result", None), "message", None),
            "id",
            None,
        )
    }
    by_provider_id: dict[str, ClaudeCallLog] = {}
    duplicates: set[str] = set()
    for provider_id_chunk in _chunks(provider_ids):
        rows = (
            session.query(ClaudeCallLog)
            .filter(
                ClaudeCallLog.anthropic_request_id.in_(provider_id_chunk),
                ClaudeCallLog.status == "ok",
            )
            .order_by(ClaudeCallLog.id.desc())
            .all()
        )
        for row in rows:
            if row.anthropic_request_id:
                provider_id = str(row.anthropic_request_id)
                if provider_id in by_provider_id:
                    duplicates.add(provider_id)
                else:
                    by_provider_id[provider_id] = row
    return by_provider_id, duplicates


def load_existing_usage_events(
    session: Any,
    *,
    batch_id: str,
) -> tuple[
    dict[tuple, UsageEvent],
    dict[tuple, list[UsageEvent]],
    dict[int, UsageEvent],
]:
    """Prefetch legacy partial-pass events in one batch-scoped query."""

    rows = (
        session.query(UsageEvent)
        .filter(
            UsageEvent.event_metadata.isnot(None),
            UsageEvent.event_metadata["batch_id"].as_string() == batch_id,
        )
        .order_by(UsageEvent.id.desc())
        .all()
    )
    exact: dict[tuple, UsageEvent] = {}
    without_custom_id: dict[tuple, list[UsageEvent]] = {}
    by_id: dict[int, UsageEvent] = {}
    for event in rows:
        by_id[int(event.id)] = event
        metadata = (
            event.event_metadata if isinstance(event.event_metadata, dict) else {}
        )
        common = (
            int(event.organization_id),
            str(event.feature),
            str(event.entity_id),
            str(event.model),
        )
        custom_id = str(metadata.get("batch_custom_id") or "")
        if custom_id:
            exact.setdefault((custom_id, *common), event)
        else:
            without_custom_id.setdefault(common, []).append(event)
    return exact, without_custom_id, by_id


def discard_receipted_usage_events(
    *,
    receipts: dict[str, dict[str, Any]],
    exact: dict[tuple, UsageEvent],
    without_custom_id: dict[tuple, list[UsageEvent]],
) -> set[int]:
    """Remove already-receipted events from legacy recovery inventory."""

    receipt_event_ids: set[int] = set()
    for receipt in receipts.values():
        try:
            usage_event_id = int(receipt.get("usage_event_id"))
        except (TypeError, ValueError):
            continue
        receipt_event_ids.add(usage_event_id)
    if not receipt_event_ids:
        return receipt_event_ids
    for key, event in list(exact.items()):
        if int(event.id) in receipt_event_ids:
            exact.pop(key, None)
    for key, events in list(without_custom_id.items()):
        remaining = [
            event for event in events if int(event.id) not in receipt_event_ids
        ]
        if remaining:
            without_custom_id[key] = remaining
        else:
            without_custom_id.pop(key, None)
    return receipt_event_ids


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cache_creation_1h(usage: object) -> Optional[int]:
    cache_creation = getattr(usage, "cache_creation", None)
    value = getattr(cache_creation, "ephemeral_1h_input_tokens", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_matches_log(log: ClaudeCallLog, usage: object) -> bool:
    expected_cache_creation_1h = _cache_creation_1h(usage)
    stored_cache_creation_1h = log.cache_creation_1h_tokens
    cache_creation_1h_matches = (
        stored_cache_creation_1h in {None, 0}
        if expected_cache_creation_1h is None
        else int(stored_cache_creation_1h or 0) == expected_cache_creation_1h
    )
    return bool(
        int(log.input_tokens or 0) == _safe_int(getattr(usage, "input_tokens", 0))
        and int(log.output_tokens or 0) == _safe_int(getattr(usage, "output_tokens", 0))
        and int(log.cache_read_tokens or 0)
        == _safe_int(getattr(usage, "cache_read_input_tokens", 0))
        and int(log.cache_creation_tokens or 0)
        == _safe_int(getattr(usage, "cache_creation_input_tokens", 0))
        and cache_creation_1h_matches
    )


def _usage_matches_event(event: UsageEvent, usage: object) -> bool:
    expected_cache_creation_1h = _cache_creation_1h(usage)
    stored_cache_creation_1h = event.cache_creation_1h_tokens
    cache_creation_1h_matches = (
        stored_cache_creation_1h in {None, 0}
        if expected_cache_creation_1h is None
        else int(stored_cache_creation_1h or 0) == expected_cache_creation_1h
    )
    return bool(
        int(event.input_tokens or 0) == _safe_int(getattr(usage, "input_tokens", 0))
        and int(event.output_tokens or 0)
        == _safe_int(getattr(usage, "output_tokens", 0))
        and int(event.cache_read_tokens or 0)
        == _safe_int(getattr(usage, "cache_read_input_tokens", 0))
        and int(event.cache_creation_tokens or 0)
        == _safe_int(getattr(usage, "cache_creation_input_tokens", 0))
        and cache_creation_1h_matches
    )


def match_existing_call_log(
    *,
    log: ClaudeCallLog,
    batch_id: str,
    custom_id: str,
    organization_id: Optional[int],
    feature: str,
    entity_id: str,
    model: str,
    usage: object,
    usage_events_by_id: dict[int, UsageEvent],
) -> Optional[tuple[UsageEvent, Optional[LegacyFallbackClaim]]]:
    """Prove an old call log belongs to this exact batch result.

    Provider message IDs are global identities, not a license to reuse another
    batch's billing rows.  Legacy partial-pass recovery is allowed only when the
    linked usage event carries this batch's durable metadata and attribution.
    """

    if organization_id is None or log.usage_event_id is None:
        return None
    if (
        log.organization_id is None
        or int(log.organization_id) != int(organization_id)
        or str(log.feature_hint or "") != str(feature)
        or str(log.model) != str(model)
        or not _usage_matches_log(log, usage)
    ):
        return None
    event = usage_events_by_id.get(int(log.usage_event_id))
    if event is None:
        return None
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    common = (
        int(organization_id),
        str(feature),
        str(entity_id),
        str(model),
    )
    if (
        str(metadata.get("batch_id") or "") != str(batch_id)
        or int(event.organization_id) != common[0]
        or str(event.feature) != common[1]
        or str(event.entity_id) != common[2]
        or str(event.model) != common[3]
        or not _usage_matches_event(event, usage)
    ):
        return None
    event_custom_id = str(metadata.get("batch_custom_id") or "")
    if event_custom_id and event_custom_id != str(custom_id):
        return None
    claim = None if event_custom_id else (common, int(event.id))
    return event, claim


def existing_usage_event(
    *,
    custom_id: str,
    organization_id: int,
    feature: str,
    entity_id: str,
    model: str,
    usage: object,
    exact: dict[tuple, UsageEvent],
    without_custom_id: dict[tuple, list[UsageEvent]],
) -> tuple[Optional[UsageEvent], Optional[LegacyFallbackClaim], bool]:
    """Find an event left by the pre-receipt implementation's partial pass."""

    common = (
        int(organization_id),
        str(feature),
        str(entity_id),
        str(model),
    )
    exact_event = exact.get((custom_id, *common))
    if exact_event is not None:
        if not _usage_matches_event(exact_event, usage):
            return None, None, True
        return exact_event, None, False
    fallback_events = without_custom_id.get(common) or []
    if not fallback_events:
        return None, None, False
    # The caller consumes this key only after its savepoint succeeds. This
    # prevents one anonymous legacy event from satisfying multiple results and
    # avoids leaking in-memory state when local receipt work rolls back.
    for event in reversed(fallback_events):
        if _usage_matches_event(event, usage):
            return event, (common, int(event.id)), False
    return None, None, True


def consume_legacy_fallback(
    without_custom_id: dict[tuple, list[UsageEvent]],
    claim: LegacyFallbackClaim,
) -> None:
    """Consume the exact anonymous event only after its savepoint succeeds."""

    common, usage_event_id = claim
    events = without_custom_id.get(common)
    if not events:
        return
    remaining = [event for event in events if int(event.id) != usage_event_id]
    if remaining:
        without_custom_id[common] = remaining
    else:
        without_custom_id.pop(common, None)


__all__ = [
    "LegacyFallbackClaim",
    "consume_legacy_fallback",
    "discard_receipted_usage_events",
    "existing_usage_event",
    "load_existing_call_logs",
    "load_existing_usage_events",
    "load_receipts",
    "match_existing_call_log",
    "normalized_receipt_payload",
]
