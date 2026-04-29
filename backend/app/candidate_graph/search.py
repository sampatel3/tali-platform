"""Search adapters: Graphiti -> the shapes the rest of Tali expects.

Three public callables, signature-compatible with the previous Cypher
implementation so callers (the search runner, the rerank step, the
endpoint) need only re-import:

- ``candidate_ids_matching_all(organization_id, predicates) -> list[int]``
- ``subgraph_for_candidates(organization_id, candidate_ids) -> GraphPayload``
- ``colleague_neighbourhood(organization_id, candidate_id) -> dict``

All three are sync; they call Graphiti through ``client.run_async``.
``candidate_id`` is the Postgres id stored in the Graphiti node's
``attributes['taali_id']`` field — set by the episode builders.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from . import client as graph_client
from ..candidate_search.schemas import (
    GraphEdge,
    GraphNode,
    GraphPayload,
    GraphPredicate,
)

logger = logging.getLogger("taali.candidate_graph.search")

# How many top-ranked Graphiti nodes/edges we pull per query. Caps blast
# radius if a query is unexpectedly broad ("everyone in tech").
DEFAULT_SEARCH_LIMIT = 50
SUBGRAPH_LIMIT = 200
NEIGHBOURHOOD_LIMIT = 60


def _query_for_predicate(predicate: GraphPredicate) -> str:
    """Turn a structured predicate into a natural-language Graphiti query."""
    value = (predicate.value or "").strip()
    if predicate.type == "worked_at":
        return f"candidates who have worked at {value}"
    if predicate.type == "studied_at":
        return f"candidates who studied at {value}"
    if predicate.type == "colleague_of":
        return f"candidates who shared a workplace with candidate {value}"
    if predicate.type == "n_hop_from":
        return f"candidates connected to candidate {value}"
    return value


def candidate_ids_for_predicate(
    *, organization_id: int, predicate: GraphPredicate
) -> set[int]:
    """Return the set of Postgres candidate ids matching one predicate.

    Empty set means "no matches" — runner treats this as a hard zero
    and short-circuits.
    """
    if not graph_client.is_configured():
        return set()
    graphiti = graph_client.get_graphiti()
    group_id = graph_client.group_id_for_org(organization_id)
    query = _query_for_predicate(predicate)
    try:
        results = graph_client.run_async(
            graphiti.search(query=query, group_ids=[group_id], num_results=DEFAULT_SEARCH_LIMIT)
        )
    except Exception as exc:
        logger.warning("Graphiti search failed for predicate=%s: %s", predicate, exc)
        return set()
    return _extract_taali_ids(results)


def candidate_ids_matching_all(
    *, organization_id: int, predicates: list[GraphPredicate]
) -> list[int]:
    """Intersection of candidate-id sets across all predicates.

    Order of the returned list is deterministic (sorted ascending) so
    downstream pagination is stable.
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
    *, organization_id: int, candidate_ids: Iterable[int]
) -> GraphPayload:
    """Return a graph payload for specific candidates via direct Cypher.

    Looks up Episode nodes by the stable episode-name pattern
    ``candidate-{id}-*`` and returns all edges connected to the entities
    those episodes introduced. Falls back to an empty payload on error.
    """
    ids = list({int(c) for c in candidate_ids})
    if not ids or not graph_client.is_configured():
        return GraphPayload()

    graphiti = graph_client.get_graphiti()
    group_id = graph_client.group_id_for_org(organization_id)
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    # Build episode-name prefixes for the requested candidates.
    # Each candidate contributes episodes named "candidate-{id}-profile",
    # "candidate-{id}-cv", etc.  We match via STARTS WITH so all episode
    # types are covered without enumerating them.
    try:
        result = graph_client.run_async(
            _cypher_subgraph_by_episodes(graphiti.driver, group_id, ids[:50]),
            timeout=8.0,
        )
        _merge_neo4j_records(result, nodes, edges)
    except Exception as exc:
        logger.exception("subgraph_for_candidates cypher failed: %s", exc)

    return GraphPayload(nodes=list(nodes.values())[:SUBGRAPH_LIMIT], edges=edges)


