"""Agentic Claude chat — the candidate-facing endpoint.

Drives the ``claude-agent-sdk`` (Anthropic's official agent loop, the same
one Claude Code uses) against the candidate's E2B sandbox via leaf A's
``Read``/``Write``/``Edit``/``Bash`` MCP tools (wired through
``AssessmentToolExecutor``). Claude fetches whatever it needs at runtime
rather than us pre-stuffing repo excerpts into the system prompt.

The whole tool loop is stored as one user-visible ``Assessment.ai_prompts``
turn so existing scoring keeps working without changes.

Coexists with the legacy ``/claude`` endpoint until shadow-score regression
confirms the agentic path scores cleanly.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ...components.assessments.claude_budget import (
    build_claude_budget_snapshot,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.claude_tool_executor import AssessmentToolExecutor
from ...components.assessments.integrity import (
    OFF_TASK,
    REFUSAL_MESSAGE,
    VOID_MESSAGE,
    WARN_MESSAGE,
    classify_turn,
    count_misuse,
    decide_action,
    strip_refusal_marker,
)
from ...components.assessments.interrogation import (
    all_resolved,
    build_interrogation_directive,
    classify_response,
    derive_interrogation_state,
    merge_state,
)
from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.service import enforce_active_or_timeout, enforce_not_paused
from ...components.assessments.task_snapshot import task_view_for_assessment
from ...components.assessments.terminal_runtime import resolve_backend_anthropic_key
from ...components.integrations.claude_agent.service import AgentSDKChatService
from ...components.integrations.e2b.service import E2BService
from ...models.role import Role
from ...models.task import Task
from ...platform.config import settings
from ...platform.database import get_db
from ...schemas.assessment import ClaudeChatRequest
from ...services.pricing_service import Feature
from ...services.role_budget_gate import can_spend_on_role
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root
from ...services.usage_metering_service import (
    InsufficientCreditsError,
    reserve,
)
from .candidate_auth import candidate_runtime_operation, validate_runtime_candidate_session
from .candidate_chat_contract import (
    build_agentic_system_prompt,
    changed_path_revisions,
    find_idempotent_chat_record,
    flatten_prompts_to_messages,
    persist_failed_chat_attempt,
    replayed_chat_response,
)
from .candidate_workspace import (
    workspace_file_revisions,
)

logger = logging.getLogger("taali.candidate_claude_chat")
router = APIRouter()

_MAX_HISTORY_MESSAGES = 20
_MAX_CONTEXT_CHARS = 12000
_chat_runtime_operation = candidate_runtime_operation("claude_chat")


def _reserve_paid_assessment_call(db: Session, *, organization_id: int) -> None:
    """Fail closed before each candidate-triggered paid model call."""
    try:
        reserve(
            db,
            organization_id=int(organization_id),
            feature=Feature.ASSESSMENT,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": (
                    "This assessment's AI credit balance has been reached. "
                    "You can keep working and submit when you're ready."
                )
            },
        ) from exc


@router.post("/{assessment_id}/claude/chat")
async def chat_with_claude_agentic(
    assessment_id: int,
    data: ClaudeChatRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _operation_id: str = Depends(_chat_runtime_operation),
):
    """Agentic Claude chat — drives ``claude-agent-sdk`` against the
    candidate's E2B sandbox. The whole tool loop appears as ONE turn in
    ``ai_prompts`` so scoring sees a clean per-user-message record.
    """
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    # Admit a paid/tool-capable turn only while the server-side timer is live.
    # This closes the gap where chat previously continued after expiry (or
    # during an outage pause) even though save/run/submit were blocked.
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)
    if data.repo_files:
        raise HTTPException(status_code=400, detail="Bulk repository replacement is disabled")

    live_task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not live_task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        task = task_view_for_assessment(assessment, live_task)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="This assessment's task definition could not be verified. Please contact the hiring team.",
        ) from exc
    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    prompts = list(getattr(assessment, "ai_prompts", None) or [])
    new_message = data.message.strip()
    if not new_message:
        raise HTTPException(status_code=400, detail="Message is required")
    request_id = str(data.request_id or "").strip()
    previous = find_idempotent_chat_record(
        prompts,
        request_id=request_id,
        message=new_message,
    )
    if previous is not None:
        return replayed_chat_response(
            previous,
            request_id=request_id,
            claude_budget=build_claude_budget_snapshot(
                budget_limit_usd=effective_budget_limit,
                prompts=prompts,
            ),
            assessment_voided=bool(getattr(assessment, "is_voided", False)),
        )
    api_key = resolve_backend_anthropic_key()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "Claude isn't available right now. Please contact your recruiter."},
        )

    # Pre-spend role-budget gate — same hard cap the legacy ``/claude`` uses.
    role = (
        db.query(Role).filter(Role.id == assessment.role_id).first()
        if getattr(assessment, "role_id", None)
        else None
    )
    if not can_spend_on_role(db, role=role):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"message": "Your Claude budget for this assessment has been reached. You can keep working and submit when you're ready."},
        )

    if not assessment.e2b_session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "The assessment workspace is not active. Please refresh and start again."},
        )
    if not request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CLAUDE_REQUEST_ID_REQUIRED",
                "message": "A request id is required before Claude can use workspace tools.",
            },
        )
    e2b = E2BService(settings.E2B_API_KEY)
    try:
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
    except Exception as exc:  # pragma: no cover — integration tests stub this
        logger.exception("Failed to connect to E2B sandbox assessment_id=%s", assessment_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "Workspace is temporarily unavailable. Please retry in a moment."},
        ) from exc

    repo_root = canonical_workspace_repo_root(task)
    executor = AssessmentToolExecutor(e2b_service=e2b, sandbox=sandbox, repo_root=repo_root)
    before_revisions = workspace_file_revisions(sandbox, repo_root)

    # Schema-driven interrogation: pull decision_points from the task's
    # extra_data (canonical source of truth), derive the latest per-dp
    # status from the transcript, then classify the candidate's new
    # message and merge with carry-forward semantics. The merged state
    # becomes (a) input to the system prompt's interrogation directive
    # for THIS turn and (b) persisted onto the new ai_prompts record
    # so the grader at submit time can replay deterministically.
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    raw_dps = extra.get("decision_points") if isinstance(extra, dict) else None
    decision_points = (
        [dp for dp in raw_dps if isinstance(dp, dict)]
        if isinstance(raw_dps, list)
        else []
    )
    prior_state = derive_interrogation_state(decision_points, prompts)

    trace_seed = request_id or uuid.uuid4().hex
    trace_root = f"assessment:{int(assessment.id)}:chat:{trace_seed}"
    role_id = int(role.id) if role is not None else None

    messages = flatten_prompts_to_messages(prompts, _MAX_HISTORY_MESSAGES)
    # Embed the live editor selection inline if the candidate provided one —
    # this is cheap and saves Claude an unnecessary ``read_file`` round-trip
    # when the question is clearly about the currently-open file.
    user_turn_content = new_message
    if data.code_context:
        path_label = (data.selected_file_path or "current_file").strip()
        user_turn_content = (
            f"{new_message}\n\n"
            f'<editor_context path="{path_label}">\n'
            f"{data.code_context[:_MAX_CONTEXT_CHARS]}\n"
            f"</editor_context>"
        )
    messages.append({"role": "user", "content": user_turn_content})

    # Run the classifier BEFORE the main chat call so the system prompt
    # sees the freshly-derived state for the candidate's current turn.
    # Skip when there are no decisions or all are already resolved —
    # avoids a Haiku call once the assessment is in pair-programmer mode.
    persist_state: dict[str, dict[str, str]] = {}
    merged_state = prior_state
    if decision_points and not all_resolved(prior_state):
        _reserve_paid_assessment_call(
            db,
            organization_id=int(assessment.organization_id),
        )
        outcome = classify_response(
            decision_points=decision_points,
            candidate_message=new_message,
            prior_state=prior_state,
            api_key=api_key,
            organization_id=int(assessment.organization_id),
            assessment_id=int(assessment.id),
            role_id=role_id,
            trace_id=f"{trace_root}:classifier",
        )
        merged_state, persist_state = merge_state(prior_state, outcome.by_dp)
        if outcome.error:
            logger.info(
                "interrogation classifier soft-failed assessment=%s err=%s",
                assessment.id, outcome.error,
            )
    elif decision_points:
        # All resolved — still persist the current state so a replay
        # of the transcript sees the carry-forward without a gap.
        persist_state = {
            dp_id: {"status": status, "raw_status": status, "rationale": "carry_forward"}
            for dp_id, status in prior_state.items()
        }

    interrogation_directive = build_interrogation_directive(decision_points, merged_state)
    system_prompt = build_agentic_system_prompt(task, interrogation_directive=interrogation_directive)

    current_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=prompts,
    )
    budget_remaining_usd = None
    if isinstance(current_budget, dict):
        remaining = current_budget.get("remaining_usd")
        if isinstance(remaining, (int, float)):
            budget_remaining_usd = float(remaining)

    _reserve_paid_assessment_call(
        db,
        organization_id=int(assessment.organization_id),
    )
    service = AgentSDKChatService(
        api_key=api_key,
        organization_id=int(assessment.organization_id),
        assessment_id=int(assessment.id),
        executor=executor,
        role_id=role_id,
        trace_id=f"{trace_root}:agent",
        # Optional per-task override; None retains the service default.
        model=(str(extra.get("agent_model")).strip() or None) if extra.get("agent_model") else None,
    )
    # An unconfigured limit must not false-trip the SDK pre-spend gate.
    effective_remaining = (
        float(budget_remaining_usd)
        if budget_remaining_usd is not None
        else 1.0
    )
    started_at = time.perf_counter()
    try:
        chat_turn = await service.run(
            messages=messages,
            system=system_prompt,
            budget_remaining_usd=effective_remaining,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.exception("Agentic chat failed assessment_id=%s", assessment_id)
        after_revisions = workspace_file_revisions(sandbox, repo_root)
        changed_paths = changed_path_revisions(
            before=before_revisions,
            after=after_revisions,
            tool_calls=[],
            sandbox=sandbox,
            repo_root=repo_root,
        )
        failure_detail = persist_failed_chat_attempt(
            assessment=assessment,
            prompts=prompts,
            request=data,
            message=new_message,
            request_id=request_id,
            changed_paths=changed_paths,
            latency_ms=latency_ms,
            interrogation_state=persist_state,
            db=db,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=failure_detail,
        ) from exc
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    after_revisions = workspace_file_revisions(sandbox, repo_root)
    changed_paths = changed_path_revisions(
        before=before_revisions,
        after=after_revisions,
        tool_calls=getattr(chat_turn, "tool_calls_made", []),
        sandbox=sandbox,
        repo_root=repo_root,
    )

    # --- Central integrity guard (components.assessments.integrity): ONE
    # contract for every task. Detect off-task / injection / system-probe,
    # flag in real time, warn at the threshold, hard-void past it. ---
    misuse_category = classify_turn(new_message, chat_turn.content)
    integrity_action = "none"
    voided = False
    if misuse_category == OFF_TASK:
        # The agent already made the semantic refusal — surface it, minus the
        # internal marker.
        response_content = strip_refusal_marker(chat_turn.content)
    elif misuse_category:
        # injection / system-probe tripped on the candidate's message — override
        # the model's reply defensively (never echo a possible leak).
        response_content = REFUSAL_MESSAGE
    else:
        response_content = chat_turn.content

    if misuse_category:
        misuse_count = count_misuse(prompts) + 1
        integrity_action = decide_action(misuse_count)
        flags = list(getattr(assessment, "prompt_fraud_flags", None) or [])
        flags.append({
            "type": f"misuse_{misuse_category}",
            "prompt_index": len(prompts),
            "confidence": 1.0,
            "evidence": misuse_category,
        })
        assessment.prompt_fraud_flags = flags
        append_assessment_timeline_event(
            assessment,
            "integrity_flag",
            {"category": misuse_category, "count": misuse_count, "action": integrity_action},
        )
        if integrity_action == "warn":
            response_content = f"{response_content}{WARN_MESSAGE}"
        elif integrity_action == "void":
            response_content = VOID_MESSAGE
            voided = True
            assessment.is_voided = True
            assessment.voided_at = utcnow()
            assessment.void_reason = (
                f"Auto-voided: repeated assessment misuse ({misuse_category}); "
                f"{misuse_count} flagged attempts."
            )

    is_first_prompt = len(prompts) == 0
    record = {
        "message": new_message,
        "response": response_content,
        "request_id": request_id or None,
        "changed_paths": changed_paths,
        "misuse": misuse_category,
        "code_context": str(data.code_context or "")[:_MAX_CONTEXT_CHARS],
        "paste_detected": bool(data.paste_detected),
        "browser_focused": bool(data.browser_focused),
        "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
        "response_latency_ms": latency_ms,
        "input_tokens": int(getattr(chat_turn, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(chat_turn, "output_tokens", 0) or 0),
        # Prompt-cache token counts. The SDK loop reuses a lot of prior
        # tool-result context across turns, so these are usually 5-15x
        # bigger than raw input_tokens. Persisting here lets the
        # candidate budget UI price them correctly (#416 — was
        # undercounting by ~2x).
        "cache_read_input_tokens": int(getattr(chat_turn, "cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(getattr(chat_turn, "cache_creation_input_tokens", 0) or 0),
        # Model alias for this chat turn — read by
        # ``summarize_prompt_usage`` to price the per-turn tokens at the
        # correct rate (Haiku today; future model swaps drift through
        # this field). Empty string when the SDK didn't report one;
        # the consumer falls back to the chat-path default.
        "model": str(getattr(chat_turn, "model", "") or ""),
        "timestamp": utcnow().isoformat(),
        # Analytics: the whole tool loop = one user-visible turn. Scoring
        # already reads ``message``/``response``/``input_tokens``/etc.; the
        # ``tool_calls_made`` field is informational so we can later answer
        # "how often did Claude reach for read_file vs apply_edit?"
        "tool_calls_made": list(getattr(chat_turn, "tool_calls_made", []) or []),
        # So scoring/analytics can branch CLI-era vs tool-use-era vs SDK-era
        # assessments without sniffing structure.
        "transport": "claude_agent_sdk",
        # Per-decision status snapshot for this turn. Read back by:
        #   1. derive_interrogation_state on the next turn (carry-forward)
        #   2. rubric_scoring.interrogation_outcome grader at submit time
        # Empty dict if no decision_points were declared for this task.
        "interrogation_state": persist_state,
    }
    prompts.append(record)
    assessment.ai_prompts = prompts
    assessment.total_input_tokens = (
        int(getattr(assessment, "total_input_tokens", 0) or 0)
        + int(getattr(chat_turn, "input_tokens", 0) or 0)
    )
    assessment.total_output_tokens = (
        int(getattr(assessment, "total_output_tokens", 0) or 0)
        + int(getattr(chat_turn, "output_tokens", 0) or 0)
    )
    append_assessment_timeline_event(
        assessment,
        "ai_prompt",
        {
            "latency_ms": latency_ms,
            "input_tokens": int(getattr(chat_turn, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(chat_turn, "output_tokens", 0) or 0),
            "tool_calls": len(getattr(chat_turn, "tool_calls_made", []) or []),
            "changed_file_count": len(changed_paths),
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "transport": "claude_agent_sdk",
        },
    )
    if is_first_prompt:
        append_assessment_timeline_event(
            assessment, "first_prompt", {"preview": new_message[:120]}
        )

    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=prompts,
    )
    db.commit()

    return {
        "content": response_content,
        "input_tokens": int(getattr(chat_turn, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(chat_turn, "output_tokens", 0) or 0),
        "latency_ms": latency_ms,
        "claude_budget": claude_budget,
        "assessment_voided": voided,
        "request_id": request_id or None,
        "changed_paths": changed_paths,
        "replayed": False,
    }
