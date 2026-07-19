"""Implementation behind the scoring-batch application routes.

The route module keeps FastAPI signatures and compatibility exports.  Runtime
lookups intentionally go through that module's namespace so existing tests and
operational hooks that replace its private Redis/progress boundaries continue
to affect the same request path after this extraction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...services.scoring_batch_fanout_recovery import (
    mark_scoring_fanout_publish_failed,
    mark_scoring_fanout_published,
    reserve_scoring_fanout_publish,
)
from ...services.scoring_batch_successors import (
    claim_scoring_successor,
    clear_scoring_successor,
    queue_scoring_successor,
)
from ...services.scoring_batch_successor_reconcile import (
    dispatch_claimed_scoring_successor,
)
from ...services.scoring_batch_terminal_contract import (
    exact_scoring_terminal_counts,
    exact_scoring_terminal_identity_error,
)
from .scoring_batch_state import (
    SCORING_DURABLE_QUEUE_CONTRACT,
    scoring_batch_exact_terminal_breakdown,
    scoring_batch_has_active_jobs,
    scoring_uses_durable_queue,
    scoring_uses_exact_receipts,
)


_RUN_CANCEL_PREFIX = "batch_score:cancel_run:"


class _Routes:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        return self._values[name]


def _durable_successor(progress: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = progress.get("queued_successor")
    if not isinstance(payload, dict):
        return None
    queue_id = payload.get("queue_id")
    include_scored = payload.get("include_scored")
    applied_after = payload.get("applied_after")
    if not isinstance(queue_id, str) or not queue_id:
        return None
    if type(include_scored) is not bool:
        return None
    if applied_after is not None and type(applied_after) is not str:
        return None
    return dict(payload)


def start_batch_score(
    routes: Mapping[str, Any],
    *,
    role_id: int,
    include_scored: bool,
    applied_after: str | None,
    dry_run: bool,
    db: Any,
    current_user: Any,
) -> dict[str, Any]:
    """Start or queue one role-scoring batch without owning its HTTP surface."""

    r = _Routes(routes)
    role = r.require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=r.JobPermission.CONTROL_AGENT,
    )
    if not r.role_has_job_spec(role):
        raise r.HTTPException(
            status_code=400,
            detail="Upload job spec before batch scoring",
        )
    applied_after_cutoff = r._batch_applied_after_cutoff(applied_after)

    if dry_run:
        all_apps_query = db.query(r.CandidateApplication).filter(
            r.CandidateApplication.role_id == role_id,
            r.CandidateApplication.organization_id == current_user.organization_id,
            r.CandidateApplication.deleted_at.is_(None),
        )
        if applied_after_cutoff is not None:
            all_apps_query = all_apps_query.join(
                r.Candidate,
                r.Candidate.id == r.CandidateApplication.candidate_id,
            ).filter(r.Candidate.workable_created_at >= applied_after_cutoff)
        all_apps = all_apps_query.all()

        def _has_cv(application: Any) -> bool:
            return bool((application.cv_text or "").strip())

        def _will_have_cv_after_cascade(application: Any) -> bool:
            return _has_cv(application) or (application.source or "") == "workable"

        will_fetch = sum(
            1
            for application in all_apps
            if not _has_cv(application) and (application.source or "") == "workable"
        )

        def _needs_pre_screen(application: Any) -> bool:
            if not _will_have_cv_after_cascade(application):
                return False
            if (
                application.pre_screen_recommendation is None
                or application.pre_screen_run_at is None
            ):
                return True
            return bool(
                application.cv_uploaded_at is not None
                and application.cv_uploaded_at > application.pre_screen_run_at
            )

        will_pre_screen = sum(
            1 for application in all_apps if _needs_pre_screen(application)
        )

        if include_scored:
            will_score = sum(
                1
                for application in all_apps
                if _will_have_cv_after_cascade(application)
            )
        else:

            def _needs_score(application: Any) -> bool:
                if not _will_have_cv_after_cascade(application):
                    return False
                if application.cv_match_score is not None:
                    return bool(
                        application.cv_match_scored_at is not None
                        and application.cv_uploaded_at is not None
                        and application.cv_uploaded_at > application.cv_match_scored_at
                    )
                if (
                    (application.pre_screen_recommendation or "") == "Below threshold"
                    and application.pre_screen_run_at is not None
                    and (
                        application.cv_uploaded_at is None
                        or application.cv_uploaded_at <= application.pre_screen_run_at
                    )
                ):
                    return False
                return True

            will_score = sum(1 for application in all_apps if _needs_score(application))

        return {
            "will_fetch_cv": int(will_fetch),
            "will_pre_screen": int(will_pre_screen),
            "will_score": int(will_score),
            "total": len(all_apps),
            "include_scored": bool(include_scored),
        }

    # PostgreSQL holds this transaction-scoped fence until the request session
    # closes. Re-read durable state only after acquiring it so two API workers
    # cannot both observe an idle role and publish duplicate paid work.
    r._lock_scoring_start_scope(db, role_id)
    existing = r.get_retained_progress(r._batch_score_progress, role_id) or {}
    latest_run = r._latest_scoring_run(
        db,
        role_id=role_id,
        organization_id=int(current_user.organization_id),
    )
    existing = r._merge_scoring_progress(existing, latest_run)
    existing["role_name"] = str(getattr(role, "name", "") or "")
    existing_run_id = r._progress_run_id(existing)
    has_active_jobs = scoring_batch_has_active_jobs(
        db,
        run_id=existing_run_id,
        progress=existing,
    )
    if r._scoring_fanout_abandoned(existing) and not has_active_jobs:
        existing = r._fail_abandoned_scoring_run(role_id, existing)
    if existing.get("status") in r._SCORING_ACTIVE_RUN_STATUSES or has_active_jobs:
        if has_active_jobs and existing.get("status") in {
            "completed",
            "cancelled",
            "failed",
        }:
            existing["status"] = (
                "cancelling" if existing.get("status") == "cancelled" else "running"
            )
            existing["terminal_at"] = None
        r.publish_active_progress(r._batch_score_progress, role_id, existing)
        queue_id = r.secrets.token_hex(16)
        if existing_run_id == 0 or not queue_scoring_successor(
            existing_run_id,
            role_id=role_id,
            organization_id=int(current_user.organization_id),
            include_scored=include_scored,
            applied_after=applied_after,
            queue_id=queue_id,
        ):
            raise r.HTTPException(
                status_code=503,
                detail="Could not persist queued scoring batch",
            )
        # Redis is a low-latency mirror only. The durable run counter above is
        # the acknowledged successor intent and remains authoritative.
        r._write_batch_queue(
            role_id,
            include_scored=include_scored,
            applied_after=applied_after,
            queue_id=queue_id,
        )
        return {
            "status": "queued",
            "total": existing.get("total", 0),
            "scored": existing.get("scored", 0),
            "include_scored": bool(include_scored),
            "run_id": existing.get("run_id"),
            "started_at": existing.get("started_at"),
        }

    target_query = db.query(r.CandidateApplication).filter(
        r.CandidateApplication.role_id == role_id,
        r.CandidateApplication.organization_id == current_user.organization_id,
        r.CandidateApplication.deleted_at.is_(None),
    )
    if not include_scored:
        target_query = target_query.filter(
            r.CandidateApplication.cv_match_score.is_(None)
        )
    if applied_after_cutoff is not None:
        target_query = target_query.join(
            r.Candidate,
            r.Candidate.id == r.CandidateApplication.candidate_id,
        ).filter(r.Candidate.workable_created_at >= applied_after_cutoff)
    target_application_ids = [
        int(application_id)
        for (application_id,) in (
            target_query.with_entities(r.CandidateApplication.id)
            .order_by(r.CandidateApplication.id)
            .all()
        )
    ]
    target_count = len(target_application_ids)

    if target_count == 0:
        return {
            "status": "nothing_to_score",
            "total": 0,
            "total_target": 0,
            "total_unscored": 0,
            "include_scored": bool(include_scored),
        }

    batch_started_at = r.datetime.now(r.timezone.utc)
    run_id = r._create_job_run(
        kind=r.JOB_KIND_SCORING_BATCH,
        scope_kind=r.SCOPE_KIND_ROLE,
        scope_id=role_id,
        organization_id=current_user.organization_id,
        counters={
            "total": target_count,
            "selected_total": target_count,
            "target_application_ids": target_application_ids,
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "queue_contract": SCORING_DURABLE_QUEUE_CONTRACT,
            "scored": 0,
            "errors": 0,
            "pre_screened_out": 0,
            "include_scored": bool(include_scored),
            "applied_after": applied_after,
            "fanout_state": "dispatching",
            "fanout_complete": False,
        },
        status="dispatching",
    )
    if run_id is None:
        raise r.HTTPException(
            status_code=503,
            detail="Could not persist scoring batch",
        )
    r.set_bounded_progress(
        r._batch_score_progress,
        role_id,
        {
            "total": target_count,
            "selected_total": target_count,
            "target_application_ids": target_application_ids,
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "queue_contract": SCORING_DURABLE_QUEUE_CONTRACT,
            "scored": 0,
            "errors": 0,
            "status": "running",
            "include_scored": bool(include_scored),
            "applied_after": applied_after,
            "fanout_state": "dispatching",
            "fanout_complete": False,
            "started_at": batch_started_at,
            "organization_id": current_user.organization_id,
            "role_name": str(getattr(role, "name", "") or ""),
            "run_id": run_id,
        },
    )
    r._write_batch_meta(
        role_id,
        total=target_count,
        started_at=batch_started_at,
        include_scored=bool(include_scored),
        run_id=run_id,
    )

    from ...tasks.scoring_tasks import batch_score_role as celery_batch_score_role

    publish_scope = {
        "run_id": int(run_id),
        "role_id": int(role_id),
        "organization_id": int(current_user.organization_id),
    }
    reserve_scoring_fanout_publish(**publish_scope)
    dispatch_pending = False
    try:
        celery_batch_score_role.delay(
            role_id,
            include_scored=include_scored,
            applied_after=applied_after,
            run_id=int(run_id),
        )
    except Exception as exc:
        r.logger.error(
            "Scoring batch dispatch failed role_id=%s error_type=%s",
            role_id,
            type(exc).__name__,
        )
        mark_scoring_fanout_publish_failed(**publish_scope)
        pending = {
            **(r._batch_score_progress.get(role_id) or {}),
            "status": "dispatching",
            "dispatch_pending": True,
        }
        r.publish_active_progress(r._batch_score_progress, role_id, pending)
        dispatch_pending = True
    else:
        mark_scoring_fanout_published(**publish_scope)

    response = {
        "status": "started",
        "total": target_count,
        "total_target": target_count,
        "total_unscored": target_count if not include_scored else 0,
        "include_scored": bool(include_scored),
        "run_id": run_id,
        "started_at": batch_started_at,
    }
    if dispatch_pending:
        response["dispatch_pending"] = True
    return response


def list_active_batch_scores(
    routes: Mapping[str, Any],
    *,
    db: Any,
    current_user: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Merge process-local and durable scoring batches for one organization."""

    r = _Routes(routes)
    organization_id = int(current_user.organization_id)
    progress_by_role: dict[int, dict[str, Any]] = {}
    for role_id, progress in r.retained_progress_items(r._batch_score_progress):
        if progress.get("organization_id") != organization_id:
            continue
        progress_by_role[int(role_id)] = dict(progress)

    for run in r._recent_scoring_runs(db, organization_id=organization_id):
        role_id = int(run.scope_id)
        progress_by_role[role_id] = r._merge_scoring_progress(
            progress_by_role.get(role_id),
            run,
        )

    for role_id, progress in list(progress_by_role.items()):
        active_jobs = scoring_batch_has_active_jobs(
            db,
            run_id=r._progress_run_id(progress),
            progress=progress,
        )
        if r._scoring_fanout_abandoned(progress) and not active_jobs:
            progress_by_role[role_id] = r._fail_abandoned_scoring_run(
                role_id,
                progress,
            )
        elif active_jobs and progress.get("status") in {
            "completed",
            "cancelled",
            "failed",
        }:
            progress["status"] = (
                "cancelling" if progress.get("status") == "cancelled" else "running"
            )
            progress["terminal_at"] = None

    unnamed_role_ids = [
        role_id
        for role_id, progress in progress_by_role.items()
        if not str(progress.get("role_name") or "")
    ]
    role_names: dict[int, str] = {}
    if unnamed_role_ids:
        role_names = {
            int(role_id): str(role_name or "")
            for role_id, role_name in (
                db.query(r.Role.id, r.Role.name)
                .filter(
                    r.Role.organization_id == organization_id,
                    r.Role.id.in_(unnamed_role_ids),
                )
                .all()
            )
        }

    active: list[dict[str, Any]] = []
    for role_id, progress in progress_by_role.items():
        if progress.get("status") not in r._SCORING_VISIBLE_RUN_STATUSES:
            continue
        role_name = str(progress.get("role_name") or role_names.get(role_id) or "")
        progress["role_name"] = role_name
        r.publish_active_progress(r._batch_score_progress, role_id, progress)
        active.append(
            {
                "role_id": role_id,
                "role_name": role_name,
                "run_id": progress.get("run_id"),
                "status": progress.get("status"),
                "total": progress.get("total", 0),
                "scored": progress.get("scored", 0),
                "errors": progress.get("errors", 0),
                "pre_screened_out": progress.get("pre_screened_out", 0),
                "started_at": progress.get("started_at"),
                "terminal_at": progress.get("terminal_at"),
            }
        )
    active.sort(
        key=lambda item: (
            r._progress_count(item.get("run_id")),
            item["role_id"],
        ),
        reverse=True,
    )
    return {"active": active}