def subgraph_for_query(*, organization_id: int, query: str) -> GraphPayload:
    """Return a graph payload for a free-text query via direct Cypher.

    Searches edge facts by substring match — much faster than the Graphiti
    Python search path because it avoids vector embedding and returns nodes
    with their full data already joined.
    """
    if not query or not graph_client.is_configured():
        return GraphPayload()

    graphiti = graph_client.get_graphiti()
    group_id = graph_client.group_id_for_org(organization_id)
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    try:
        result = graph_client.run_async(
            _cypher_subgraph_by_query(graphiti.driver, group_id, query, limit=SUBGRAPH_LIMIT),
            timeout=8.0,
        )
        _merge_neo4j_records(result, nodes, edges)
    except Exception as exc:
        logger.exception("subgraph_for_query cypher failed: %s", exc)

    return GraphPayload(nodes=list(nodes.values())[:SUBGRAPH_LIMIT], edges=edges)


# ---------------------------------------------------------------------------
# Direct Cypher helpers — bypass graphiti.search() which returns EntityEdge
# objects with source_node/target_node = None (not populated on search results).
# ---------------------------------------------------------------------------


async def _cypher_subgraph_by_query(
    driver, group_id: str, query: str, limit: int = 100
):
    """Edges whose fact text contains the query substring, with nodes joined.

    Uses string formatting instead of parameterized queries because the Neo4j
    driver version in use does not accept a plain dict as the second positional
    argument to execute_query — parameterized calls either throw or return empty.

    Safety:
    - ``group_id`` is always ``org-{int}`` — no user-controlled chars.
    - ``query`` has single-quotes escaped and is truncated to 200 chars.
    - ``limit`` is a Python int — safe to interpolate directly.
    """
    safe_query = query.replace("'", "\\'")[:200]
    cypher = f"""
        MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity)
        WHERE e.group_id = '{group_id}'
          AND toLower(e.fact) CONTAINS toLower('{safe_query}')
        RETURN
          s.uuid AS s_uuid, s.name AS s_name, properties(s) AS s_props,
          t.uuid AS t_uuid, t.name AS t_name, properties(t) AS t_props,
          e.uuid AS e_uuid, e.name AS e_name, e.fact AS e_fact,
          e.valid_at AS e_valid_at, e.invalid_at AS e_invalid_at
        LIMIT {int(limit)}
        """
    return await driver.execute_query(cypher)


async def _cypher_subgraph_by_episodes(
    driver, group_id: str, candidate_ids: list[int]
):
    """Edges introduced by episodes for the given Postgres candidate IDs.

    Uses string formatting instead of parameterized queries (same reason as
    _cypher_subgraph_by_query above).

    Safety:
    - ``candidate_ids`` are Python ints — safe to interpolate directly.
    - ``group_id`` is always ``org-{int}`` — no user-controlled chars.

    We do NOT filter Episode nodes by group_id property because it may not
    be stored on Episode nodes in this Graphiti version. Instead we rely on:
    - The ``candidate-{id}-`` STARTS WITH prefix on ep.name (candidate-scoped).
    - The ``e.group_id`` filter on the RELATES_TO edge (org-scoped).
    """
    # Episode names are stable: "candidate-{id}-profile", "candidate-{id}-cv", …
    # Build a Cypher list literal of prefix strings.
    prefix_list = ", ".join(f"'candidate-{int(cid)}-'" for cid in candidate_ids)
    cypher = f"""
        WITH [{prefix_list}] AS prefixes
        UNWIND prefixes AS prefix
        MATCH (ep:Episode)
        WHERE ep.name STARTS WITH prefix
        MATCH (ep)-[:MENTIONS]->(s:Entity)-[e:RELATES_TO]->(t:Entity)
        WHERE e.group_id = '{group_id}'
        RETURN
          s.uuid AS s_uuid, s.name AS s_name, properties(s) AS s_props,
          t.uuid AS t_uuid, t.name AS t_name, properties(t) AS t_props,
          e.uuid AS e_uuid, e.name AS e_name, e.fact AS e_fact,
          e.valid_at AS e_valid_at, e.invalid_at AS e_invalid_at
        LIMIT 200
        """
    return await driver.execute_query(cypher)


