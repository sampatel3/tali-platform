"""Ordinary-role worker for the application Process cascade."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...platform.config import settings
from ...services.application_automation_service import run_auto_reject_if_needed
from ...services.cv_score_orchestrator import enqueue_score
from ...services.interview_support_service import refresh_application_interview_support
from ...services.logical_role_batch_operations import is_related_role
from .batch_runtime_state import clear_cancel_flag as _clear_cancel_flag
from .job_authorization import JobPermission, require_job_permission
from .role_process_related_worker import run_related_role_process
from .role_process_scope import (
    _PROCESS_CANCEL_PREFIX,
    _apply_application_ids_filter,
    _apply_stage_filter,
    _empty_process_progress,
    _matches_stage_filter,
    _process_progress,
    _set_process_progress,
    is_process_cancelled,
)
from .role_support import refresh_application_score_cache

logger = logging.getLogger("taali.applications.process")


def run_process(
    role_id: int,
    org_id: int,
    *,
    fetch_cvs: bool,
    refresh_cvs: bool,
    pre_screen: bool,
    refresh_pre_screen: bool,
    score_mode: str,
    sync_graph: bool = False,
    refresh_graph: bool = False,
    stage_filter: str | None = None,
    application_ids: list[int] | None = None,
    user_id: int | None = None,
    session_factory,
    fetch_cv,
    select_pre_screen_targets,
    select_graph_sync_candidates,
    refresh_rank_score,
) -> None:
    """Background worker: cascade fetch → pre-screen → score → graph sync.

    Each step is independently toggleable. ``sync_graph`` runs the
    Graphiti projection for the candidates of THIS role only (not the
    whole org).

    ``stage_filter`` narrows the cascade to one segment of the candidate
    table (``applied|invited|in_assessment|review|advanced|rejected``).
    ``None`` / ``"all"`` runs the full role. ``application_ids`` is an
    explicit override — when set, only those specific applications are
    processed (stage_filter is ignored).

    Updates ``_process_progress[role_id]`` in real time so the status endpoint
    can report combined progress.
    """
    from ...components.scoring.pre_screen_execution import (
        execute_pre_screen_with_role_fence,
    )
    from ...services.claude_client_resolver import get_client_for_org
    from ...services.pre_screening_service import execute_pre_screen_only

    db = session_factory()
    progress = _process_progress.get(role_id) or _empty_process_progress()
    try:
        if user_id is not None:
            initiating_user = (
                db.query(User)
                .filter(
                    User.id == int(user_id),
                    User.organization_id == int(org_id),
                )
                .one_or_none()
            )
            if initiating_user is None:
                progress["status"] = "failed"
                progress["error"] = "initiating_user_unavailable"
                _set_process_progress(role_id, progress)
                return
            try:
                require_job_permission(
                    db,
                    current_user=initiating_user,
                    role_id=role_id,
                    permission=JobPermission.CONTROL_AGENT,
                    lock_for_update=False,
                )
            except HTTPException:
                progress["status"] = "failed"
                progress["error"] = "authorization_revoked"
                _set_process_progress(role_id, progress)
                return
        org = db.query(Organization).filter(Organization.id == org_id).first()
        role = (
            db.query(Role)
            .filter(
                Role.id == role_id,
                Role.organization_id == org_id,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if not org or not role:
            progress["status"] = "failed"
            _set_process_progress(role_id, progress)
            return

        progress["role_name"] = role.name
        progress["status"] = "running"
        progress["current_step"] = None
        _set_process_progress(role_id, progress)

        if is_related_role(role):
            if pre_screen or refresh_pre_screen:
                progress["status"] = "failed"
                progress["error"] = "related_role_pre_screen_unavailable"
                _set_process_progress(role_id, progress)
                return
            run_related_role_process(
                db,
                role=role,
                org=org,
                progress=progress,
                fetch_cvs=fetch_cvs,
                refresh_cvs=refresh_cvs,
                score_mode=score_mode,
                sync_graph=sync_graph,
                refresh_graph=refresh_graph,
                stage_filter=stage_filter,
                application_ids=application_ids,
                user_id=user_id,
                fetch_cv=fetch_cv,
            )
            return

        org_client = get_client_for_org(org)

        # ── Step 1: Fetch CVs ────────────────────────────────────────────
        if fetch_cvs:
            progress["current_step"] = "fetch"
            fetch_query = (
                db.query(CandidateApplication)
                .options(joinedload(CandidateApplication.candidate))
                .filter(
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.organization_id == org_id,
                    CandidateApplication.deleted_at.is_(None),
                    CandidateApplication.source == "workable",
                )
            )
            if application_ids:
                fetch_query = _apply_application_ids_filter(fetch_query, application_ids)
            else:
                fetch_query = _apply_stage_filter(fetch_query, stage_filter)
            apps_to_fetch = fetch_query.all()
            # In refresh mode every Workable-sourced application gets re-
            # fetched regardless of whether a CV is already cached. The
            # default still skips cached CVs to keep the common-case run
            # cheap.
            if not refresh_cvs:
                apps_to_fetch = [a for a in apps_to_fetch if not (a.cv_text or "").strip()]
            progress["fetch"]["total"] = len(apps_to_fetch)
            _set_process_progress(role_id, progress)

            for idx, app in enumerate(apps_to_fetch):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    _set_process_progress(role_id, progress)
                    return
                try:
                    success = False
                    # Refresh mode bypasses the candidate-row short-circuit
                    # and goes back to Workable for the freshest CV. The
                    # cached candidate row is still updated as a side
                    # effect of the Workable fetch.
                    if (
                        not refresh_cvs
                        and app.candidate
                        and (app.candidate.cv_text or "").strip()
                    ):
                        # CV already on the candidate row — copy it onto the app.
                        app.cv_file_url = app.candidate.cv_file_url
                        app.cv_filename = app.candidate.cv_filename
                        app.cv_text = app.candidate.cv_text
                        app.cv_uploaded_at = app.candidate.cv_uploaded_at
                        success = True
                    elif (app.source or "") == "workable":
                        success = bool(fetch_cv(app, app.candidate, db, org))
                    if success:
                        progress["fetch"]["fetched"] += 1
                    else:
                        progress["fetch"]["unavailable"] += 1
                except Exception:
                    logger.exception("Process fetch failed for application_id=%s", app.id)
                    progress["fetch"]["errors"] += 1
                progress["fetch"]["attempted"] = idx + 1
                _set_process_progress(role_id, progress)
                if (idx + 1) % 3 == 0:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            try:
                db.commit()
            except Exception:
                db.rollback()

        # ── Step 2: Pre-screen ───────────────────────────────────────────
        if pre_screen or refresh_pre_screen:
            progress["current_step"] = "pre_screen"
            apps = select_pre_screen_targets(
                db, role_id=role_id, organization_id=org_id, refresh=refresh_pre_screen
            )
            if application_ids:
                _id_set = {int(i) for i in application_ids}
                apps = [a for a in apps if a.id in _id_set]
            elif stage_filter and stage_filter != "all":
                apps = [a for a in apps if _matches_stage_filter(a, stage_filter)]
            progress["pre_screen"]["total"] = len(apps)
            _set_process_progress(role_id, progress)

            for idx, app in enumerate(apps):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    _set_process_progress(role_id, progress)
                    return
                try:
                    result = execute_pre_screen_with_role_fence(
                        db,
                        application=app,
                        role=role,
                        execute=lambda: execute_pre_screen_only(
                            app, db=db, client=org_client
                        ),
                    )
                    if result.get("status") == "error":
                        progress["pre_screen"]["errors"] += 1
                    if result.get("status") == "ok":
                        from ...tasks.automation_tasks import (
                            run_application_auto_reject,
                        )

                        try:
                            run_application_auto_reject.delay(int(app.id))
                        except Exception:
                            logger.exception(
                                "Process pre-screen auto-reject dispatch failed "
                                "for application_id=%s",
                                app.id,
                            )
                except Exception:
                    logger.exception("Process pre-screen failed for application_id=%s", app.id)
                    progress["pre_screen"]["errors"] += 1
                progress["pre_screen"]["processed"] = idx + 1
                _set_process_progress(role_id, progress)

        # ── Step 3: Score ────────────────────────────────────────────────
        if score_mode in ("new", "all"):
            progress["current_step"] = "score"
            progress["score"]["mode"] = score_mode
            include_scored = score_mode == "all"
            apps_query = (
                db.query(CandidateApplication)
                .options(
                    joinedload(CandidateApplication.candidate),
                    joinedload(CandidateApplication.role),
                    joinedload(CandidateApplication.interviews),
                    joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
                )
                .filter(
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.organization_id == org_id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
            if application_ids:
                apps_query = _apply_application_ids_filter(apps_query, application_ids)
            else:
                apps_query = _apply_stage_filter(apps_query, stage_filter)
            if not include_scored:
                apps_query = apps_query.filter(CandidateApplication.cv_match_score.is_(None))
            apps = apps_query.all()
            progress["score"]["total"] = len(apps)
            _set_process_progress(role_id, progress)

            job_spec_text = ((role.job_spec_text if role else None) or "").strip()
            for idx, app in enumerate(apps):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    _set_process_progress(role_id, progress)
                    return
                try:
                    cv_text = (app.cv_text or "").strip()
                    if not cv_text or not job_spec_text or not settings.ANTHROPIC_API_KEY:
                        # Can't score without inputs — count as filtered for visibility.
                        progress["score"]["filtered"] += 1
                        progress["score"]["scored"] = idx + 1
                        _set_process_progress(role_id, progress)
                        continue
                    if not include_scored:
                        if app.cv_match_score is not None:
                            progress["score"]["scored"] = idx + 1
                            _set_process_progress(role_id, progress)
                            continue
                        if (
                            (app.pre_screen_recommendation or "") == "Below threshold"
                            and app.pre_screen_run_at is not None
                            and (app.cv_uploaded_at is None or app.cv_uploaded_at <= app.pre_screen_run_at)
                        ):
                            progress["score"]["filtered"] += 1
                            progress["score"]["scored"] = idx + 1
                            _set_process_progress(role_id, progress)
                            continue
                    job = enqueue_score(
                        db,
                        app,
                        force=include_scored,
                        requires_active_agent=False,
                    )
                    if job is not None and job.status == "error":
                        progress["score"]["errors"] += 1
                    refresh_rank_score(app)
                    refresh_application_score_cache(app, db=db)
                    refresh_application_interview_support(app)
                    run_auto_reject_if_needed(
                        db=db, org=org, app=app, role=role, actor_type="system"
                    )
                    db.flush()
                except Exception:
                    logger.exception("Process score failed for application_id=%s", app.id)
                    progress["score"]["errors"] += 1
                progress["score"]["scored"] = idx + 1
                _set_process_progress(role_id, progress)
                if (idx + 1) % 5 == 0:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            try:
                db.commit()
            except Exception:
                db.rollback()

        # ── Step 4: Graph sync ───────────────────────────────────────────
        if sync_graph:
            from ...candidate_graph import sync as graph_sync_module
            from ...candidate_graph import client as graph_client
            from ...models.candidate import Candidate as _Candidate

            progress["current_step"] = "graph_sync"
            _set_process_progress(role_id, progress)

            if not graph_client.is_configured():
                # Not configured — record as a single error rather than failing
                # the whole process.
                progress["graph_sync"]["errors"] = 1
                _set_process_progress(role_id, progress)
            else:
                candidate_ids = select_graph_sync_candidates(
                    db,
                    organization_id=org_id,
                    refresh=refresh_graph,
                    role_id=role_id,
                )
                if application_ids or (stage_filter and stage_filter != "all"):
                    # Constrain to candidate_ids whose application is in
                    # the scoped selection — otherwise a recruiter who
                    # ticked 5 boxes still pays for the graph indexing of
                    # the other 300+.
                    scope_query = (
                        db.query(CandidateApplication.candidate_id)
                        .filter(
                            CandidateApplication.role_id == role_id,
                            CandidateApplication.organization_id == org_id,
                            CandidateApplication.deleted_at.is_(None),
                            CandidateApplication.candidate_id.isnot(None),
                        )
                    )
                    if application_ids:
                        scope_query = _apply_application_ids_filter(scope_query, application_ids)
                    else:
                        scope_query = _apply_stage_filter(scope_query, stage_filter)
                    scoped_candidate_ids = {int(cid) for (cid,) in scope_query.all()}
                    candidate_ids = [cid for cid in candidate_ids if int(cid) in scoped_candidate_ids]
                progress["graph_sync"]["total"] = len(candidate_ids)
                _set_process_progress(role_id, progress)

                for idx, cid in enumerate(candidate_ids):
                    if is_process_cancelled(role_id):
                        progress["status"] = "cancelled"
                        _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                        try:
                            db.commit()
                        except Exception:
                            db.rollback()
                        _set_process_progress(role_id, progress)
                        return
                    try:
                        cand = db.query(_Candidate).filter(_Candidate.id == cid).first()
                        if cand is None:
                            progress["graph_sync"]["errors"] += 1
                        else:
                            sent = graph_sync_module.sync_candidate(
                                cand,
                                db=db,
                                include_cv_text=True,
                                bill_organization_id=org_id,
                                bill_role_id=role_id,
                                bill_user_id=user_id,
                                require_role_admission=True,
                            )
                            if sent == 0:
                                # Graphiti returned no episodes — likely an
                                # LLM extraction issue. Count as error so it
                                # surfaces in the toaster, but don't bail.
                                progress["graph_sync"]["errors"] += 1
                    except Exception:
                        logger.exception("Process graph sync failed for candidate_id=%s", cid)
                        progress["graph_sync"]["errors"] += 1
                    progress["graph_sync"]["synced"] = idx + 1
                    _set_process_progress(role_id, progress)
                    if (idx + 1) % 5 == 0:
                        try:
                            db.commit()
                        except Exception:
                            db.rollback()
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        progress["current_step"] = None
        progress["status"] = "completed"
        _set_process_progress(role_id, progress)
    except Exception:
        logger.exception("Process cascade failed for role_id=%s", role_id)
        progress["status"] = "failed"
        _set_process_progress(role_id, progress)
    finally:
        db.close()
