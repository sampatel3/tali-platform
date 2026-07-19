"""Durably enqueue pre-screen-only batches for one or more roles.

This command is a compatibility wrapper around the same durable Celery path
used by the recruiter API. It persists a ``BackgroundJobRun`` before broker
publication, materializes recoverable per-application items in the worker, and
then exits. It never calls the paid provider synchronously.

Usage:
    DATABASE_URL=... python -m scripts.fast_prescreen_roles \
        --role-ids 110 111 112 113
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.domains.assessments_runtime.role_support import role_has_job_spec
from app.models.background_job_run import (
    JOB_KIND_PRE_SCREEN_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.role import Role
from app.services.background_job_runs import create_run
from app.tasks.prescreen_tasks import (
    PRESCREEN_ACTIVE_RUN_STATUSES,
    dispatch_prescreen_batch_roots,
    select_prescreen_target_ids,
)


log = logging.getLogger("taali.scripts.fast_prescreen")


def dispatch_role(db: Session, *, role_id: int, dry_run: bool) -> dict[str, Any]:
    """Plan or durably publish one role's canonical pre-screen-only batch."""

    role = db.query(Role).filter(Role.id == int(role_id)).one_or_none()
    if role is None:
        return {"status": "missing_role", "role_id": int(role_id)}
    organization_id = int(role.organization_id)
    if not role_has_job_spec(role):
        return {
            "status": "missing_job_spec",
            "role_id": int(role.id),
            "organization_id": organization_id,
        }

    target_ids = select_prescreen_target_ids(
        db,
        role_id=int(role.id),
        organization_id=organization_id,
        refresh=False,
    )
    target_count = len(target_ids)
    if dry_run:
        return {
            "status": "dry_run",
            "role_id": int(role.id),
            "organization_id": organization_id,
            "total": target_count,
        }

    existing = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_PRE_SCREEN_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(role.id),
            BackgroundJobRun.organization_id == organization_id,
            BackgroundJobRun.status.in_(PRESCREEN_ACTIVE_RUN_STATUSES),
            BackgroundJobRun.finished_at.is_(None),
        )
        .order_by(BackgroundJobRun.id.desc())
        .first()
    )
    if existing is not None:
        counters = dict(existing.counters or {})
        return {
            "status": "already_running",
            "role_id": int(role.id),
            "organization_id": organization_id,
            "run_id": int(existing.id),
            "total": int(counters.get("total", target_count) or 0),
        }
    if target_count == 0:
        return {
            "status": "nothing_to_pre_screen",
            "role_id": int(role.id),
            "organization_id": organization_id,
            "total": 0,
        }

    run_id = create_run(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=int(role.id),
        organization_id=organization_id,
        counters={
            "total": target_count,
            "processed": 0,
            "succeeded": 0,
            "errors": 0,
            "skipped": 0,
            "refresh": False,
        },
        status="queued",
    )
    if run_id is None:
        return {
            "status": "persist_failed",
            "role_id": int(role.id),
            "organization_id": organization_id,
            "total": target_count,
        }

    try:
        dispatch = dispatch_prescreen_batch_roots(run_id=int(run_id))
    except Exception as exc:
        log.error(
            "Pre-screen batch publication deferred role_id=%s run_id=%s stage=root_claim error_type=%s",
            role.id,
            run_id,
            type(exc).__name__,
        )
        dispatch = {"dispatch_errors": 1}

    return {
        "status": "recovering" if dispatch.get("dispatch_errors") else "started",
        "role_id": int(role.id),
        "organization_id": organization_id,
        "run_id": int(run_id),
        "total": target_count,
        "dispatch_recovering": bool(dispatch.get("dispatch_errors")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Durably enqueue pre-screen-only batches for roles."
    )
    parser.add_argument("--role-ids", type=int, nargs="+", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from app.platform.logging import setup_logging

    setup_logging()
    if not os.environ.get("DATABASE_URL"):
        log.error("Fast pre-screen refused stage=startup error_code=database_url_missing")
        return 2

    from app.platform.database import SessionLocal

    results: list[dict[str, Any]] = []
    db = SessionLocal()
    try:
        for role_id in dict.fromkeys(args.role_ids):
            try:
                result = dispatch_role(db, role_id=int(role_id), dry_run=bool(args.dry_run))
            except Exception as exc:
                db.rollback()
                log.error(
                    "Fast pre-screen role failed role_id=%s stage=planning error_type=%s",
                    role_id,
                    type(exc).__name__,
                )
                result = {"status": "error", "role_id": int(role_id)}
            results.append(result)
            log.info(
                "Fast pre-screen role result role_id=%s status=%s total=%s run_id=%s",
                result.get("role_id"),
                result.get("status"),
                result.get("total", 0),
                result.get("run_id"),
            )
    finally:
        db.close()

    failed_statuses = {
        "error",
        "missing_job_spec",
        "missing_role",
        "persist_failed",
    }
    failed = sum(result.get("status") in failed_statuses for result in results)
    log.info(
        "Fast pre-screen enqueue complete roles=%s failed=%s dry_run=%s",
        len(results),
        failed,
        bool(args.dry_run),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
