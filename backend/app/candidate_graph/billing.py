"""Estimate and record Graphiti episode cost into UsageEvent.

Graphiti's Anthropic client makes LLM calls inside ``add_episode`` that we
can't directly observe (no token counts come back). We estimate cost per
episode from body length:

  input_tokens  ≈ len(body) // 4 + EXTRACTION_PROMPT_OVERHEAD_TOKENS
  output_tokens ≈ EXTRACTION_OUTPUT_TOKENS

The estimate is rough but lets the spend flow into the role's monthly
budget so recruiters see semantic-search cost alongside scoring and
pre-screen. Reconcile against Anthropic's bill periodically and tune
the constants.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..platform.config import settings
from ..services.pricing_service import Feature
from ..services.usage_metering_service import record_event

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
    input_tokens, output_tokens = estimate_episode_tokens(episode_body)
    try:
        record_event(
            db,
            organization_id=organization_id,
            feature=Feature.GRAPH_SYNC,
            model=settings.GRAPHITI_LLM_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user_id=user_id,
            role_id=role_id,
            entity_id=str(candidate_id) if candidate_id else None,
            metadata={"episode": episode_name, "estimate": True},
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to record graph_sync usage for %s", episode_name)
