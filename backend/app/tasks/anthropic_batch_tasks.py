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

import logging

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.anthropic_batch")


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


@celery_app.task(name="app.tasks.anthropic_batch_tasks.poll_cv_parse_batches")
def poll_cv_parse_batches() -> dict:
    """Poll open cv_parse batches; apply results for the ended ones.

    The metered client's ``results()`` records spend and flips the batch
    row to ``ended`` (idempotently) before we apply, so a crash between
    metering and applying leaves rows parse-pending — a later sweep
    resubmits them — rather than ever losing the spend record.
    """
    from ..models.anthropic_batch_job import AnthropicBatchJob
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_metered_client

    db = SessionLocal()
    try:
        rows = (
            db.query(AnthropicBatchJob)
            .filter(
                AnthropicBatchJob.feature == "cv_parse",
                AnthropicBatchJob.status == "submitted",
            )
            .all()
        )
        if not rows:
            return {"status": "ok", "open": 0}

        from ..cv_parsing.batch import apply_batch_results

        polled = []
        for row in rows:
            try:
                client = get_metered_client(organization_id=row.organization_id)
                batch = client.messages.batches.retrieve(row.batch_id)
                processing_status = str(
                    getattr(batch, "processing_status", "") or ""
                )
                if processing_status != "ended":
                    polled.append({"batch_id": row.batch_id, "status": processing_status})
                    continue
                # results() meters every entry and latches the batch row to
                # status='ended' in its own session.
                entries = client.messages.batches.results(row.batch_id)
                summary = apply_batch_results(db, entries)
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
