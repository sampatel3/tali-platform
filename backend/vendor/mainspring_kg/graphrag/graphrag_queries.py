"""Multi-hop Cypher queries used by Phase 2+ sub-agents.

Companion to ``sub_agent_graph_queries.md``. Every query takes an
explicit temporal anchor ``t`` so we get reproducible audits ("what did
the system see when it scored this candidate three months ago?") and
no leakage during retraining.

All queries run via the shared Graphiti driver
(``client.run_async`` + ``graphiti.driver.execute_query``). Failures
degrade silently to empty lists — every consumer must treat an empty
return as "no graph signal" rather than "Cypher failed", because we
never want a Graphiti hiccup to crash an agent cycle.

Each function returns a list of dicts (Cypher records flattened).
Property names match the field names recruiters/auditors expect in the
``structured_evidence`` panels — keep them stable; the UI keys off them.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from . import client as graph_client


logger = logging.getLogger("taali.candidate_graph.graphrag_queries")


def _execute(query: str, **params: Any) -> list[dict[str, Any]]:
    """Run a Cypher query on the shared Graphiti driver.

    Returns a list of record dicts. Returns ``[]`` on any failure —
    sub-agents must treat that as "no signal" rather than "error".
    """
    if not graph_client.is_configured():
        return []
    try:
        graphiti = graph_client.get_graphiti()
    except Exception as exc:
        logger.warning("graphiti unavailable: %s", exc)
        return []

    async def _run() -> list[dict[str, Any]]:
        try:
            result = await graphiti.driver.execute_query(query, **params)
        except Exception as exc:
            logger.warning("Cypher failed: %s\n%s", exc, query[:200])
            return []
        # Graphiti's driver returns (records, summary, keys) for Neo4j-style
        # drivers and a plain list for some others. Normalise.
        records = result[0] if isinstance(result, tuple) else result
        out: list[dict[str, Any]] = []
        for r in records or []:
            if hasattr(r, "data"):
                out.append(r.data())
            elif isinstance(r, dict):
                out.append(dict(r))
            else:
                try:
                    out.append(dict(r))
                except Exception:
                    continue
        return out

    try:
        return graph_client.run_async(_run(), timeout=30.0)
    except Exception as exc:
        logger.warning("graphiti run_async failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Pre-screen queries (§1 of sub_agent_graph_queries.md)
# ---------------------------------------------------------------------------


def role_must_haves(
    *, group_id: str, role_id: str, t: dt.datetime
) -> list[dict[str, Any]]:
    """Mandatory requirements of a role at time ``t``.

    Used by pre_screen.
    """
    return _execute(
        """
        MATCH (r:Role {role_id: $role_id, group_id: $group_id})
              -[req:REQUIRES]->(s:Skill)
        WHERE coalesce(req.mandatory, false) = true
          AND (req.valid_from IS NULL OR req.valid_from <= $t)
          AND (req.valid_to   IS NULL OR req.valid_to   >  $t)
        RETURN coalesce(s.skill_id, s.uuid) AS skill_id,
               s.name AS name,
               req.notes AS notes
        """,
        role_id=role_id,
        group_id=group_id,
        t=t,
    )


def candidate_claimed_skills(
    *, group_id: str, candidate_id: str, t: dt.datetime
) -> list[dict[str, Any]]:
    """Skills the candidate declared (HAS_SKILL edges)."""
    return _execute(
        """
        MATCH (c:Candidate {candidate_id: $candidate_id, group_id: $group_id})
              -[h:HAS_SKILL]->(s:Skill)
        WHERE (h.valid_from IS NULL OR h.valid_from <= $t)
          AND (h.valid_to   IS NULL OR h.valid_to   >  $t)
        RETURN coalesce(s.skill_id, s.uuid) AS skill_id,
               s.name AS name,
               h.evidence AS evidence,
               h.years AS years
        """,
        candidate_id=candidate_id,
        group_id=group_id,
        t=t,
    )


# ---------------------------------------------------------------------------
# CV scoring queries (§2)
# ---------------------------------------------------------------------------


def role_requirements_weighted(
    *, group_id: str, role_id: str, t: dt.datetime
) -> list[dict[str, Any]]:
    """Weighted requirements for the role — used by cv_scoring."""
    return _execute(
        """
        MATCH (r:Role {role_id: $role_id, group_id: $group_id})
              -[req:REQUIRES]->(s:Skill)
        WHERE (req.valid_from IS NULL OR req.valid_from <= $t)
          AND (req.valid_to   IS NULL OR req.valid_to   >  $t)
        RETURN coalesce(s.skill_id, s.uuid) AS skill_id,
               s.name AS skill,
               req.weight AS weight,
               coalesce(req.mandatory, false) AS mandatory
        ORDER BY coalesce(req.weight, 0) DESC
        """,
        role_id=role_id,
        group_id=group_id,
        t=t,
    )


def successful_skill_patterns(
    *,
    group_id: str,
    role_id: str,
    t: dt.datetime,
    min_quality: float = 0.7,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """3-hop: skills frequently present in top-performer hires for this role family."""
    return _execute(
        """
        MATCH (target:Role {role_id: $role_id, group_id: $group_id})
        MATCH (role:Role {group_id: $group_id})
              WHERE coalesce(role.role_family, role.role_id) =
                    coalesce(target.role_family, target.role_id)
        MATCH (role)<-[:APPLIED_FOR]-(c:Candidate)-[:HAS_SKILL]->(s:Skill)
        MATCH (c)<-[:FOR_CANDIDATE]-(d:DecisionEvent)-[:RESULTED_IN]->(o:HiringOutcome)
        WHERE o.outcome_type = 'hired'
          AND coalesce(o.quality_signal, 0) >= $min_quality
          AND (d.created_at IS NULL OR d.created_at <= $t)
        RETURN s.name AS skill,
               count(DISTINCT c) AS frequency,
               avg(coalesce(o.quality_signal, 0.0)) AS avg_quality
        ORDER BY frequency DESC
        LIMIT $limit
        """,
        role_id=role_id,
        group_id=group_id,
        t=t,
        min_quality=min_quality,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Graph priors queries (§4) — the GraphRAG agent
# ---------------------------------------------------------------------------


def referrer_signal(
    *, group_id: str, referrer_id: str, t: dt.datetime
) -> dict[str, Any]:
    """Aggregate referrer track record up to time ``t``."""
    # Walks Candidate→Outcome directly so the backfill path (no
    # DecisionEvent) and the live path are both matched. Both writers
    # emit the direct Candidate-[:RESULTED_IN]->HiringOutcome edge.
    rows = _execute(
        """
        MATCH (ref:Referrer {referrer_id: $referrer_id, group_id: $group_id})
              <-[:REFERRED_BY]-(c:Candidate {group_id: $group_id})
              -[:RESULTED_IN]->(o:HiringOutcome {group_id: $group_id})
        WHERE (o.observed_at IS NULL OR o.observed_at <= $t)
        RETURN
            count(DISTINCT c) AS total_referrals,
            sum(CASE WHEN o.outcome_type = 'hired' THEN 1 ELSE 0 END) AS hires,
            avg(coalesce(o.quality_signal, 0.0)) AS avg_quality_signal,
            sum(CASE WHEN o.outcome_type = 'hired'
                      AND coalesce(o.quality_signal, 0) >= 0.7
                 THEN 1 ELSE 0 END) AS top_performers
        """,
        referrer_id=referrer_id,
        group_id=group_id,
        t=t,
    )
    if not rows:
        return {
            "total_referrals": 0,
            "hires": 0,
            "avg_quality_signal": None,
            "top_performers": 0,
        }
    return rows[0]


def company_overlap_with_top_performers(
    *,
    group_id: str,
    candidate_id: str,
    role_id: str,
    t: dt.datetime,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """2-hop: candidate's companies that also hosted top performers in this role family."""
    # Walks Candidate→Outcome directly (no DecisionEvent middle hop).
    # Backfilled hires from Workable history match the same way as live
    # Tali-driven hires.
    return _execute(
        """
        MATCH (target:Role {role_id: $role_id, group_id: $group_id})
        MATCH (cand:Candidate {candidate_id: $candidate_id, group_id: $group_id})
              -[:WORKED_AT]->(co:Company)
        MATCH (co)<-[:WORKED_AT]-(top:Candidate {group_id: $group_id})
        MATCH (top)-[:RESULTED_IN]->(o:HiringOutcome {group_id: $group_id})
        MATCH (top)-[:APPLIED_FOR]->(role:Role)
        WHERE (coalesce(role.role_family, role.role_id) =
               coalesce(target.role_family, target.role_id))
          AND o.outcome_type = 'hired'
          AND (o.observed_at IS NULL OR o.observed_at <= $t)
          AND top.candidate_id <> cand.candidate_id
        RETURN co.name AS company,
               count(DISTINCT top) AS overlap_top_performers,
               avg(coalesce(o.quality_signal, 0.0)) AS avg_quality
        ORDER BY overlap_top_performers DESC
        LIMIT $limit
        """,
        candidate_id=candidate_id,
        role_id=role_id,
        group_id=group_id,
        t=t,
        limit=limit,
    )


