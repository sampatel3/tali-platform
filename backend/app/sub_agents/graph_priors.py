"""Graph-priors sub-agent — v2 (Phase 2: GraphRAG).

Spec §6.2.4: this agent reasons over *paths*, not over nodes. Each
scored signal is a path (referrer track record, company overlap with
top performers, skill→role→outcome aggregates) with a weight and
temporal validity. The synthesised prior is what the policy engine
consumes.

Algorithm:
  1. If Graphiti is reachable and the candidate has a Graphiti node,
     run the four multi-hop queries in ``graphrag_queries`` (referrer
     signal, company overlap, similar candidates, skill→outcome paths)
     at the cycle's ``decision_time`` anchor.
  2. Synthesise the rows into a calibrated prior using
     ``synthesise_prior`` (weighted average over present signal
     components; confidence = signal-density).
  3. Fall back to the legacy heuristic (colleague neighbourhood +
     time decay over CandidateApplication outcomes) when:
       - Graphiti is not configured for the org, OR
       - The Cypher queries return nothing (e.g. fresh graph, no
         outcomes ingested yet).
     The fallback is preserved so the agent never blocks decisions
     during the GraphRAG rollout.
  4. Cold start: when no signal source produces output and the legacy
     heuristic is also empty, return ``confidence=0`` so the policy
     engine collapses the prior's weight to 0 cleanly.

The orchestrator owns same-cycle deduplication. This sub-agent deliberately
does not retain process-global results because graph evidence and execution
authority can change between calls.
"""

from __future__ import annotations

import logging
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
from ..models.role import ROLE_KIND_SISTER, Role
from ..platform.database import SessionLocal
from ..services.metered_async_anthropic_client import GraphProviderAdmissionError
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.graph_priors")


