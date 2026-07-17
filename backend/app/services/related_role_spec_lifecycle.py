"""Shared, cost-aware lifecycle for related-role job-spec changes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_STALE,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from .related_role_roster import active_source_applications_for_related_role
from .sister_role_service import (
    application_cv_text,
    archive_sister_evaluation_result,
    ensure_sister_evaluations,
    source_application_is_globally_closed,
    text_fingerprint,
)

logger = logging.getLogger("taali.related_role_specs")

# Same holistic-scoring planning estimate used by the ordinary role rescore
# preview. Keep one named source so creation, spec edits, HTTP, and Agent Chat
# never quote different prices for the same work.
RELATED_ROLE_SCORE_COST_USD = 0.083
_SESSION_PAYLOADS_KEY = "related_role_spec_rescore_role_ids"
_SESSION_HOOK_KEY = "related_role_spec_rescore_hook_installed"


def _require_related_role(role: Role) -> None:
    if (
        str(role.role_kind or "") != ROLE_KIND_SISTER
        or not role.ats_owner_role_id
    ):
        raise ValueError("Role is not a coupled related role")


def _estimate(count: int) -> dict[str, int | float]:
    bounded = max(0, int(count))
    return {
        "count": bounded,
        "est_cost_usd": round(bounded * RELATED_ROLE_SCORE_COST_USD, 2),
    }


def estimate_related_role_spec_rescore(
    db: Session, role: Role
) -> dict[str, int | float]:
    """Return the currently unapproved stale scope without mutating it."""

    _require_related_role(role)
    count = (
        db.query(SisterRoleEvaluation.id)
        .filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.status == SISTER_EVAL_STALE,
        )
        .count()
    )
    return _estimate(count)


def mark_related_role_spec_evaluations_stale(
    db: Session, role: Role
) -> dict[str, int | float]:
    """Invalidate old-spec scores without authorizing new paid work.

    Saving a specification is not permission to spend against the whole shared
    ATS roster. Scoreable rows move to an explicit ``stale`` state, which the
    recovery worker deliberately ignores. The existing manual Re-score action
    (or a separately confirmed Agent Chat command) resets those rows to
    ``pending`` and owns dispatch. Historical results remain in ``history``.
    """

    _require_related_role(role)
    applications = active_source_applications_for_related_role(db, role)
    existing = {
        int(item.source_application_id): item
        for item in db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.organization_id == int(role.organization_id),
        )
    }
    current_ids = {int(application.id) for application in applications}
    for application_id, evaluation in existing.items():
        if application_id not in current_ids:
            evaluation.status = SISTER_EVAL_EXCLUDED
            evaluation.error_message = "Source application left the owner roster"
            evaluation.last_error_code = "source_application_outside_owner_roster"

    spec_hash = text_fingerprint(role.job_spec_text)
    now = datetime.now(timezone.utc)
    for application in applications:
        application_id = int(application.id)
        cv_text = application_cv_text(application)
        if source_application_is_globally_closed(application):
            next_status = SISTER_EVAL_EXCLUDED
        elif not cv_text:
            next_status = SISTER_EVAL_UNSCORABLE
        else:
            next_status = SISTER_EVAL_STALE
        evaluation = existing.get(application_id)
        if evaluation is None:
            evaluation = SisterRoleEvaluation(
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                source_application_id=application_id,
                status=next_status,
                spec_fingerprint=spec_hash,
                cv_fingerprint=text_fingerprint(cv_text) if cv_text else None,
                queued_at=now,
                error_message=(
                    "No CV text available"
                    if next_status == SISTER_EVAL_UNSCORABLE
                    else (
                        "Shared ATS application is closed"
                        if next_status == SISTER_EVAL_EXCLUDED
                        else "Job specification changed; re-score approval required"
                    )
                ),
                last_error_code=(
                    "spec_changed_awaiting_rescore_approval"
                    if next_status == SISTER_EVAL_STALE
                    else None
                ),
            )
            db.add(evaluation)
            existing[application_id] = evaluation
        elif evaluation.spec_fingerprint != spec_hash:
            archive_sister_evaluation_result(evaluation)
            evaluation.status = next_status
            evaluation.spec_fingerprint = spec_hash
            evaluation.cv_fingerprint = (
                text_fingerprint(cv_text) if cv_text else None
            )
            # Keep the previous result visible while clearly labelling it
            # stale. The archived snapshot preserves its old fingerprint, and
            # the status prevents it being mistaken for a current-spec score.
            evaluation.error_message = (
                "No CV text available"
                if next_status == SISTER_EVAL_UNSCORABLE
                else (
                    "Shared ATS application is closed"
                    if next_status == SISTER_EVAL_EXCLUDED
                    else "Job specification changed; re-score approval required"
                )
            )
            evaluation.attempts = 0
            evaluation.next_attempt_at = None
            evaluation.dispatch_attempted_at = None
            evaluation.last_error_code = (
                "spec_changed_awaiting_rescore_approval"
                if next_status == SISTER_EVAL_STALE
                else None
            )
            evaluation.queued_at = now
            evaluation.started_at = None

    db.flush()
    stale_count = sum(
        1
        for evaluation in existing.values()
        if evaluation.status == SISTER_EVAL_STALE
        and int(evaluation.source_application_id) in current_ids
    )
    return _estimate(stale_count)


def reset_related_role_spec_evaluations(
    db: Session, role: Role
) -> dict[str, int | float]:
    """Archive scores, rebuild the roster, and report the fresh scoring scope."""
    counts = ensure_sister_evaluations(db, role, reset_existing=True)
    return _estimate(int(counts.get("pending") or 0))


def dispatch_related_role_spec_scoring(role: Role | int) -> None:
    """Best-effort scoring kick; Beat recovers the committed pending rows."""
    from ..tasks.sister_role_tasks import score_sister_role

    role_id = int(role.id) if isinstance(role, Role) else int(role)
    try:
        score_sister_role.apply_async(args=[role_id], queue="scoring")
    except Exception as exc:  # pragma: no cover - durable pending rows recover
        logger.error(
            "Related-role spec scoring kick unavailable role_id=%s "
            "error_code=queue_unavailable error_type=%s",
            role_id,
            type(exc).__name__,
        )


def _install_after_commit_dispatch(session: Session) -> None:
    if session.info.get(_SESSION_HOOK_KEY):
        return
    session.info[_SESSION_HOOK_KEY] = True

    @event.listens_for(session, "after_commit")
    def _dispatch_after_outer_commit(committed_session: Session) -> None:
        # Releasing a SAVEPOINT also emits after_commit. Paid work can start
        # only once the root transaction and its command receipt are durable.
        if committed_session.in_nested_transaction():
            return
        payloads = committed_session.info.pop(_SESSION_PAYLOADS_KEY, {})
        for role_id in sorted(payloads):
            dispatch_related_role_spec_scoring(int(role_id))

    @event.listens_for(session, "after_soft_rollback")
    def _discard_rolled_back_payloads(
        rolled_back_session: Session, previous_transaction
    ) -> None:
        payloads = rolled_back_session.info.get(_SESSION_PAYLOADS_KEY, {})
        if not payloads:
            return
        if getattr(previous_transaction, "parent", None) is None:
            rolled_back_session.info.pop(_SESSION_PAYLOADS_KEY, None)
            return
        for role_id, payload in list(payloads.items()):
            if payload.get("transaction") is previous_transaction:
                payloads.pop(role_id, None)


def queue_related_role_spec_rescore(
    db: Session, role: Role
) -> dict[str, int | float]:
    """Authorize related-role scoring and kick it only after commit.

    Resetting rows to ``pending`` is the durable authorization receipt. Beat
    recovers a lost broker kick, while the session hook avoids publishing work
    that a later rollback would make invisible.
    """

    _require_related_role(role)
    now = datetime.now(timezone.utc)
    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.status == SISTER_EVAL_STALE,
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .with_for_update(of=SisterRoleEvaluation)
        .all()
    )
    for evaluation in evaluations:
        evaluation.status = SISTER_EVAL_PENDING
        evaluation.error_message = None
        evaluation.attempts = 0
        evaluation.next_attempt_at = None
        evaluation.dispatch_attempted_at = None
        evaluation.last_error_code = None
        evaluation.queued_at = now
        evaluation.started_at = None
        evaluation.scored_at = None
    db.flush()
    estimate = _estimate(len(evaluations))
    if not evaluations:
        return estimate
    payloads = db.info.setdefault(_SESSION_PAYLOADS_KEY, {})
    payloads[int(role.id)] = {
        "transaction": db.get_nested_transaction() or db.get_transaction(),
    }
    _install_after_commit_dispatch(db)
    return estimate
