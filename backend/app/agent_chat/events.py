"""Durable, low-noise background events for role Agent Chat.

Background work already records authoritative state in domain rows such as
``AgentRun``.  This module publishes a compact, recruiter-safe notification
into the role conversation in the *same transaction* as that state change.
The message is idempotent by source event key, costs no model tokens, and is
excluded from model replay by ``agent_chat.engine``.

Event cards are informational.  Suggested follow-ups are editable composer
text only; they never authorize or execute an action.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AgentConversation,
    AgentConversationMessage,
    MESSAGE_KIND_EVENT,
)
from ..models.agent_run import AgentRun
from ..models.role import Role
from .service import ensure_conversation, post_agent_message


logger = logging.getLogger("taali.agent_chat.events")

EVENT_CARD_TYPE = "agent_event"
EVENT_STOP_PREFIX = "agent_event:"
EVENT_SEVERITIES = frozenset({"info", "success", "warning", "error"})
RUN_FAILURE_THROTTLE_HOURS = 6

_RUN_TRIGGER_LABELS = {
    "cron": "Scheduled review",
    "event": "New-activity review",
    "manual": "Manual run",
}
_RUN_EVENT_STATUSES = frozenset({"failed", "aborted", "budget_paused"})
_RUN_ERRORS_WITH_OWN_CARD = frozenset({"missing_job_spec", "skipped_overlap"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str:
    timestamp = value or _now()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.isoformat()


def _clean(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _event_receipt(event_type: str, event_key: str) -> str:
    digest = hashlib.sha256(
        f"{event_type.strip()}:{event_key.strip()}".encode("utf-8")
    ).hexdigest()
    return f"{EVENT_STOP_PREFIX}{digest}"


def _normalized_details(details: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in details or []:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("label"), limit=80)
        value = _clean(item.get("value"), limit=300)
        if label and value:
            out.append({"label": label, "value": value})
    return out[:10]


def _normalized_suggestions(
    suggestions: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in suggestions or []:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("label"), limit=60)
        prompt = _clean(item.get("prompt"), limit=800)
        if label and prompt:
            out.append({"label": label, "prompt": prompt})
    return out[:3]


def _safe_relative_href(value: Any) -> str:
    href = str(value or "").strip()[:500]
    if not href.startswith("/") or href.startswith("//") or "\\" in href:
        return ""
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return ""
    return href


def post_agent_event(
    db: Session,
    *,
    role: Role,
    event_type: str,
    event_key: str,
    severity: str,
    title: str,
    summary: str,
    details: list[dict[str, Any]] | None = None,
    source: dict[str, Any] | None = None,
    suggestions: list[dict[str, Any]] | None = None,
    occurred_at: datetime | None = None,
) -> AgentConversationMessage | None:
    """Persist one idempotent transcript event for ``role``.

    ``None`` means the exact source event was already published.  The role's
    conversation row is locked before the duplicate check so concurrent task
    retries serialize on production databases.
    """

    normalized_type = _clean(event_type, limit=80)
    normalized_key = _clean(event_key, limit=300)
    normalized_title = _clean(title, limit=180)
    normalized_summary = _clean(summary, limit=1200)
    normalized_severity = str(severity or "info").strip().lower()
    if normalized_severity not in EVENT_SEVERITIES:
        normalized_severity = "info"
    if not all((normalized_type, normalized_key, normalized_title, normalized_summary)):
        raise ValueError("agent event requires type, key, title, and summary")

    conversation = ensure_conversation(
        db,
        organization_id=int(role.organization_id),
        role=role,
    )
    conversation = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == int(conversation.id),
            AgentConversation.organization_id == int(role.organization_id),
            AgentConversation.role_id == int(role.id),
        )
        .with_for_update()
        .one()
    )
    receipt = _event_receipt(normalized_type, normalized_key)
    existing = (
        db.query(AgentConversationMessage.id)
        .filter(
            AgentConversationMessage.organization_id == int(role.organization_id),
            AgentConversationMessage.role_id == int(role.id),
            AgentConversationMessage.source_key == receipt,
        )
        .first()
    )
    if existing is not None:
        return None

    safe_source: dict[str, Any] | None = None
    if isinstance(source, dict):
        source_type = _clean(source.get("type"), limit=80)
        source_id = source.get("id")
        source_label = _clean(source.get("label"), limit=120)
        href = _safe_relative_href(source.get("href"))
        normalized_source_id: int | str | None = None
        if isinstance(source_id, int) and not isinstance(source_id, bool):
            normalized_source_id = source_id
        elif isinstance(source_id, str):
            normalized_source_id = _clean(source_id, limit=120) or None
        if source_type and normalized_source_id is not None:
            safe_source = {"type": source_type, "id": normalized_source_id}
            if source_label:
                safe_source["label"] = source_label
            if href:
                safe_source["href"] = href

    card = {
        "type": EVENT_CARD_TYPE,
        "event_type": normalized_type,
        "severity": normalized_severity,
        "title": normalized_title,
        "summary": normalized_summary,
        "details": _normalized_details(details),
        "source": safe_source,
        "occurred_at": _iso(occurred_at),
        "suggestions": _normalized_suggestions(suggestions),
    }
    try:
        # The unique source key is the final race guard. A nested transaction
        # contains a losing concurrent insert without rolling back the AgentRun
        # or other domain work in the caller's outer transaction.
        with db.begin_nested():
            message = post_agent_message(
                db,
                conversation=conversation,
                text=f"{normalized_title}\n\n{normalized_summary}",
                actions=[card],
                kind=MESSAGE_KIND_EVENT,
                stop_reason=receipt,
            )
            message.source_key = receipt
            db.flush()
        return message
    except IntegrityError:
        return None


def _run_details(run: AgentRun) -> list[dict[str, str]]:
    details = [
        {
            "label": "Trigger",
            "value": _RUN_TRIGGER_LABELS.get(str(run.trigger), str(run.trigger).title()),
        },
        {"label": "Status", "value": str(run.status).replace("_", " ").title()},
    ]
    if run.rounds_executed is not None:
        details.append({"label": "Rounds", "value": str(int(run.rounds_executed))})
    details.append(
        {"label": "Decisions created", "value": str(int(run.decisions_emitted or 0))}
    )
    cost_micro = int(run.total_cost_micro_usd or 0)
    if cost_micro > 0:
        details.append(
            {"label": "Model cost", "value": f"${cost_micro / 1_000_000:.4f}"}
        )
    return details


def _budget_event_key(
    db: Session,
    *,
    role: Role,
    reason: str,
    occurred_at: datetime | None = None,
) -> str:
    if reason.startswith("insufficient organization credits"):
        # One warning per credit-grant epoch. Hourly cycles may keep hitting the
        # same empty balance; a later top-up gets a new positive ledger id and
        # therefore a fresh warning if that new balance is exhausted too.
        from ..models.billing_credit_ledger import BillingCreditLedger

        grants = db.query(func.max(BillingCreditLedger.id)).filter(
            BillingCreditLedger.organization_id == int(role.organization_id),
            BillingCreditLedger.delta > 0,
        )
        if occurred_at is not None:
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            grants = grants.filter(BillingCreditLedger.created_at <= occurred_at)
        latest_grant_id = grants.scalar() or 0
        return (
            f"org_credit_exhausted:{int(role.organization_id)}:"
            f"grant:{int(latest_grant_id)}"
        )
    # Old/imported budget-paused runs may predate ``agent_paused_at``. Keep
    # their reconciliation key stable instead of using the current clock and
    # generating a fresh warning every five minutes.
    pause_epoch = _iso(role.agent_paused_at) if role.agent_paused_at else "unrecorded"
    return f"role_budget_pause:{int(role.id)}:{pause_epoch}"


def _run_failure_event_key(*, role: Role, run: AgentRun, status: str) -> str:
    reason = str(run.error or "").strip().lower()
    if reason == "model_client_unavailable":
        category = "model_client"
    elif reason.startswith("anthropic call failed"):
        category = "model_call"
    elif reason.startswith("watchdog"):
        category = "watchdog"
    elif "no-progress circuit breaker" in reason:
        category = "no_progress"
    elif "token budget" in reason:
        category = "cycle_token_budget"
    elif "max_tool_rounds" in reason:
        category = "round_limit"
    else:
        # Raw operational errors often contain request ids, URLs, or other
        # changing diagnostics. Treat them as one safe category so those
        # details cannot defeat the six-hour noise throttle.
        category = "operational_error"
    occurred = run.finished_at or run.started_at or _now()
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    bucket_seconds = RUN_FAILURE_THROTTLE_HOURS * 60 * 60
    bucket = int(occurred.timestamp()) // bucket_seconds
    return f"agent_run:{int(role.id)}:{status}:{category}:bucket:{bucket}"


def _budget_suggestions() -> list[dict[str, str]]:
    return [
        {
            "label": "Review budget",
            "prompt": (
                "Show this role's agent budget, recent spend, and what is needed "
                "to resume. Do not change anything."
            ),
        },
        {
            "label": "Recommend a cap",
            "prompt": (
                "Recommend a safe monthly agent budget from recent usage. "
                "Show the trade-off and do not change it."
            ),
        },
    ]


def post_role_budget_pause_event(
    db: Session,
    *,
    role: Role,
    reason: str,
) -> AgentConversationMessage | None:
    """Publish the universal role-spend gate's first pause transition."""

    from ..agent_runtime import budget_guard

    cap_cents = budget_guard.role_monthly_usd_cents(role)
    spent_cents = budget_guard.month_to_date_spend_cents(db, role=role)
    return post_agent_event(
        db,
        role=role,
        event_type="agent_budget_guard",
        event_key=_budget_event_key(db, role=role, reason=str(reason or "")),
        severity="warning",
        title="Agent work paused at the monthly budget",
        summary=(
            "I paused paid work on this role before exceeding its monthly cap. "
            "Read-only analysis remains available while you review the budget."
        ),
        details=[
            {"label": "Monthly cap", "value": f"${cap_cents / 100:.2f}"},
            {"label": "Month-to-date spend", "value": f"${spent_cents / 100:.2f}"},
        ],
        source={
            "type": "role_budget",
            "id": int(role.id),
            "label": "Role agent budget",
        },
        suggestions=_budget_suggestions(),
        occurred_at=role.agent_paused_at,
    )


