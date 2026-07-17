"""Detached, provider-safe snapshots for candidate assessment chat."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .chat_idempotency import candidate_chat_authority_fingerprint


@dataclass(frozen=True)
class CandidateChatAssessmentSnapshot:
    id: int
    organization_id: int
    task_id: int
    role_id: int | None
    e2b_session_id: str
    is_demo: bool
    prompts: list[dict[str, Any]]
    prompt_fingerprint: str
    task_fingerprint: str
    role_fingerprint: str | None


@dataclass(frozen=True)
class CandidateChatTaskSnapshot:
    id: int
    name: str
    description: str | None
    scenario: str | None
    task_key: str | None
    repo_structure: dict[str, Any] | None
    extra_data: dict[str, Any] | None
    claude_budget_limit_usd: float | None
    updated_at: datetime | None


def snapshot_candidate_chat_task(task: Any) -> CandidateChatTaskSnapshot:
    return CandidateChatTaskSnapshot(
        id=int(task.id),
        name=str(task.name or ""),
        description=getattr(task, "description", None),
        scenario=getattr(task, "scenario", None),
        task_key=getattr(task, "task_key", None),
        repo_structure=deepcopy(getattr(task, "repo_structure", None)),
        extra_data=deepcopy(getattr(task, "extra_data", None)),
        claude_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
        updated_at=getattr(task, "updated_at", None),
    )


def candidate_chat_task_fingerprint(task: Any) -> str:
    snapshot = snapshot_candidate_chat_task(task)
    return candidate_chat_authority_fingerprint(
        {
            "id": snapshot.id,
            "name": snapshot.name,
            "description": snapshot.description,
            "scenario": snapshot.scenario,
            "task_key": snapshot.task_key,
            "repo_structure": snapshot.repo_structure,
            "extra_data": snapshot.extra_data,
            "claude_budget_limit_usd": snapshot.claude_budget_limit_usd,
            "updated_at": snapshot.updated_at,
        }
    )


def candidate_chat_role_fingerprint(role: Any | None) -> str | None:
    if role is None:
        return None
    return candidate_chat_authority_fingerprint(
        {
            "id": int(role.id),
            "version": int(getattr(role, "version", 0) or 0),
            "monthly_usd_budget_cents": getattr(
                role, "monthly_usd_budget_cents", None
            ),
            "agent_paused_at": getattr(role, "agent_paused_at", None),
            "deleted_at": getattr(role, "deleted_at", None),
            "updated_at": getattr(role, "updated_at", None),
        }
    )


__all__ = [
    "CandidateChatAssessmentSnapshot",
    "CandidateChatTaskSnapshot",
    "candidate_chat_role_fingerprint",
    "candidate_chat_task_fingerprint",
    "snapshot_candidate_chat_task",
]
