"""One-shot backfill: re-ground employer names on already-parsed
``cv_sections`` rows.

Why this exists
---------------
``app.cv_parsing.grounding`` flags experience entries whose ``company`` name
can't be found in the CV text (a symptom of scrambled multi-column PDF
extraction — see grounding.py). New parses get grounded inline in
``parse_and_store_cv_sections``; this script applies the same grounding to
the historical rows that were parsed before grounding existed, **without
re-parsing** (no LLM call — it's a pure, deterministic re-read of the stored
blob against the stored ``cv_text``).

Behaviour
---------
- **Targeted**: rows with a non-null ``cv_sections`` and non-empty
  ``cv_text`` (and not soft-deleted). ``--org`` / ``--application-id`` scope.
- **Idempotent / restartable**: per-row commit; only writes when a flag
  actually changes, so re-running is a no-op once converged.
- **Cheap**: no model calls. ``--limit`` bounds a trial; ``--sleep``
  throttles; ``--workers`` parallelises (own DB session per worker).

Usage
-----
::

    # See what would change for one application (the reported case):
    python -m app.scripts.reground_cv_employers --application-id 58234 --dry-run

    # One org, real run:
    python -m app.scripts.reground_cv_employers --org 2
"""

from __future__ import annotations

import argparse
import copy
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.cv_parsing.grounding import ground_cv_sections
from app.models.candidate_application import CandidateApplication
from app.platform.database import SessionLocal

logger = logging.getLogger("taali.reground_cv_employers")


@dataclass
class Stats:
    examined: int = 0
    updated: int = 0
    flagged_employers: int = 0
    unchanged: int = 0
    skipped_no_text: int = 0
    skipped_no_sections: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"examined={self.examined} updated={self.updated} "
            f"flagged_employers={self.flagged_employers} unchanged={self.unchanged} "
            f"no_text={self.skipped_no_text} no_sections={self.skipped_no_sections} "
            f"errors={len(self.errors)}"
        )


def _flag_signature(sections: dict) -> list[bool]:
    """The ordered ``company_unverified`` flags — used to detect real change."""
    experience = sections.get("experience") if isinstance(sections, dict) else None
    if not isinstance(experience, list):
        return []
    return [bool(e.get("company_unverified")) for e in experience if isinstance(e, dict)]


def _process_one(application_id: int, *, dry_run: bool) -> tuple[str, int]:
    """Re-ground one application. Returns ``(status, flagged_count)`` where
    status is ``updated``, ``unchanged``, ``skip_no_text``, ``skip_no_sections``
    or ``fail``. Own session per call — safe under ThreadPoolExecutor.
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
            return "fail", 0
        sections = app.cv_sections
        if not isinstance(sections, dict) or not isinstance(sections.get("experience"), list):
            return "skip_no_sections", 0
        candidate = app.candidate
        cv_text = (app.cv_text or "").strip() or (
            (candidate.cv_text if candidate else "") or ""
        ).strip()
        if not cv_text:
            return "skip_no_text", 0

        before = _flag_signature(sections)
        # Deep-copy so we reassign a fresh object (JSON column change detection)
        # and never mutate the loaded instance before we've decided to write.
        updated = copy.deepcopy(sections)
        flagged = ground_cv_sections(updated, cv_text)
        after = _flag_signature(updated)

        if after == before:
            return "unchanged", len(flagged)
        if dry_run:
            return "updated", len(flagged)

        app.cv_sections = updated
        if candidate is not None and isinstance(candidate.cv_sections, dict):
            cand_copy = copy.deepcopy(candidate.cv_sections)
            ground_cv_sections(cand_copy, cv_text)
            candidate.cv_sections = cand_copy
        try:
            db.commit()
        except Exception:
            db.rollback()
            return "fail", 0
        return "updated", len(flagged)
    finally:
        db.close()


def _ids(db: Session, *, org: Optional[int], limit: Optional[int]) -> list[int]:
    q = (
        db.query(CandidateApplication.id)
        .filter(
            CandidateApplication.cv_sections.isnot(None),
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
    parser.add_argument("--application-id", type=int, default=None, help="Re-ground a single application id.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between rows (serial mode).")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers. Default 1 (serial).")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.application_id:
        ids = [args.application_id]
    else:
        db = SessionLocal()
        try:
            ids = _ids(db, org=args.org, limit=args.limit)
        finally:
            db.close()

    logger.info(
        "Re-ground plan: %d applications with cv_sections (org=%s workers=%d%s)",
        len(ids), args.org, args.workers, " DRY RUN" if args.dry_run else "",
    )

    stats = Stats()
    started = time.monotonic()

    def _tally(result: tuple[str, int]) -> None:
        status, flagged = result
        stats.examined += 1
        if status == "updated":
            stats.updated += 1
            stats.flagged_employers += flagged
        elif status == "unchanged":
            stats.unchanged += 1
        elif status == "skip_no_text":
            stats.skipped_no_text += 1
        elif status == "skip_no_sections":
            stats.skipped_no_sections += 1

    if args.workers <= 1:
        for idx, app_id in enumerate(ids, 1):
            try:
                result = _process_one(app_id, dry_run=args.dry_run)
            except Exception as exc:
                stats.errors.append(f"app_id={app_id}: {exc}")
                continue
            _tally(result)
            if idx % 50 == 0:
                logger.info("progress %d/%d  %s", idx, len(ids), stats.render())
            if args.sleep:
                time.sleep(args.sleep)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(_process_one, app_id, dry_run=args.dry_run): app_id
                for app_id in ids
            }
            for idx, fut in enumerate(as_completed(futures), 1):
                try:
                    result = fut.result()
                except Exception as exc:
                    stats.errors.append(f"app_id={futures[fut]}: {exc}")
                    continue
                _tally(result)
                if idx % 50 == 0:
                    logger.info("progress %d/%d  %s", idx, len(ids), stats.render())

    duration = time.monotonic() - started
    print("\n=== reground cv employers summary ===")
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
