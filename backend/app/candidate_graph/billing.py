"""DEPRECATED 2026-05-26 — heuristic Graphiti billing.

This module estimated Graphiti episode cost from ``len(body) // 4``,
which massively under-counted (Graphiti's actual extraction prompts
are 15-30k tokens). On 2026-05-23 the heuristic hid 16M of 19M Haiku
input tokens behind a $0.41 estimate, producing -41% reconciliation
drift on Haiku.

The replacement is ``services/metered_async_anthropic_client``:
``MeteredAsyncAnthropic`` wraps Graphiti's underlying ``AsyncAnthropic``
and writes a ``claude_call_log`` row PER REAL Anthropic call with
exact tokens from ``response.usage``. When ``graph_metering_ctx`` is
set by the dispatch path it also writes a ``usage_event`` so the
spend flows into the org's role budget the same way it always did,
but with real numbers.

This module is kept only so external callers that imported it
continue to load; ``record_episode_cost`` is now a no-op that logs a
deprecation warning. Once we confirm no caller invokes it (search:
``grep -rn record_episode_cost``), delete the whole file.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger("taali.candidate_graph.billing")

EXTRACTION_PROMPT_OVERHEAD_TOKENS = 800
EXTRACTION_OUTPUT_TOKENS = 400


def estimate_episode_tokens(body: str) -> tuple[int, int]:
    body_tokens = max(1, len(body or "") // 4)
    return body_tokens + EXTRACTION_PROMPT_OVERHEAD_TOKENS, EXTRACTION_OUTPUT_TOKENS


def record_episode_cost(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
    user_id: int | None,
    candidate_id: int | None,
    episode_name: str,
    episode_body: str,
) -> None:
    """DEPRECATED — no-op. Real metering now happens in
    ``services/metered_async_anthropic_client``, which wraps Graphiti's
    underlying ``AsyncAnthropic`` and writes a claude_call_log row per
    real Anthropic call with exact tokens. See module docstring.
    """
    logger.warning(
        "candidate_graph.billing.record_episode_cost is deprecated and "
        "no longer records spend (called for episode=%s, org=%s). "
        "Real metering is in MeteredAsyncAnthropic since 2026-05-26.",
        episode_name, organization_id,
    )
    # Reference unused params to keep linters quiet; this function
    # is a deliberate no-op pending deletion.
    _ = (db, role_id, user_id, candidate_id, episode_body)
