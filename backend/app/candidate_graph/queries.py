"""Cypher queries: graph predicates and subgraph fetch.

Every template begins with a tenancy filter on ``organization_id``. The
``Neo4jClient`` wrapper would be the ideal enforcement point; for v1 we
inline the filter here and rely on test coverage to catch drift.
"""

from __future__ import annotations

import logging
from typing import Iterable

from . import client as graph_client
from ..candidate_search.schemas import (
    GraphEdge,
    GraphNode,
    GraphPayload,
    GraphPredicate,
)

logger = logging.getLogger("taali.candidate_graph.queries")


def _normalise_company(name: str) -> str:
    """Lower-case + strip for company-name lookup."""
    return (name or "").strip().lower()


def candidate_ids_for_predicate(
    *,
    organization_id: int,
    predicate: GraphPredicate,
) -> set[int]:
    """Return the set of candidate ids matching one graph predicate.

    Empty set means "no matches" (not an error). Raises only if Neo4j is
    misconfigured or the query is malformed — runner traps these.
    """
    org_id = int(organization_id)
    with graph_client.session() as s:
        if predicate.type == "worked_at":
            cypher = """
            MATCH (p:Person)-[r:WORKED_AT]->(c:Company)
            WHERE p.organization_id = $org_id
              AND c.organization_id = $org_id
              AND r.organization_id = $org_id
              AND c.name_normalized = $company_norm
            RETURN DISTINCT p.id AS candidate_id
            """
            result = s.run(
                cypher,
                org_id=org_id,
                company_norm=_normalise_company(predicate.value),
            )
        elif predicate.type == "studied_at":
            cypher = """
            MATCH (p:Person)-[r:STUDIED_AT]->(sch:School)
            WHERE p.organization_id = $org_id
              AND sch.organization_id = $org_id
              AND r.organization_id = $org_id
              AND sch.name_normalized = $school_norm
            RETURN DISTINCT p.id AS candidate_id
            """
            result = s.run(
                cypher,
                org_id=org_id,
                school_norm=_normalise_company(predicate.value),
            )
        elif predicate.type == "colleague_of":
            cypher = """
            MATCH (target:Person {id: $target_id, organization_id: $org_id})
                  -[:WORKED_AT]->(c:Company)<-[:WORKED_AT]-(p:Person)
            WHERE p.organization_id = $org_id
              AND c.organization_id = $org_id
              AND p.id <> target.id
            RETURN DISTINCT p.id AS candidate_id
            """
            try:
                target_id = int(predicate.value)
            except (TypeError, ValueError):
                logger.info("colleague_of predicate value is not an int id: %r", predicate.value)
                return set()
            result = s.run(cypher, org_id=org_id, target_id=target_id)
        elif predicate.type == "n_hop_from":
            n = predicate.n_hops or 2
            n = max(1, min(4, int(n)))
            cypher = f"""
            MATCH (target:Person {{id: $target_id, organization_id: $org_id}})
            MATCH (p:Person {{organization_id: $org_id}})
            WHERE p.id <> target.id
              AND EXISTS {{
                MATCH path = (target)-[*1..{n}]-(p)
                WHERE ALL(r IN relationships(path) WHERE r.organization_id = $org_id)
              }}
            RETURN DISTINCT p.id AS candidate_id
            """
            try:
                target_id = int(predicate.value)
            except (TypeError, ValueError):
                return set()
            result = s.run(cypher, org_id=org_id, target_id=target_id)
        else:
            logger.warning("Unknown predicate type: %r", predicate.type)
            return set()

        return {int(r["candidate_id"]) for r in result}


def candidate_ids_matching_all(
    *,
    organization_id: int,
    predicates: list[GraphPredicate],
) -> list[int]:
    """Intersection of candidate-id sets across all predicates.

    Empty list iff at least one predicate matched nothing OR every
    predicate failed to evaluate.
    """
    if not predicates:
        return []
    aggregate: set[int] | None = None
    for predicate in predicates:
        ids = candidate_ids_for_predicate(
            organization_id=organization_id, predicate=predicate
        )
        if not ids:
            return []
        aggregate = ids if aggregate is None else (aggregate & ids)
        if not aggregate:
            return []
    return sorted(aggregate or set())


