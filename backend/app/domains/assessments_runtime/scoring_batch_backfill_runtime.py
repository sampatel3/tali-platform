"""Cross-role scoring backfill implementation behind thin FastAPI routes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...services.scoring_batch_fanout_recovery import (
    mark_scoring_fanout_publish_failed,
    mark_scoring_fanout_published,
    reserve_scoring_fanout_publish,
)
from ...services.scoring_backfill_recovery import (
    SCORING_BACKFILL_PLAN_VERSION,
    normalize_scoring_backfill_plan,
    scoring_backfill_child_counters,
    scoring_backfill_fanout_accounted,
    scoring_backfill_plan_digest,
    scoring_backfill_plan_from_counters,
)
from ...services.scoring_batch_terminal_contract import (
    exact_scoring_terminal_counts,
)
from .scoring_batch_state import (
    SCORING_ACTIVE_RUN_STATUSES,
    SCORING_DURABLE_QUEUE_CONTRACT,
    latest_scoring_backfill_run,
    latest_scoring_run,
    merge_scoring_progress,
    progress_count,
    progress_datetime,
    progress_run_id,
    scoring_batch_exact_terminal_breakdown,
    scoring_batch_has_active_jobs,
    scoring_uses_exact_receipts,
)


class _Routes:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        return self._values[name]


def _batch_counters(
    *,
    target_ids: list[int],
    include_scored: bool,
    applied_after: str | None,
    parent_run_id: int,
) -> dict[str, Any]:
    return scoring_backfill_child_counters(
        target_ids=target_ids,
        include_scored=include_scored,
        applied_after=applied_after,
        parent_run_id=parent_run_id,
    )


def dispatch_scoring_backfill(
    routes: Mapping[str, Any],
    *,
    applied_after: str | None,
    include_scored: bool,
    db: Any,
    current_user: Any,
) -> dict[str, Any]:
    r = _Routes(routes)
    cutoff = r._batch_applied_after_cutoff(applied_after)
    organization_id = int(current_user.organization_id)
    batch_started_at = r.datetime.now(r.timezone.utc)
    roles = (
        db.query(r.Role)
        .filter(
            r.Role.organization_id == organization_id,
            r.Role.deleted_at.is_(None),
        )
        .order_by(r.Role.id)
        .all()
    )
    role_plan: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for role in roles:
        role_id = int(role.id)
        if not r.role_has_job_spec(role):
            skipped.append({"role_id": role_id, "reason": "no_job_spec"})
            continue

        r._lock_scoring_start_scope(db, role_id)
        existing = merge_scoring_progress(
            r.get_retained_progress(r._batch_score_progress, role_id),
            latest_scoring_run(
                db,
                role_id=role_id,
                organization_id=organization_id,
            ),
        )
        existing_run_id = progress_run_id(existing)
        existing_jobs_active = scoring_batch_has_active_jobs(
            db,
            run_id=existing_run_id,
            progress=existing,
        )
        if (
            existing.get("status") in SCORING_ACTIVE_RUN_STATUSES
            or existing_jobs_active
        ):
            skipped.append({"role_id": role_id, "reason": "already_running"})
            continue

        target_query = db.query(r.CandidateApplication.id).filter(
            r.CandidateApplication.role_id == role_id,
            r.CandidateApplication.organization_id == organization_id,
            r.CandidateApplication.deleted_at.is_(None),
        )
        if not include_scored:
            target_query = target_query.filter(
                r.CandidateApplication.cv_match_score.is_(None)
            )
        if cutoff is not None:
            target_query = target_query.join(
                r.Candidate,
                r.CandidateApplication.candidate_id == r.Candidate.id,
            ).filter(r.Candidate.workable_created_at >= cutoff)
        target_ids = [
            int(row[0])
            for row in target_query.order_by(r.CandidateApplication.id).all()
        ]
        if not target_ids:
            skipped.append({"role_id": role_id, "reason": "nothing_to_score"})
            continue

        role_plan.append(
            {
                "role_id": role_id,
                "role_name": str(getattr(role, "name", "") or ""),
                "target_application_ids": target_ids,
            }
        )

    canonical_plan = normalize_scoring_backfill_plan(role_plan)
    if canonical_plan is None:
        raise r.HTTPException(
            status_code=500,
            detail="Could not build scoring backfill plan",
        )
    total_target = sum(len(entry["target_application_ids"]) for entry in canonical_plan)
    initial_parent_counters = {
        "backfill_parent": True,
        "applied_after": applied_after,
        "include_scored": bool(include_scored),
        "role_plan_version": SCORING_BACKFILL_PLAN_VERSION,
        "role_plan": canonical_plan,
        "role_plan_digest": scoring_backfill_plan_digest(canonical_plan),
        "fanout_cursor": 0,
        "children": [],
        "skipped": skipped,
        "total_target": total_target,
        "fanout_complete": False,
        "fanout_lease_expires_at": (
            batch_started_at + r.timedelta(minutes=2)
        ).isoformat(),
    }
    parent_run_id = r._create_job_run(
        kind=r.JOB_KIND_SCORING_BATCH,
        scope_kind=r.SCOPE_KIND_ORG,
        scope_id=organization_id,
        organization_id=organization_id,
        counters=initial_parent_counters,
        status="dispatching",
    )
    if parent_run_id is None:
        raise r.HTTPException(
            status_code=503,
            detail="Could not persist scoring backfill",
        )

    children: list[dict[str, Any]] = []
    dispatched: list[dict[str, Any]] = []
    receipt_recovery_pending = False

    def _parent_counters(*, fanout_complete: bool) -> dict[str, Any]:
        counters = {
            **initial_parent_counters,
            "children": list(children),
            "fanout_cursor": len(children),
            "fanout_complete": fanout_complete,
        }
        if fanout_complete or receipt_recovery_pending:
            counters.pop("fanout_lease_expires_at", None)
        return counters

    for plan_entry in canonical_plan:
        role_id = int(plan_entry["role_id"])
        target_ids = list(plan_entry["target_application_ids"])

        child_run_id = r._create_job_run(
            kind=r.JOB_KIND_SCORING_BATCH,
            scope_kind=r.SCOPE_KIND_ROLE,
            scope_id=role_id,
            organization_id=organization_id,
            counters=_batch_counters(
                target_ids=target_ids,
                include_scored=include_scored,
                applied_after=applied_after,
                parent_run_id=parent_run_id,
            ),
            status="dispatching",
            dispatch_key=f"scoring-backfill:{parent_run_id}:{role_id}",
        )
        if child_run_id is None:
            receipt_recovery_pending = True
            r._update_job_run(
                parent_run_id,
                status="dispatching",
                counters=_parent_counters(fanout_complete=False),
            )
            break

        child = {
            "role_id": role_id,
            "target": len(target_ids),
            "run_id": int(child_run_id),
            "started_at": batch_started_at.isoformat(),
            "dispatch_status": "dispatching",
        }
        children.append(child)
        r._update_job_run(
            parent_run_id,
            status="dispatching",
            counters=_parent_counters(fanout_complete=False),
        )
        progress = {
            **_batch_counters(
                target_ids=target_ids,
                include_scored=include_scored,
                applied_after=applied_after,
                parent_run_id=parent_run_id,
            ),
            "status": "running",
            "started_at": batch_started_at,
            "organization_id": organization_id,
            "role_name": str(plan_entry.get("role_name") or ""),
            "run_id": child_run_id,
        }
        r.set_bounded_progress(r._batch_score_progress, role_id, progress)
        r._write_batch_meta(
            role_id,
            total=len(target_ids),
            started_at=batch_started_at,
            include_scored=bool(include_scored),
            run_id=child_run_id,
        )

        from ...tasks.scoring_tasks import batch_score_role as celery_batch_score_role

        publish_scope = {
            "run_id": int(child_run_id),
            "role_id": role_id,
            "organization_id": organization_id,
        }
        reserve_scoring_fanout_publish(**publish_scope)
        try:
            celery_batch_score_role.delay(
                role_id,
                include_scored=include_scored,
                applied_after=applied_after,
                run_id=int(child_run_id),
            )
        except Exception as exc:
            child["dispatch_status"] = "recovery_pending"
            dispatched.append(dict(child))
            r.logger.error(
                "Scoring backfill dispatch failed role_id=%s error_type=%s",
                role_id,
                type(exc).__name__,
            )
            mark_scoring_fanout_publish_failed(**publish_scope)
        else:
            child["dispatch_status"] = "dispatched"
            dispatched.append(dict(child))
            mark_scoring_fanout_published(**publish_scope)
        r._update_job_run(
            parent_run_id,
            status="running",
            counters=_parent_counters(fanout_complete=False),
        )

    fanout_complete = len(children) == len(canonical_plan)
    parent_finished = fanout_complete and not canonical_plan
    parent_status = (
        "completed" if parent_finished else ("running" if children else "dispatching")
    )
    parent_counters = _parent_counters(fanout_complete=fanout_complete)
    r._update_job_run(
        parent_run_id,
        status=parent_status,
        counters=parent_counters,
        finished=parent_finished,
    )
    total_target = int(parent_counters["total_target"])
    meta = {
        **parent_counters,
        "parent_run_id": parent_run_id,
        "started_at": batch_started_at.isoformat(),
        "roles": children,
    }
    client = r._redis_client()
    if client:
        try:
            client.set(
                r._BACKFILL_META_KEY.format(org_id=organization_id),
                json.dumps(meta),
                ex=r._BACKFILL_META_TTL,
            )
        except Exception:
            pass

    if receipt_recovery_pending and dispatched:
        response_status = "partially_dispatched"
    elif dispatched:
        response_status = "dispatched"
    elif receipt_recovery_pending:
        response_status = "recovery_pending"
    else:
        response_status = "nothing_to_score"
    return {
        "status": response_status,
        "parent_run_id": parent_run_id,
        "roles_dispatched": len(dispatched),
        "roles_skipped": len(skipped),
        "total_target": total_target,
        "applied_after": applied_after,
        "dispatched": dispatched,
        "skipped": skipped,
    }


def _child_status(
    r: _Routes,
    *,
    db: Any,
    entry: dict[str, Any],
    organization_id: int,
    parent_run_id: int | None = None,
    expected_target_ids: list[int] | None = None,
) -> dict[str, Any]:
    role_id = int(entry["role_id"])
    run_id = progress_count(entry.get("run_id"))
    run = (
        db.query(r.BackgroundJobRun)
        .filter(
            r.BackgroundJobRun.id == run_id,
            r.BackgroundJobRun.kind == r.JOB_KIND_SCORING_BATCH,
            r.BackgroundJobRun.scope_kind == r.SCOPE_KIND_ROLE,
            r.BackgroundJobRun.scope_id == role_id,
            r.BackgroundJobRun.organization_id == organization_id,
        )
        .one_or_none()
        if run_id
        else None
    )
    strict_child = parent_run_id is not None and expected_target_ids is not None
    run_counters = dict(run.counters or {}) if run is not None else {}
    exact_child_owned = not strict_child or bool(
        run is not None
        and run.dispatch_key == f"scoring-backfill:{parent_run_id}:{role_id}"
        and type(run_counters.get("backfill_parent_run_id")) is int
        and run_counters.get("backfill_parent_run_id") == parent_run_id
        and run_counters.get("queue_contract") == SCORING_DURABLE_QUEUE_CONTRACT
        and run_counters.get("target_application_ids") == expected_target_ids
        and progress_count(entry.get("target")) == len(expected_target_ids)
    )
    if not exact_child_owned:
        target = len(expected_target_ids or [])
        return {
            "role_id": role_id,
            "run_id": run_id or None,
            "target": target,
            "scored": 0,
            "pre_screened_out": 0,
            "errors": target,
            "not_processed": 0,
            "status": "failed",
            "active_receipts": False,
            "receipt_invalid": True,
        }
    retained_progress = dict(
        r.get_retained_progress(r._batch_score_progress, role_id) or {}
    )
    if strict_child and progress_run_id(retained_progress) != run_id:
        # A newer role-local run must never replace the immutable child named
        # by this parent receipt while the parent is being aggregated.
        retained_progress = {}
    progress = merge_scoring_progress(retained_progress, run)
    target = progress_count(entry.get("target")) or progress_count(
        progress.get("total")
    )
    resolved_run_id = progress_run_id(progress)
    started_at = progress_datetime(progress.get("started_at"))
    uses_exact_receipts = scoring_uses_exact_receipts(progress)
    cancelled_receipts = 0
    if uses_exact_receipts:
        breakdown = scoring_batch_exact_terminal_breakdown(
            db,
            run_id=resolved_run_id,
            progress=progress,
        )
        scored = breakdown.scored
        errors = breakdown.errors
        cancelled_receipts = breakdown.cancelled
        pre_screened_out = breakdown.pre_screened_out
    elif started_at is not None:
        scored, errors, pre_screened_out = r.batch_score_terminal_counts(
            db,
            role_id=role_id,
            started_at=started_at,
        )
    else:
        scored = errors = pre_screened_out = 0
    status = str(progress.get("status") or entry.get("dispatch_status") or "idle")
    exact_not_processed = 0
    exact_contract_invalid = False
    exact_identity_outside_dispatch = False
    exact_dispatched_ids: frozenset[int] = frozenset()
    exact_terminal_ids: frozenset[int] = frozenset()
    exact_active_ids: frozenset[int] = frozenset()
    if uses_exact_receipts:
        exact = exact_scoring_terminal_counts(
            progress,
            scored=scored,
            errors=errors + cancelled_receipts,
            pre_screened_out=pre_screened_out,
        )
        exact_contract_invalid = exact is None or (
            bool(target) and exact.target_total != target
        )
        raw_dispatched_ids = progress.get("dispatched_application_ids")
        if not isinstance(raw_dispatched_ids, list) or any(
            type(value) is not int or value <= 0 for value in raw_dispatched_ids
        ):
            exact_contract_invalid = True
        else:
            exact_dispatched_ids = frozenset(raw_dispatched_ids)
            exact_terminal_ids = breakdown.terminal_application_ids
            exact_active_ids = breakdown.active_application_ids
            if not (exact_terminal_ids | exact_active_ids) <= exact_dispatched_ids:
                exact_contract_invalid = True
                exact_identity_outside_dispatch = True
        if exact is not None and not exact_contract_invalid:
            target = exact.target_total
            if status in {"cancelled", "cancelling"}:
                exact_not_processed = exact.not_enqueued + cancelled_receipts
            else:
                errors = exact.errors + exact.not_enqueued
    elif bool(progress.get("fanout_complete")) and status not in {
        "cancelled",
        "cancelling",
    }:
        errors += progress_count(progress.get("not_enqueued"))

    active = (
        bool(exact_active_ids)
        if uses_exact_receipts
        else scoring_batch_has_active_jobs(
            db,
            run_id=resolved_run_id,
            progress=progress,
        )
    )
    if (
        uses_exact_receipts
        and not active
        and exact_dispatched_ids != exact_terminal_ids
    ):
        exact_contract_invalid = True
    if exact_identity_outside_dispatch:
        # An active job not named by the immutable dispatch receipt is corrupt
        # ownership evidence, not legitimate work that can make this parent wait.
        active = False
    if active and status in {"completed", "cancelled", "failed"}:
        status = "cancelling" if status == "cancelled" else "running"
    processed = scored + errors + pre_screened_out
    accounted = processed + exact_not_processed
    if exact_contract_invalid and not active:
        status = "failed"
        errors += max(0, target - processed)
        counters = dict(run.counters or {}) if run is not None else {}
        counters.update(
            scored=scored,
            errors=errors,
            pre_screened_out=pre_screened_out,
        )
        r._update_job_run(
            resolved_run_id,
            status=status,
            counters=counters,
            error="scoring_batch_invalid_terminal_receipts",
            finished=True,
        )
    elif (
        not active
        and target
        and accounted == target
        and status in SCORING_ACTIVE_RUN_STATUSES
    ):
        status = (
            "cancelled"
            if status == "cancelling"
            else ("failed" if bool(progress.get("fanout_failed")) else "completed")
        )
        counters = dict(run.counters or {}) if run is not None else {}
        counters.update(
            scored=scored,
            errors=errors,
            pre_screened_out=pre_screened_out,
        )
        if r._update_job_run(
            resolved_run_id,
            status=status,
            counters=counters,
            finished=True,
        ):
            progress["status"] = status
    processed = scored + errors + pre_screened_out
    terminal_deficit = (
        max(0, target - processed)
        if not active and status in {"completed", "cancelled", "failed"}
        else 0
    )
    if terminal_deficit and status != "cancelled":
        # A terminal child that did not account for its exact cohort is a
        # failed child, even when an older producer optimistically stamped it
        # completed. Reconcile the missing targets as errors so parent totals
        # cannot claim success with processed < target.
        errors += terminal_deficit
        status = "failed"
    return {
        "role_id": role_id,
        "run_id": resolved_run_id or run_id or None,
        "target": target,
        "scored": scored,
        "pre_screened_out": pre_screened_out,
        "errors": errors,
        "not_processed": terminal_deficit if status == "cancelled" else 0,
        "status": status,
        "active_receipts": active,
        "receipt_invalid": exact_contract_invalid,
    }


def read_scoring_backfill_status(
    routes: Mapping[str, Any],
    *,
    db: Any,
    current_user: Any,
) -> dict[str, Any]:
    r = _Routes(routes)
    organization_id = int(current_user.organization_id)
    parent = latest_scoring_backfill_run(
        db,
        organization_id=organization_id,
    )
    meta: dict[str, Any] | None = None
    if parent is not None:
        meta = {
            **dict(parent.counters or {}),
            "parent_run_id": int(parent.id),
            "started_at": parent.started_at,
        }
    else:
        client = r._redis_client()
        if client:
            try:
                raw = client.get(r._BACKFILL_META_KEY.format(org_id=organization_id))
                if raw:
                    meta = json.loads(raw)
            except Exception:
                pass
    if not meta:
        return {"status": "no_backfill", "roles": []}

    strict_plan = meta.get("role_plan_version") == SCORING_BACKFILL_PLAN_VERSION
    strict_role_plan = (
        scoring_backfill_plan_from_counters(meta) if strict_plan else None
    )
    plan_by_role = {
        int(item["role_id"]): list(item["target_application_ids"])
        for item in strict_role_plan or []
    }
    parent_run_id = progress_count(meta.get("parent_run_id"))
    entries = list(meta.get("children") or meta.get("roles") or [])
    role_statuses = [
        _child_status(
            r,
            db=db,
            entry=entry,
            organization_id=organization_id,
            parent_run_id=(parent_run_id if strict_plan else None),
            expected_target_ids=(
                plan_by_role.get(int(entry["role_id"]), []) if strict_plan else None
            ),
        )
        for entry in entries
        if isinstance(entry, dict) and type(entry.get("role_id")) is int
    ]
    fanout_accounted = (
        scoring_backfill_fanout_accounted(meta)
        if strict_plan
        else bool(meta.get("fanout_complete"))
    )
    total_scored = sum(item["scored"] for item in role_statuses)
    total_pre_screened_out = sum(item["pre_screened_out"] for item in role_statuses)
    total_not_processed = sum(item["not_processed"] for item in role_statuses)
    total_errors = sum(item["errors"] for item in role_statuses) + sum(
        progress_count(item.get("target"))
        for item in list(meta.get("skipped") or [])
        if isinstance(item, dict)
        and item.get("reason") in {"durable_receipt_failed", "dispatch_failed"}
        and not item.get("run_id")
    )
    all_complete = fanout_accounted and all(
        (
            item["status"] in {"completed", "cancelled", "failed"}
            and not item["active_receipts"]
        )
        for item in role_statuses
    )
    terminal_child_statuses = {item["status"] for item in role_statuses}
    invalid_child_receipt = any(
        bool(item.get("receipt_invalid")) for item in role_statuses
    )
    if parent is not None and parent.status == "failed":
        status = "failed"
    elif not all_complete:
        status = "running"
    elif "failed" in terminal_child_statuses:
        status = "failed"
    elif "cancelled" in terminal_child_statuses:
        status = "cancelled"
    else:
        status = "completed"
    if (
        parent is not None
        and all_complete
        and parent.finished_at is None
        and status in {"completed", "cancelled", "failed"}
    ):
        counters = dict(parent.counters or {})
        counters.update(
            total_scored=total_scored,
            total_pre_screened_out=total_pre_screened_out,
            total_errors=total_errors,
            total_not_processed=total_not_processed,
        )
        r._update_job_run(
            int(parent.id),
            status=status,
            counters=counters,
            error=(
                "scoring_backfill_child_receipt_invalid"
                if invalid_child_receipt
                else None
            ),
            finished=True,
        )

    return {
        "status": status,
        "parent_run_id": meta.get("parent_run_id"),
        "applied_after": meta.get("applied_after"),
        "started_at": meta.get("started_at"),
        "total_target": progress_count(meta.get("total_target")),
        "total_scored": total_scored,
        "total_pre_screened_out": total_pre_screened_out,
        "total_errors": total_errors,
        "total_not_processed": total_not_processed,
        "processed": total_scored + total_errors + total_pre_screened_out,
        "roles": role_statuses,
        "skipped": list(meta.get("skipped") or []),
    }
