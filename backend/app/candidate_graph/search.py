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
    """Return a graph payload covering the given candidates and their
    immediate connections (companies, schools, skills, mentioned people).

    Uses Graphiti's per-candidate hybrid search, capped to ``SUBGRAPH_LIMIT``
    total nodes so the cytoscape view stays readable.
    """
    ids = list({int(c) for c in candidate_ids})
    if not ids or not graph_client.is_configured():
        return GraphPayload()

    graphiti = graph_client.get_graphiti()
    group_id = graph_client.group_id_for_org(organization_id)

    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    # 25-second wall-clock budget; 10s per-candidate call timeout.
    # Returns partial results when budget is exhausted rather than hanging.
    WALL_BUDGET = 25.0
    PER_CALL_TIMEOUT = 10.0
    deadline = time.monotonic() + WALL_BUDGET

    for candidate_id in ids[:50]:  # bound the per-call work
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info(
                "subgraph_for_candidates: time budget exhausted at %d nodes", len(nodes)
            )
            break
        try:
            results = graph_client.run_async(
                graphiti.search(
                    query=f"facts about candidate {candidate_id}",
                    group_ids=[group_id],
                    num_results=20,
                ),
                timeout=min(PER_CALL_TIMEOUT, remaining),
            )
        except Exception as exc:
            logger.debug("subgraph search failed candidate_id=%s: %s", candidate_id, exc)
            continue
        _merge_results_into_payload(results, nodes, edges, anchor_taali_id=candidate_id)
        if len(nodes) >= SUBGRAPH_LIMIT:
            break

    return GraphPayload(nodes=list(nodes.values())[:SUBGRAPH_LIMIT], edges=edges)


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


def _label_for(node_attrs: dict, fallback_labels: list[str]) -> str:
    """Pick the closest match in our GraphNode label vocabulary."""
    raw = " ".join([*(fallback_labels or []), str(node_attrs.get("kind", ""))])
    raw_lower = raw.lower()
    if "person" in raw_lower or "candidate" in raw_lower:
        return "Person"
    if "company" in raw_lower or "employer" in raw_lower or "organization" in raw_lower:
        return "Company"
    if "school" in raw_lower or "university" in raw_lower or "institution" in raw_lower:
        return "School"
    if "skill" in raw_lower or "technology" in raw_lower:
        return "Skill"
    if "country" in raw_lower or "location" in raw_lower:
        return "Country"
    return "Skill"  # safe default for free-form Graphiti entities


def _edge_label_for(name: str) -> str:
    upper = (name or "").upper()
    if "WORKED" in upper or "EMPLOYED" in upper:
        return "WORKED_AT"
    if "STUDIED" in upper or "EDUCATED" in upper or "ATTENDED" in upper:
        return "STUDIED_AT"
    if "SKILL" in upper or "PROFICIENT" in upper or "USED" in upper:
        return "HAS_SKILL"
    if "LOCATED" in upper or "LIVES" in upper:
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
        source_label = _label_for(fact.get("source_attributes") or {}, fact.get("source_labels") or [])
        target_label = _label_for(fact.get("target_attributes") or {}, fact.get("target_labels") or [])
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
                label=_edge_label_for(fact.get("edge_label") or fact.get("name") or ""),
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