def _merge_neo4j_records(result, nodes: dict, edges: list) -> None:
    """Build GraphNode/GraphEdge objects from raw Cypher records."""
    if result is None:
        return
    for record in result.records or []:
        s_uuid = record.get("s_uuid")
        t_uuid = record.get("t_uuid")
        if not s_uuid or not t_uuid:
            continue
        s_name = record.get("s_name") or "?"
        t_name = record.get("t_name") or "?"
        s_props = dict(record.get("s_props") or {})
        t_props = dict(record.get("t_props") or {})
        e_name = record.get("e_name") or ""
        e_fact = record.get("e_fact") or ""

        # In Graphiti RELATES_TO edges the source is always the "subject"
        # entity (typically a Person) — use edge context to refine labels.
        # e.name is None in current Graphiti schema, so fall back to the fact text.
        edge_label = _edge_label_for(e_name, fact=e_fact)
        s_label = _label_for(s_props, [], s_name, is_source=True)
        t_label = _label_for(t_props, [], t_name, edge_context=edge_label)
        s_id = _node_id_for(s_uuid, s_label, s_props, None)
        t_id = _node_id_for(t_uuid, t_label, t_props, None)

        if s_id not in nodes:
            nodes[s_id] = GraphNode(
                id=s_id,
                label=s_label,
                name=s_name,
                extra={"uuid": s_uuid, "headline": s_props.get("headline")},
            )
        if t_id not in nodes:
            nodes[t_id] = GraphNode(
                id=t_id,
                label=t_label,
                name=t_name,
                extra={"uuid": t_uuid},
            )

        edges.append(
            GraphEdge(
                source=s_id,
                target=t_id,
                label=edge_label,
                extra={
                    "fact": record.get("e_fact"),
                    "valid_at": _stringify(record.get("e_valid_at")),
                    "invalid_at": _stringify(record.get("e_invalid_at")),
                },
            )
        )


def colleague_neighbourhood(
    *,
    organization_id: int,
    candidate_id: int,
    max_companies: int = 10,
    max_colleagues_per_company: int = 5,
) -> dict:
    """Compact neighbourhood used by the rerank prompt.

    Returns ``{"companies": [...], "schools": [...], "skills": [...]}``
    where each company entry has ``{name, title, colleagues: [...]}``.
    Empty dict when Graphiti is unavailable.
    """
    if not graph_client.is_configured():
        return {"companies": [], "schools": [], "skills": []}

    graphiti = graph_client.get_graphiti()
    group_id = graph_client.group_id_for_org(organization_id)
    try:
        results = graph_client.run_async(
            graphiti.search(
                query=f"work history, education, and colleagues of candidate {candidate_id}",
                group_ids=[group_id],
                num_results=NEIGHBOURHOOD_LIMIT,
            )
        )
    except Exception as exc:
        logger.warning("colleague_neighbourhood failed: %s", exc)
        return {"companies": [], "schools": [], "skills": []}

    companies: dict[str, dict] = {}
    schools: list[str] = []
    skills: list[str] = []

    for fact in _iter_facts(results):
        edge_label = (fact.get("edge_label") or fact.get("name") or "").upper()
        target = fact.get("target_name") or ""
        if not target:
            continue
        if "WORKED_AT" in edge_label or "EMPLOYED" in edge_label:
            entry = companies.setdefault(
                target, {"name": target, "title": fact.get("attributes", {}).get("title", ""), "colleagues": []}
            )
            colleague = fact.get("attributes", {}).get("colleague_name")
            if colleague and colleague not in entry["colleagues"] and len(entry["colleagues"]) < max_colleagues_per_company:
                entry["colleagues"].append(colleague)
        elif "STUDIED" in edge_label or "EDUCATED" in edge_label:
            if target not in schools:
                schools.append(target)
        elif "SKILL" in edge_label or "PROFICIENT" in edge_label:
            if target not in skills:
                skills.append(target)

    return {
        "companies": list(companies.values())[:max_companies],
        "schools": schools[:10],
        "skills": skills[:30],
    }


