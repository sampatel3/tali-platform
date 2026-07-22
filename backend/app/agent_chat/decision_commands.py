"""Role/org-scoped Decision Hub commands for Agent Chat.

Mutations call the canonical route functions in process—never loopback HTTP—
so stale-input guards and serialized ATS writeback stay single-sourced.  This
boundary adds non-disclosing conversation-role checks, narrows legacy
overrides to supported UI alternatives, and returns compact tool-result data.
Expected failures raise :class:`DecisionCommandError`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..candidate_search.role_scope import (
    CandidateRoleScope,
    resolve_candidate_role_scope,
)
from ..models.agent_decision import AgentDecision
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from ..models.user import User
from ..services import decision_staleness
from ..services.decision_role_context import related_decision_staleness
from .decision_teach import teach_decision


# Keep this vocabulary aligned with frontend/src/shared/decisions/
# decisionActions.js.  The underlying legacy endpoint accepts a wider set of
# actions for backwards compatibility; a model-facing command should not.
SUPPORTED_ALTERNATIVES: Mapping[str, tuple[str, ...]] = {
    "send_assessment": ("reject", "skip_assessment_advance"),
    "advance_to_interview": ("send_assessment", "reject"),
    "reject": ("send_assessment", "advance"),
    "skip_assessment_reject": (),
    "resend_assessment_invite": ("reject", "skip_assessment_advance"),
    "escalate_low_confidence": (),
}

# ``approve_decision.run`` has concrete dispatch implementations for these
# five types.  In particular, ``escalate_low_confidence`` is a request for
# recruiter adjudication, not an executable recommendation.
APPROVABLE_DECISION_TYPES = frozenset({
    "advance_to_interview", "reject", "skip_assessment_reject",
    "send_assessment", "resend_assessment_invite",
})


class DecisionCommandError(ValueError):
    """An expected, recruiter-actionable command failure.

    ``code`` is stable for tests/future structured error handling.  ``str``
    includes it because the current Agent Chat engine serializes exceptions by
    stringifying them.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(f"{self.code}: {self.message}")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _ensure_context(role: Role, user: User) -> int:
    """Return the organization id after checking the chat's security scope."""
    role_org = getattr(role, "organization_id", None)
    user_org = getattr(user, "organization_id", None)
    if role_org is None or user_org is None or int(role_org) != int(user_org):
        # Do not reveal whether a role/decision exists in another org.
        raise DecisionCommandError(
            "scope_mismatch",
            "This role is not available in the recruiter's organization.",
        )
    return int(role_org)


def _scoped_decision(
    db: Session,
    role: Role,
    user: User,
    decision_id: int,
) -> AgentDecision:
    org_id = _ensure_context(role, user)
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == org_id,
            AgentDecision.role_id == int(role.id),
        )
        .one_or_none()
    )
    if decision is None:
        # Intentionally identical for a missing id, another role, or another
        # organization so a guessed decision id cannot cross the chat scope.
        raise DecisionCommandError(
            "decision_not_found",
            f"Decision {int(decision_id)} was not found in this role.",
        )
    return decision


def _candidate_scope(
    db: Session,
    role: Role,
    *,
    organization_id: int,
) -> CandidateRoleScope:
    """Resolve the same membership authority used by every candidate tool."""

    try:
        return resolve_candidate_role_scope(
            db,
            organization_id=int(organization_id),
            role_id=int(role.id),
        )
    except ValueError as exc:
        raise DecisionCommandError(
            "scope_mismatch",
            "This role is not available in the recruiter's organization.",
        ) from exc


def _scoped_decision_subject(
    db: Session,
    *,
    scope: CandidateRoleScope,
    decision: AgentDecision,
) -> tuple[CandidateApplication, Candidate | None, SisterRoleEvaluation | None]:
    """Load the decision subject only through the logical role's live roster."""

    query = (
        db.query(CandidateApplication, Candidate)
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.id == int(decision.application_id),
            CandidateApplication.organization_id == int(scope.organization_id),
        )
    )
    subject = scope.scope_visible_roster(query).one_or_none()
    if subject is None:
        raise DecisionCommandError(
            "decision_subject_not_found",
            f"The candidate application for decision {int(decision.id)} is unavailable.",
        )
    application, candidate = subject
    evaluation = (
        scope.evaluation_map(db, application_ids=[int(application.id)]).get(
            int(application.id)
        )
        if scope.is_related
        else None
    )
    return application, candidate, evaluation


