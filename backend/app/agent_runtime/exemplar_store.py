"""Per-agent exemplar store — Phase 3 §6.8.1 of the architecture spec.

Each teach event that attributed to a specific sub-agent becomes an
exemplar — a snapshot of the candidate features the agent saw, plus the
recruiter's correction. At score time the agent retrieves top-k similar
exemplars and injects them as few-shot.

Similarity is plain cosine over the JSON feature vector. Pre-pilot
volumes (<500 rows per agent per role) don't justify pgvector; the
nightly D4 eviction keeps the table bounded.

Write path:
  ``write_exemplar(db, feedback, features, agent_score, corrected_score)``
  is called from the teach action when ``attributed_to`` names a
  specific sub-agent (not ``policy_combination``).

Retrieval path:
  ``retrieve_top_k(db, agent_name, query_features, org, role, k=3)``
  returns ranked ``AgentExemplar`` rows + similarity floats. The
  retrieved rows have their ``use_count`` incremented and
  ``last_used_at`` updated atomically.

D4 eviction:
  ``evict_overflow(db, agent_name, org, role, cap=500)`` runs nightly.
  Sort by ``-age_in_days + correction_magnitude*30 + use_count*5``,
  drop the bottom of the list when over cap.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..models.agent_exemplar import AgentExemplar
from ..models.decision_feedback import DecisionFeedback


logger = logging.getLogger("taali.agent_runtime.exemplar_store")


DEFAULT_CAP_PER_AGENT = 500


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def write_exemplar(
    db: Session,
    *,
    feedback: DecisionFeedback,
    features: dict[str, float],
    agent_score: float,
    corrected_score: float | None,
) -> AgentExemplar | None:
    """Persist one exemplar row for the attributed sub-agent.

    Returns the row, or ``None`` when no exemplar should be written
    (attribution missing or set to ``policy_combination`` — the latter
    feeds the policy fitter, not a per-agent store).
    """
    agent_name = feedback.attributed_to
    if not agent_name or agent_name == "policy_combination":
        return None
    row = AgentExemplar(
        organization_id=int(feedback.organization_id),
        role_id=int(feedback.role_id) if feedback.role_id else None,
        agent_name=str(agent_name),
        source_feedback_id=int(feedback.id),
        features_json=dict(features or {}),
        agent_score=float(agent_score),
        corrected_score=(float(corrected_score) if corrected_score is not None else None),
        direction=feedback.direction,
        attributed_reason=feedback.correction_text,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity over the union of keys; missing keys treated as 0.

    Returns 0.0 when either vector has zero norm.
    """
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(float(a.get(k, 0.0)) * float(b.get(k, 0.0)) for k in keys)
    na = math.sqrt(sum(float(a.get(k, 0.0)) ** 2 for k in keys))
    nb = math.sqrt(sum(float(b.get(k, 0.0)) ** 2 for k in keys))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def retrieve_top_k(
    db: Session,
    *,
    agent_name: str,
    organization_id: int,
    role_id: int | None,
    query_features: dict[str, float],
    k: int = 3,
    fallback_org_wide: bool = True,
) -> list[tuple[AgentExemplar, float]]:
    """Return ``[(exemplar, similarity), ...]`` ranked by cosine similarity.

    Strategy:
    1. Query exemplars for this (agent, org, role).
    2. If fewer than ``k`` exist and ``fallback_org_wide`` is set, also
       pull org-wide rows (role_id IS NULL OR != role_id) so the cold-
       start case has *some* few-shot to inject.
    3. Compute cosine in-process and return the top ``k``.
    4. Increment ``use_count`` and stamp ``last_used_at`` on the
       returned rows.

    Pre-pilot volumes (<500 rows per agent per role) keep this fast.
    """
    rows: list[AgentExemplar] = list(
        db.query(AgentExemplar)
        .filter(
            AgentExemplar.organization_id == organization_id,
            AgentExemplar.agent_name == agent_name,
            AgentExemplar.role_id == role_id,
        )
        .order_by(desc(AgentExemplar.created_at))
        .limit(500)
        .all()
    )
    if len(rows) < k and fallback_org_wide:
        more = (
            db.query(AgentExemplar)
            .filter(
                AgentExemplar.organization_id == organization_id,
                AgentExemplar.agent_name == agent_name,
                AgentExemplar.role_id != role_id,
            )
            .order_by(desc(AgentExemplar.created_at))
            .limit(500)
            .all()
        )
        # Append without duplicates on id.
        existing_ids = {r.id for r in rows}
        rows.extend(r for r in more if r.id not in existing_ids)

    scored: list[tuple[AgentExemplar, float]] = []
    for row in rows:
        sim = _cosine(query_features, row.features_json or {})
        scored.append((row, sim))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[: max(0, int(k))]

    if top:
        now = datetime.now(timezone.utc)
        for exemplar, _ in top:
            exemplar.use_count = int(exemplar.use_count or 0) + 1
            exemplar.last_used_at = now
        try:
            db.flush()
        except Exception as exc:
            logger.warning("failed to update use_count: %s", exc)

    return top


# ---------------------------------------------------------------------------
# Eviction (nightly job)
# ---------------------------------------------------------------------------


def _eviction_score(row: AgentExemplar, *, now: datetime) -> float:
    """Higher = keep. Matches D4 from section_10_decisions.md (sign-
    flipped so we can sort descending and trim the tail).

    score = -age_in_days + correction_magnitude*30 + use_count*5
    """
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = ((now - created).total_seconds() / 86400.0) if created else 0.0
    corrected = row.corrected_score
    correction_magnitude = (
        abs(float(corrected) - float(row.agent_score))
        if corrected is not None
        else 0.0
    )
    return (
        -age_days
        + correction_magnitude * 30.0
        + int(row.use_count or 0) * 5.0
    )


