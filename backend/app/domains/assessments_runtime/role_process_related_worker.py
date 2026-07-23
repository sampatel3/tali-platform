"""Related-role implementation of the application Process cascade."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...services.logical_role_application_authority import (
    LogicalRoleApplicationAuthorizationError,
)
from ...services.logical_role_batch_operations import (
    context_fetch_transport,
    context_has_cv,
    filter_contexts_stage,
    logical_role_contexts,
    related_score_targets,
)
from ...services.related_role_rescreen_service import (
    RelatedRoleRescreenUnavailableError,
    rescreen_related_role_candidates,
)
from .batch_runtime_state import clear_cancel_flag as _clear_cancel_flag
from .role_process_scope import (
    _PROCESS_CANCEL_PREFIX,
    _set_process_progress,
    is_process_cancelled,
)

logger = logging.getLogger("taali.applications.process.related")


def _related_role_rescreen_or_409(
    db: Session,
    *,
    role: Role,
    application_ids: list[int],
    reason: str,
):
    try:
        return rescreen_related_role_candidates(
            db,
            role,
            reason=reason,
            application_ids=application_ids,
            require_all_memberships=True,
        )
    except RelatedRoleRescreenUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def run_related_role_process(
    db: Session,
    *,
    role: Role,
    org: Organization,
    progress: dict,
    fetch_cvs: bool,
    refresh_cvs: bool,
    score_mode: str,
    sync_graph: bool,
    refresh_graph: bool,
    stage_filter: str | None,
    application_ids: list[int] | None,
    user_id: int | None,
    fetch_cv,
) -> None:
    """Run supported Process steps against a related role's own membership."""

    role_id = int(role.id)
    try:
        contexts = logical_role_contexts(
            db,
            role=role,
            application_ids=application_ids,
        )
    except LogicalRoleApplicationAuthorizationError:
        progress["status"] = "failed"
        progress["error"] = "logical_role_membership_changed"
        _set_process_progress(role_id, progress)
        return
    if not application_ids:
        contexts = filter_contexts_stage(contexts, stage=stage_filter)

    if fetch_cvs:
        progress["current_step"] = "fetch"
        transports: dict[int, CandidateApplication] = {}
        for context in contexts:
            transport = context_fetch_transport(context)
            if transport is None or str(transport.source or "") != "workable":
                continue
            if not refresh_cvs and context_has_cv(context):
                continue
            transports[int(transport.id)] = transport
        progress["fetch"]["total"] = len(transports)
        _set_process_progress(role_id, progress)
        for index, transport in enumerate(transports.values(), start=1):
            if is_process_cancelled(role_id):
                db.rollback()
                progress["status"] = "cancelled"
                _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                _set_process_progress(role_id, progress)
                return
            try:
                if fetch_cv(
                    transport,
                    transport.candidate,
                    db,
                    org,
                ):
                    progress["fetch"]["fetched"] += 1
                else:
                    progress["fetch"]["unavailable"] += 1
            except Exception:
                logger.exception(
                    "Related-role Process CV fetch failed role_id=%s "
                    "application_id=%s",
                    role_id,
                    transport.id,
                )
                progress["fetch"]["errors"] += 1
            progress["fetch"]["attempted"] = index
            _set_process_progress(role_id, progress)
        try:
            db.commit()
        except Exception:
            db.rollback()
            progress["status"] = "failed"
            progress["error"] = "cv_fetch_commit_failed"
            _set_process_progress(role_id, progress)
            return

    if score_mode in {"new", "all"}:
        progress["current_step"] = "score"
        progress["score"]["mode"] = score_mode
        try:
            # Re-resolve after fetch and immediately before the role-local
            # reset so a removed membership cannot ride a stale worker list.
            contexts = logical_role_contexts(
                db,
                role=role,
                application_ids=[context.application_id for context in contexts],
            )
        except LogicalRoleApplicationAuthorizationError:
            progress["status"] = "failed"
            progress["error"] = "logical_role_membership_changed"
            _set_process_progress(role_id, progress)
            return
        targets = related_score_targets(
            contexts,
            include_scored=score_mode == "all",
        )
        progress["score"]["total"] = len(targets)
        _set_process_progress(role_id, progress)
        try:
            outcome = _related_role_rescreen_or_409(
                db,
                role=role,
                application_ids=[context.application_id for context in targets],
                reason="recruiter:related_role_process_score",
            )
        except HTTPException as exc:
            progress["status"] = "failed"
            progress["error"] = str(exc.detail)
            _set_process_progress(role_id, progress)
            return
        progress["score"]["scored"] = outcome.reset_count
        progress["score"]["filtered"] = outcome.unscorable_count
        _set_process_progress(role_id, progress)

    if sync_graph:
        from ...candidate_graph import client as graph_client
        from ...candidate_graph import sync as graph_sync_module

        progress["current_step"] = "graph_sync"
        candidates = {context.candidate_id: context.candidate for context in contexts}
        progress["graph_sync"]["total"] = len(candidates)
        _set_process_progress(role_id, progress)
        if not graph_client.is_configured():
            progress["graph_sync"]["errors"] = 1
        else:
            for index, candidate in enumerate(candidates.values(), start=1):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    _set_process_progress(role_id, progress)
                    return
                try:
                    graph_sync_module.sync_candidate(
                        candidate,
                        db=db,
                        include_cv_text=True,
                        bill_organization_id=int(org.id),
                        bill_role_id=role_id,
                        bill_user_id=user_id,
                        require_role_admission=True,
                        force_resync=refresh_graph,
                    )
                except Exception:
                    logger.exception(
                        "Related-role Process graph sync failed role_id=%s "
                        "candidate_id=%s",
                        role_id,
                        candidate.id,
                    )
                    progress["graph_sync"]["errors"] += 1
                progress["graph_sync"]["synced"] = index
                _set_process_progress(role_id, progress)

    progress["current_step"] = None
    progress["status"] = "completed"
    _set_process_progress(role_id, progress)
