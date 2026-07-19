"""Resolve durable candidate-chat work before terminal assessment grading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.role import Role
from ...models.task import Task
from .candidate_chat_audit import append_candidate_chat_no_replay_resolution
from .candidate_chat_runtime import (
    CandidateChatHooks,
    _finalize_checkpointed_turn,
    _prepared_from_claim,
    _query_for_chat_replay,
)
from .chat_idempotency import (
    IN_DOUBT_STATES,
    close_in_doubt_candidate_chat_claims_without_replay,
    list_candidate_chat_claims,
)
from .claude_budget import (
    build_claude_budget_snapshot,
    resolve_effective_budget_limit_usd,
)
from .repository import validate_assessment_token


@dataclass(frozen=True)
class CandidateChatFinalizationHooks:
    build_budget_snapshot: Callable[..., dict[str, Any]]
    resolve_budget_limit: Callable[..., float | None]


def finalize_or_block_candidate_chat_for_submit(
    db: Session,
    *,
    assessment_id: int,
    token: str,
    hooks: CandidateChatHooks | CandidateChatFinalizationHooks | None = None,
    close_in_doubt_without_replay: bool = False,
) -> bool:
    """Finalize exact success, then block or terminally close ambiguous work.

    Candidate-initiated submission uses the default fail-closed behavior.  A
    timeout finalizer may explicitly close ambiguous calls without replay so
    the current workspace can still be graded after the assessment has ended.
    """

    assessment = _query_for_chat_replay(db, int(assessment_id))
    validate_assessment_token(assessment, token)
    claims = list_candidate_chat_claims(assessment.prompt_analytics)
    pending = [
        (claim_key, claim)
        for claim_key, claim in claims.items()
        if str(claim.get("state") or "") == "agent_completed"
    ]
    if len(pending) > 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple AI replies require reconciliation before submission",
        )
    finalized = False
    if pending:
        if hooks is None:
            hooks = CandidateChatFinalizationHooks(
                build_budget_snapshot=build_claude_budget_snapshot,
                resolve_budget_limit=resolve_effective_budget_limit_usd,
            )
        task = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
        role = (
            db.query(Role).filter(Role.id == assessment.role_id).one_or_none()
            if assessment.role_id
            else None
        )
        if task is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Task not found")
        claim_key, claim = pending[0]
        prepared = _prepared_from_claim(
            assessment=assessment,
            task=task,
            role=role,
            prompts=list(assessment.ai_prompts or []),
            claim_key=claim_key,
            claim=claim,
            budget_limit_usd=hooks.resolve_budget_limit(
                is_demo=bool(assessment.is_demo),
                task_budget_limit_usd=task.claude_budget_limit_usd,
            ),
        )
        try:
            _finalize_checkpointed_turn(
                db,
                prepared,
                token,
                fallback_data=None,
                claim=claim,
                hooks=hooks,
            )
            finalized = True
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A completed AI reply must be reconciled before submission",
            ) from exc

    assessment = _query_for_chat_replay(db, int(assessment_id))
    validate_assessment_token(assessment, token)
    remaining_claims = list_candidate_chat_claims(assessment.prompt_analytics)
    unresolved = next(
        (
            claim
            for claim in remaining_claims.values()
            if str(claim.get("state") or "") in IN_DOUBT_STATES
            or str(claim.get("state") or "") == "agent_completed"
        ),
        None,
    )
    if unresolved is not None and close_in_doubt_without_replay:
        if any(
            str(claim.get("state") or "") == "agent_completed"
            for claim in remaining_claims.values()
        ):
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A completed AI reply must be reconciled before submission",
            )
        assessment.prompt_analytics = (
            close_in_doubt_candidate_chat_claims_without_replay(
                assessment.prompt_analytics,
                reason="assessment_timeout_workspace_graded",
            )
        )
        append_candidate_chat_no_replay_resolution(
            assessment,
            remaining_claims,
            reason="assessment_timeout_workspace_graded",
        )
        db.commit()
        unresolved = None
    db.rollback()
    if unresolved is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An AI request requires reconciliation before submission",
        )
    return finalized


__all__ = [
    "CandidateChatFinalizationHooks",
    "finalize_or_block_candidate_chat_for_submit",
]