def similar_past_candidates(
    *,
    group_id: str,
    candidate_id: str,
    t: dt.datetime,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Graph-based similarity by shared skills + companies, with outcomes."""
    # Outcome lookup walks Candidate→Outcome directly; backfilled hires
    # from Workable history match the same way as live Tali-driven hires.
    return _execute(
        """
        MATCH (cand:Candidate {candidate_id: $candidate_id, group_id: $group_id})
        MATCH (cand)-[:HAS_SKILL]->(s:Skill)<-[:HAS_SKILL]-(other:Candidate {group_id: $group_id})
        WHERE other.candidate_id <> cand.candidate_id
        WITH cand, other, count(DISTINCT s) AS shared_skills
        OPTIONAL MATCH (cand)-[:WORKED_AT]->(co:Company)<-[:WORKED_AT]-(other)
        WITH other, shared_skills, count(DISTINCT co) AS shared_companies
        OPTIONAL MATCH (other)-[:RESULTED_IN]->(o:HiringOutcome {group_id: $group_id})
        WHERE (o.observed_at IS NULL OR o.observed_at <= $t)
        RETURN other.candidate_id AS candidate_id,
               shared_skills,
               shared_companies,
               o.outcome_type AS outcome,
               coalesce(o.quality_signal, 0.0) AS quality_signal,
               (shared_skills * 1.0 + shared_companies * 2.0) AS similarity_score
        ORDER BY similarity_score DESC
        LIMIT $top_n
        """,
        candidate_id=candidate_id,
        group_id=group_id,
        t=t,
        top_n=top_n,
    )


def skill_to_outcome_paths(
    *,
    group_id: str,
    candidate_id: str,
    role_id: str,
    t: dt.datetime,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """3-hop aggregate: candidate's skills → other candidates → role family → outcomes."""
    # Outcome lookup walks Candidate→Outcome directly; backfilled hires
    # from Workable history match the same way as live Tali-driven hires.
    return _execute(
        """
        MATCH (target:Role {role_id: $role_id, group_id: $group_id})
        MATCH (cand:Candidate {candidate_id: $candidate_id, group_id: $group_id})
              -[:HAS_SKILL]->(s:Skill)
        MATCH (s)<-[:HAS_SKILL]-(other:Candidate {group_id: $group_id})
              -[:APPLIED_FOR]->(role:Role)
        WHERE coalesce(role.role_family, role.role_id) =
              coalesce(target.role_family, target.role_id)
        // OPTIONAL so the denominator is ALL applicants with the skill in the
        // role family, not only those carrying a materialised outcome node.
        // Under positive-only outcome sync (2026-06-07) a non-hired candidate
        // has no HiringOutcome node; counting them as non-hired here yields
        // the true base hire rate instead of ~100%.
        OPTIONAL MATCH (other)-[:RESULTED_IN]->(o:HiringOutcome {group_id: $group_id})
              WHERE (o.observed_at IS NULL OR o.observed_at <= $t)
        RETURN s.name AS skill,
               count(DISTINCT other) AS candidates_with_skill,
               avg(CASE WHEN o.outcome_type = 'hired' THEN 1.0 ELSE 0.0 END) AS hire_rate,
               avg(coalesce(o.quality_signal, 0.0)) AS avg_quality_signal
        ORDER BY candidates_with_skill DESC
        LIMIT $limit
        """,
        candidate_id=candidate_id,
        role_id=role_id,
        group_id=group_id,
        t=t,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Synthesis helper — turn a list of multi-hop rows into a single
# calibrated prior (0..1) the policy engine consumes. Used by Phase 2's
# upgraded graph_priors sub-agent.
# ---------------------------------------------------------------------------


def synthesise_prior(
    *,
    referrer: dict[str, Any] | None,
    overlap_rows: list[dict[str, Any]],
    similar_rows: list[dict[str, Any]],
    skill_outcome_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Combine graph-prior rows into ``p_advance`` + a confidence number.

    The synthesis is deliberately simple and inspectable:
      - Each signal source contributes a score in [0, 1] with a weight.
      - Weights are fixed (not learned at this layer) — the fitted policy
        layer (Phase 3) re-weights all four sub-agent outputs anyway.
      - Confidence rises with the count of supporting paths.

    Returning the per-source breakdown lets the orchestrator embed it
    as ``structured_evidence`` so recruiters can see exactly which
    relational signals moved the prior.
    """
    components: list[tuple[str, float, float, str]] = []  # (name, value, weight, summary)

    # Referrer: hires / total_referrals if signal volume is present.
    if referrer and (referrer.get("total_referrals") or 0) > 0:
        total = float(referrer["total_referrals"])
        hires = float(referrer.get("hires") or 0)
        top_performers = float(referrer.get("top_performers") or 0)
        # Top-performer fraction weighted higher than raw hire rate.
        score = min(1.0, (hires + top_performers) / max(1.0, 2.0 * total))
        summary = (
            f"Referrer: {int(hires)} hires of {int(total)} referrals "
            f"({int(top_performers)} top-quartile)"
        )
        components.append(("referrer", score, 0.35, summary))

    # Company overlap with top performers.
    if overlap_rows:
        # Sum top-performer overlap, clamp.
        total_overlap = sum(
            int(r.get("overlap_top_performers") or 0) for r in overlap_rows
        )
        # 5+ overlapping top performers ≈ saturated signal.
        score = min(1.0, total_overlap / 5.0)
        top_company = overlap_rows[0].get("company", "company")
        summary = (
            f"Worked at {top_company} (+{len(overlap_rows) - 1} other "
            f"shared companies); {total_overlap} top-quartile hires came "
            f"through that path"
        )
        components.append(("company_overlap", score, 0.30, summary))

    # Similar past candidates by skill + company.
    if similar_rows:
        hired_neighbours = sum(
            1 for r in similar_rows if (r.get("outcome") or "").lower() == "hired"
        )
        # 3+ hired similar candidates ≈ strong neighbourhood evidence.
        score = min(1.0, hired_neighbours / 3.0)
        summary = (
            f"{hired_neighbours} of {len(similar_rows)} graph-similar past "
            f"candidates were hired"
        )
        components.append(("similar_candidates", score, 0.20, summary))

    # Skill → role family → outcome paths.
    if skill_outcome_rows:
        weighted = sum(
            (r.get("hire_rate") or 0.0) * (r.get("candidates_with_skill") or 0)
            for r in skill_outcome_rows
        )
        denom = sum((r.get("candidates_with_skill") or 0) for r in skill_outcome_rows)
        score = (weighted / denom) if denom else 0.0
        summary = (
            f"Skill→role-family hire rate across {denom} prior candidates: "
            f"{score:.2f}"
        )
        components.append(("skill_outcome_paths", score, 0.15, summary))

    if not components:
        return {
            "p_advance": None,
            "confidence": 0.0,
            "components": [],
            "synthesis_note": "no graph paths produced any signal",
        }

    # Weighted average; uncovered weight collapses (we don't penalise for
    # the absence of a signal source — empty referrer signal shouldn't
    # drag the prior to zero).
    used_weight = sum(weight for _, _, weight, _ in components)
    weighted_sum = sum(value * weight for _, value, weight, _ in components)
    p_advance = weighted_sum / used_weight if used_weight > 0 else 0.0

    # Confidence: each component is a ~25% contribution to confidence; floor
    # at 0.05 once we have any signal at all so the policy engine doesn't
    # treat the prior as "absent" when there's one strong path.
    confidence = max(0.05, min(1.0, len(components) / 4.0))

    return {
        "p_advance": float(p_advance),
        "confidence": float(confidence),
        "components": [
            {"name": n, "score": v, "weight": w, "summary": s}
            for n, v, w, s in components
        ],
    }


__all__ = [
    "candidate_claimed_skills",
    "company_overlap_with_top_performers",
    "referrer_signal",
    "role_must_haves",
    "role_requirements_weighted",
    "similar_past_candidates",
    "skill_to_outcome_paths",
    "successful_skill_patterns",
    "synthesise_prior",
]
