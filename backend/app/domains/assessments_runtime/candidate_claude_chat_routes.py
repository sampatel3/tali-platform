"""Candidate-facing Agent SDK chat over the assessment E2B workspace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ...components.assessments.candidate_chat_prompting import (
    build_agentic_system_prompt,
    flatten_prompts_to_messages,
)
from ...components.assessments.candidate_chat_runtime import (
    CandidateChatHooks,
    run_candidate_chat,
)
from ...components.assessments.claude_budget import (
    build_claude_budget_snapshot,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.claude_tool_executor import AssessmentToolExecutor
from ...components.assessments.interrogation import classify_response
from ...components.assessments.service import (
    finalize_timed_out_assessment,
    timeout_finalization_http_detail,
)
from ...components.assessments.terminal_runtime import resolve_backend_anthropic_key
from ...components.integrations.claude_agent.service import AgentSDKChatService
from ...components.integrations.e2b.service import E2BService
from ...models.assessment import Assessment
from ...platform.config import settings
from ...platform.database import get_db
from ...schemas.assessment import ClaudeChatRequest
from ...services.role_budget_gate import can_spend_on_role
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root
from ...services.usage_metering_service import reserve
from .workspace_serialization import (
    async_assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)

router = APIRouter()

# Compatibility aliases for tests and internal documentation that referenced
# these helpers at their original route-module location.
_build_agentic_system_prompt = build_agentic_system_prompt
_flatten_prompts_to_messages = flatten_prompts_to_messages


def _candidate_chat_hooks() -> CandidateChatHooks:
    """Bind patchable provider seams without leaking ORM rows to them."""

    return CandidateChatHooks(
        e2b_service_cls=E2BService,
        tool_executor_cls=AssessmentToolExecutor,
        agent_service_cls=AgentSDKChatService,
        resolve_api_key=resolve_backend_anthropic_key,
        can_spend_on_role=can_spend_on_role,
        reserve=reserve,
        build_budget_snapshot=build_claude_budget_snapshot,
        resolve_budget_limit=resolve_effective_budget_limit_usd,
        classify_response=classify_response,
        workspace_repo_root=canonical_workspace_repo_root,
        e2b_api_key=settings.E2B_API_KEY,
    )


@router.post("/{assessment_id}/claude/chat")
async def chat_with_claude_agentic(
    assessment_id: int,
    data: ClaudeChatRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Run one durable, serialized chat turn with no provider-time DB txn."""

    try:
        prepare_assessment_workspace_mutex(db)
        async with async_assessment_workspace_mutex(
            db,
            assessment_id=int(assessment_id),
        ):
            return await run_candidate_chat(
                assessment_id=int(assessment_id),
                data=data,
                token=x_assessment_token,
                db=db,
                hooks=_candidate_chat_hooks(),
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if (
            exc.status_code != status.HTTP_409_CONFLICT
            or detail.get("code") != "ASSESSMENT_TIME_EXPIRED"
        ):
            raise
        # The chat workspace lock is released before the timeout submit path
        # acquires the same serialization scope inside submit_assessment.
        db.rollback()
        assessment = (
            db.query(Assessment)
            .filter(Assessment.id == int(assessment_id))
            .one_or_none()
        )
        result = (
            finalize_timed_out_assessment(assessment, db)
            if assessment is not None
            else {
                "status": "skipped",
                "reason": "not_found",
                "assessment_id": int(assessment_id),
            }
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=timeout_finalization_http_detail(result),
        ) from exc


__all__ = ["chat_with_claude_agentic", "router"]
