"""Beat tasks driving the Message Batches API pipelines.

Currently cv_parse only (the purest batch fit: background backfill /
post-sync parsing nobody is waiting on, 50% of standard pricing).
Prescreen and score are candidates to follow through the same
submit/poll shape once their latency tradeoffs are signed off.

Two halves, both cheap no-ops when there's nothing to do:

* ``submit_cv_parse_batches`` — gated on ``CV_PARSE_BATCH_ENABLED``;
  sweeps parse-pending applications into per-org batch submissions.
* ``poll_cv_parse_batches`` — deliberately NOT gated on the flag, so
  flipping it off still drains any in-flight batches instead of
  stranding their (already-paid-for) results.

Metering happens inside ``MeteredAnthropicClient`` (claude_call_log +
usage_events at ``service_tier="batch"``, idempotent per batch) — these
tasks only move application state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.anthropic_batch")

_RESULT_APPLICATION_KEY = "_result_application"
_RESULT_APPLIED_STATUS = "results_applied"
_RESULT_POLL_STATUSES = ("submitted", "ended")


@dataclass(frozen=True)
class _BatchPollSnapshot:
    batch_id: str
    organization_id: int | None
    context: dict


@celery_app.task(name="app.tasks.anthropic_batch_tasks.submit_cv_parse_batches")
def submit_cv_parse_batches() -> dict:
    """Sweep parse-pending applications into per-org batch submissions."""
    from ..cv_parsing.batch import sweep_pending_applications
    from ..platform.config import settings
    from ..platform.database import SessionLocal

    if not settings.CV_PARSE_BATCH_ENABLED:
        return {"status": "disabled"}

    db = SessionLocal()
    try:
        summary = sweep_pending_applications(db)
        db.commit()
        if summary["batches"] or summary["cache_applied"]:
            logger.info("cv_parse batch sweep: %s", summary)
        return {"status": "ok", **summary}
    except Exception:
        db.rollback()
        logger.exception("cv_parse batch sweep failed")
        return {"status": "error"}
    finally:
        db.close()


def _retrieve_with_key_fallback(get_metered_client, row):
    """Retrieve a batch with the org-routed client, falling back to the
    shared-key client when the org's key can't see it.

    A batch is tied to the API key that created it. With per-org workspace
    keys enabled, the submit-time resolver can fall back to the shared key
    (e.g. provisioning failure) while the poll-time resolver later resolves
    the org's workspace key — the workspace key then 404s on a batch the
    shared key owns, and the row would sit in-flight forever. On a
    not-found from the org-routed client, retry once with the shared
    client and use whichever one succeeded for results() too.
    """
    client = get_metered_client(organization_id=row.organization_id)
    try:
        return client, client.messages.batches.retrieve(row.batch_id)
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if row.organization_id is None or status_code != 404:
            raise
        logger.warning(
            "batch %s not visible to org %s's key (404) — retrying with "
            "the shared key (batch likely submitted on shared-key fallback).",
            row.batch_id,
            row.organization_id,
        )
        shared = get_metered_client()
        return shared, shared.messages.batches.retrieve(row.batch_id)


@celery_app.task(name="app.tasks.anthropic_batch_tasks.poll_cv_parse_batches")
def poll_cv_parse_batches() -> dict:
    """Poll open cv_parse batches; apply results for the ended ones.

    The metered client's ``results()`` records spend and flips the batch row to
    ``ended`` (idempotently) before local application. ``ended`` therefore means
    paid results are recoverable but not yet applied; only the same transaction
    that applies them advances the row to ``results_applied`` and stores a
    receipt. A restart refetches results, never submits another provider batch.
    """
    from ..models.anthropic_batch_job import AnthropicBatchJob
    from ..platform.database import SessionLocal
    from ..services.anthropic_batch_submission import (
        recover_known_accepted_batch_submissions,
    )
    from ..services.claude_client_resolver import get_metered_client

    recovery = recover_known_accepted_batch_submissions(feature="cv_parse")
    if recovery["recovered"] or recovery["already_owned"]:
        logger.info("cv_parse known-accepted batch recovery: %s", recovery)
    if recovery["collisions"] or recovery["errors"]:
        logger.warning("cv_parse batch recovery needs attention: %s", recovery)

    db = SessionLocal()
    try:
        stored_rows = (
            db.query(AnthropicBatchJob)
            .filter(
                AnthropicBatchJob.feature == "cv_parse",
                AnthropicBatchJob.status.in_(_RESULT_POLL_STATUSES),
            )
            .all()
        )
        if not stored_rows:
            return {"status": "ok", "open": 0}
        rows = [
            _BatchPollSnapshot(
                batch_id=str(row.batch_id),
                organization_id=(
                    int(row.organization_id)
                    if row.organization_id is not None
                    else None
                ),
                context=deepcopy(row.context) if isinstance(row.context, dict) else {},
            )
            for row in stored_rows
        ]
        # Provider polling and batch-result download can be slow. The immutable
        # snapshots above are all they need; release the SQL read transaction.
        db.rollback()

        from ..cv_parsing.batch import apply_batch_results

        polled = []
        for row in rows:
            try:
                client, batch = _retrieve_with_key_fallback(
                    get_metered_client, row
                )
                processing_status = str(
                    getattr(batch, "processing_status", "") or ""
                )
                if processing_status != "ended":
                    polled.append({"batch_id": row.batch_id, "status": processing_status})
                    continue
                # results() meters every entry and latches the batch row to
                # status='ended' in its own session.
                entries = client.messages.batches.results(row.batch_id)

                # Do not hold a database transaction across the provider read.
                # Once entries are local, serialize only their short DB apply.
                # Application mutations, the terminal state, and its receipt
                # commit atomically, so a crash leaves the row retryable.
                apply_row = (
                    db.query(AnthropicBatchJob)
                    .filter(AnthropicBatchJob.batch_id == row.batch_id)
                    .populate_existing()
                    .with_for_update()
                    .one_or_none()
                )
                if apply_row is None:
                    raise RuntimeError(
                        f"missing Anthropic batch anchor {row.batch_id}"
                    )
                if apply_row.status == _RESULT_APPLIED_STATUS:
                    receipt = (
                        apply_row.context.get(_RESULT_APPLICATION_KEY, {})
                        if isinstance(apply_row.context, dict)
                        else {}
                    )
                    summary = deepcopy(receipt.get("summary", {}))
                    db.rollback()
                elif apply_row.metered_at is None or apply_row.status != "ended":
                    # results() deliberately returns provider entries even if
                    # its best-effort metering helper fails. Never terminally
                    # receipt those entries: leave the anchor pollable so the
                    # next pass retries the idempotent metering latch first.
                    db.rollback()
                    polled.append(
                        {
                            "batch_id": row.batch_id,
                            "status": "metering_pending",
                        }
                    )
                    logger.warning(
                        "cv_parse batch %s results not applied because durable "
                        "metering is incomplete",
                        row.batch_id,
                    )
                    continue
                else:
                    apply_context = (
                        deepcopy(apply_row.context)
                        if isinstance(apply_row.context, dict)
                        else deepcopy(row.context)
                    )
                    summary = apply_batch_results(
                        db,
                        entries,
                        context=apply_context,
                        organization_id=apply_row.organization_id,
                    )
                    receipt_context = deepcopy(apply_context)
                    receipt_context[_RESULT_APPLICATION_KEY] = {
                        "version": 1,
                        "state": "applied",
                        "applied_at": datetime.now(timezone.utc).isoformat(),
                        "summary": deepcopy(summary),
                    }
                    apply_row.context = receipt_context
                    apply_row.status = _RESULT_APPLIED_STATUS
                    db.commit()
                polled.append(
                    {"batch_id": row.batch_id, "status": "ended", **summary}
                )
                logger.info(
                    "cv_parse batch %s applied: %s", row.batch_id, summary
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "cv_parse batch poll failed batch_id=%s", row.batch_id
                )
                polled.append({"batch_id": row.batch_id, "status": "error"})
        return {"status": "ok", "open": len(rows), "polled": polled}
    finally:
        db.close()
