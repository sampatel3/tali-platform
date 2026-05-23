"""Recruiter sends a decision back to teach the agent.

This is the third action on every pending decision (alongside approve and
override). It does three things at once:

1. Inserts a ``decision_feedback`` row capturing the reviewer's correction,
   tagged failure mode, and scope (this decision / role / org).
2. Flips the source decision's ``status`` to ``reverted_for_feedback`` so it
   reappears in the queue with the prior correction attached.
3. For ``scope`` of ``role`` or ``org`` the row is also the input to the
   nightly retune job. ``scope='org'`` requires a second admin to co-sign
   before the retune fires (``cosign_required=True``).

The actual retune pipeline (consuming ``decision_feedback`` rows and
producing ``rubric_revisions``) lives outside this action — see
``docs/HOME_HUB_DESIGN.md §5.4``. This action is only the ingestion side.

Idempotency: a teach action on a decision already in ``reverted_for_feedback``
status is allowed (replaces the prior feedback row's effect on the decision
pointer; the prior row stays in history). A teach action on a decision in
any other terminal state (``approved``, ``overridden``, ``discarded``,
``expired``) returns 409.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.decision_feedback import (
    ATTRIBUTED_TO_VALUES,
    FAILURE_MODES,
    FEEDBACK_DIRECTIONS,
    FEEDBACK_SCOPES,
    DecisionFeedback,
)
from .types import ACTOR_RECRUITER, Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    failure_mode: str,
    correction_text: str,
    scope: str,
    role_id: Optional[int] = None,
    # v2 attribution fields (architecture spec §6.5). Nullable for
    # back-compat with the legacy three-field shape; the v2 UI always
    # supplies them.
    attributed_to: Optional[str] = None,
    direction: Optional[str] = None,
    graph_write_hints: Optional[list[dict]] = None,
) -> Tuple[DecisionFeedback, AgentDecision]:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="teach is recruiter-only")
    if failure_mode not in FAILURE_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported failure_mode={failure_mode!r}",
        )
    if scope not in FEEDBACK_SCOPES:
        raise HTTPException(status_code=422, detail=f"unsupported scope={scope!r}")
    if attributed_to is not None and attributed_to not in ATTRIBUTED_TO_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported attributed_to={attributed_to!r}",
        )
    if direction is not None and direction not in FEEDBACK_DIRECTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported direction={direction!r}",
        )
    if not (correction_text or "").strip():
        raise HTTPException(status_code=422, detail="correction_text is required")

    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status not in ("pending", "reverted_for_feedback"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"agent_decision {decision_id} is {decision.status}; "
                "only pending decisions can be sent back & taught"
            ),
        )

    # ``role`` scope must carry a role_id — default to the decision's role
    # so the frontend doesn't have to repeat itself. ``org`` scope ignores
    # any role_id (always null in the row).
    resolved_role_id: Optional[int]
    if scope == "org":
        resolved_role_id = None
    elif scope == "role":
        resolved_role_id = int(role_id) if role_id is not None else int(decision.role_id)
    else:  # decision
        resolved_role_id = int(decision.role_id)

    cosign_required = scope == "org"

    feedback = DecisionFeedback(
        decision_id=int(decision.id),
        reviewer_id=int(actor.user_id),
        organization_id=int(organization_id),
        role_id=resolved_role_id,
        failure_mode=failure_mode,
        correction_text=correction_text.strip(),
        scope=scope,
        cosign_required=cosign_required,
        attributed_to=attributed_to,
        direction=direction,
        graph_write_hints=list(graph_write_hints) if graph_write_hints else None,
    )
    db.add(feedback)
    db.flush()  # populate feedback.id so we can backref it on the decision

    now = datetime.now(timezone.utc)
    decision.status = "reverted_for_feedback"
    decision.resolved_at = now
    decision.resolved_by_user_id = int(actor.user_id)
    decision.resolution_note = correction_text.strip()
    decision.feedback_id = int(feedback.id)
    decision.human_disposition = "taught"

    # Phase 3 §6.8.1: when attribution names a specific sub-agent,
    # write an exemplar so the next score-time retrieval picks it up.
    # ``policy_combination`` doesn't go here — it feeds the policy
    # fitter instead.
    if attributed_to and attributed_to != "policy_combination":
        try:
            from ..agent_runtime import exemplar_store

            features, agent_score = _features_and_agent_score_from_decision(
                decision, agent_name=attributed_to
            )
            corrected = _corrected_score_from_direction(agent_score, direction)
            exemplar_store.write_exemplar(
                db,
                feedback=feedback,
                features=features,
                agent_score=agent_score,
                corrected_score=corrected,
            )
        except Exception:
            # Exemplar writes are best-effort — never block the teach
            # action. The decision_feedback row is already the source
            # of truth; the exemplar is a derived index.
            import logging
            logging.getLogger("taali.actions.teach_decision").warning(
                "exemplar write failed for feedback_id=%s", getattr(feedback, "id", None)
            )

    # Phase 6 §6.8.3: route graph_write_hints through the writeback
    # pipeline. Low-risk hints auto-commit, medium queue for cosign,
    # high get blocked. Failures here never roll back the feedback row.
    if graph_write_hints:
        try:
            from ..graph_writeback.pipeline import write_back_from_feedback

            write_back_from_feedback(
                db, feedback=feedback, proposing_user_id=int(actor.user_id)
            )
        except Exception:
            import logging
            logging.getLogger("taali.actions.teach_decision").warning(
                "graph writeback failed for feedback_id=%s",
                getattr(feedback, "id", None),
            )

    return feedback, decision


# ---------------------------------------------------------------------------
# Exemplar-feature helpers (kept private to the teach action so the
# heuristics live with the consumer).
# ---------------------------------------------------------------------------


def _features_and_agent_score_from_decision(
    decision: AgentDecision, *, agent_name: str
) -> tuple[dict[str, float], float]:
    """Extract a canonical feature vector + the agent's original score.

    Pre-pilot decisions store sub-agent scores in
    ``decision.evidence["scores"][agent_name]``; we read whatever's
    there and let the exemplar store's cosine retriever handle
    missing keys.
    """
    evidence = decision.evidence or {}
    scores = (evidence.get("scores") or {}) if isinstance(evidence, dict) else {}
    blob = scores.get(agent_name) or {}
    agent_score = float(
        blob.get("score") or blob.get("confidence") or decision.confidence or 0.0
    )
    # Normalise to the [0, 1] scale that ``_corrected_score_from_direction``
    # (delta 0.15, clamps to 0.0/1.0) operates on. Sub-agent scores are
    # sometimes stored on a [0, 100] percentage scale; left un-normalised a
    # value like 75 collapses to 1.0 under "under" and barely moves under
    # "over". Anything already in [0, 1] is passed through untouched.
    if agent_score > 1.0:
        agent_score = agent_score / 100.0
    # Build a flat feature dict via the store helper.
    from ..agent_runtime.exemplar_store import features_from_sub_agent_output
    features = features_from_sub_agent_output(blob, agent_name=agent_name)
    return features, agent_score


def _corrected_score_from_direction(
    agent_score: float, direction: str | None
) -> float | None:
    """When the recruiter only said over/under (no explicit number),
    estimate the corrected score symmetrically. ``None`` direction →
    leave the corrected_score null and let the policy fitter see the
    raw direction tag only.
    """
    if direction is None:
        return None
    delta = 0.15  # 15% of [0..1] is a meaningful shove without being a guess
    if direction == "over":
        return max(0.0, agent_score - delta)
    if direction == "under":
        return min(1.0, agent_score + delta)
    return None
