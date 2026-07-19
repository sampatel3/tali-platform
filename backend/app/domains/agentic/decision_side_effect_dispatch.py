"""Compatibility home for deferred decision side-effect dispatch."""

from __future__ import annotations

import logging
from typing import Optional


logger = logging.getLogger("taali.agentic.routes")


def enqueue_decision_side_effects(
    decision_id: int,
    *,
    workable_target_stage: Optional[str],
    reject_notify: bool,
) -> None:
    """Best-effort dispatch after the canonical decision state is committed."""

    try:
        from ...tasks.decision_tasks import apply_decision_side_effects

        apply_decision_side_effects.delay(
            int(decision_id),
            workable_target_stage=workable_target_stage,
            reject_notify=bool(reject_notify),
        )
    except Exception:  # pragma: no cover - defensive compatibility path
        logger.warning(
            "failed to enqueue decision side effects decision_id=%s",
            decision_id,
            exc_info=True,
        )


__all__ = ["enqueue_decision_side_effects"]
