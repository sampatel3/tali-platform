"""Knowledge-graph candidate search and bounded chat topology payloads."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session, joinedload

from ..candidate_graph import client as graph_client
from ..candidate_graph import search as graph_search
from ..models.candidate_application import CandidateApplication
from ..models.user import User
from ..services.provider_error_evidence import safe_provider_error_code
from .payloads import application_summary

logger = logging.getLogger("taali.mcp.graph_handlers")


def graph_search_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Search the tenant's Graphiti facts and return open applications."""

    text = (query or "").strip()
    if not text:
        raise ValueError("query must be non-empty")
    if not graph_client.is_configured():
        return {
            "applications": [],
            "graph_facts": [],
            "warnings": [
                {
                    "code": "neo4j_unavailable",
                    "message": "Knowledge graph is not configured for this deployment.",
                }
            ],
        }

    try:
        payload = graph_search.subgraph_for_query(
            organization_id=int(user.organization_id),
            query=text,
        )
    except Exception as exc:
        logger.warning(
            "Knowledge graph search failed error_code=%s",
            safe_provider_error_code(exc, operation="graph_search_candidates"),
        )
        return {
            "applications": [],
            "graph_facts": [],
            "warnings": [
                {
                    "code": "neo4j_unavailable",
                    "message": "Knowledge graph is temporarily unavailable.",
                }
            ],
        }
    candidate_ids: list[int] = []
    seen: set[int] = set()
    for node in payload.nodes:
        if node.label != "Person":
            continue
        raw = node.extra.get("taali_id") if isinstance(node.extra, dict) else None
        try:
            candidate_id = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            candidate_id = None
        if candidate_id is None or candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate_ids.append(candidate_id)

    if not candidate_ids:
        return {
            "applications": [],
            "graph_facts": facts_from_payload(payload, limit=10),
            "graph": graph_topology(payload),
            "warnings": [],
        }

    applications = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.candidate_id.in_(candidate_ids),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .all()
    )
    applications.sort(
        key=lambda application: (
            application.taali_score_cache_100
            if application.taali_score_cache_100 is not None
            else float("-inf")
        ),
        reverse=True,
    )
    capped = applications[: max(1, min(int(limit), 100))]
    return {
        "applications": [application_summary(application) for application in capped],
        "graph_facts": facts_from_payload(payload, limit=10),
        "graph": graph_topology(payload),
        "warnings": [],
    }


def graph_topology(payload) -> dict[str, Any]:
    """Build a bounded, referentially complete graph for the chat renderer."""

    raw_nodes = payload.nodes or []
    candidate_edges = list((payload.edges or [])[:100])
    nodes_by_id = {node.id: node for node in raw_nodes}
    kept_ids: list[str] = []
    seen_kept: set[str] = set()

    def _try_add(node_id: str) -> None:
        if not node_id or node_id in seen_kept or len(kept_ids) >= 60:
            return
        if node_id not in nodes_by_id:
            return
        seen_kept.add(node_id)
        kept_ids.append(node_id)

    for edge in candidate_edges:
        _try_add(edge.source)
        _try_add(edge.target)
    for node in raw_nodes:
        if len(kept_ids) >= 60:
            break
        _try_add(node.id)

    nodes = [
        {
            "id": nodes_by_id[node_id].id,
            "label": nodes_by_id[node_id].label,
            "name": nodes_by_id[node_id].name,
            "extra": (
                nodes_by_id[node_id].extra
                if isinstance(nodes_by_id[node_id].extra, dict)
                else {}
            ),
        }
        for node_id in kept_ids
    ]
    edges = [
        {
            "source": edge.source,
            "target": edge.target,
            "label": edge.label,
            "fact": (
                (edge.extra or {}).get("fact")
                if isinstance(edge.extra, dict)
                else None
            ),
        }
        for edge in candidate_edges
        if edge.source in seen_kept and edge.target in seen_kept
    ]
    return {"nodes": nodes, "edges": edges}


def facts_from_payload(payload, *, limit: int) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for edge in payload.edges or []:
        fact = (edge.extra or {}).get("fact") if isinstance(edge.extra, dict) else None
        if not fact:
            continue
        facts.append(
            {
                "fact": str(fact),
                "source": edge.source,
                "target": edge.target,
                "label": str(edge.label),
            }
        )
        if len(facts) >= limit:
            break
    return facts


__all__ = ["facts_from_payload", "graph_search_candidates", "graph_topology"]