# ---------------------------------------------------------------------------
# Result shape adapters
# ---------------------------------------------------------------------------


def _iter_facts(results: Any) -> Iterable[dict]:
    """Yield Graphiti search results as plain dicts.

    Graphiti's ``search`` returns a list of ``EntityEdge`` objects (or
    similar). Each has ``.fact``, ``.source_node``, ``.target_node``,
    ``.attributes``. We normalise to a dict so downstream code doesn't
    couple to Graphiti's class hierarchy.
    """
    if results is None:
        return
    seq = results if isinstance(results, (list, tuple)) else getattr(results, "edges", None) or []
    for item in seq:
        if isinstance(item, dict):
            yield item
            continue
        try:
            source = getattr(item, "source_node", None)
            target = getattr(item, "target_node", None)
            yield {
                "uuid": getattr(item, "uuid", None),
                "name": getattr(item, "name", None),
                "fact": getattr(item, "fact", None),
                "edge_label": getattr(item, "name", None),
                "valid_at": getattr(item, "valid_at", None),
                "invalid_at": getattr(item, "invalid_at", None),
                "source_uuid": getattr(source, "uuid", None) if source is not None else None,
                "source_name": getattr(source, "name", None) if source is not None else None,
                "source_labels": list(getattr(source, "labels", []) or []) if source is not None else [],
                "source_attributes": dict(getattr(source, "attributes", {}) or {}) if source is not None else {},
                "target_uuid": getattr(target, "uuid", None) if target is not None else None,
                "target_name": getattr(target, "name", None) if target is not None else None,
                "target_labels": list(getattr(target, "labels", []) or []) if target is not None else [],
                "target_attributes": dict(getattr(target, "attributes", {}) or {}) if target is not None else {},
                "attributes": dict(getattr(item, "attributes", {}) or {}),
            }
        except Exception as exc:
            logger.debug("Skipping unparseable Graphiti result: %s", exc)


def _extract_taali_ids(results: Any) -> set[int]:
    """Pull Postgres candidate ids out of Graphiti search results.

    Episode bodies tag each candidate with ``(taali_id={candidate.id})``,
    which Graphiti extracts as a property on the Person entity. We also
    fall back to scanning fact text for the same pattern when the
    attribute isn't promoted.
    """
    import re

    ids: set[int] = set()
    pattern = re.compile(r"taali_id\s*[=:]\s*(\d+)")
    for fact in _iter_facts(results):
        for attrs in (fact.get("source_attributes") or {}, fact.get("target_attributes") or {}, fact.get("attributes") or {}):
            value = attrs.get("taali_id")
            if value is not None:
                try:
                    ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        for text_field in ("fact", "source_name", "target_name", "edge_label"):
            text = fact.get(text_field) or ""
            for m in pattern.finditer(str(text)):
                try:
                    ids.add(int(m.group(1)))
                except ValueError:
                    pass
    return ids