def _require_pending(decision: AgentDecision, *, operation: str) -> None:
    if str(decision.status) != "pending":
        raise DecisionCommandError(
            "decision_not_pending",
            (
                f"Decision {int(decision.id)} is {decision.status!r}; "
                f"only pending decisions can be {operation}."
            ),
            details={"decision_id": int(decision.id), "status": str(decision.status)},
        )


def _translate_http_error(exc: HTTPException) -> DecisionCommandError:
    detail = exc.detail
    if isinstance(detail, Mapping):
        code = str(detail.get("code") or f"decision_http_{exc.status_code}")
        message = str(detail.get("message") or detail)
        details = dict(detail)
    else:
        code = f"decision_http_{exc.status_code}"
        message = str(detail or "Decision action failed.")
        details = {"status_code": int(exc.status_code)}
    return DecisionCommandError(code, message, details=details)


def _model_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    raise TypeError(f"unsupported decision result: {type(value).__name__}")


def _compact_result(value: Any) -> dict[str, Any]:
    """Keep mutation results useful without replaying the full Hub payload."""
    raw = _model_dict(value)
    keys = (
        "id", "decision_id", "accepted", "role_id", "application_id", "candidate_name",
        "decision_type", "recommendation", "status", "override_action",
        "resolution_note", "snoozed_until", "superseded", "queued", "task_id", "detail",
    )
    out = {key: raw[key] for key in keys if key in raw}
    for key, value_ in tuple(out.items()):
        if isinstance(value_, datetime):
            out[key] = value_.isoformat()
    return out


def _role_family_snapshot(
    db: Session,
    role: Role,
    *,
    organization_id: int,
) -> dict[str, Any]:
    """Return the complete named family inside the authorized organization."""
    # Keep the heavier role response helpers out of Agent Chat module startup.
    # The scoped loader prevents a malformed cross-tenant owner/sibling link
    # from entering the confirmation payload.
    from ..domains.assessments_runtime.role_support import (
        role_family_response,
        roles_with_families,
    )

    loaded_role = roles_with_families(
        db,
        [int(role.id)],
        organization_id=int(organization_id),
    ).get(int(role.id))
    if loaded_role is None:  # Defensive: _ensure_context already authorized it.
        return {
            "owner": {"id": int(role.id), "name": str(role.name)},
            "related": [],
        }
    return _model_dict(role_family_response(loaded_role))


def _staleness(
    db: Session,
    decision: AgentDecision,
    *,
    application: CandidateApplication,
    role: Role,
    cache: decision_staleness.StalenessCache,
    related_evaluation: SisterRoleEvaluation | None = None,
) -> dict[str, Any]:
    try:
        report = (
            related_decision_staleness(
                db,
                decision,
                related_evaluation,
                application=application,
                role=role,
                cache=cache,
            )
            if related_evaluation is not None
            else decision_staleness.evaluate(
                db,
                decision,
                application=application,
                role=role,
                cache=cache,
            )
        )
    except Exception:  # pragma: no cover - the Hub also fails open on read
        return {"is_stale": False, "staleness_reasons": [], "staleness_summary": None}
    return {
        "is_stale": bool(report.is_stale),
        "staleness_reasons": list(report.reasons),
        "staleness_summary": report.summary,
    }


def _pending_decision_row(
    db: Session,
    role: Role,
    decision: AgentDecision,
    application: CandidateApplication,
    candidate: Candidate | None,
    *,
    cache: decision_staleness.StalenessCache,
    role_family: dict[str, Any],
    related_evaluation: SisterRoleEvaluation | None = None,
    approval_requires_workable_stage: bool = False,
) -> dict[str, Any]:
    """The single live projection shared by list and confirmation preview."""
    decision_type = str(decision.decision_type)
    return {
        "decision_id": int(decision.id),
        "application_id": int(decision.application_id),
        "candidate_name": getattr(candidate, "full_name", None) or "Unnamed candidate",
        "decision_type": decision_type,
        "recommendation": str(decision.recommendation),
        "role_family": role_family,
        "reasoning": str(decision.reasoning or ""),
        "confidence": float(decision.confidence) if decision.confidence is not None else None,
        "created_at": _iso(decision.created_at),
        "snoozed_until": _iso(decision.snoozed_until),
        "can_approve": decision_type in APPROVABLE_DECISION_TYPES,
        "approval_requires_workable_stage": bool(
            decision_type == "advance_to_interview"
            and approval_requires_workable_stage
        ),
        "supported_alternatives": list(SUPPORTED_ALTERNATIVES.get(decision_type, ())),
        **_staleness(
            db,
            decision,
            application=application,
            role=role,
            cache=cache,
            related_evaluation=related_evaluation,
        ),
    }


