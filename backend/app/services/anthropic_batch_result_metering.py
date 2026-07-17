"""Durable, per-result metering receipts for Anthropic Message Batches."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from ..batch_metering.result_processing import (
    meter_one_result as _meter_one_result,
    prepare_provider_outcome as _prepare_provider_outcome,
    result_details as _result_details,
)
from ..batch_metering.result_receipts import (
    consume_legacy_fallback as _consume_legacy_fallback,
    discard_receipted_usage_events as _discard_receipted_usage_events,
    load_existing_call_logs as _load_existing_call_logs,
    load_existing_usage_events as _load_existing_usage_events,
    load_receipts as _load_receipts,
    match_existing_call_log as _match_existing_call_log,
)
from ..models.anthropic_batch_job import AnthropicBatchJob
from ..platform.database import SessionLocal
from .anthropic_batch_result_attribution import (
    result_identity_issues,
    store_attribution_pending,
    store_attribution_validated,
    strict_result_contract,
)
from .pricing_service import Feature

logger = logging.getLogger("taali.metered_anthropic")


def _prepare_anchor(
    *, batch_id: str, entries: list
) -> tuple[str, Optional[frozenset[str]]]:
    """Create a legacy anchor or validate a strict anchor before any billing."""

    with SessionLocal() as session:
        row = (
            session.query(AnthropicBatchJob)
            .filter_by(batch_id=batch_id)
            .with_for_update()
            .first()
        )
        if row is None:
            logger.warning(
                "metered_anthropic: results for unknown batch_id=%s "
                "(submitted outside the wrapper?) — metering as "
                "Feature.OTHER with no org attribution.",
                batch_id,
            )
            session.add(
                AnthropicBatchJob(
                    batch_id=batch_id,
                    feature=Feature.OTHER.value,
                    request_count=len(entries),
                    status="submitted",
                    context={},
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # A concurrent poll may have created the same compatibility row.
                session.rollback()
            row = (
                session.query(AnthropicBatchJob)
                .filter_by(batch_id=batch_id)
                .with_for_update()
                .one()
            )
        receipts, receipt_conflicts = _load_receipts(session, row)
        expected_ids, issues = strict_result_contract(
            row,
            entries,
            receipts=receipts,
        )
        if receipt_conflicts:
            issues.append("conflicting_metering_receipts")
        if expected_ids is None:
            if row.metered_at is not None:
                return "latched", None
            legacy_issues = result_identity_issues(entries)
            if legacy_issues:
                logger.error(
                    "metered_anthropic: unsafe legacy result identities "
                    "failed closed (batch_id=%s issues=%s)",
                    batch_id,
                    sorted(set(legacy_issues)),
                )
                return "reconciliation_pending", None
            return "legacy", None
        if issues:
            store_attribution_pending(
                row,
                entries=entries,
                expected_ids=expected_ids,
                issues=issues,
            )
            if row.status == "ended":
                row.status = "submitted"
            if row.status != "results_applied":
                row.metered_at = None
            session.commit()
            logger.error(
                "metered_anthropic: strict batch result attribution failed "
                "closed (batch_id=%s issues=%s)",
                batch_id,
                sorted(set(issues)),
            )
            return "reconciliation_pending", expected_ids
        validation_written = store_attribution_validated(
            row,
            entries=entries,
            expected_ids=expected_ids,
        )
        if row.metered_at is not None:
            if validation_written:
                session.commit()
            return "latched", expected_ids
        if validation_written:
            session.commit()
        return "strict", expected_ids


def meter_batch_results_safe(messages: Any, *, batch_id: str, entries: list) -> None:
    """Meter a result stream idempotently without hiding provider results."""

    try:
        mode, strict_expected_ids = _prepare_anchor(
            batch_id=batch_id,
            entries=entries,
        )
        if mode in {"latched", "reconciliation_pending"}:
            return
        with SessionLocal() as session:
            row = (
                session.query(AnthropicBatchJob)
                .filter_by(batch_id=batch_id)
                .with_for_update()
                .one()
            )
            if row.metered_at is not None:
                return
            context = dict(row.context) if isinstance(row.context, dict) else {}
            receipts, receipt_conflicts = _load_receipts(session, row)
            if strict_expected_ids is not None:
                current_expected_ids, issues = strict_result_contract(
                    row,
                    entries,
                    receipts=receipts,
                )
                receipt_ids = set(receipts)
                if current_expected_ids != strict_expected_ids:
                    issues.append("submitted_attribution_changed")
                if receipt_ids.difference(strict_expected_ids):
                    issues.append("unexpected_metering_receipts")
                if receipt_conflicts:
                    issues.append("conflicting_metering_receipts")
                if issues:
                    if "prior_reconciliation_pending" in issues:
                        # Another poller invalidated this anchor after this
                        # call's prepare phase. Preserve that poller's first
                        # and latest observation exactly; this already-vetted
                        # stale worker must not overwrite or inflate it.
                        session.rollback()
                        return
                    store_attribution_pending(
                        row,
                        entries=entries,
                        expected_ids=strict_expected_ids,
                        issues=issues,
                    )
                    session.commit()
                    logger.error(
                        "metered_anthropic: strict batch attribution changed "
                        "before latch (batch_id=%s issues=%s)",
                        batch_id,
                        sorted(set(issues)),
                    )
                    return
            elif receipt_conflicts:
                logger.error(
                    "metered_anthropic: conflicting legacy/normalized "
                    "receipts require manual reconciliation (batch_id=%s ids=%s)",
                    batch_id,
                    sorted(receipt_conflicts),
                )
                return

            details_by_entry = [
                _result_details(row, context, entry) for entry in entries
            ]
            existing_logs, duplicate_log_provider_ids = _load_existing_call_logs(
                session,
                entries=entries,
            )
            (
                exact_usage_events,
                usage_events_without_custom_id,
                usage_events_by_id,
            ) = _load_existing_usage_events(session, batch_id=batch_id)
            claimed_usage_event_ids = _discard_receipted_usage_events(
                receipts=receipts,
                exact=exact_usage_events,
                without_custom_id=usage_events_without_custom_id,
            )
            existing_log_matches = {}
            provider_replay_ids: set[str] = set()
            for details in details_by_entry:
                custom_id = str(details["custom_id"])
                existing_receipt = receipts.get(custom_id)
                if isinstance(existing_receipt, dict) and str(
                    existing_receipt.get("state") or ""
                ) in {"metered", "skipped"}:
                    continue
                provider_message_id = details["provider_message_id"]
                if details["result_type"] != "succeeded" or provider_message_id is None:
                    continue
                provider_message_id = str(provider_message_id)
                existing_log = existing_logs.get(provider_message_id)
                if provider_message_id in duplicate_log_provider_ids:
                    provider_replay_ids.add(provider_message_id)
                    continue
                if existing_log is None:
                    continue
                match = _match_existing_call_log(
                    log=existing_log,
                    batch_id=batch_id,
                    custom_id=custom_id,
                    organization_id=details["organization_id"],
                    feature=str(row.feature),
                    entity_id=str(details["entity_id"]),
                    model=str(details["model"]),
                    usage=details["usage"],
                    usage_events_by_id=usage_events_by_id,
                )
                if match is None or int(match[0].id) in claimed_usage_event_ids:
                    provider_replay_ids.add(provider_message_id)
                    continue
                claimed_usage_event_ids.add(int(match[0].id))
                if match[1] is not None:
                    # Reserve anonymous legacy evidence for this exact log
                    # before entry order can let another result claim it.
                    _consume_legacy_fallback(
                        usage_events_without_custom_id,
                        match[1],
                    )
                existing_log_matches[custom_id] = (
                    existing_log,
                    match[0],
                    match[1],
                )
            if provider_replay_ids:
                issues = ["provider_message_id_replay"]
                if strict_expected_ids is not None:
                    store_attribution_pending(
                        row,
                        entries=entries,
                        expected_ids=strict_expected_ids,
                        issues=issues,
                    )
                    if row.status == "ended":
                        row.status = "submitted"
                    row.metered_at = None
                    session.commit()
                else:
                    session.rollback()
                logger.error(
                    "metered_anthropic: provider message identity replay "
                    "failed closed (batch_id=%s provider_ids=%s)",
                    batch_id,
                    sorted(provider_replay_ids)[:20],
                )
                return
            # These markers use independent durable transactions. Complete
            # them before local receipt writes so SQLite tests and production
            # never self-block on a transaction that already holds metering
            # rows. A crash here remains recoverable from the provider-success
            # marker; every operation is idempotent on replay.
            for details in details_by_entry:
                _prepare_provider_outcome(
                    messages,
                    details,
                    receipts=receipts,
                )

            failed = 0
            for details in details_by_entry:
                custom_id = str(details["custom_id"])
                try:
                    with session.begin_nested():
                        (
                            state,
                            receipt,
                            committed_log,
                            consumed_fallback_key,
                        ) = _meter_one_result(
                            session,
                            messages,
                            details,
                            batch_row=row,
                            receipts=receipts,
                            existing_logs=existing_logs,
                            existing_log_matches=existing_log_matches,
                            exact_usage_events=exact_usage_events,
                            usage_events_without_custom_id=(
                                usage_events_without_custom_id
                            ),
                        )
                    if receipt is not None:
                        receipts[custom_id] = receipt
                    provider_message_id = details["provider_message_id"]
                    if committed_log is not None and provider_message_id:
                        # Only publish ORM rows to the cross-entry lookup after
                        # the nested transaction successfully released its
                        # savepoint. A failed savepoint may detach or expire
                        # the row and must leave no in-memory idempotency hint.
                        existing_logs[str(provider_message_id)] = committed_log
                    if consumed_fallback_key is not None:
                        _consume_legacy_fallback(
                            usage_events_without_custom_id,
                            consumed_fallback_key,
                        )
                    if state == "failed":
                        failed += 1
                except Exception:
                    failed += 1
                    logger.exception(
                        "metered_anthropic: atomic batch-result receipt "
                        "failed (batch_id=%s custom_id=%s)",
                        batch_id,
                        custom_id,
                    )

            if failed:
                # Preserve every successful result receipt while leaving the
                # whole-batch latch open. Replay sees the normalized unique
                # rows and retries only missing entries.
                session.commit()
                logger.error(
                    "metered_anthropic: %d of %d batch result(s) failed their "
                    "atomic metering receipt (batch_id=%s) — NOT latching; "
                    "the next results() call retries only missing receipts.",
                    failed,
                    len(entries),
                    batch_id,
                )
                return

            if strict_expected_ids is not None:
                current_expected_ids, issues = strict_result_contract(
                    row,
                    entries,
                    receipts=receipts,
                )
                receipt_ids = set(receipts)
                if current_expected_ids != strict_expected_ids:
                    issues.append("submitted_attribution_changed")
                if receipt_ids.difference(strict_expected_ids):
                    issues.append("unexpected_metering_receipts")
                if issues:
                    store_attribution_pending(
                        row,
                        entries=entries,
                        expected_ids=strict_expected_ids,
                        issues=issues,
                    )
                    session.commit()
                    logger.error(
                        "metered_anthropic: strict batch attribution changed "
                        "before latch (batch_id=%s issues=%s)",
                        batch_id,
                        sorted(set(issues)),
                    )
                    return
                expected_ids = set(strict_expected_ids)
            else:
                expected_ids = {
                    str(details["custom_id"]) for details in details_by_entry
                }
            missing = expected_ids.difference(receipts)
            if missing:
                logger.error(
                    "metered_anthropic: batch result receipt set incomplete "
                    "(batch_id=%s missing=%s) — NOT latching",
                    batch_id,
                    sorted(missing),
                )
                session.commit()
                return
            row.metered_at = datetime.now(timezone.utc)
            row.metered_count = sum(
                1
                for custom_id in expected_ids
                if str((receipts.get(custom_id) or {}).get("state")) == "metered"
            )
            row.status = "ended"
            session.commit()
    except Exception:
        logger.exception(
            "metered_anthropic: batch results metering failed "
            "(batch_id=%s) — results were still returned to the caller, but "
            "reconciliation will undercount until results() is called again.",
            batch_id,
        )


__all__ = ["meter_batch_results_safe"]