_COMPANY_SUFFIXES = (
    " ltd", " limited", " inc", " corp", " llc", " gmbh", " plc", " pvt",
    " group", " holdings", " technologies", " technology", " tech", " systems",
    " solutions", " services", " consulting", " international", " global",
    " ventures", " partners", " associates", " agency", " studio", " labs",
    " software", " digital", " media", " bank", " financial", " capital",
    " investments", " logistics", " aviation", " healthcare", " pharma",
    " energy", " telecom", " networks", " cloud", " ai", " data",
)
_SCHOOL_KEYWORDS = (
    "university", "college", "school", "institute", "academy", "polytechnic",
    "iit", "iim", "nit", "faculty", "department", "campus",
)
_COUNTRY_KEYWORDS = (
    "india", "usa", "uk", "uae", "canada", "australia", "germany", "france",
    "singapore", "united states", "united kingdom", "united arab emirates",
)


def _label_for(
    node_attrs: dict,
    fallback_labels: list[str],
    name: str = "",
    *,
    is_source: bool = False,
    edge_context: str = "",
) -> str:
    """Pick the closest match in our GraphNode label vocabulary.

    Priority:
    1. Explicit kind/label on the node (e.g. from older backfills).
    2. Source-side heuristic: in RELATES_TO edges the source is almost always
       a Person (the entity about whom the fact is stated).
    3. Name-based heuristics: company suffix, school keyword, country name.
    4. Edge context: if edge is WORKED_AT the target is likely Company, etc.
    5. Safe default: Skill (renders small and generic).
    """
    raw = " ".join([*(fallback_labels or []), str(node_attrs.get("kind", ""))])
    raw_lower = raw.lower()
    # 1. Explicit label on node
    if "person" in raw_lower or "candidate" in raw_lower:
        return "Person"
    if "company" in raw_lower or "employer" in raw_lower or "organization" in raw_lower:
        return "Company"
    if "school" in raw_lower or "university" in raw_lower or "institution" in raw_lower:
        return "School"
    if "country" in raw_lower or "location" in raw_lower:
        return "Country"
    if "skill" in raw_lower or "technology" in raw_lower:
        return "Skill"

    name_lower = (name or "").lower().strip()

    # 2. Source side — Graphiti always puts the "subject" entity as the source
    #    of RELATES_TO edges in employment/education facts. If this is the
    #    source and has a name that doesn't match company/school patterns,
    #    treat as Person.
    if is_source:
        if any(s in name_lower for s in _COMPANY_SUFFIXES):
            return "Company"
        if any(kw in name_lower for kw in _SCHOOL_KEYWORDS):
            return "School"
        return "Person"

    # 3. Name-based heuristics for target nodes — use substring, not endswith,
    #    because company names often have trailing context (" - EMEA", " (India)").
    if any(s in name_lower for s in _COMPANY_SUFFIXES):
        return "Company"
    if any(kw in name_lower for kw in _SCHOOL_KEYWORDS):
        return "School"
    if name_lower in _COUNTRY_KEYWORDS or (len(name_lower) == 2 and name_lower.isalpha()):
        return "Country"

    # 4. Edge context — only use for WORKED_AT/STUDIED_AT/LOCATED_IN where we
    #    know the target type; HAS_SKILL default stays Skill.
    if edge_context == "WORKED_AT":
        return "Company"
    if edge_context == "STUDIED_AT":
        return "School"
    if edge_context == "LOCATED_IN":
        return "Country"

    return "Skill"


