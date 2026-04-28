"""Candidate knowledge graph (Graphiti-backed).

Public surface:

    from app.candidate_graph import is_configured, sync_candidate
    from app.candidate_graph.search import (
        candidate_ids_matching_all,
        subgraph_for_candidates,
        colleague_neighbourhood,
    )

Postgres remains the source of truth. Graphiti owns the temporal
knowledge graph in Neo4j: ingests Candidate profiles, full interview
transcripts, raw CV text, and pipeline events as episodes; extracts
entities and relationships with Anthropic; embeds them with Voyage AI;
serves hybrid (graph + BM25 + vector) search on top.

Multi-tenancy: every episode is namespaced via Graphiti's ``group_id``,
set to ``f"org-{organization_id}"``. Cross-org traversal is impossible
because all search APIs filter on group_id.

The integration is optional: when ``settings.NEO4J_URI`` or
``settings.VOYAGE_API_KEY`` is empty, the module degrades silently —
sync is a no-op, queries return empty results, NL search drops graph
predicates with a warning.
"""

from .client import is_configured, get_graphiti, group_id_for_org  # noqa: F401
from .sync import sync_candidate, sync_interview, sync_event  # noqa: F401

__all__ = [
    "is_configured",
    "get_graphiti",
    "group_id_for_org",
    "sync_candidate",
    "sync_interview",
    "sync_event",
]