def evict_overflow(
    db: Session,
    *,
    agent_name: str,
    organization_id: int,
    role_id: int | None,
    cap: int = DEFAULT_CAP_PER_AGENT,
) -> int:
    """Drop the lowest-scoring rows when the store exceeds ``cap``.

    Returns the number of rows deleted.
    """
    rows = (
        db.query(AgentExemplar)
        .filter(
            AgentExemplar.organization_id == organization_id,
            AgentExemplar.agent_name == agent_name,
            AgentExemplar.role_id == role_id,
        )
        .all()
    )
    if len(rows) <= cap:
        return 0
    now = datetime.now(timezone.utc)
    scored = sorted(rows, key=lambda r: _eviction_score(r, now=now), reverse=True)
    # Keep the top-``cap``; drop the rest.
    to_drop = scored[cap:]
    for row in to_drop:
        db.delete(row)
    db.flush()
    return len(to_drop)


# ---------------------------------------------------------------------------
# Feature extraction helper — turn a SubAgentResult.output blob into a
# canonical feature dict the cosine retriever can use. Phase 3 wires
# the orchestrator into this; sub-agents that don't emit a feature blob
# fall through to a single-key vector (score only).
# ---------------------------------------------------------------------------


def features_from_sub_agent_output(
    output: dict, *, agent_name: str
) -> dict[str, float]:
    """Project a sub-agent's output dict to a flat feature vector.

    Conventions:
      - Numeric scalars stay as-is.
      - Boolean flags become 0.0 / 1.0.
      - Nested dicts contribute their numeric leaves keyed
        ``"<parent>_<child>"``.
      - Lists are summarised by length (``"<key>__n"``).
    """
    feats: dict[str, float] = {}

    def _emit(key: str, value):
        if isinstance(value, bool):
            feats[key] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            feats[key] = float(value)
        elif isinstance(value, dict):
            for k2, v2 in value.items():
                _emit(f"{key}_{k2}", v2)
        elif isinstance(value, list):
            feats[f"{key}__n"] = float(len(value))

    for k, v in (output or {}).items():
        _emit(k, v)
    # Always include an ``agent_name_hash`` so cross-agent confusion is
    # impossible at retrieval time (cosine over different agents'
    # vectors would still match on shared keys).
    feats[f"agent_{agent_name}"] = 1.0
    return feats


def render_exemplars_for_prompt(
    db: Session,
    *,
    agent_name: str,
    organization_id: int,
    role_id: int | None,
    query_features: dict[str, float],
    k: int = 2,
) -> str:
    """Build the few-shot block a sub-agent prepends to its prompt.

    Returns the empty string when no exemplars exist for this
    (agent, org, role) — the prompt then has no overhead and the call
    is indistinguishable from a pre-exemplar run.

    k defaults to 2 to bound the per-call token cost. Each exemplar
    contributes roughly 200-400 tokens depending on the recruiter's
    correction text length; capped at k=2 the few-shot overhead is
    ~500 tokens per call which the prompt cache will amortise after
    the first round.

    Cost guard: a cheap COUNT() check first so we don't pay the cosine
    walk on an empty store (the common pre-pilot case).
    """
    # Cheap pre-check: skip retrieval if the store is empty for this
    # (agent, org). Avoids cosine work on every call until the first
    # teach event for this agent has been recorded.
    if not _store_has_rows(db, agent_name=agent_name, organization_id=organization_id):
        return ""
    hits = retrieve_top_k(
        db,
        agent_name=agent_name,
        organization_id=organization_id,
        role_id=role_id,
        query_features=query_features,
        k=k,
        fallback_org_wide=True,
    )
    if not hits:
        return ""
    blocks: list[str] = [
        "### Past corrections to learn from",
        (
            "These are recruiter teach events on candidates similar to "
            "the one you are scoring now. The recruiter said the agent's "
            "score was wrong in the direction shown. Calibrate your "
            "current score accordingly."
        ),
    ]
    for i, (exemplar, similarity) in enumerate(hits, start=1):
        agent_score = float(exemplar.agent_score or 0.0)
        corrected = (
            f"{float(exemplar.corrected_score):.2f}"
            if exemplar.corrected_score is not None
            else "unspecified"
        )
        direction = exemplar.direction or "unknown direction"
        reason = (exemplar.attributed_reason or "").strip()
        if len(reason) > 400:
            reason = reason[:400] + "…"
        blocks.append(
            f"Example {i} (similarity={similarity:.2f}): "
            f"agent scored {agent_score:.2f}, recruiter corrected to "
            f"{corrected} ({direction}). Reason: {reason or '(none)'}"
        )
    return "\n".join(blocks)


def _store_has_rows(
    db: Session, *, agent_name: str, organization_id: int
) -> bool:
    """Cheap existence check for the cost-guard pre-filter."""
    try:
        return (
            db.query(AgentExemplar.id)
            .filter(
                AgentExemplar.organization_id == organization_id,
                AgentExemplar.agent_name == agent_name,
            )
            .limit(1)
            .first()
            is not None
        )
    except Exception:
        return False


__all__ = [
    "DEFAULT_CAP_PER_AGENT",
    "evict_overflow",
    "features_from_sub_agent_output",
    "render_exemplars_for_prompt",
    "retrieve_top_k",
    "write_exemplar",
]
