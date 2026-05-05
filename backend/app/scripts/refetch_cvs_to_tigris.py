"""One-shot recovery: re-fetch CVs from Workable for every application
whose ``cv_file_url`` points at the legacy ephemeral local-disk path.

Why this exists
---------------
Before the Tigris cutover, Workable-sourced CVs were saved to
``/app/uploads/cv/...`` on the API container. Railway's filesystem is
ephemeral, so every redeploy wiped the files while leaving the DB rows
pointing at non-existent paths. This script walks the DB, finds every
application with a Workable candidate ID and a dead local path, and
re-pulls the CV from Workable — which now uploads straight to Tigris.

Behaviour
---------
- **Idempotent**: rows whose ``cv_file_url`` already points at object
  storage (``stored_document_s3_key`` returns a key) are skipped.
- **Restartable**: per-row commit. ``Ctrl-C`` mid-run loses at most one
  in-flight fetch.
- **Rate-limit-aware**: serial by default (Workable allows 10 req/10s,
  but recovery isn't time-critical). ``--workers N`` parallelises with
  the same semaphore-style cap as the full sync.
- **Read-only on Workable**: just GETs candidate detail + resume binary.
- **Bounded**: ``--limit N`` for trial runs.

Usage
-----
::

    # From the backend dir, dry-run a small batch:
    python -m app.scripts.refetch_cvs_to_tigris --dry-run --limit 20

    # Real run, all applications:
    python -m app.scripts.refetch_cvs_to_tigris

    # Real run with parallel workers (under Workable's rate limit):
    python -m app.scripts.refetch_cvs_to_tigris --workers 3

Env requirements
----------------
- All the regular ``AWS_*`` env vars wired up at Tigris.
- The Workable org must have a valid OAuth token and subdomain
  (existing operator setup — no extra config).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.platform.database import SessionLocal
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.services.document_service import stored_document_s3_key


logger = logging.getLogger("taali.refetch_cvs")


@dataclass
class Stats:
    examined: int = 0
    skipped_already_in_tigris: int = 0
    skipped_no_workable_id: int = 0
    skipped_org_not_connected: int = 0
    refetched: int = 0
    refetch_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"examined={self.examined} "
            f"already_in_tigris={self.skipped_already_in_tigris} "
            f"no_workable_id={self.skipped_no_workable_id} "
            f"org_not_connected={self.skipped_org_not_connected} "
            f"refetched={self.refetched} "
            f"failed={self.refetch_failed} "
            f"errors={len(self.errors)}"
        )


def _fetch_one(application_id: int, *, dry_run: bool) -> tuple[int, str]:
    """Re-fetch one application's CV. Returns ``(app_id, status)`` where
    status is one of ``ok``, ``skip_already``, ``skip_no_wid``,
    ``skip_org``, ``fail``.

    Each call gets its own session — important when running with
    ``ThreadPoolExecutor`` since SQLAlchemy sessions are not
    thread-safe.
    """
    from app.domains.assessments_runtime.applications_routes import _try_fetch_cv_from_workable

    db: Session = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.organization),
            )
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if app is None:
            return application_id, "fail"

        # Idempotency: already in object storage? Skip.
        if stored_document_s3_key(app.cv_file_url or "") is not None:
            return application_id, "skip_already"

        candidate = app.candidate
        org = app.organization
        if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
            return application_id, "skip_org"
        if not (app.workable_candidate_id or (candidate and candidate.workable_candidate_id)):
            return application_id, "skip_no_wid"

        if dry_run:
            return application_id, "ok"

        ok = _try_fetch_cv_from_workable(app, candidate, db, org)
        if not ok:
            return application_id, "fail"
        try:
            db.commit()
        except Exception:
            db.rollback()
            return application_id, "fail"
        return application_id, "ok"
    finally:
        db.close()


def _candidate_application_ids_to_refetch(db: Session, *, limit: Optional[int]) -> list[int]:
    """Return application IDs that look like they need a re-fetch.

    A row is in scope when it has a ``cv_file_url`` AND that URL doesn't
    parse as an object-storage URL (i.e. it's the legacy local path).
    The ``stored_document_s3_key`` filter happens in Python because the
    URL parser handles multiple host styles cleanly.
    """
    q = db.query(CandidateApplication.id, CandidateApplication.cv_file_url).filter(
        CandidateApplication.cv_file_url.isnot(None),
        CandidateApplication.deleted_at.is_(None),
    )
    if limit:
        q = q.limit(int(limit))
    out: list[int] = []
    for app_id, file_url in q.all():
        if stored_document_s3_key(file_url or "") is None:
            out.append(app_id)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would refetch; don't write anything.")
    parser.add_argument("--limit", type=int, default=None, help="Max applications to consider.")
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel workers. Default 1 (serial). Workable rate limit is 10 req/10s — keep <=3.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.workers < 1:
        args.workers = 1
    if args.workers > 5:
        logger.warning("--workers=%d exceeds Workable rate limit headroom; capping to 3", args.workers)
        args.workers = 3

    db: Session = SessionLocal()
    try:
        ids = _candidate_application_ids_to_refetch(db, limit=args.limit)
    finally:
        db.close()

    logger.info(
        "Refetch plan: %d applications need attention (workers=%d%s)",
        len(ids), args.workers, " DRY RUN" if args.dry_run else "",
    )

    stats = Stats()
    started = time.monotonic()

    if args.workers == 1:
        for idx, app_id in enumerate(ids, 1):
            try:
                _, status = _fetch_one(app_id, dry_run=args.dry_run)
            except Exception as exc:
                stats.errors.append(f"app_id={app_id}: {exc}")
                stats.refetch_failed += 1
                continue
            stats.examined += 1
            if status == "ok":
                stats.refetched += 1
            elif status == "skip_already":
                stats.skipped_already_in_tigris += 1
            elif status == "skip_no_wid":
                stats.skipped_no_workable_id += 1
            elif status == "skip_org":
                stats.skipped_org_not_connected += 1
            else:
                stats.refetch_failed += 1
            if idx % 25 == 0:
                logger.info("progress %d/%d  %s", idx, len(ids), stats.render())
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_fetch_one, app_id, dry_run=args.dry_run): app_id for app_id in ids}
            for idx, fut in enumerate(as_completed(futures), 1):
                try:
                    _, status = fut.result()
                except Exception as exc:
                    stats.errors.append(f"app_id={futures[fut]}: {exc}")
                    stats.refetch_failed += 1
                    continue
                stats.examined += 1
                if status == "ok":
                    stats.refetched += 1
                elif status == "skip_already":
                    stats.skipped_already_in_tigris += 1
                elif status == "skip_no_wid":
                    stats.skipped_no_workable_id += 1
                elif status == "skip_org":
                    stats.skipped_org_not_connected += 1
                else:
                    stats.refetch_failed += 1
                if idx % 25 == 0:
                    logger.info("progress %d/%d  %s", idx, len(ids), stats.render())

    duration = time.monotonic() - started
    print("\n=== refetch summary ===")
    print(stats.render())
    print(f"duration: {duration:.1f}s")
    if stats.errors:
        print("\nfirst errors:")
        for err in stats.errors[:25]:
            print(f"  - {err}")
        if len(stats.errors) > 25:
            print(f"  ... and {len(stats.errors) - 25} more")
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    sys.exit(main())