def post_agent_run_event(
    db: Session,
    *,
    role: Role,
    run: AgentRun,
) -> AgentConversationMessage | None:
    """Publish a failure/budget notification for one terminal agent run."""

    if int(run.organization_id) != int(role.organization_id) or int(run.role_id) != int(
        role.id
    ):
        raise ValueError("agent run does not belong to the supplied role")
    status = str(run.status or "")
    error_code = str(run.error or "").strip()
    if status not in _RUN_EVENT_STATUSES or error_code in _RUN_ERRORS_WITH_OWN_CARD:
        return None

    if status == "budget_paused":
        severity = "warning"
        title = "Agent run stopped at a budget guard"
        summary = (
            "I stopped before spending more. The role may need more monthly budget "
            "or organization credits before autonomous work can continue."
        )
        suggestions = _budget_suggestions()
    elif status == "failed":
        severity = "error"
        title = "Agent run failed"
        summary = (
            "The run stopped before it could finish. Any decisions already created "
            "remain in this thread; unfinished work can be retried safely."
        )
        suggestions = [
            {
                "label": "Explain failure",
                "prompt": (
                    "Explain the latest failed agent run in plain language and "
                    "recommend one safe next step."
                ),
            },
            {
                "label": "Preview retry",
                "prompt": "Preview running the agent again. Do not start it yet.",
            },
        ]
    else:
        severity = "warning"
        title = "Agent run stopped before completion"
        summary = (
            "The cycle ended without reaching its normal completion point. "
            "Any decisions already created remain visible, and unfinished work can retry."
        )
        suggestions = [
            {
                "label": "Explain stop",
                "prompt": (
                    "Explain why the latest agent run stopped and recommend one safe next step."
                ),
            },
            {
                "label": "Preview retry",
                "prompt": "Preview running the agent again. Do not start it yet.",
            },
        ]

    event_type = "agent_budget_guard" if status == "budget_paused" else "agent_run_terminal"
    event_key = (
        _budget_event_key(
            db,
            role=role,
            reason=error_code,
            occurred_at=run.finished_at or run.started_at,
        )
        if status == "budget_paused"
        else _run_failure_event_key(role=role, run=run, status=status)
    )
    return post_agent_event(
        db,
        role=role,
        event_type=event_type,
        event_key=event_key,
        severity=severity,
        title=title,
        summary=summary,
        details=_run_details(run),
        source={
            "type": "agent_run",
            "id": int(run.id),
            "label": f"Agent run #{int(run.id)}",
        },
        suggestions=suggestions,
        occurred_at=run.finished_at,
    )


