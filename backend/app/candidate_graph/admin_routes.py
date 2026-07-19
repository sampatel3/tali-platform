"""Implementation helpers for the two billable Graphiti admin diagnostics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException, Request

from ..candidate_search.input_contracts import bounded_candidate_search_query
from ..services.provider_error_evidence import safe_provider_error_code
from . import client as graph_client
from .admin_operations import (
    attributed_admin_graph_call,
    require_admin_graph_organization,
)
from .episodes import Episode

logger = logging.getLogger("taali.candidate_graph.admin")


def _required_org_id(request: Request) -> int:
    try:
        raw_org_id = request.query_params.get("org_id")
        if raw_org_id is None:
            raise ValueError("org_id is required")
        return require_admin_graph_organization(int(raw_org_id))
    except (TypeError, ValueError) as exc:
        detail = str(exc) if str(exc).startswith("org_id") else "org_id must be a positive integer"
        raise HTTPException(status_code=400, detail=detail) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _authority(request: Request, operation: str):
    try:
        org_id = _required_org_id(request)
        authority = attributed_admin_graph_call(org_id, operation=operation)
        authority.__enter__()
        return org_id, authority
    except HTTPException:
        raise


def search_debug_response(
    request: Request,
) -> dict[str, Any]:
    raw_query = request.query_params.get("q")
    if raw_query is None:
        raise HTTPException(status_code=400, detail="q is required")
    try:
        query = bounded_candidate_search_query(raw_query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    org_id, authority = _authority(request, "search-debug")
    provider_failure: HTTPException | None = None
    try:
        if not graph_client.is_configured():
            return {"status": "unconfigured"}
        group_id = graph_client.group_id_for_org(org_id)
        graphiti = graph_client.get_graphiti()
        results = graph_client.run_async(
            graphiti.search(query=query, group_ids=[group_id], num_results=5),
            timeout=15.0,
        )
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_debug_search")
        logger.error("Graphiti debug search failed error_code=%s", code)
        provider_failure = HTTPException(
            status_code=503,
            detail="Graph search is temporarily unavailable",
        )
    finally:
        authority.__exit__(None, None, None)
    if provider_failure is not None:
        raise provider_failure

    if results is None:
        return {"results": None, "count": 0}
    items = (
        results
        if isinstance(results, (list, tuple))
        else getattr(results, "edges", results) or []
    )
    materialized = list(items)
    out = []
    for item in materialized[:5]:
        source = getattr(item, "source_node", None)
        target = getattr(item, "target_node", None)
        out.append(
            {
                "type": type(item).__name__,
                "uuid": getattr(item, "uuid", None),
                "fact": getattr(item, "fact", None),
                "has_source_node": source is not None,
                "has_target_node": target is not None,
                "source_uuid": getattr(source, "uuid", None) if source else None,
                "source_name": getattr(source, "name", None) if source else None,
                "target_uuid": getattr(target, "uuid", None) if target else None,
                "target_name": getattr(target, "name", None) if target else None,
                "group_id": getattr(item, "group_id", None),
            }
        )
    return {
        "query": query,
        "group_id": group_id,
        "count": len(materialized),
        "results": out,
    }


def test_episode_response(
    request: Request,
) -> dict[str, Any]:
    org_id, authority = _authority(request, "test-episode")
    provider_failure: HTTPException | None = None
    try:
        if not graph_client.is_configured():
            return {"status": "unconfigured"}
        organization_group_id = graph_client.group_id_for_org(org_id)
        debug_group_id = f"{organization_group_id}:admin-connectivity"
        episode = Episode(
            name="test-episode-debug",
            body=(
                "Subject candidate: Test Person (taali_id=0)\n"
                "This is a test episode for connectivity verification."
            ),
            source_description="admin.test",
            reference_time=datetime.now(timezone.utc),
            # Never mix a synthetic connectivity subject into the candidate
            # search group. A deterministic UUID also makes repeated probes
            # update the same diagnostic episode instead of growing junk data.
            group_id=debug_group_id,
        )
        graphiti = graph_client.get_graphiti()
        from graphiti_core.nodes import EpisodeType  # type: ignore[import-not-found]

        graph_client.run_async(
            graphiti.add_episode(
                name=episode.name,
                episode_body=episode.body,
                source=EpisodeType.text,
                source_description=episode.source_description,
                reference_time=episode.reference_time,
                group_id=episode.group_id,
                uuid=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"taali:{debug_group_id}:test-episode",
                    )
                ),
            ),
            timeout=120.0,
        )
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_test_episode")
        logger.error("Graphiti test episode failed error_code=%s", code)
        provider_failure = HTTPException(
            status_code=503,
            detail="Graphiti test episode failed; see server logs.",
        )
    finally:
        authority.__exit__(None, None, None)
    if provider_failure is not None:
        raise provider_failure
    return {"status": "ok", "episodes_sent": 1}


def _safe_records(result: Any) -> list[dict[str, str | None]]:
    return [
        {
            key: str(record[key]) if record[key] is not None else None
            for key in record.keys()
        }
        for record in (result.records or [])
    ]


def cypher_debug_response(request: Request) -> dict[str, Any]:
    """Run the three bounded, organization-scoped Cypher diagnostics."""

    raw_query = request.query_params.get("q")
    if raw_query is None:
        raise HTTPException(status_code=400, detail="q is required")
    try:
        query = bounded_candidate_search_query(raw_query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    org_id = _required_org_id(request)
    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    group_id = graph_client.group_id_for_org(org_id)
    provider_failure: HTTPException | None = None
    try:
        graphiti = graph_client.get_graphiti()
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_debug_connect")
        logger.error("Graphiti Cypher debug connection failed error_code=%s", code)
        provider_failure = HTTPException(
            status_code=503,
            detail="Graph database is temporarily unavailable",
        )
    if provider_failure is not None:
        raise provider_failure

    out: dict[str, Any] = {"group_id": group_id, "query": query}
    try:
        result = graph_client.run_async(
            graphiti.driver.execute_query(
                "MATCH ()-[e]->() RETURN DISTINCT type(e) AS t LIMIT 10"
            ),
            timeout=10.0,
        )
        out["rel_types"] = [str(record["t"]) for record in (result.records or [])]
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_rel_diagnostic")
        logger.error("Graphiti relationship-type diagnostic failed error_code=%s", code)
        out["rel_types_error"] = "unavailable"

    try:
        result = graph_client.run_async(
            graphiti.driver.execute_query(
                "MATCH (s)-[e]->(t) WHERE e.group_id = $group_id "
                "RETURN type(e) AS rel, e.fact AS fact, s.name AS s, "
                "t.name AS t LIMIT 5",
                parameters_={"group_id": group_id},
            ),
            timeout=10.0,
        )
        out["org_edges_sample"] = _safe_records(result)
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_edge_diagnostic")
        logger.error("Graphiti organization-edge diagnostic failed error_code=%s", code)
        out["org_edges_error"] = "unavailable"

    try:
        result = graph_client.run_async(
            graphiti.driver.execute_query(
                "MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity) "
                "WHERE e.group_id = $group_id "
                "AND toLower(e.fact) CONTAINS toLower($query) "
                "RETURN s.uuid AS s_uuid, s.name AS s, t.uuid AS t_uuid, "
                "t.name AS t, e.name AS e_name, e.fact AS fact LIMIT 10",
                parameters_={"group_id": group_id, "query": query},
            ),
            timeout=10.0,
        )
        out["cypher_matches"] = _safe_records(result)
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_match_diagnostic")
        logger.error("Graphiti Cypher-match diagnostic failed error_code=%s", code)
        out["cypher_error"] = "unavailable"
    return out


__all__ = [
    "cypher_debug_response",
    "search_debug_response",
    "test_episode_response",
]
