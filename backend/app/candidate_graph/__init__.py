"""Candidate knowledge graph (Neo4j-backed).

Public surface:

    from app.candidate_graph import is_configured, sync_candidate
    from app.candidate_graph.queries import (
        candidate_ids_matching_all,
        subgraph_for_candidates,
    )

Postgres remains the source of truth for candidates. Neo4j is a derived
projection: idempotent upsert from ``candidate.experience_entries``,
``education_entries``, ``skills``, and the candidate's own location.
Multi-tenancy is enforced at query time — every node and edge carries
``organization_id`` and every Cypher template begins with a tenancy
filter.

The integration is optional: when ``settings.NEO4J_URI`` is empty the
module degrades silently (sync is a no-op, queries return empty results,
search drops graph predicates with a warning).
"""

from .client import is_configured, get_driver  # noqa: F401
from .sync import sync_candidate  # noqa: F401

__all__ = ["is_configured", "get_driver", "sync_candidate"]