def subgraph_for_candidates(
    *,
    organization_id: int,
    candidate_ids: Iterable[int],
    max_neighbour_companies: int = 10,
    max_neighbour_skills: int = 5,
) -> GraphPayload:
    """Return a graph payload covering the given candidates and their
    immediate connections (companies, schools, skills).

    Bounded by ``max_neighbour_*`` to keep the UI graph readable.
    """
    ids = [int(c) for c in candidate_ids]
    if not ids:
        return GraphPayload()

    with graph_client.session() as s:
        # Persons + their WORKED_AT edges, capped per candidate.
        rows_workat = s.run(
            """
            MATCH (p:Person)-[r:WORKED_AT]->(c:Company)
            WHERE p.organization_id = $org_id
              AND c.organization_id = $org_id
              AND p.id IN $ids
            WITH p, c, r
            ORDER BY coalesce(r.end_date, '9999') DESC
            WITH p, collect({c: c, r: r})[..$max_companies] AS rels
            UNWIND rels AS rel
            RETURN p.id AS person_id,
                   p.full_name AS person_name,
                   p.headline AS headline,
                   rel.c.name_normalized AS company_norm,
                   rel.c.name_display AS company_name,
                   rel.r.title AS title,
                   rel.r.start_date AS start_date,
                   rel.r.end_date AS end_date
            """,
            org_id=int(organization_id),
            ids=ids,
            max_companies=int(max_neighbour_companies),
        ).data()

        rows_skills = s.run(
            """
            MATCH (p:Person)-[r:HAS_SKILL]->(sk:Skill)
            WHERE p.organization_id = $org_id
              AND sk.organization_id = $org_id
              AND p.id IN $ids
            WITH p, collect(sk)[..$max_skills] AS sks
            UNWIND sks AS sk
            RETURN p.id AS person_id, sk.name_normalized AS skill_norm, sk.name_display AS skill_name
            """,
            org_id=int(organization_id),
            ids=ids,
            max_skills=int(max_neighbour_skills),
        ).data()

        rows_school = s.run(
            """
            MATCH (p:Person)-[r:STUDIED_AT]->(sch:School)
            WHERE p.organization_id = $org_id
              AND sch.organization_id = $org_id
              AND p.id IN $ids
            RETURN p.id AS person_id,
                   sch.name_normalized AS school_norm,
                   sch.name_display AS school_name
            """,
            org_id=int(organization_id),
            ids=ids,
        ).data()

    return _assemble_payload(
        rows_workat=rows_workat,
        rows_skills=rows_skills,
        rows_school=rows_school,
    )


def _assemble_payload(
    *,
    rows_workat: list[dict],
    rows_skills: list[dict],
    rows_school: list[dict],
) -> GraphPayload:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    def upsert(node_id: str, label: str, name: str, extra: dict | None = None) -> str:
        if node_id not in nodes:
            nodes[node_id] = GraphNode(
                id=node_id, label=label, name=name, extra=extra or {}
            )
        return node_id

    for row in rows_workat:
        person_id = f"person:{row['person_id']}"
        upsert(
            person_id,
            "Person",
            row.get("person_name") or "Unknown",
            extra={"headline": row.get("headline")},
        )
        company_id = f"company:{row['company_norm']}"
        upsert(company_id, "Company", row.get("company_name") or row["company_norm"])
        edges.append(
            GraphEdge(
                source=person_id,
                target=company_id,
                label="WORKED_AT",
                extra={
                    "title": row.get("title"),
                    "start_date": row.get("start_date"),
                    "end_date": row.get("end_date"),
                },
            )
        )

    for row in rows_skills:
        person_id = f"person:{row['person_id']}"
        upsert(person_id, "Person", "")  # name filled by workat rows
        skill_id = f"skill:{row['skill_norm']}"
        upsert(skill_id, "Skill", row.get("skill_name") or row["skill_norm"])
        edges.append(GraphEdge(source=person_id, target=skill_id, label="HAS_SKILL"))

    for row in rows_school:
        person_id = f"person:{row['person_id']}"
        upsert(person_id, "Person", "")
        school_id = f"school:{row['school_norm']}"
        upsert(school_id, "School", row.get("school_name") or row["school_norm"])
        edges.append(GraphEdge(source=person_id, target=school_id, label="STUDIED_AT"))

    return GraphPayload(nodes=list(nodes.values()), edges=edges)


def colleague_neighbourhood(
    *,
    organization_id: int,
    candidate_id: int,
    max_companies: int = 10,
    max_colleagues_per_company: int = 5,
) -> dict:
    """Compact neighbourhood used by the rerank prompt.

    Returns ``{"companies": [{name, title, colleagues: [name, ...]}], "schools": [...], "skills": [...]}``.
    """
    if not graph_client.is_configured():
        return {"companies": [], "schools": [], "skills": []}

    with graph_client.session() as s:
        companies = s.run(
            """
            MATCH (target:Person {id: $cid, organization_id: $org_id})
                  -[r:WORKED_AT]->(c:Company)
            WHERE c.organization_id = $org_id
            OPTIONAL MATCH (c)<-[r2:WORKED_AT]-(p:Person)
              WHERE p.organization_id = $org_id AND p.id <> target.id
            WITH c, r, collect(DISTINCT p.full_name)[..$max_colleagues] AS colleagues
            ORDER BY coalesce(r.end_date, '9999') DESC
            LIMIT $max_companies
            RETURN c.name_display AS company,
                   r.title AS title,
                   r.start_date AS start_date,
                   r.end_date AS end_date,
                   colleagues
            """,
            cid=int(candidate_id),
            org_id=int(organization_id),
            max_companies=int(max_companies),
            max_colleagues=int(max_colleagues_per_company),
        ).data()

        schools = s.run(
            """
            MATCH (target:Person {id: $cid, organization_id: $org_id})
                  -[:STUDIED_AT]->(sch:School)
            WHERE sch.organization_id = $org_id
            RETURN sch.name_display AS school
            """,
            cid=int(candidate_id),
            org_id=int(organization_id),
        ).data()

        skills = s.run(
            """
            MATCH (target:Person {id: $cid, organization_id: $org_id})
                  -[:HAS_SKILL]->(sk:Skill)
            WHERE sk.organization_id = $org_id
            RETURN sk.name_display AS skill
            LIMIT 50
            """,
            cid=int(candidate_id),
            org_id=int(organization_id),
        ).data()

    return {
        "companies": companies,
        "schools": [r["school"] for r in schools],
        "skills": [r["skill"] for r in skills],
    }
