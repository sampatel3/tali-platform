"""Graph-priors sub-agent.

Composes existing graph search adapters — no new Cypher/Graphiti
queries — into a per-application "p_advance prior".

Algorithm:
  1. Pull the candidate's neighbourhood via
     ``candidate_graph.search.colleague_neighbourhood``.
  2. Translate the company / school / skill anchors into structured
     ``GraphPredicate``s and call
     ``candidate_graph.search.candidate_ids_matching_all`` to find
     intersection candidates in the same org.
  3. Filter to candidates in the same role family (uses
     ``cv_matching/calibrators/extractor._default_role_family_mapper``).
  4. For each remaining neighbour, look up the recruiter outcome from
     ``CandidateApplication.application_outcome`` + recent
     ``CandidateApplicationEvent`` history.
  5. Apply time decay: ``weight = max(0, 1 - days_since / decay_days)``.
  6. ``p_advance = sum(weight * advanced_label) / sum(weight)``.
  7. Cold start: when the effective neighbour count is below
     ``min_neighbours_for_prior`` (read from the active policy's
     ``graph_prior_config``), return ``confidence=0`` so the engine
     degrades the prior weight to 0 cleanly.

Per-cycle in-memory cache prevents re-running graph queries when the
orchestrator calls ``evaluate_policy`` more than once for the same
application within a cycle.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..candidate_graph import search as graph_search
from ..candidate_graph import client as graph_client
from ..candidate_search.schemas import GraphPredicate
from ..cv_matching.calibrators.extractor import _default_role_family_mapper
from ..decision_policy.engine import load_active_policy
from ..decision_policy.schema import GraphPriorConfig, PolicyJson
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.database import SessionLocal
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.graph_priors")


# Cycle-scoped cache: keyed by (application_id, role_id). The
# orchestrator clears it via ``clear_cycle_cache`` between agent cycles
# (see Phase 5 retune integration); within a cycle it short-circuits
# repeat calls.
_CYCLE_CACHE: dict[tuple[int, int], SubAgentResult] = {}
_CACHE_LOCK = threading.Lock()


def clear_cycle_cache() -> None:
    """Reset the per-cycle in-memory cache. Called by the orchestrator
    at cycle entry so a long-running worker doesn't carry stale priors.
    """
    with _CACHE_LOCK:
        _CYCLE_CACHE.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_graph_config(
    db: Session, *, organization_id: int, role_id: int
) -> GraphPriorConfig:
    """Fall back to a stable default when no policy is loaded yet."""
    try:
        row = load_active_policy(
            db, organization_id=organization_id, role_id=role_id
        )
        policy = PolicyJson.model_validate(row.policy_json or {})
        return policy.graph_prior_config
    except Exception:
        return GraphPriorConfig()


def _outcome_to_advanced_label(outcome: str | None) -> float | None:
    """1.0 if the candidate's outcome counts as advanced, 0.0 if not,
    None if we shouldn't use this neighbour as signal at all (still
    open).
    """
    o = (outcome or "").lower()
    if o in {"hired"}:
        return 1.0
    if o in {"rejected"}:
        return 0.0
    return None


def _decay_weight(
    decided_at: datetime | None, *, decay_days: int, now: datetime
) -> float:
    if decided_at is None or decay_days <= 0:
        return 0.0
    if decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - decided_at).total_seconds() / 86400.0)
    return max(0.0, 1.0 - (days / float(decay_days)))


def _candidate_predicates(neigh: dict[str, Any]) -> list[GraphPredicate]:
    """Turn the colleague_neighbourhood payload into intersection
    predicates for ``candidate_ids_matching_all``. We only use the
    company anchors — schools/skills are too broad and would dilute
    the cohort.
    """
    predicates: list[GraphPredicate] = []
    for company in (neigh.get("companies") or [])[:3]:
        name = (company.get("name") or "").strip()
        if name:
            predicates.append(GraphPredicate(type="worked_at", value=name))
    return predicates


# ---------------------------------------------------------------------------
# Sub-agent
# ---------------------------------------------------------------------------


class GraphPriorsSubAgent:
    name = "graph_priors"

    def run(
        self, req: SubAgentRequest, *, db: Session | None = None
    ) -> SubAgentResult:
        cache_key = (int(req.application_id), int(req.role_id))
        if not req.skip_cache:
            with _CACHE_LOCK:
                hit = _CYCLE_CACHE.get(cache_key)
            if hit is not None:
                return SubAgentResult(
                    sub_agent=self.name,
                    ok=hit.ok,
                    output=dict(hit.output),
                    confidence=hit.confidence,
                    cache_hit=True,
                )

        session = db or SessionLocal()
        owns = db is None
        try:
            result = self._run(req, session)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("graph_priors sub-agent crashed")
            result = SubAgentResult(
                sub_agent=self.name, ok=False, error=f"unexpected: {exc}"
            )
        finally:
            if owns:
                session.close()

        with _CACHE_LOCK:
            _CYCLE_CACHE[cache_key] = result
        return result

    def _run(self, req: SubAgentRequest, db: Session) -> SubAgentResult:
        config = _resolve_graph_config(
            db,
            organization_id=int(req.organization_id),
            role_id=int(req.role_id),
        )
        if not config.enabled:
            return _empty_result("graph priors disabled in policy")
        if not graph_client.is_configured():
            return _empty_result("graph not configured for this org")

        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == req.application_id,
                CandidateApplication.organization_id == req.organization_id,
            )
            .one_or_none()
        )
        if app is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"application {req.application_id} not found",
            )
        candidate = (
            db.query(Candidate).filter(Candidate.id == app.candidate_id).one_or_none()
        )
        role = db.query(Role).filter(Role.id == req.role_id).one_or_none()
        if candidate is None or role is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="candidate or role not found",
            )

        # 1. Neighbourhood payload (cheap on graph hit; cached upstream).
        try:
            neigh = graph_search.colleague_neighbourhood(
                organization_id=int(req.organization_id),
                candidate_id=int(candidate.id),
                max_companies=int(config.neighbourhood_size),
            )
        except Exception as exc:
            logger.warning("colleague_neighbourhood crashed: %s", exc)
            return _empty_result(f"neighbourhood error: {exc}")

        predicates = _candidate_predicates(neigh)
        if not predicates:
            return _empty_result("no graph anchors for candidate")

        # 2. Intersection — candidates in the org sharing all anchors.
        try:
            neighbour_ids = graph_search.candidate_ids_matching_all(
                organization_id=int(req.organization_id),
                predicates=predicates,
            )
        except Exception as exc:
            logger.warning("candidate_ids_matching_all crashed: %s", exc)
            return _empty_result(f"intersection error: {exc}")
        # Drop self-reference.
        neighbour_ids = [int(i) for i in neighbour_ids if int(i) != int(candidate.id)]
        if not neighbour_ids:
            return _empty_result("zero neighbours")

        # 3. Same-role-family filter using the existing slugifier.
        target_family = _default_role_family_mapper(role.name)
        applications = (
            db.query(CandidateApplication, Role.name)
            .join(Role, Role.id == CandidateApplication.role_id)
            .filter(
                CandidateApplication.organization_id == req.organization_id,
                CandidateApplication.candidate_id.in_(neighbour_ids),
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )
        same_family = [
            (app_row, role_name)
            for app_row, role_name in applications
            if _default_role_family_mapper(role_name) == target_family
        ]

        # 4. Outcome → advanced label + 5. decay.
        now = datetime.now(timezone.utc)
        weighted_sum = 0.0
        weight_sum = 0.0
        effective_count = 0
        for app_row, _role_name in same_family:
            label = _outcome_to_advanced_label(app_row.application_outcome)
            if label is None:
                continue
            decided_at = (
                app_row.application_outcome_updated_at
                or app_row.updated_at
                or app_row.created_at
            )
            weight = _decay_weight(decided_at, decay_days=config.decay_days, now=now)
            if weight <= 0.0:
                continue
            weighted_sum += weight * label
            weight_sum += weight
            effective_count += 1

        if (
            effective_count < int(config.min_neighbours_for_prior)
            or weight_sum <= 0.0
        ):
            return _empty_result(
                f"cold start: effective_neighbour_count={effective_count} "
                f"< min_neighbours_for_prior={config.min_neighbours_for_prior}"
            )

        p_advance = weighted_sum / weight_sum
        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output={
                "p_advance": float(p_advance),
                "p_hired": float(p_advance),  # same proxy in v1
                "neighbour_count": int(effective_count),
                "neighbour_ids": [int(a.id) for a, _ in same_family[:50]],
                "confidence": min(
                    1.0,
                    effective_count / max(1, 2 * int(config.min_neighbours_for_prior)),
                ),
            },
            confidence=min(
                1.0,
                effective_count / max(1, 2 * int(config.min_neighbours_for_prior)),
            ),
            cache_hit=False,
        )


def _empty_result(reason: str) -> SubAgentResult:
    return SubAgentResult(
        sub_agent="graph_priors",
        ok=True,
        output={
            "p_advance": None,
            "p_hired": None,
            "neighbour_count": 0,
            "neighbour_ids": [],
            "confidence": 0.0,
            "reason": reason,
        },
        confidence=0.0,
        cache_hit=False,
    )


GRAPH_PRIORS_SUB_AGENT: SubAgent = GraphPriorsSubAgent()
register_sub_agent(GRAPH_PRIORS_SUB_AGENT)


__all__ = [
    "GRAPH_PRIORS_SUB_AGENT",
    "GraphPriorsSubAgent",
    "clear_cycle_cache",
]