def get_pending_decision(
    db: Session,
    role: Role,
    user: User,
    decision_id: int,
) -> dict[str, Any]:
    """Return the list's exact row; explicit snoozes remain previewable."""
    org_id = _ensure_context(role, user)
    decision = _scoped_decision(db, role, user, decision_id)
    _require_pending(decision, operation="previewed")
    scope = _candidate_scope(db, role, organization_id=org_id)
    application, candidate, evaluation = _scoped_decision_subject(
        db,
        scope=scope,
        decision=decision,
    )
    return _pending_decision_row(
        db,
        role,
        decision,
        application,
        candidate,
        cache=decision_staleness.StalenessCache(),
        role_family=_role_family_snapshot(
            db,
            role,
            organization_id=org_id,
        ),
        related_evaluation=evaluation,
        approval_requires_workable_stage=bool(
            getattr(scope.application_role, "workable_job_id", None)
        ),
    )


def list_pending_decisions(
    db: Session,
    role: Role,
    user: User,
    *,
    include_snoozed: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """List pending rows, hiding active snoozes unless explicitly requested."""
    org_id = _ensure_context(role, user)
    scope = _candidate_scope(db, role, organization_id=org_id)
    cap = max(1, min(int(limit), 50))
    now = datetime.now(timezone.utc)
    query = (
        db.query(AgentDecision, CandidateApplication, Candidate)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            AgentDecision.organization_id == org_id,
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
    )
    # A pending decision is actionable only while its logical role membership
    # is live. Related-role membership is the evaluation row; the source/ATS
    # application may belong to another role or be soft-deleted evidence.
    query = scope.scope_visible_roster(query)
    if not include_snoozed:
        query = query.filter(
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            )
        )

    total = int(query.count())
    rows = (
        query.order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .limit(cap)
        .all()
    )
    cache = decision_staleness.StalenessCache()
    from ..components.scoring.freshness import latest_score_attempts

    application_ids = [int(application.id) for _, application, _ in rows]
    latest_attempts = latest_score_attempts(db, application_ids)
    cache.latest_score_attempt.update(
        {application_id: latest_attempts.get(application_id) for application_id in application_ids}
    )
    role_family = _role_family_snapshot(db, role, organization_id=org_id)
    evaluations = scope.evaluation_map(db, application_ids=application_ids)
    decisions = [
        _pending_decision_row(
            db,
            role,
            decision,
            application,
            candidate,
            cache=cache,
            role_family=role_family,
            related_evaluation=evaluations.get(int(application.id)),
            approval_requires_workable_stage=bool(
                getattr(scope.application_role, "workable_job_id", None)
            ),
        )
        for decision, application, candidate in rows
    ]

    return {
        "ok": True,
        "type": "pending_decisions",
        "role_id": int(role.id),
        "role_name": str(role.name),
        "include_snoozed": bool(include_snoozed),
        "count": total,
        "returned": len(decisions),
        "decisions": decisions,
    }


def approve_decision(
    db: Session,
    role: Role,
    user: User,
    *,
    decision_id: int,
    note: str | None = None,
    workable_target_stage: str | None = None,
) -> dict[str, Any]:
    """Approve one recommendation through the canonical Hub workflow."""
    decision = _scoped_decision(db, role, user, decision_id)
    _require_pending(decision, operation="approved")
    scope = _candidate_scope(
        db,
        role,
        organization_id=int(decision.organization_id),
    )
    _scoped_decision_subject(db, scope=scope, decision=decision)
    if str(decision.decision_type) not in APPROVABLE_DECISION_TYPES:
        raise DecisionCommandError(
            "decision_not_approvable",
            (
                f"Decision type {decision.decision_type!r} has no executable "
                "approval action; ask the recruiter to adjudicate it instead."
            ),
        )
    if (
        str(decision.decision_type) == "advance_to_interview"
        and getattr(scope.application_role, "workable_job_id", None)
        and not str(workable_target_stage or "").strip()
    ):
        raise DecisionCommandError(
            "workable_stage_required",
            "Pick the destination Workable stage before approving this advance.",
        )

    # Lazy import avoids pulling the full route surface into read-only Agent
    # Chat startup and prevents a future route→chat import from cycling.
    from ..domains.agentic import routes as agentic_routes

    try:
        result = agentic_routes.approve(
            decision_id=int(decision.id),
            body=agentic_routes.ApproveBody(
                note=(str(note).strip() if note is not None else None),
                workable_target_stage=(
                    str(workable_target_stage).strip()
                    if workable_target_stage is not None
                    else None
                ),
            ),
            # Agent Chat never bypasses stale-input protection.  The recruiter
            # can request re-evaluation and then approve the fresh card.
            force=False,
            db=db,
            current_user=user,
        )
    except HTTPException as exc:
        raise _translate_http_error(exc) from exc
    return {"ok": True, "operation": "approve_decision", **_compact_result(result)}