def _edge_label_for(name: str, fact: str = "") -> str:
    # Graphiti RELATES_TO edges have e.name = None in the current schema.
    # Fall back to the fact text when the edge name is absent.
    text = (name or fact or "").upper()
    if not text:
        return "HAS_SKILL"
    # Education: checked first to avoid "ATTENDED" → WORKED_AT
    if any(kw in text for kw in ("STUDIED", "STUDIES", "EDUCATED", "ATTENDED", "GRADUATED", "ENROLLED", "UNIVERSITY", "COLLEGE")):
        return "STUDIED_AT"
    # "holds the position of X at Company" — the "at Company" means WORKED_AT
    # "holds the position of X" (no "at") — pure title, target is a Skill node
    if any(kw in text for kw in ("HOLDS THE POSITION", "JOB TITLE", "HOLDS THE ROLE", "HOLDS THE JOB")):
        if " AT " in text:
            return "WORKED_AT"
        return "HAS_SKILL"
    # Employment at a company — "works as ... at", "worked at", "employed at"
    if any(kw in text for kw in ("WORKED AT", "WORKS AT", "EMPLOYED AT", "EMPLOYED BY", "HIRED AT", "HIRED BY", "WORKS FOR", "WORKED FOR", "WORKS AS")):
        return "WORKED_AT"
    # Generic employment verbs — broader but after the specific patterns above
    if any(kw in text for kw in ("POSITION", "TITLE", "ROLE")):
        return "HAS_SKILL"
    if any(kw in text for kw in ("WORKED", "WORKS", "EMPLOYED", "EMPLOY", "HIRED")):
        return "WORKED_AT"
    if any(kw in text for kw in ("SKILL", "PROFICIENT", "USES", "USED", "KNOWS", "EXPERTISE")):
        return "HAS_SKILL"
    if any(kw in text for kw in ("LOCATED", "LIVES", "BASED", "RESIDES", "RESIDE")):
        return "LOCATED_IN"
    return "HAS_SKILL"  # safe default


def _merge_results_into_payload(
    results: Any,
    nodes: dict,
    edges: list,
    *,
    anchor_taali_id: int | None = None,
) -> None:
    """Mutate ``nodes`` and ``edges`` with the contents of one search batch."""
    for fact in _iter_facts(results):
        source_uuid = fact.get("source_uuid")
        target_uuid = fact.get("target_uuid")
        if not source_uuid or not target_uuid:
            continue
        edge_label_str = _edge_label_for(fact.get("edge_label") or fact.get("name") or "", fact=fact.get("fact") or "")
        source_label = _label_for(fact.get("source_attributes") or {}, fact.get("source_labels") or [], fact.get("source_name") or "", is_source=True)
        target_label = _label_for(fact.get("target_attributes") or {}, fact.get("target_labels") or [], fact.get("target_name") or "", edge_context=edge_label_str)
        source_name = fact.get("source_name") or "?"
        target_name = fact.get("target_name") or "?"
        # Override Person nodes' id so the frontend can join back to Postgres.
        source_id = _node_id_for(source_uuid, source_label, fact.get("source_attributes"), anchor_taali_id)
        target_id = _node_id_for(target_uuid, target_label, fact.get("target_attributes"), anchor_taali_id)

        if source_id not in nodes:
            nodes[source_id] = GraphNode(
                id=source_id,
                label=source_label,
                name=source_name,
                extra={
                    "uuid": source_uuid,
                    "headline": (fact.get("source_attributes") or {}).get("headline"),
                },
            )
        if target_id not in nodes:
            nodes[target_id] = GraphNode(
                id=target_id,
                label=target_label,
                name=target_name,
                extra={"uuid": target_uuid},
            )
        edges.append(
            GraphEdge(
                source=source_id,
                target=target_id,
                label=edge_label_str,
                extra={
                    "fact": fact.get("fact"),
                    "valid_at": _stringify(fact.get("valid_at")),
                    "invalid_at": _stringify(fact.get("invalid_at")),
                },
            )
        )


def _node_id_for(uuid: str, label: str, attrs: dict | None, anchor_taali_id: int | None) -> str:
    """Frontend node id format: ``person:<taali_id>`` for Person nodes,
    ``<lowercase_label>:<uuid_short>`` for everything else."""
    if label == "Person":
        attrs = attrs or {}
        taali_id = attrs.get("taali_id") or anchor_taali_id
        if taali_id:
            return f"person:{int(taali_id)}"
    short_uuid = (uuid or "")[:12] or "x"
    return f"{label.lower()}:{short_uuid}"


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
