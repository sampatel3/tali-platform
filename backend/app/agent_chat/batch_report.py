"""Manual batch-processing → role-chat status + completion messages.

When a recruiter kicks a manual batch — the unified Process cascade
(fetch CVs → pre-screen → score), a standalone batch pre-screen, or a full
batch re-score — it runs in the background and, until now, only surfaced in
the floating jobs toaster. If the recruiter navigated away they had no record
of whether the run finished or what it did.

This module gives every batch a paper trail in the role's agent chat:

* :func:`post_started` drops a "started" message the moment the batch is
  kicked, carrying a ``batch_process`` card (status ``running``) the chat dock
  uses to poll the timeline — the same trick the re-screen report uses so the
  follow-up appears without a manual refresh.
* :func:`post_completion` posts the "what completed" summary once the batch
  settles (composed deterministically from the counters — no LLM, so it's
  exact and free) and bumps the conversation so the sidebar unread badge fires.
  It is idempotent per run: several code paths can race to report the same
  batch (the status poll, the daemon thread's finally, the Celery backstop),
  so the first one wins via a Redis set-if-absent guard and the rest no-op.

The ``kind`` strings line up with the toaster's job kinds so the chat copy and
the live progress row describe the same run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    MESSAGE_KIND_CHAT,
    AgentConversation,
    AgentConversationMessage,
)
from ..models.role import Role

logger = logging.getLogger("taali.agent_chat")


# Batch kinds — keep in step with the toaster's job rows.
KIND_PROCESS = "process"
KIND_BATCH_SCORE = "batch_score"
KIND_BATCH_PRE_SCREEN = "batch_pre_screen"

# Card types the chat renders / polls on. ``batch_process`` (running) drives the
# dock's silent timeline poll; ``batch_complete`` is the terminal summary card.
CARD_BATCH_RUNNING = "batch_process"
CARD_BATCH_DONE = "batch_complete"

# The "still working" verb per kind, for the started message.
_STARTED_VERB = {
    KIND_PROCESS: "Processing",
    KIND_BATCH_SCORE: "Scoring",
    KIND_BATCH_PRE_SCREEN: "Pre-screening",
}

# Dedup flag TTL. Longer than any expected batch so a slow run can't report
# twice once the flag expires; short enough that a re-run of the same role
# later gets a fresh flag (the token also differs per run).
_REPORTED_TTL_SECONDS = 6 * 3600


def _fmt(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


# ---------------------------------------------------------------------------
# Redis dedup guard
# ---------------------------------------------------------------------------


def _redis():
    """Lazy redis client; None on any failure so callers degrade gracefully."""
    try:
        import redis  # type: ignore

        from ..platform.config import settings

        return redis.Redis.from_url(settings.REDIS_URL)
    except Exception:
        return None


def _reported_key(kind: str, role_id: int, token: str) -> str:
    return f"batch_report:done:{kind}:{int(role_id)}:{token or '-'}"


def claim_completion(kind: str, role_id: int, token: str) -> bool:
    """Atomically claim the right to post THIS run's completion message.

    Returns True for the first caller (it should post), False for every
    subsequent one (a peer already reported, or is about to). ``token`` is the
    batch's start timestamp so each run gets its own flag. When Redis is
    unavailable we return True — a duplicate message beats a silent miss, and
    the threaded paths only call this once anyway.
    """
    client = _redis()
    if client is None:
        return True
    try:
        # set NX — only the first writer succeeds.
        return bool(client.set(_reported_key(kind, role_id, token), "1", nx=True, ex=_REPORTED_TTL_SECONDS))
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Message composition (deterministic — no LLM)
# ---------------------------------------------------------------------------


def compose_started(role_name: str | None, kind: str, total: int, steps: list[str]) -> str:
    role = role_name or "this role"
    verb = _STARTED_VERB.get(kind, "Processing")
    steps_txt = " → ".join(steps) if steps else verb.lower()
    return (
        f"{verb} {_fmt(total)} candidate(s) on {role} ({steps_txt}). "
        "I'll post the result here when it's done — you can watch live progress "
        "in the jobs panel meanwhile."
    )


def _outcome_verb(status: str) -> str:
    if status == "cancelled":
        return "Cancelled"
    if status == "failed":
        return "Stopped (an error interrupted the run)"
    return "Finished"


def compose_completion(role_name: str | None, kind: str, counts: dict, *, status: str = "completed") -> str:
    """Human one-liner summarising what the batch did, from its counters."""
    role = role_name or "this role"
    verb = _outcome_verb(status)

    if kind == KIND_PROCESS:
        parts: list[str] = []
        fetch = counts.get("fetch") or {}
        if int(fetch.get("total") or 0) > 0:
            seg = f"fetched {_fmt(fetch.get('fetched', 0))} of {_fmt(fetch.get('total', 0))} CVs"
            if int(fetch.get("errors") or 0):
                seg += f" ({_fmt(fetch['errors'])} failed)"
            parts.append(seg)
        ps = counts.get("pre_screen") or {}
        if int(ps.get("total") or 0) > 0:
            seg = f"pre-screened {_fmt(ps.get('processed', 0))} of {_fmt(ps.get('total', 0))}"
            if int(ps.get("errors") or 0):
                seg += f" ({_fmt(ps['errors'])} errors)"
            parts.append(seg)
        sc = counts.get("score") or {}
        if int(sc.get("total") or 0) > 0:
            seg = f"scored {_fmt(sc.get('scored', 0))} of {_fmt(sc.get('total', 0))}"
            extra: list[str] = []
            if int(sc.get("filtered") or 0):
                extra.append(f"{_fmt(sc['filtered'])} below cut-off")
            if int(sc.get("errors") or 0):
                extra.append(f"{_fmt(sc['errors'])} errors")
            if extra:
                seg += f" ({', '.join(extra)})"
            parts.append(seg)
        gs = counts.get("graph_sync") or {}
        if int(gs.get("total") or 0) > 0:
            parts.append(f"synced {_fmt(gs.get('synced', 0))} to the knowledge graph")
        body = "; ".join(parts) if parts else "nothing needed processing"
        return f"{verb} processing {role} — {body}."

    if kind == KIND_BATCH_SCORE:
        total = counts.get("total", 0)
        seg = f"scored {_fmt(counts.get('scored', 0))} of {_fmt(total)} candidate(s)"
        extra = []
        if int(counts.get("pre_screened_out") or 0):
            extra.append(f"{_fmt(counts['pre_screened_out'])} below cut-off")
        if int(counts.get("errors") or 0):
            extra.append(f"{_fmt(counts['errors'])} errors")
        if extra:
            seg += f" ({', '.join(extra)})"
        return f"{verb} scoring {role} — {seg}."

    if kind == KIND_BATCH_PRE_SCREEN:
        total = counts.get("total", 0)
        seg = f"pre-screened {_fmt(counts.get('processed', 0))} of {_fmt(total)} candidate(s)"
        if int(counts.get("errors") or 0):
            seg += f" ({_fmt(counts['errors'])} errors)"
        return f"{verb} pre-screening {role} — {seg}."

    return f"{verb} processing {role}."


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def _bump(conversation: AgentConversation) -> None:
    now = datetime.now(timezone.utc)
    conversation.last_message_at = now
    conversation.updated_at = now


def post_started(
    db: Session,
    *,
    conversation: AgentConversation,
    role: Role,
    kind: str,
    total: int,
    steps: list[str],
    token: str,
) -> AgentConversationMessage:
    """Post the "batch started" message. Caller commits."""
    text = compose_started(role.name, kind, total, steps)
    card = {
        "type": CARD_BATCH_RUNNING,
        "status": "running",
        "kind": kind,
        "role_id": int(role.id),
        "total": int(total),
        "steps": steps,
        # The chat dock polls the per-role status endpoint for live counts;
        # this token ties the started card to the completion that follows.
        "token": token,
    }
    msg = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role_id=role.id,
        author_role=AUTHOR_ROLE_ASSISTANT,
        kind=MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": text}],
        text=text,
        actions=[card],
        model=None,
        stop_reason="batch_started",
    )
    db.add(msg)
    _bump(conversation)
    db.flush()
    return msg


def post_completion(
    db: Session,
    *,
    conversation: AgentConversation,
    role: Role,
    kind: str,
    counts: dict,
    token: str,
    status: str = "completed",
) -> AgentConversationMessage | None:
    """Post the "batch complete" summary, exactly once per run.

    Returns the message, or None when another path already reported this run
    (the Redis claim lost) — so the caller can treat None as "already done".
    Caller commits.
    """
    if not claim_completion(kind, int(role.id), token):
        return None

    text = compose_completion(role.name, kind, counts, status=status)
    card = {
        "type": CARD_BATCH_DONE,
        "status": status,
        "kind": kind,
        "role_id": int(role.id),
        "counts": counts,
        "token": token,
    }
    msg = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role_id=role.id,
        author_role=AUTHOR_ROLE_ASSISTANT,
        kind=MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": text}],
        text=text,
        actions=[card],
        model=None,
        stop_reason="batch_complete",
    )
    db.add(msg)
    _bump(conversation)
    db.flush()
    return msg


__all__ = [
    "KIND_PROCESS",
    "KIND_BATCH_SCORE",
    "KIND_BATCH_PRE_SCREEN",
    "CARD_BATCH_RUNNING",
    "CARD_BATCH_DONE",
    "claim_completion",
    "compose_started",
    "compose_completion",
    "post_started",
    "post_completion",
]
