"""Short-transaction runtime for candidate assessment chat."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus
from ...models.role import Role
from ...models.task import Task
from ...services.pricing_service import Feature
from ...services.usage_metering_service import InsufficientCreditsError
from .candidate_chat_checkpoint import (
    restore_candidate_chat_input,
    restore_candidate_chat_turn,
    serialize_candidate_chat_input,
    serialize_candidate_chat_turn,
)
from .candidate_chat_audit import (
    append_candidate_chat_no_replay_resolution,
    append_candidate_chat_reconciliation_required,
)
from .candidate_chat_contracts import (
    CandidateChatAssessmentSnapshot,
    CandidateChatTaskSnapshot,
    candidate_chat_role_fingerprint,
    candidate_chat_task_fingerprint,
    snapshot_candidate_chat_task,
)
from .candidate_chat_finalization import finalize_candidate_chat_turn
from .candidate_chat_prompting import (
    build_agentic_system_prompt,
    flatten_prompts_to_messages,
)
from .candidate_chat_provider_failure import ambiguous_chat_failure_http
from .chat_idempotency import (
    RequestIdConflictError,
    RequestOutcomeInDoubtError,
    candidate_chat_prompt_fingerprint,
    candidate_chat_request_hash,
    claim_candidate_chat_request,
    get_candidate_chat_claim,
    list_candidate_chat_claims,
    reconcile_noncurrent_candidate_chat_claims,
    replay_candidate_chat_request,
    update_candidate_chat_claim,
)
from .interrogation import (
    all_resolved,
    build_interrogation_directive,
    derive_interrogation_state,
    merge_state,
)
from .repository import (
    append_assessment_timeline_event,
    time_remaining_seconds,
    utcnow,
    validate_assessment_token,
)
from .assessment_guards import enforce_not_paused

logger = logging.getLogger("taali.candidate_claude_chat")

_MAX_HISTORY_MESSAGES = 20
_MAX_CONTEXT_CHARS = 12000
_AGENT_CHAT_WALL_TIMEOUT_SECONDS = 600.0

@dataclass(frozen=True)
class CandidateChatHooks:
    e2b_service_cls: Any
    tool_executor_cls: Any
    agent_service_cls: Any
    resolve_api_key: Callable[[], str | None]
    can_spend_on_role: Callable[..., bool]
    reserve: Callable[..., int]
    build_budget_snapshot: Callable[..., dict[str, Any]]
    resolve_budget_limit: Callable[..., float | None]
    classify_response: Callable[..., Any]
    workspace_repo_root: Callable[[Any], str]
    e2b_api_key: str | None


@dataclass(frozen=True)
class _PreparedChat:
    assessment: CandidateChatAssessmentSnapshot
    task: CandidateChatTaskSnapshot
    claim_key: str
    request_id: str | None
    request_hash: str
    claim: dict[str, Any]
    budget_limit_usd: float | None
    api_key: str | None


@dataclass(frozen=True)
class _PendingCheckpoint:
    prepared: _PreparedChat
    claim: dict[str, Any]
    current_request_id: str | None
    current_request_hash: str
    same_payload: bool


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _assert_provider_detached(db: Session, phase: str) -> None:
    if db.in_transaction():
        raise RuntimeError(f"request transaction remained open before {phase}")


def _query_active_for_update(db: Session, assessment_id: int) -> Assessment:
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.status == AssessmentStatus.IN_PROGRESS,
            Assessment.is_voided.is_(False),
        )
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Active assessment not found")
    return assessment


def _query_for_chat_replay(db: Session, assessment_id: int) -> Assessment:
    """Load any assessment state so an exact committed response can replay."""

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == int(assessment_id))
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _prepared_from_claim(
    *,
    assessment: Assessment,
    task: Task,
    role: Role | None,
    prompts: list[dict[str, Any]],
    claim_key: str,
    claim: dict[str, Any],
    budget_limit_usd: float | None,
    api_key: str | None = None,
) -> _PreparedChat:
    """Rebuild finalization authority entirely from durable claim evidence."""

    current_task_fingerprint = candidate_chat_task_fingerprint(task)
    current_role_fingerprint = candidate_chat_role_fingerprint(role)
    return _PreparedChat(
        assessment=CandidateChatAssessmentSnapshot(
            id=int(assessment.id),
            organization_id=int(assessment.organization_id),
            task_id=int(assessment.task_id),
            role_id=int(assessment.role_id) if assessment.role_id else None,
            e2b_session_id=str(
                claim.get("e2b_session_id") or assessment.e2b_session_id or ""
            ),
            is_demo=bool(assessment.is_demo),
            prompts=prompts,
            prompt_fingerprint=str(
                claim.get("prompt_fingerprint")
                or candidate_chat_prompt_fingerprint(prompts)
            ),
            task_fingerprint=str(
                claim.get("task_fingerprint") or current_task_fingerprint
            ),
            role_fingerprint=(
                str(claim.get("role_fingerprint"))
                if claim.get("role_fingerprint") is not None
                else current_role_fingerprint
            ),
        ),
        task=snapshot_candidate_chat_task(task),
        claim_key=str(claim_key),
        request_id=(str(claim.get("request_id") or "").strip() or None),
        request_hash=str(claim.get("request_hash") or ""),
        claim=dict(claim),
        budget_limit_usd=budget_limit_usd,
        api_key=api_key,
    )


def _prepare_claim(
    db: Session,
    *,
    assessment_id: int,
    data: Any,
    token: str,
    hooks: CandidateChatHooks,
) -> _PreparedChat | _PendingCheckpoint | dict[str, Any]:
    assessment = _query_for_chat_replay(db, assessment_id)
    validate_assessment_token(assessment, token)
    task = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    role = (
        db.query(Role).filter(Role.id == assessment.role_id).one_or_none()
        if assessment.role_id
        else None
    )
    message = data.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    request_id = (data.request_id or "").strip() or None
    request_hash = candidate_chat_request_hash(
        message=message,
        code_context=data.code_context,
        selected_file_path=data.selected_file_path,
        paste_detected=data.paste_detected,
        browser_focused=data.browser_focused,
        time_since_last_prompt_ms=data.time_since_last_prompt_ms,
    )
    prompts = deepcopy(list(assessment.ai_prompts or []))
    budget_limit = hooks.resolve_budget_limit(
        is_demo=bool(assessment.is_demo),
        task_budget_limit_usd=task.claude_budget_limit_usd,
    )
    replay = replay_candidate_chat_request(
        prompts=prompts,
        request_id=request_id,
        message=message,
        request_hash=request_hash,
        budget_limit_usd=budget_limit,
    )
    if replay is not None:
        db.rollback()
        return replay
    prompt_fingerprint = candidate_chat_prompt_fingerprint(prompts)
    task_fingerprint = candidate_chat_task_fingerprint(task)
    role_fingerprint = candidate_chat_role_fingerprint(role)
    claim_key = request_id or f"anonymous:{uuid.uuid4().hex}"
    claims = list_candidate_chat_claims(assessment.prompt_analytics)
    pending = next(
        (
            (prior_key, prior_claim)
            for prior_key, prior_claim in claims.items()
            if str(prior_claim.get("state") or "") == "agent_completed"
        ),
        None,
    )
    if pending is not None:
        prior_key, prior_claim = pending
        prior_hash = str(prior_claim.get("request_hash") or "")
        if prior_key == claim_key and prior_hash != request_hash:
            raise RequestIdConflictError(
                "request_id was already used for a different request"
            )
        return _PendingCheckpoint(
            prepared=_prepared_from_claim(
                assessment=assessment,
                task=task,
                role=role,
                prompts=prompts,
                claim_key=prior_key,
                claim=prior_claim,
                budget_limit_usd=budget_limit,
            ),
            claim=prior_claim,
            current_request_id=request_id,
            current_request_hash=request_hash,
            same_payload=prior_hash == request_hash,
        )
    if request_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request_id is required for a new AI request",
        )
    if (
        assessment.status != AssessmentStatus.IN_PROGRESS
        or bool(assessment.is_voided)
    ):
        raise HTTPException(status_code=404, detail="Active assessment not found")
    enforce_not_paused(assessment)
    if time_remaining_seconds(assessment) <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ASSESSMENT_TIME_EXPIRED",
                "message": "Assessment time expired; no new AI request was started",
            },
        )

    analytics = reconcile_noncurrent_candidate_chat_claims(
        assessment.prompt_analytics,
        current_claim_key=claim_key,
    )
    append_candidate_chat_no_replay_resolution(
        assessment,
        claims,
        current_claim_key=claim_key,
        reason="superseded_by_distinct_request",
    )
    analytics, claim = claim_candidate_chat_request(
        analytics,
        claim_key=claim_key,
        request_id=request_id,
        request_hash=request_hash,
        prompt_fingerprint=prompt_fingerprint,
    )
    prior_task_fingerprint = claim.get("task_fingerprint")
    prior_role_fingerprint = claim.get("role_fingerprint")
    prior_workspace = claim.get("e2b_session_id")
    if prior_task_fingerprint and prior_task_fingerprint != task_fingerprint:
        raise _conflict("The assessment task changed while this chat request was pending")
    if prior_role_fingerprint and prior_role_fingerprint != role_fingerprint:
        raise _conflict("The assessment role changed while this chat request was pending")
    if prior_workspace and prior_workspace != assessment.e2b_session_id:
        raise _conflict("The assessment workspace changed while this chat request was pending")
    claim_state = str(claim.get("state") or "claimed")
    api_key: str | None = None
    if claim_state != "agent_completed":
        if not hooks.can_spend_on_role(db, role=role):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "message": "Your Claude budget for this assessment has been reached. You can keep working and submit when you're ready."
                },
            )
        if not assessment.e2b_session_id:
            raise _conflict(
                {
                    "message": "The assessment workspace is not active. Please refresh and start again."
                }
            )
        api_key = hooks.resolve_api_key()
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Claude isn't available right now. Please contact your recruiter."
                },
            )
    analytics = update_candidate_chat_claim(
        analytics,
        claim_key=claim_key,
        request_hash=request_hash,
        state=claim_state,
        updates={
            "task_fingerprint": task_fingerprint,
            "role_fingerprint": role_fingerprint,
            "e2b_session_id": assessment.e2b_session_id,
        },
    )
    assessment.prompt_analytics = analytics
    current_claim = get_candidate_chat_claim(analytics, claim_key=claim_key) or claim
    prepared = _prepared_from_claim(
        assessment=assessment,
        task=task,
        role=role,
        prompts=prompts,
        claim_key=claim_key,
        claim=current_claim,
        budget_limit_usd=budget_limit,
        api_key=str(api_key) if api_key else None,
    )
    db.commit()
    return prepared


def _load_exact_authority(
    db: Session, prepared: _PreparedChat, token: str
) -> tuple[Assessment, dict[str, Any]]:
    assessment = _query_active_for_update(db, prepared.assessment.id)
    validate_assessment_token(assessment, token)
    if (
        int(assessment.task_id) != prepared.assessment.task_id
        or (int(assessment.role_id) if assessment.role_id else None)
        != prepared.assessment.role_id
        or str(assessment.e2b_session_id or "")
        != prepared.assessment.e2b_session_id
        or candidate_chat_prompt_fingerprint(list(assessment.ai_prompts or []))
        != prepared.assessment.prompt_fingerprint
    ):
        raise _conflict("Assessment authority changed while the AI request was running")
    task = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
    role = (
        db.query(Role).filter(Role.id == assessment.role_id).one_or_none()
        if assessment.role_id
        else None
    )
    if task is None or candidate_chat_task_fingerprint(task) != prepared.assessment.task_fingerprint:
        raise _conflict("Assessment task changed while the AI request was running")
    if candidate_chat_role_fingerprint(role) != prepared.assessment.role_fingerprint:
        raise _conflict("Assessment role changed while the AI request was running")
    claim = get_candidate_chat_claim(
        assessment.prompt_analytics, claim_key=prepared.claim_key
    )
    if claim is None or str(claim.get("request_hash") or "") != prepared.request_hash:
        raise _conflict("The durable chat request claim changed")
    return assessment, claim


def _advance_claim(
    db: Session,
    prepared: _PreparedChat,
    token: str,
    *,
    state: str,
    updates: dict[str, Any] | None = None,
    timeline_error_ms: int | None = None,
) -> dict[str, Any]:
    assessment, current_claim = _load_exact_authority(db, prepared, token)
    updated_analytics = update_candidate_chat_claim(
        assessment.prompt_analytics,
        claim_key=prepared.claim_key,
        request_hash=prepared.request_hash,
        state=state,
        updates=updates,
    )
    assessment.prompt_analytics = updated_analytics
    if (
        state == "manual_reconciliation_required"
        and str(current_claim.get("state") or "") != state
    ):
        append_candidate_chat_reconciliation_required(
            assessment,
            prior_state=str(current_claim.get("state") or "unknown"),
        )
    if timeline_error_ms is not None:
        append_assessment_timeline_event(
            assessment, "ai_prompt_error", {"latency_ms": timeline_error_ms}
        )
    updated_claim = (
        get_candidate_chat_claim(updated_analytics, claim_key=prepared.claim_key) or {}
    )
    db.commit()
    return updated_claim


def _persist_agent_checkpoint(
    db: Session,
    prepared: _PreparedChat,
    token: str,
    *,
    data: Any,
    chat_turn: Any,
    latency_ms: int,
    merged_state: dict[str, Any],
    persist_state: dict[str, Any],
) -> dict[str, Any]:
    """Checkpoint a known provider result, retrying only the DB write."""

    updates = {
        "chat_turn_checkpoint": serialize_candidate_chat_turn(chat_turn),
        "finalization_input": serialize_candidate_chat_input(data),
        "provider_disposition": "succeeded",
        "provider_stop_reason": (
            str(getattr(chat_turn, "stop_reason", "") or "").strip() or None
        ),
        "latency_ms": max(int(latency_ms), 0),
        "merged_state": dict(merged_state),
        "persist_state": dict(persist_state),
        "last_error": None,
    }
    try:
        return _advance_claim(
            db,
            prepared,
            token,
            state="agent_completed",
            updates=updates,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to checkpoint completed chat result; re-reading once assessment_id=%s",
            prepared.assessment.id,
        )
        _assessment, current_claim = _load_exact_authority(db, prepared, token)
        if str(current_claim.get("state") or "") == "agent_completed":
            restore_candidate_chat_turn(current_claim.get("chat_turn_checkpoint"))
            db.rollback()
            return current_claim
        db.rollback()
        return _advance_claim(
            db,
            prepared,
            token,
            state="agent_completed",
            updates=updates,
        )


def _finalize_checkpointed_turn(
    db: Session,
    prepared: _PreparedChat,
    token: str,
    *,
    fallback_data: Any | None,
    claim: dict[str, Any],
    hooks: CandidateChatHooks,
) -> dict[str, Any]:
    """Finalize a durable provider result without repeating provider work."""

    chat_turn = restore_candidate_chat_turn(claim.get("chat_turn_checkpoint"))
    if not chat_turn.success:
        raise ValueError("Unsuccessful provider evidence cannot be finalized as a reply")
    try:
        finalization_input = restore_candidate_chat_input(
            claim.get("finalization_input")
        )
    except ValueError:
        if fallback_data is None:
            raise
        finalization_input = fallback_data
    try:
        return finalize_candidate_chat_turn(
            db=db,
            prepared=prepared,
            token=token,
            data=finalization_input,
            chat_turn=chat_turn,
            latency_ms=max(int(claim.get("latency_ms") or 0), 0),
            persist_state=dict(claim.get("persist_state") or {}),
            build_budget_snapshot=hooks.build_budget_snapshot,
            load_authority=_load_exact_authority,
        )
    except Exception:
        db.rollback()
        try:
            _advance_claim(
                db,
                prepared,
                token,
                state="agent_completed",
                updates={"last_error": "finalization_failed"},
            )
        except Exception:
            # If the original commit actually succeeded, authority now includes
            # the prompt and an exact retry will replay it. Otherwise the
            # already-durable checkpoint remains resumable.
            db.rollback()
        raise


def _record_unsuccessful_chat_turn(
    db: Session,
    prepared: _PreparedChat,
    token: str,
    *,
    data: Any,
    chat_turn: Any,
    latency_ms: int,
    merged_state: dict[str, Any],
    persist_state: dict[str, Any],
) -> None:
    """Persist a false ChatTurn without ever committing it as an AI reply."""

    stop_reason = str(getattr(chat_turn, "stop_reason", "") or "").strip()
    updates = {
        "chat_turn_checkpoint": serialize_candidate_chat_turn(chat_turn),
        "finalization_input": serialize_candidate_chat_input(data),
        "latency_ms": max(int(latency_ms), 0),
        "merged_state": dict(merged_state),
        "persist_state": dict(persist_state),
        "provider_stop_reason": stop_reason or None,
    }
    if stop_reason in {"budget_exhausted", "role_budget_exhausted"}:
        updates.update(
            provider_disposition="definite_pre_provider_budget_rejection",
            last_error=stop_reason,
        )
        _advance_claim(
            db,
            prepared,
            token,
            state="classifier_completed",
            updates=updates,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "Your Claude budget for this assessment has been reached. You can keep working and submit when you're ready."
            },
        )
    if stop_reason == "metering_admission_failed":
        updates.update(
            provider_disposition="definite_pre_provider_retryable_failure",
            last_error=stop_reason,
        )
        _advance_claim(
            db,
            prepared,
            token,
            state="classifier_completed",
            updates=updates,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "Claude isn't available right now. Please retry."},
        )
    updates.update(
        provider_disposition="manual_reconciliation_required",
        reconciliation_disposition="provider_outcome_not_replayed",
        last_error=stop_reason or "agent_result_error",
    )
    _advance_claim(
        db,
        prepared,
        token,
        state="manual_reconciliation_required",
        updates=updates,
        timeline_error_ms=latency_ms,
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"message": "Claude hit a problem. Please start a new request."},
    )


def _alias_completed_chat_response(
    db: Session,
    *,
    prepared: _PreparedChat,
    token: str,
    data: Any,
    request_id: str,
    request_hash: str,
) -> dict[str, Any]:
    """Attach a new exact request id to one committed prompt without re-spend."""

    assessment = _query_for_chat_replay(db, prepared.assessment.id)
    validate_assessment_token(assessment, token)
    prompts = deepcopy(list(assessment.ai_prompts or []))
    source = next(
        (
            record
            for record in reversed(prompts)
            if isinstance(record, dict)
            and str(record.get("request_hash") or "") == prepared.request_hash
        ),
        None,
    )
    if source is None:
        raise _conflict("The checkpointed chat response could not be recovered")
    aliases = list(source.get("request_aliases") or [])
    existing = next(
        (
            item
            for item in aliases
            if isinstance(item, dict)
            and str(item.get("request_id") or "") == request_id
        ),
        None,
    )
    if existing is not None and str(existing.get("request_hash") or "") != request_hash:
        raise _conflict("request_id was already used for a different request")
    if existing is None:
        aliases.append(
            {
                "request_id": request_id,
                "request_hash": request_hash,
                "aliased_at": utcnow().isoformat(),
            }
        )
        source["request_aliases"] = aliases
        assessment.ai_prompts = prompts
        db.commit()
    else:
        db.rollback()
    replay = replay_candidate_chat_request(
        prompts=prompts,
        request_id=request_id,
        message=data.message.strip(),
        request_hash=request_hash,
        budget_limit_usd=prepared.budget_limit_usd,
    )
    if replay is None:
        raise _conflict("The checkpointed chat response alias was not persisted")
    return replay


def _reserve_paid_call(
    db: Session, hooks: CandidateChatHooks, organization_id: int
) -> None:
    try:
        hooks.reserve(
            db,
            organization_id=int(organization_id),
            feature=Feature.ASSESSMENT,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "This assessment's AI credit balance has been reached. You can keep working and submit when you're ready."
            },
        ) from exc
    finally:
        db.rollback()


def _interrogation_inputs(prepared: _PreparedChat) -> tuple[list[dict], dict[str, str]]:
    extra = prepared.task.extra_data if isinstance(prepared.task.extra_data, dict) else {}
    raw_points = extra.get("decision_points") if isinstance(extra, dict) else None
    points = [item for item in raw_points if isinstance(item, dict)] if isinstance(raw_points, list) else []
    return points, derive_interrogation_state(points, prepared.assessment.prompts)


async def run_candidate_chat(
    *,
    assessment_id: int,
    data: Any,
    token: str,
    db: Session,
    hooks: CandidateChatHooks,
) -> dict[str, Any]:
    """Execute one chat turn with durable checkpoints around paid work."""

    prepared: _PreparedChat | None = None
    for _recovery_attempt in range(2):
        try:
            prepared_or_replay = _prepare_claim(
                db,
                data=data,
                assessment_id=assessment_id,
                token=token,
                hooks=hooks,
            )
        except (RequestIdConflictError, RequestOutcomeInDoubtError) as exc:
            db.rollback()
            raise _conflict(str(exc)) from exc
        except Exception:
            db.rollback()
            raise
        if isinstance(prepared_or_replay, dict):
            return prepared_or_replay
        if isinstance(prepared_or_replay, _PendingCheckpoint):
            pending = prepared_or_replay
            try:
                recovered = _finalize_checkpointed_turn(
                    db,
                    pending.prepared,
                    token,
                    fallback_data=(
                        data
                        if pending.same_payload
                        and pending.current_request_id
                        == pending.prepared.request_id
                        else None
                    ),
                    claim=pending.claim,
                    hooks=hooks,
                )
            except ValueError as exc:
                db.rollback()
                raise _conflict(str(exc)) from exc
            if pending.same_payload:
                if (
                    pending.current_request_id
                    and pending.current_request_id != pending.prepared.request_id
                ):
                    return _alias_completed_chat_response(
                        db,
                        prepared=pending.prepared,
                        token=token,
                        data=data,
                        request_id=pending.current_request_id,
                        request_hash=pending.current_request_hash,
                    )
                return recovered
            continue
        prepared = prepared_or_replay
        break
    if prepared is None:
        raise _conflict("A checkpointed chat result could not be finalized")

    _assert_provider_detached(db, "E2B connect")
    e2b = hooks.e2b_service_cls(hooks.e2b_api_key)
    try:
        sandbox = e2b.connect_sandbox(prepared.assessment.e2b_session_id)
    except Exception as exc:
        logger.exception("Failed to connect to E2B sandbox assessment_id=%s", assessment_id)
        state = "classifier_completed" if prepared.claim.get("state") == "classifier_completed" else "retryable"
        try:
            _advance_claim(db, prepared, token, state=state, updates={"last_error": "workspace_unavailable"})
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "Workspace is temporarily unavailable. Please retry in a moment."},
        ) from exc

    repo_root = hooks.workspace_repo_root(prepared.task)
    executor = hooks.tool_executor_cls(e2b_service=e2b, sandbox=sandbox, repo_root=repo_root)
    points, prior_state = _interrogation_inputs(prepared)
    claim = prepared.claim
    if str(claim.get("state") or "") == "classifier_completed":
        merged_state = dict(claim.get("merged_state") or prior_state)
        persist_state = dict(claim.get("persist_state") or {})
    else:
        merged_state = prior_state
        persist_state: dict[str, Any] = {}
        classifier_error: str | None = None
        if points and not all_resolved(prior_state):
            _reserve_paid_call(db, hooks, prepared.assessment.organization_id)
            _advance_claim(db, prepared, token, state="classifier_started")
            _assert_provider_detached(db, "interrogation classifier")
            try:
                outcome = await asyncio.to_thread(
                    hooks.classify_response,
                    decision_points=points,
                    candidate_message=data.message.strip(),
                    prior_state=prior_state,
                    api_key=prepared.api_key,
                    organization_id=prepared.assessment.organization_id,
                    assessment_id=prepared.assessment.id,
                    role_id=prepared.assessment.role_id,
                    trace_id=f"assessment:{prepared.assessment.id}:chat:{prepared.request_id or prepared.claim_key}:classifier",
                )
            except Exception as exc:
                logger.exception(
                    "interrogation classifier raised assessment=%s", assessment_id
                )
                classifier_error = type(exc).__name__
                merged_state, persist_state = merge_state(prior_state, {})
            else:
                merged_state, persist_state = merge_state(prior_state, outcome.by_dp)
                if outcome.error:
                    classifier_error = str(outcome.error)
                    logger.info(
                        "interrogation classifier soft-failed assessment=%s err=%s",
                        assessment_id,
                        outcome.error,
                    )
        elif points:
            persist_state = {
                point_id: {"status": value, "raw_status": value, "rationale": "carry_forward"}
                for point_id, value in prior_state.items()
            }
        classifier_updates: dict[str, Any] = {
            "merged_state": merged_state,
            "persist_state": persist_state,
        }
        if classifier_error:
            classifier_updates["classifier_error"] = classifier_error
        claim = _advance_claim(
            db,
            prepared,
            token,
            state="classifier_completed",
            updates=classifier_updates,
        )

    extra = prepared.task.extra_data if isinstance(prepared.task.extra_data, dict) else {}
    messages = flatten_prompts_to_messages(prepared.assessment.prompts, _MAX_HISTORY_MESSAGES)
    user_content = data.message.strip()
    if data.code_context:
        path = (data.selected_file_path or "current_file").strip()
        user_content = f'{user_content}\n\n<editor_context path="{path}">\n{data.code_context[:_MAX_CONTEXT_CHARS]}\n</editor_context>'
    messages.append({"role": "user", "content": user_content})
    directive = build_interrogation_directive(points, merged_state)
    system_prompt = build_agentic_system_prompt(prepared.task, directive)
    current_budget = hooks.build_budget_snapshot(
        budget_limit_usd=prepared.budget_limit_usd,
        prompts=prepared.assessment.prompts,
    )
    remaining = current_budget.get("remaining_usd") if isinstance(current_budget, dict) else None
    effective_remaining = float(remaining) if isinstance(remaining, (int, float)) else 1.0

    try:
        _reserve_paid_call(db, hooks, prepared.assessment.organization_id)
    except HTTPException:
        _advance_claim(
            db,
            prepared,
            token,
            state="classifier_completed",
            updates={"merged_state": merged_state, "persist_state": persist_state, "last_error": "insufficient_credits"},
        )
        raise
    if not prepared.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "Claude isn't available right now."},
        )
    try:
        service = hooks.agent_service_cls(
            api_key=prepared.api_key,
            organization_id=prepared.assessment.organization_id,
            assessment_id=prepared.assessment.id,
            executor=executor,
            role_id=prepared.assessment.role_id,
            trace_id=f"assessment:{prepared.assessment.id}:chat:{prepared.request_id or prepared.claim_key}:agent",
            model=(str(extra.get("agent_model")).strip() or None) if extra.get("agent_model") else None,
        )
    except Exception as exc:
        _advance_claim(
            db,
            prepared,
            token,
            state="classifier_completed",
            updates={
                "merged_state": merged_state,
                "persist_state": persist_state,
                "last_error": "agent_service_unavailable",
                "provider_disposition": "definite_pre_provider_retryable_failure",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "Claude isn't available right now. Please retry."},
        ) from exc
    _advance_claim(db, prepared, token, state="agent_started")
    _assert_provider_detached(db, "Agent SDK")
    started_at = time.perf_counter()
    try:
        chat_turn = await asyncio.wait_for(
            service.run(
                messages=messages,
                system=system_prompt,
                budget_remaining_usd=effective_remaining,
            ),
            timeout=_AGENT_CHAT_WALL_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning("Agentic chat timed out assessment_id=%s", assessment_id)
        raise ambiguous_chat_failure_http(
            db,
            prepared=prepared,
            token=token,
            assessment_id=int(assessment_id),
            advance_claim=_advance_claim,
            latency_ms=latency_ms,
            last_error="agent_call_timed_out",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            message="Claude took too long to finish. The prior request will not be replayed; please start a new request.",
            logger=logger,
        ) from exc
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.exception("Agentic chat failed assessment_id=%s", assessment_id)
        raise ambiguous_chat_failure_http(
            db,
            prepared=prepared,
            token=token,
            assessment_id=int(assessment_id),
            advance_claim=_advance_claim,
            latency_ms=latency_ms,
            last_error="agent_call_raised",
            status_code=status.HTTP_502_BAD_GATEWAY,
            message="Claude hit a problem. The prior request will not be replayed; please start a new request.",
            logger=logger,
        ) from exc
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    if not bool(getattr(chat_turn, "success", True)):
        _record_unsuccessful_chat_turn(
            db,
            prepared,
            token,
            data=data,
            chat_turn=chat_turn,
            latency_ms=latency_ms,
            merged_state=merged_state,
            persist_state=persist_state,
        )
    try:
        claim = _persist_agent_checkpoint(
            db,
            prepared,
            token,
            data=data,
            chat_turn=chat_turn,
            latency_ms=latency_ms,
            merged_state=merged_state,
            persist_state=persist_state,
        )
        return _finalize_checkpointed_turn(
            db,
            prepared,
            token,
            fallback_data=data,
            claim=claim,
            hooks=hooks,
        )
    except ValueError as exc:
        db.rollback()
        raise _conflict(str(exc)) from exc


__all__ = ["CandidateChatHooks", "run_candidate_chat"]