def override_decision(
    db: Session,
    role: Role,
    user: User,
    *,
    decision_id: int,
    alternative: str,
    note: str,
    workable_target_stage: str | None = None,
) -> dict[str, Any]:
    """Reject the recommendation and execute one supported alternative."""
    decision = _scoped_decision(db, role, user, decision_id)
    _require_pending(decision, operation="overridden")
    scope = _candidate_scope(
        db,
        role,
        organization_id=int(decision.organization_id),
    )
    _scoped_decision_subject(db, scope=scope, decision=decision)
    action = str(alternative or "").strip()
    allowed = SUPPORTED_ALTERNATIVES.get(str(decision.decision_type), ())
    if action not in allowed:
        raise DecisionCommandError(
            "unsupported_decision_alternative",
            (
                f"{action!r} is not an allowed alternative for "
                f"{decision.decision_type!r}. Allowed: {list(allowed)}."
            ),
            details={"allowed": list(allowed)},
        )
    rationale = str(note or "").strip()
    if not rationale:
        raise DecisionCommandError(
            "override_reason_required",
            "Give a brief reason for the override so the agent can learn from it.",
        )
    if (
        action == "advance"
        and getattr(scope.application_role, "workable_job_id", None)
        and not str(workable_target_stage or "").strip()
    ):
        raise DecisionCommandError(
            "workable_stage_required",
            "Pick the destination Workable stage before overriding to advance.",
        )

    from ..domains.agentic import routes as agentic_routes

    try:
        result = agentic_routes.override(
            decision_id=int(decision.id),
            body=agentic_routes.OverrideBody(
                override_action=action,
                note=rationale,
                workable_target_stage=(
                    str(workable_target_stage).strip()
                    if workable_target_stage is not None
                    else None
                ),
            ),
            db=db,
            current_user=user,
        )
    except HTTPException as exc:
        raise _translate_http_error(exc) from exc
    return {"ok": True, "operation": "override_decision", **_compact_result(result)}


def snooze_decision(
    db: Session,
    role: Role,
    user: User,
    *,
    decision_id: int,
) -> dict[str, Any]:
    """Hide a pending decision for the Hub's canonical one-hour window."""
    decision = _scoped_decision(db, role, user, decision_id)
    _require_pending(decision, operation="snoozed")
    scope = _candidate_scope(
        db,
        role,
        organization_id=int(decision.organization_id),
    )
    _scoped_decision_subject(db, scope=scope, decision=decision)

    from ..domains.agentic import hub_feedback_routes

    try:
        result = hub_feedback_routes.snooze_decision(
            decision_id=int(decision.id),
            db=db,
            current_user=user,
        )
    except HTTPException as exc:
        raise _translate_http_error(exc) from exc
    return {"ok": True, "operation": "snooze_decision", **_compact_result(result)}


def re_evaluate_decision(
    db: Session,
    role: Role,
    user: User,
    *,
    decision_id: int,
) -> dict[str, Any]:
    """Refresh a pending card using the canonical score-or-agent rerun path."""
    decision = _scoped_decision(db, role, user, decision_id)
    _require_pending(decision, operation="re-evaluated")
    scope = _candidate_scope(
        db,
        role,
        organization_id=int(decision.organization_id),
    )
    _scoped_decision_subject(db, scope=scope, decision=decision)

    from ..domains.agentic import routes as agentic_routes

    try:
        result = agentic_routes.re_evaluate(
            decision_id=int(decision.id),
            db=db,
            current_user=user,
        )
    except HTTPException as exc:
        raise _translate_http_error(exc) from exc
    return {"ok": True, "operation": "re_evaluate_decision", **_compact_result(result)}


__all__ = [
    "APPROVABLE_DECISION_TYPES",
    "DecisionCommandError",
    "SUPPORTED_ALTERNATIVES",
    "approve_decision",
    "get_pending_decision",
    "list_pending_decisions",
    "override_decision",
    "re_evaluate_decision",
    "snooze_decision",
    "teach_decision",
]
