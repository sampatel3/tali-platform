"""Pydantic schemas for natural-language candidate search.

The parser produces a ``ParsedFilter``; the runner consumes it and
returns a ``SearchOutput``. Both shapes are part of the public API
surfaced at ``GET /applications?nl_query=...``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


GraphPredicateType = Literal[
    "worked_at",          # company match: candidate has WORKED_AT this company
    "studied_at",         # school match
    "colleague_of",       # shared an employer with named person/candidate
    "n_hop_from",         # within N hops of a named candidate
]


class GraphPredicate(BaseModel):
    """One graph-shaped condition extracted from an NL query.

    ``value`` is the entity name (e.g. company name) or candidate id.
    Server-side normalisation lower-cases and strips it before lookup.
    """

    type: GraphPredicateType
    value: str
    n_hops: Optional[int] = Field(default=None, ge=1, le=4)


class ParsedFilter(BaseModel):
    """Structured representation of an NL query.

    Hard filters (``skills_*``, ``locations_*``, ``min_years_experience``)
    translate to Postgres JSONB clauses. ``graph_predicates`` translate to
    Cypher and require Neo4j to be configured. ``soft_criteria`` are
    qualitative phrases the SQL layer can't express (e.g. "in production")
    — they trigger a Claude rerank on the SQL/Cypher result set.
    ``keywords`` is the residual ILIKE fallback against ``cv_text``.
    ``free_text`` carries the original query for diagnostics.
    """

    skills_all: list[str] = Field(default_factory=list)
    skills_any: list[str] = Field(default_factory=list)
    locations_country: list[str] = Field(default_factory=list)
    locations_region: list[str] = Field(default_factory=list)
    min_years_experience: Optional[int] = Field(default=None, ge=0, le=60)
    graph_predicates: list[GraphPredicate] = Field(default_factory=list)
    soft_criteria: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    free_text: Optional[str] = None

    def is_empty(self) -> bool:
        """True when no usable filter was extracted."""
        return not (
            self.skills_all
            or self.skills_any
            or self.locations_country
            or self.locations_region
            or self.min_years_experience
            or self.graph_predicates
            or self.soft_criteria
            or self.keywords
        )


class SearchWarning(BaseModel):
    """Soft warning surfaced to the UI alongside results."""

    code: Literal[
        "parser_failed",
        "neo4j_unavailable",
        "rerank_skipped",
        "rerank_partial",
        "graph_predicate_dropped",
    ]
    message: str


class GraphNode(BaseModel):
    id: str
    label: Literal["Person", "Company", "School", "Skill", "Country"]
    name: str
    extra: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    label: Literal["WORKED_AT", "STUDIED_AT", "HAS_SKILL", "LOCATED_IN"]
    extra: dict = Field(default_factory=dict)


class GraphPayload(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class SearchOutput(BaseModel):
    """End-to-end output returned to the route handler."""

    application_ids: list[int]
    parsed_filter: ParsedFilter
    warnings: list[SearchWarning] = Field(default_factory=list)
    rerank_applied: bool = False
    subgraph: Optional[GraphPayload] = None