def read_batch_score_status(
    routes: Mapping[str, Any],
    *,
    role_id: int,
    db: Any,
    current_user: Any,
) -> dict[str, Any]:
    """Reconcile and return one role's scoring-batch status."""

    r = _Routes(routes)
    role = r.require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=r.JobPermission.CONTROL_AGENT,
    )
    r._lock_scoring_start_scope(db, role_id)
    organization_id = int(current_user.organization_id)
    latest_run = r._latest_scoring_run(
        db,
        role_id=role_id,
        organization_id=organization_id,
    )
    progress = r._merge_scoring_progress(
        r.get_retained_progress(r._batch_score_progress, role_id),
        latest_run,
    )
    if not progress.get("role_name"):
        progress["role_name"] = str(getattr(role, "name", "") or "")
    total = r._progress_count(progress.get("total"))
    started_at = r._progress_datetime(progress.get("started_at"))

    if total == 0 and r._progress_run_id(progress) == 0:
        meta = r._read_batch_meta(role_id)
        if meta:
            total = r._progress_count(meta.get("total"))
            started_at = started_at or r._progress_datetime(meta.get("started_at"))
            raw_run_id = meta.get("run_id")
            recovered_run_id = (
                raw_run_id if type(raw_run_id) is int and raw_run_id > 0 else None
            )
            progress = {
                **progress,
                "total": total,
                "started_at": started_at,
                "include_scored": bool(meta.get("include_scored")),
                "organization_id": organization_id,
                "run_id": recovered_run_id,
            }

    run_id = r._progress_run_id(progress)
    has_active_jobs = scoring_batch_has_active_jobs(
        db,
        run_id=run_id,
        progress=progress,
    )
    if r._scoring_fanout_abandoned(progress) and not has_active_jobs:
        progress = r._fail_abandoned_scoring_run(role_id, progress)

    scored = 0
    score_errors = 0
    pre_screened_out = 0
    cancelled_receipts = 0
    uses_exact_receipts = total > 0 and scoring_uses_exact_receipts(progress)
    if uses_exact_receipts:
        breakdown = scoring_batch_exact_terminal_breakdown(
            db,
            run_id=run_id,
            progress=progress,
        )
        scored = breakdown.scored
        score_errors = breakdown.errors
        cancelled_receipts = breakdown.cancelled
        pre_screened_out = breakdown.pre_screened_out
    elif total > 0 and started_at is not None:
        scored, score_errors, pre_screened_out = r.batch_score_terminal_counts(
            db,
            role_id=role_id,
            started_at=started_at,
            application_ids=r._progress_application_ids(progress),
        )
    status = str(progress.get("status") or "idle")
    exact_contract_invalid = False
    exact_contract_incomplete = False
    exact_not_enqueued = 0
    if uses_exact_receipts:
        exact = exact_scoring_terminal_counts(
            progress,
            scored=scored,
            errors=score_errors + cancelled_receipts,
            pre_screened_out=pre_screened_out,
        )
        identity_error = exact_scoring_terminal_identity_error(
            progress,
            terminal_application_ids=breakdown.terminal_application_ids,
            active_application_ids=breakdown.active_application_ids,
            drained=not has_active_jobs,
        )
        exact_contract_invalid = bool(
            exact is None or identity_error == "scoring_batch_invalid_terminal_receipts"
        )
        exact_contract_incomplete = bool(
            identity_error == "scoring_batch_incomplete_terminal_receipts"
        )
        if exact is not None:
            total = exact.target_total
            exact_not_enqueued = exact.not_enqueued
    fanout_complete = bool(progress.get("fanout_complete"))
    if uses_exact_receipts and exact is not None:
        exact_contract_incomplete = bool(
            exact_contract_incomplete
            or (
                exact.accounted != exact.target_total
                and (fanout_complete or status in {"completed", "cancelled", "failed"})
            )
        )
    not_enqueued = (
        exact_not_enqueued
        if uses_exact_receipts
        else (r._progress_count(progress.get("not_enqueued")) if fanout_complete else 0)
    )
    cancelled = status in {"cancelled", "cancelling"}
    errors = score_errors + (0 if cancelled else cancelled_receipts + not_enqueued)
    if exact_contract_invalid:
        errors = max(
            errors,
            max(0, total - scored - pre_screened_out - cancelled_receipts),
        )
    accounted = (
        scored + score_errors + cancelled_receipts + pre_screened_out + not_enqueued
    )
    progress = {
        **progress,
        "total": total,
        "scored": scored,
        "errors": errors,
        "pre_screened_out": pre_screened_out,
        "not_processed": cancelled_receipts + not_enqueued if cancelled else 0,
        "started_at": started_at,
    }

    if has_active_jobs and status in {"completed", "cancelled", "failed"}:
        status = "cancelling" if status == "cancelled" else "running"
        progress["terminal_at"] = None
    if status == "idle" and total > 0 and started_at is not None:
        status = "running"
        progress["status"] = status

    prior_status = status
    transitioned_to: str | None = None
    if (exact_contract_invalid or exact_contract_incomplete) and not has_active_jobs:
        status = "failed"
        if prior_status != "failed":
            transitioned_to = status
    elif (
        not has_active_jobs
        and total > 0
        and (fanout_complete or not uses_exact_receipts)
        and (
            accounted == total
            if uses_exact_receipts
            else (scored + errors + pre_screened_out) >= total
        )
    ):
        if status in {"dispatching", "queued", "running"}:
            status = "failed" if bool(progress.get("fanout_failed")) else "completed"
            transitioned_to = status
        elif status == "cancelling":
            status = "cancelled"
            transitioned_to = status

    progress["status"] = status
    if transitioned_to is not None:
        durable_counters = {
            key: value
            for key, value in progress.items()
            if key
            not in {
                "status",
                "started_at",
                "terminal_at",
                "organization_id",
                "run_id",
                "role_name",
            }
            and not (key == "not_processed" and value == 0)
        }
        run_id = r._progress_run_id(progress)
        terminal_error = None
        if exact_contract_invalid:
            terminal_error = "scoring_batch_invalid_terminal_receipts"
        elif exact_contract_incomplete:
            terminal_error = "scoring_batch_incomplete_terminal_receipts"
        update_fields: dict[str, Any] = {
            "status": transitioned_to,
            "counters": durable_counters,
            "finished": True,
        }
        if terminal_error is not None:
            update_fields["error"] = terminal_error
        durable_updated = run_id == 0 or bool(
            r._update_job_run(run_id, **update_fields)
        )
        if durable_updated:
            r.set_bounded_progress(r._batch_score_progress, role_id, progress)
            r._delete_batch_meta(role_id)
        else:
            # Keep the old lifecycle visible and, critically, do not consume a
            # queued successor while another worker still sees an active run.
            status = prior_status
            transitioned_to = None
            progress["status"] = status
            r.publish_active_progress(r._batch_score_progress, role_id, progress)
    elif status in r._SCORING_ACTIVE_RUN_STATUSES:
        r.publish_active_progress(r._batch_score_progress, role_id, progress)
    elif status in {"completed", "cancelled", "failed"}:
        r.set_bounded_progress(r._batch_score_progress, role_id, progress)
        r._delete_batch_meta(role_id)

    terminal_and_drained = (
        status in {"completed", "cancelled", "failed"}
        and not has_active_jobs
        and not exact_contract_invalid
        and not exact_contract_incomplete
    )
    durable_queue = scoring_uses_durable_queue(progress)
    parent_run_id = r._progress_run_id(progress)
    if terminal_and_drained:
        queued_params = (
            claim_scoring_successor(
                parent_run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            if durable_queue
            else r._claim_batch_queue(role_id)
        )
    else:
        queued_params = (
            _durable_successor(progress)
            if durable_queue
            else r._read_batch_queue(role_id)
        )
    queued_next: dict[str, Any] | None = None
    if queued_params is not None:
        if terminal_and_drained:
            q_include = bool(queued_params.get("include_scored"))
            q_after = queued_params.get("applied_after")
            result = dispatch_claimed_scoring_successor(
                db,
                parent_run_id=parent_run_id,
                role_id=role_id,
                organization_id=organization_id,
                claimed=queued_params,
                create_run_fn=r._create_job_run,
                durable_claim=durable_queue,
            )
            outcome = str(result.get("outcome") or "released")
            q_application_ids = list(result.get("target_application_ids") or [])
            q_count = len(q_application_ids)
            if outcome in {"started", "deduplicated", "recovery_pending"}:
                q_run_id = int(result["run_id"])
                latest_child = r._latest_scoring_run(
                    db,
                    role_id=role_id,
                    organization_id=organization_id,
                )
                progress = r._merge_scoring_progress({}, latest_child)
                if r._progress_run_id(progress) != q_run_id:
                    progress = {
                        "total": q_count,
                        "selected_total": q_count,
                        "target_application_ids": q_application_ids,
                        "dispatched_application_ids": [],
                        "score_job_ids": [],
                        "owned_score_job_ids": [],
                        "queue_contract": SCORING_DURABLE_QUEUE_CONTRACT,
                        "status": "running",
                        "include_scored": q_include,
                        "started_at": r.datetime.now(r.timezone.utc),
                        "organization_id": organization_id,
                        "run_id": q_run_id,
                    }
                progress["role_name"] = str(getattr(role, "name", "") or "")
                r.set_bounded_progress(r._batch_score_progress, role_id, progress)
                r._write_batch_meta(
                    role_id,
                    total=q_count,
                    started_at=progress["started_at"],
                    include_scored=q_include,
                    run_id=q_run_id,
                )
                total = q_count
                scored = errors = pre_screened_out = 0
                started_at = progress["started_at"]
                status = "running"
                r._clear_batch_queue(role_id)
            elif outcome == "retry_queued":
                retry_queue_id = str(result.get("retry_queue_id") or "")
                queued_next = {"include_scored": q_include}
                status = "failed"
                if retry_queue_id:
                    r._write_batch_queue(
                        role_id,
                        include_scored=q_include,
                        applied_after=q_after,
                        queue_id=retry_queue_id,
                    )
            elif outcome in {"released", "dispatch_failed"}:
                queued_next = {"include_scored": q_include}
            else:
                r._clear_batch_queue(role_id)
        else:
            queued_next = {"include_scored": queued_params.get("include_scored")}

    role_name = r.batch_score_role_name(
        db,
        progress=progress,
        role_id=role_id,
        organization_id=current_user.organization_id,
    )
    progress["role_name"] = role_name
    return {
        "status": status,
        "total": total,
        "scored": scored,
        "errors": errors,
        "pre_screened_out": pre_screened_out,
        "include_scored": bool(progress.get("include_scored")),
        "pre_screen_enabled": bool(r.settings.ENABLE_PRE_SCREEN_GATE),
        "role_name": role_name,
        "queued": queued_next,
        "run_id": progress.get("run_id"),
        "started_at": progress.get("started_at") or started_at,
        "terminal_at": progress.get("terminal_at"),
    }


def cancel_scoring_batch(
    routes: Mapping[str, Any],
    *,
    role_id: int,
    db: Any,
    current_user: Any,
) -> dict[str, Any]:
    """Cancel queued and pending work for one role-scoring batch."""

    r = _Routes(routes)
    role = r.require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=r.JobPermission.CONTROL_AGENT,
    )
    r._lock_scoring_start_scope(db, role_id)
    latest_run = r._latest_scoring_run(
        db,
        role_id=role_id,
        organization_id=int(current_user.organization_id),
    )
    progress = r._merge_scoring_progress(
        r.get_retained_progress(r._batch_score_progress, role_id),
        latest_run,
    )
    if not progress.get("role_name"):
        progress["role_name"] = str(getattr(role, "name", "") or "")
    run_id = r._progress_run_id(progress)
    durable_updated = False
    cancelled_count = 0
    active_jobs = scoring_batch_has_active_jobs(
        db,
        run_id=run_id,
        progress=progress,
    )
    if progress.get("status") in r._SCORING_ACTIVE_RUN_STATUSES or active_jobs:
        durable_updated = bool(
            r._update_job_run(
                run_id,
                status="cancelling",
                cancel_requested=True,
            )
        )
        if durable_updated:
            progress["status"] = "cancelling"
            r.set_bounded_progress(r._batch_score_progress, role_id, progress)
            # Durable cancellation is authoritative. Redis is only a run-scoped
            # wake-up hint and its availability never changes the receipt.
            r._set_cancel_flag(_RUN_CANCEL_PREFIX, run_id)
            clear_scoring_successor(
                run_id,
                role_id=role_id,
                organization_id=int(current_user.organization_id),
            )
            r._clear_batch_queue(role_id)
            try:
                from ...services.score_job_dispatch import (
                    cancel_pending_batch_score_jobs,
                )

                cancelled_count = cancel_pending_batch_score_jobs(
                    db,
                    batch_run_id=run_id,
                )
            except Exception as exc:
                r.logger.error(
                    "Owned pending-job cancellation failed role_id=%s "
                    "run_id=%s error_type=%s",
                    role_id,
                    run_id,
                    type(exc).__name__,
                )
                db.rollback()
    elif run_id and _durable_successor(progress) is not None:
        durable_updated = bool(r._update_job_run(run_id, cancel_requested=True))
        if durable_updated:
            clear_scoring_successor(
                run_id,
                role_id=role_id,
                organization_id=int(current_user.organization_id),
            )
            r._clear_batch_queue(role_id)

    return {
        "ok": bool(durable_updated),
        "role_id": role_id,
        "status": progress.get("status", "idle"),
        "pending_jobs_cancelled": cancelled_count,
    }
