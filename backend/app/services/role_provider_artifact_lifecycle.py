"""Transactional invalidation and post-commit refresh for role artifacts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models.role import Role
from .role_provider_generation import (
    RoleProviderGeneration,
    capture_role_provider_generation,
)

logger = logging.getLogger("taali.role_provider_artifacts")

_SESSION_HOOK_KEY = "role_provider_artifact_refresh_hook"
_SESSION_PENDING_KEY = "role_provider_artifact_refresh_by_transaction"
ARTIFACT_RETRY_TIMESTAMP_FORMAT = "utc_iso_v1"


def _dispatch_refresh(role_id: int, *, requires_running_agent: bool) -> None:
    """Kick both idempotent workers after their invalidation is committed."""

    try:
        from ..tasks.automation_tasks import generate_role_interview_focus

        generate_role_interview_focus.delay(
            int(role_id),
            requires_running_agent=bool(requires_running_agent),
        )
    except Exception:
        logger.exception(
            "interview-focus refresh kick failed role_id=%s; sweep will retry",
            role_id,
        )
    try:
        from ..tasks.automation_tasks import regenerate_role_tech_questions

        regenerate_role_tech_questions.delay(int(role_id))
    except Exception:
        logger.exception(
            "tech-question refresh kick failed role_id=%s; sweep will retry",
            role_id,
        )


def _merge_pending(
    target: dict[int, bool], source: dict[int, bool]
) -> None:
    for role_id, requires_running_agent in source.items():
        target[int(role_id)] = bool(requires_running_agent)


def _is_same_or_descendant(transaction, ancestor) -> bool:
    current = transaction
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "parent", None)
    return False


def _discard_transaction_lineage(session: Session, transaction) -> None:
    """Discard refreshes still owned by an ended transaction lineage."""

    pending_by_transaction = session.info.get(_SESSION_PENDING_KEY)
    if not pending_by_transaction:
        return
    for owner in tuple(pending_by_transaction):
        if _is_same_or_descendant(owner, transaction):
            pending_by_transaction.pop(owner, None)
    if not pending_by_transaction:
        session.info.pop(_SESSION_PENDING_KEY, None)


def _install_after_commit_dispatch(session: Session) -> None:
    if session.info.get(_SESSION_HOOK_KEY):
        return
    session.info[_SESSION_HOOK_KEY] = True

    @event.listens_for(session, "after_commit")
    def _dispatch_after_commit(committed_session: Session) -> None:
        pending_by_transaction = committed_session.info.get(_SESSION_PENDING_KEY)
        if not pending_by_transaction:
            return

        nested = committed_session.get_nested_transaction()
        if nested is not None:
            nested_pending = pending_by_transaction.pop(nested, None)
            if nested_pending:
                parent = getattr(nested, "parent", None)
                if parent is not None:
                    _merge_pending(
                        pending_by_transaction.setdefault(parent, {}),
                        nested_pending,
                    )
            if not pending_by_transaction:
                committed_session.info.pop(_SESSION_PENDING_KEY, None)
            return

        root = committed_session.get_transaction()
        committed = pending_by_transaction.pop(root, None) if root else None
        committed_session.info.pop(_SESSION_PENDING_KEY, None)
        if not committed:
            return
        for role_id, requires_running_agent in sorted(committed.items()):
            _dispatch_refresh(
                int(role_id),
                requires_running_agent=bool(requires_running_agent),
            )

    @event.listens_for(session, "after_soft_rollback")
    def _discard_after_rollback(
        rolled_back_session: Session, previous_transaction
    ) -> None:
        _discard_transaction_lineage(rolled_back_session, previous_transaction)

    @event.listens_for(session, "after_transaction_end")
    def _discard_after_transaction_end(
        ended_session: Session, ended_transaction
    ) -> None:
        """Cover implicit rollback from Session.close/reset before reuse.

        ``Session.info`` survives both operations, while SQLAlchemy does not
        emit ``after_soft_rollback`` for every implicit transaction teardown.
        Successful commits have already drained or transferred their bucket in
        ``after_commit``; anything still owned by an ended lineage is unsafe to
        release from a later, unrelated transaction.
        """

        _discard_transaction_lineage(ended_session, ended_transaction)


def _schedule_after_commit(
    db: Session,
    *,
    role_id: int,
    requires_running_agent: bool,
) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        raise RuntimeError("role artifact invalidation requires an active transaction")
    pending_by_transaction = db.info.setdefault(_SESSION_PENDING_KEY, {})
    pending = pending_by_transaction.setdefault(transaction, {})
    pending[int(role_id)] = bool(requires_running_agent)
    _install_after_commit_dispatch(db)


def _mark_refresh_pending(role: Role) -> None:
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    now = datetime.now(timezone.utc).isoformat()
    for key in ("interview_focus_provisioning", "tech_questions_provisioning"):
        state = dict(provisioning.get(key) or {})
        state.update(
            {
                "status": "pending",
                "last_error": None,
                "next_attempt_at": None,
                "next_attempt_format": None,
                "updated_at": now,
            }
        )
        if key == "tech_questions_provisioning":
            state["failure_count"] = 0
        provisioning[key] = state
    role.assessment_task_provisioning = provisioning


def invalidate_role_provider_artifacts_if_changed(
    db: Session,
    *,
    role: Role,
    previous: RoleProviderGeneration | None,
    requires_running_agent: bool | None = None,
) -> bool:
    """Invalidate and enqueue only when durable provider inputs changed.

    The caller must already own the Role row lock. Changes are flushed before
    comparison, then the broker kick is retained by the transaction and emitted
    only after the root commit. Multiple changes to one role in a transaction
    collapse to one kick; worker advisory locks and generation checks collapse
    rapid committed edits to one paid generation. Explicit recruiter edits use
    the role's current running state by default. Callers that must remain
    activation-gated (such as inactive requisition republish) can require the
    running-agent fence explicitly.
    """

    db.flush()
    current = capture_role_provider_generation(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    if (
        previous is None
        or current is None
        or previous == current
    ):
        return False

    role.interview_focus = None
    role.interview_focus_generated_at = None
    role.screening_pack_template = None
    role.tech_interview_pack_template = None
    role.tech_questions_signature = None
    if current.job_spec_text:
        _mark_refresh_pending(role)
        _schedule_after_commit(
            db,
            role_id=int(role.id),
            requires_running_agent=(
                bool(role.agentic_mode_enabled)
                if requires_running_agent is None
                else bool(requires_running_agent)
            ),
        )
    return True


__all__ = ["invalidate_role_provider_artifacts_if_changed"]
