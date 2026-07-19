"""One-shot backfill: parse ``cv_text`` into ``cv_sections`` for every
application that has raw CV text but a null ``cv_sections``.

Why this exists
---------------
For most of the platform's life only one code path parsed CVs into
structured sections (``_try_fetch_cv_from_workable``), and it ran only
when an application had *no* ``cv_text`` yet. The Workable bulk sync,
however, stores ``cv_text`` directly (no parse), so every synced
candidate ended up with raw text but ``cv_sections = NULL`` — and the
candidate page fell back to a naive split-by-heading render of the raw
(often column-scrambled) PDF text, producing fragmented "skills" chips
and mixed-up sections.

The forward fix enqueues ``parse_application_cv_sections`` on ingest.
This script drains the historical backlog.

Behaviour
---------
- **Targeted**: only rows with non-empty ``cv_text`` and null
  ``cv_sections`` (and not soft-deleted). ``--org`` scopes to one org.
- **Idempotent / restartable**: per-row commit; re-running skips rows that
  now have sections. A parse failure leaves the row null (retryable).
- **Cost-aware**: each successful parse is one Haiku 4.5 call, content-hash
  cached — duplicate CVs collapse to a cache hit. ``--limit`` bounds a
  trial run; ``--sleep`` throttles; ``--workers`` parallelises (each
  worker gets its own DB session).

Usage
-----
::

    # Dry run a small batch for one org:
    python -m app.scripts.backfill_cv_sections --org 2 --limit 20 --dry-run

    # Backfill a single application (e.g. to verify end-to-end):
    python -m app.scripts.backfill_cv_sections --application-id 58070

    # Real run, one org, gentle:
    python -m app.scripts.backfill_cv_sections --org 2 --sleep 0.1
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.cv_parsing.apply import parse_and_store_cv_sections
from app.models.candidate_application import CandidateApplication
from app.platform.database import SessionLocal

logger = logging.getLogger("taali.backfill_cv_sections")


@dataclass
class Stats:
    examined: int = 0
    parsed: int = 0
    skipped_already: int = 0
    skipped_no_text: int = 0
    parse_failed: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"examined={self.examined} parsed={self.parsed} "
            f"already={self.skipped_already} no_text={self.skipped_no_text} "
            f"parse_failed={self.parse_failed} errors={len(self.errors)}"
        )


def _process_one(application_id: int, *, dry_run: bool, force: bool) -> str:
    """Parse one application's CV. Returns one of ``ok``, ``skip_already``,
    ``skip_no_text``, ``parse_failed``, ``fail``.

    Own session per call — safe under ThreadPoolExecutor.
    """
    db: Session = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if app is None:
            return "fail"
        if app.cv_sections is not None and not force:
            return "skip_already"
        candidate = app.candidate
        has_text = bool((app.cv_text or "").strip()) or bool(
            ((candidate.cv_text if candidate else "") or "").strip()
        )
        if not has_text:
            return "skip_no_text"
        if dry_run:
            return "ok"

        wrote = parse_and_store_cv_sections(app, db=db, force=force)
        if not wrote:
            # Parse failed — leave cv_sections null so a later run can retry.
            db.rollback()
            return "parse_failed"
        try:
            db.commit()
        except Exception:
            db.rollback()
            return "fail"
        return "ok"
    finally:
        db.close()


def _ids_to_backfill(
    db: Session, *, org: Optional[int], limit: Optional[int]
) -> list[int]:
    q = (
        db.query(CandidateApplication.id)
        .filter(
            CandidateApplication.cv_sections.is_(None),
            CandidateApplication.cv_text.isnot(None),
            func.length(func.trim(CandidateApplication.cv_text)) > 0,
            CandidateApplication.deleted_at.is_(None),
        )
        .order_by(CandidateApplication.id)
    )
    if org is not None:
        q = q.filter(CandidateApplication.organization_id == org)
    if limit:
        q = q.limit(int(limit))
    return [row[0] for row in q.all()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", type=int, default=None, help="Scope to one organization_id.")
    parser.add_argument("--limit", type=int, default=None, help="Max applications to consider.")
    parser.add_argument("--application-id", type=int, default=None, help="Backfill a single application id.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would parse; write nothing.")
    parser.add_argument("--force", action="store_true", help="Re-parse even when cv_sections already set.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between rows (serial mode).")
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel workers. Default 1 (serial). Each makes Haiku calls — keep modest to respect rate limits.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    from app.platform.logging import setup_logging

    root_logger = setup_logging()
    log_level = getattr(logging, args.log_level)
    root_logger.setLevel(log_level)
    for handler in root_logger.handlers:
        handler.setLevel(log_level)

    if args.application_id:
        ids = [args.application_id]
    else:
        db = SessionLocal()
        try:
            ids = _ids_to_backfill(db, org=args.org, limit=args.limit)
        finally:
            db.close()

    logger.info(
        "Backfill plan: %d applications need cv_sections (org=%s workers=%d%s)",
        len(ids), args.org, args.workers, " DRY RUN" if args.dry_run else "",
    )

    stats = Stats()
    started = time.monotonic()

    def _tally(status: str) -> None:
        stats.examined += 1
        if status == "ok":
            stats.parsed += 1
        elif status == "skip_already":
            stats.skipped_already += 1
        elif status == "skip_no_text":
            stats.skipped_no_text += 1
        elif status == "parse_failed":
            stats.parse_failed += 1

    if args.workers <= 1:
        for idx, app_id in enumerate(ids, 1):
            try:
                status = _process_one(app_id, dry_run=args.dry_run, force=args.force)
            except Exception as exc:
                error_type = type(exc).__name__
                stats.errors.append((int(app_id), error_type))
                logger.error(
                    "CV section backfill failed application_id=%s stage=parse error_type=%s",
                    app_id,
                    error_type,
                )
                continue
            _tally(status)
            if idx % 25 == 0:
                logger.info("progress %d/%d  %s", idx, len(ids), stats.render())
            if args.sleep:
                time.sleep(args.sleep)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(_process_one, app_id, dry_run=args.dry_run, force=args.force): app_id
                for app_id in ids
            }
            for idx, fut in enumerate(as_completed(futures), 1):
                try:
                    status = fut.result()
                except Exception as exc:
                    app_id = int(futures[fut])
                    error_type = type(exc).__name__
                    stats.errors.append((app_id, error_type))
                    logger.error(
                        "CV section backfill failed application_id=%s stage=parse error_type=%s",
                        app_id,
                        error_type,
                    )
                    continue
                _tally(status)
                if idx % 25 == 0:
                    logger.info("progress %d/%d  %s", idx, len(ids), stats.render())

    duration = time.monotonic() - started
    logger.info("CV section backfill complete %s duration_seconds=%.1f", stats.render(), duration)
    if stats.errors:
        for app_id, error_type in stats.errors[:25]:
            logger.error(
                "CV section backfill error application_id=%s error_type=%s",
                app_id,
                error_type,
            )
        if len(stats.errors) > 25:
            logger.error("CV section backfill additional_errors=%s", len(stats.errors) - 25)
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    sys.exit(main())