def clear_cycle_cache() -> None:
    """Compatibility no-op; GraphPriors no longer keeps global results."""


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

    # Read budget for the GraphRAG path. Conservative defaults — the
    # synthesiser already collapses to confidence=0 when no signal
    # source produces output, so these only affect *which* signals
    # we attempt to read. Post-cutover the actual queries run inside
    # mainspring's vendored GraphitiBackend, which holds the identical
    # 10 / 10 / 15 limits; these are retained as documentation of the
    # read budget the sub-agent expects.
    GRAPHRAG_MAX_SIMILAR = 10
    GRAPHRAG_MAX_OVERLAP_COMPANIES = 10
    GRAPHRAG_MAX_SKILL_PATHS = 15

    @staticmethod
    def _graph_backend():
        """Return the vendored mainspring GraphitiBackend (lazy singleton).

        Imported lazily so the substrate's optional graphiti-core / neo4j
        extra is only touched when the GraphRAG path actually runs; the
        backend itself imports those libs lazily too, so this never forces
        them at module load.
        """
        backend = getattr(GraphPriorsSubAgent, "_GRAPH_BACKEND", None)
        if backend is None:
            from vendor.mainspring_kg.graphiti import GraphitiBackend

            backend = GraphitiBackend()
            GraphPriorsSubAgent._GRAPH_BACKEND = backend
        return backend

    def _try_graphrag(
        self,
        req: SubAgentRequest,
        *,
        app: CandidateApplication,
        config: GraphPriorConfig,
    ) -> SubAgentResult | None:
        """Multi-hop GraphRAG path. Returns None when it can't run; a
        ``SubAgentResult`` with ``ok=True, p_advance=None`` when it ran
        but produced no signal (caller will fall through to the legacy
        heuristic); a populated result when it produced a calibrated
        prior.
        """
        # ``_run`` has already bound this application to the exact live
        # organization/role authority (including sister-role ownership).
        candidate_taali_id = int(app.candidate_id)
        role_taali_id = int(req.role_id)

        # GraphRAG queries key off the Graphiti-side candidate_id /
        # role_id properties. We use the Tali IDs as the canonical
        # identifiers (the ingestion path writes them as ``taali_id=N``
        # in the episode body, which Graphiti's extractor binds to entity
        # properties named ``candidate_id`` / ``role_id``). The vendored
        # GraphitiBackend stringifies brand_id/case_id/role_id into the
        # same group_id + candidate_id + role_id the queries match on. If
        # the graph hasn't ingested this candidate yet, the queries return
        # empty and the synthesiser produces no signal — caller falls
        # through.
        t = datetime.now(timezone.utc)

        # Referrer identity is optional and the column shape differs
        # across orgs (Workable-sourced vs manually-entered). Probe for
        # any of the known field names; missing → no referrer signal.
        referrer_id = None
        for attr in ("referrer_id", "referrer_email", "referrer"):
            value = getattr(app, attr, None)
            if value:
                referrer_id = str(value)
                break

        # ADR-0010 KG cutover: route the GraphRAG prior through mainspring's
        # vendored GraphitiBackend.get_priors instead of tali's local
        # graphrag_queries.synthesise_prior. The vendored backend runs the
        # SAME four multi-hop Cypher queries (character-identical port, same
        # limits and temporal anchor) and the SAME synthesise_prior, so over
        # the same Neo4j graph the prior is identical by construction. We
        # still probe the app for the referrer id (tali-specific column
        # knowledge) and pass it in; everything else the backend derives.
        try:
            with graph_search.graph_provider_context(
                int(req.organization_id),
                "graph_priors",
                role_id=role_taali_id,
                require_role_authority=True,
            ):
                priors = self._graph_backend().get_priors(
                    brand_id=int(req.organization_id),
                    case_id=candidate_taali_id,
                    role_id=role_taali_id,
                    referrer_id=referrer_id,
                    as_of=t,
                )
        except GraphProviderAdmissionError:
            raise
        except Exception as exc:  # pragma: no cover — backend never raises, defensive
            logger.warning("vendored GraphitiBackend.get_priors failed: %s", exc)
            return None

        # Adapt mainspring's Priors back to tali's expected dict shape so the
        # downstream policy weighted-scoring is UNCHANGED. The backend maps the
        # synthesiser's "no graph signal" sentinel onto Priors.empty (empty
        # examples / zero confidence), which we translate back to p_advance=None
        # so the caller falls through to the legacy heuristic exactly as before.
        components = list(priors.examples or [])
        if not components:
            # GraphRAG ran but produced nothing — let the caller fall
            # through to the legacy heuristic.
            return SubAgentResult(
                sub_agent=self.name,
                ok=True,
                output={
                    "p_advance": None,
                    "confidence": 0.0,
                    "synthesis_note": "no graph paths produced any signal",
                },
                confidence=0.0,
            )

        p_advance = float(priors.p_advance)
        confidence = float(priors.confidence)

        # GraphRAG calibrated uncertainty: 1 - confidence is a reasonable
        # initial mapping until Phase 3 isotonic calibration produces a
        # better number from realised outcomes.
        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output={
                "p_advance": p_advance,
                "p_hired": p_advance,
                "neighbour_count": int(priors.neighbour_count),
                "confidence": confidence,
                "components": components,
                "source": "graphrag",
            },
            confidence=confidence,
            uncertainty=max(0.0, min(1.0, 1.0 - confidence)),
            citations=[
                {
                    "node_ids": [],
                    "edge_ids": [],
                    "summary": c.get("summary", ""),
                }
                for c in components
            ],
        )

    def run(
        self, req: SubAgentRequest, *, db: Session | None = None
    ) -> SubAgentResult:
        session = db or SessionLocal()
        owns = db is None
        try:
            result = self._run(req, session)
        except GraphProviderAdmissionError:
            # A live Pause/disable decision is execution authority, not an empty
            # graph signal. Let the autonomous boundary abort the cycle.
            raise
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("graph_priors sub-agent crashed")
            result = SubAgentResult(
                sub_agent=self.name, ok=False, error=f"unexpected: {exc}"
            )
        finally:
            if owns:
                session.close()
        return result

    def _run(self, req: SubAgentRequest, db: Session) -> SubAgentResult:
        role = (
            db.query(Role)
            .filter(
                Role.id == int(req.role_id),
                Role.organization_id == int(req.organization_id),
                Role.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if role is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"role {req.role_id} not found",
            )

        expected_application_role_id = int(role.id)
        if str(role.role_kind or "") == ROLE_KIND_SISTER:
            if role.ats_owner_role_id is None:
                return SubAgentResult(
                    sub_agent=self.name,
                    ok=False,
                    error=f"sister role {req.role_id} has no ATS owner role",
                )
            expected_application_role_id = int(role.ats_owner_role_id)
            owner_exists = (
                db.query(Role.id)
                .filter(
                    Role.id == expected_application_role_id,
                    Role.organization_id == int(req.organization_id),
                    Role.deleted_at.is_(None),
                )
                .one_or_none()
            )
            if owner_exists is None:
                return SubAgentResult(
                    sub_agent=self.name,
                    ok=False,
                    error=f"ATS owner role {expected_application_role_id} not found",
                )

        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(req.application_id),
                CandidateApplication.organization_id == int(req.organization_id),
                CandidateApplication.role_id == expected_application_role_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if app is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=(
                    f"application {req.application_id} not found for role "
                    f"{expected_application_role_id}"
                ),
            )

        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.id == int(app.candidate_id),
                Candidate.organization_id == int(req.organization_id),
                Candidate.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if candidate is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"candidate {app.candidate_id} not found",
            )

        config = _resolve_graph_config(
            db,
            organization_id=int(req.organization_id),
            role_id=int(req.role_id),
        )
        if not config.enabled:
            return _empty_result("graph priors disabled in policy")
        if not graph_client.is_configured():
            return _empty_result("graph not configured for this org")

        # GraphRAG path (Phase 2) — try multi-hop queries first. The
        # heuristic path remains as a fallback so this rolls out
        # without breaking organisations whose graph is sparse.
        graphrag = self._try_graphrag(req, app=app, config=config)
        if graphrag is not None and graphrag.ok and (graphrag.output.get("p_advance") is not None):
            return graphrag

        # 1. Neighbourhood payload (cheap on graph hit; cached upstream).
        try:
            neigh = graph_search.colleague_neighbourhood(
                organization_id=int(req.organization_id),
                candidate_id=int(candidate.id),
                role_id=int(req.role_id),
                require_role_authority=True,
                max_companies=int(config.neighbourhood_size),
            )
        except GraphProviderAdmissionError:
            raise
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
                role_id=int(req.role_id),
                require_role_authority=True,
            )
        except GraphProviderAdmissionError:
            raise
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
                Role.organization_id == req.organization_id,
                Role.deleted_at.is_(None),
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
        confidence = min(
            1.0,
            effective_count / max(1, 2 * int(config.min_neighbours_for_prior)),
        )
        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output={
                "p_advance": float(p_advance),
                "p_hired": float(p_advance),  # same proxy in v1
                "neighbour_count": int(effective_count),
                "neighbour_ids": [int(a.id) for a, _ in same_family[:50]],
                "confidence": confidence,
                "source": "heuristic",
            },
            confidence=confidence,
            uncertainty=max(0.0, min(1.0, 1.0 - confidence)),
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