def try_post_agent_run_event(
    db: Session,
    *,
    role: Role,
    run: AgentRun,
) -> AgentConversationMessage | None:
    """Fail-open wrapper that cannot poison the caller's domain transaction."""

    try:
        with db.begin_nested():
            return post_agent_run_event(db, role=role, run=run)
    except Exception:  # pragma: no cover - notification must not break the run
        logger.exception(
            "agent run event publication failed role_id=%s run_id=%s",
            getattr(role, "id", None),
            getattr(run, "id", None),
        )
        return None


def try_post_role_budget_pause_event(
    db: Session,
    *,
    role: Role,
    reason: str,
) -> AgentConversationMessage | None:
    """Fail-open, transaction-contained role budget notification."""

    try:
        with db.begin_nested():
            return post_role_budget_pause_event(db, role=role, reason=reason)
    except Exception:  # pragma: no cover - budget enforcement must still win
        logger.exception(
            "role budget event publication failed role_id=%s",
            getattr(role, "id", None),
        )
        return None


__all__ = [
    "EVENT_CARD_TYPE",
    "EVENT_SEVERITIES",
    "RUN_FAILURE_THROTTLE_HOURS",
    "post_agent_event",
    "post_agent_run_event",
    "post_role_budget_pause_event",
    "try_post_agent_run_event",
    "try_post_role_budget_pause_event",
]
