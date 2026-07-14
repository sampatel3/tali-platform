"""Deterministic bulk decisioning — give EVERY scored candidate a verdict.

The decision-policy engine verdict is fully deterministic, so we don't
need the bounded LLM cycle to work through a large cohort one candidate at a
time. This pass runs the
engine over every undecided, scored, open candidate using the scores
ALREADY stored on the application — no sub-agents, no Anthropic calls —
and queues the verdict through the normal ``queue_decision`` guard stack
(one-pending-per-app, cross-cycle dedup, terminal-state refusal).

Coverage is EVERY scored candidate, not just pre-screen-passers. A scored
candidate below the pre-screen line is owned by nobody else: the
pre-screen reject emitter defers once a candidate is cv_match-scored
("agent owns the cv_match decision"), so without this pass it can only be
decided by the LLM — and strands when the LLM is unreachable. Banding the
engine on role-fit covers it deterministically.

Banding (after the effective-threshold overlay collapses the boundary):
  - role_fit < threshold              -> reject
  - role_fit >= threshold, has task   -> send_assessment   (needs pre_screen >= 50)
  - role_fit >= threshold, no task    -> advance_to_interview (needs pre_screen >= 50)
  - role_fit >= threshold, pre_screen < 50 -> no_action (left to LLM/recruiter)

The send_assessment rule independently gates on ``pre_screen_min`` (50),
which ``apply_effective_threshold`` leaves untouched — so a low-pre-screen
candidate can never be auto-sent/advanced; it either rejects on role-fit
or falls through to ``no_action``. The LLM agent still runs afterward for
those judgment/abstention/recruiter cases; ``find_apps_in_state`` excludes
apps that now have a pending decision, so there's no double-queue.

This module was split into cohesive submodules to stay within the
api/service file-size gate. The public surface below is unchanged: import
from ``app.services.bulk_decision_service`` exactly as before.
"""
from __future__ import annotations

from ._shared import recompute_persisted_verdict
from .auto_correct import auto_correct_stale_verdict
from .cohort import (
    DEFAULT_PER_TICK_LIMIT,
    VOLUME_GUARD_PENDING_LIMIT,
    decide_role_cohort,
)
from .post_handover import decide_post_handover
from .score_time import ensure_deterministic_decision
from .stage_toggle import reconcile_pending_positive_decisions

__all__ = [
    "DEFAULT_PER_TICK_LIMIT",
    "VOLUME_GUARD_PENDING_LIMIT",
    "auto_correct_stale_verdict",
    "decide_post_handover",
    "decide_role_cohort",
    "ensure_deterministic_decision",
    "recompute_persisted_verdict",
    "reconcile_pending_positive_decisions",
]
