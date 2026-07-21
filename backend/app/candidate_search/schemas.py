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
    Cypher and require Neo4j to be configured. ``soft_criteria`` are required
    qualitative phrases the SQL layer can't express (e.g. "in production").
    ``preferred_criteria`` are qualitative phrases the recruiter explicitly
    hedged as optional (e.g. "ideally Big Four"). Both are grounded for the
    bounded top-candidate path, but only required criteria gate the primary
    shortlist.
    ``keywords`` is the residual ILIKE fallback against ``cv_text``.
    ``free_text`` carries the original query for diagnostics.
    """

    skills_all: list[str] = Field(default_factory=list)
    skills_any: list[str] = Field(default_factory=list)
    titles_all: list[str] = Field(default_factory=list)
    titles_any: list[str] = Field(default_factory=list)
    locations_country: list[str] = Field(default_factory=list)
    locations_region: list[str] = Field(default_factory=list)
    min_years_experience: Optional[int] = Field(default=None, ge=0, le=60)
    graph_predicates: list[GraphPredicate] = Field(default_factory=list)
    # Boolean relation between legacy flat graph predicates. Typed search plans
    # can express nested logic; this compatibility field prevents an explicit
    # employer/school OR from silently becoming an intersection.
    graph_predicate_operator: Literal["all", "any"] = "all"
    soft_criteria: list[str] = Field(default_factory=list)
    preferred_criteria: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    free_text: Optional[str] = None
    # Internal provenance bit: the paid/model parser could not produce a valid
    # structure and the whole query was retained only as a lexical fallback.
    # Callers use this to avoid presenting a definitive evidence-screened zero.
    parse_degraded: bool = False

    def is_empty(self) -> bool:
        """True when no usable filter was extracted."""
        return not (
            self.skills_all
            or self.skills_any
            or self.titles_all
            or self.titles_any
            or self.locations_country
            or self.locations_region
            or self.min_years_experience
            or self.graph_predicates
            or self.soft_criteria
            or self.preferred_criteria
            or self.keywords
        )


class SearchWarning(BaseModel):
    """Soft warning surfaced to the UI alongside results."""

    code: Literal[
        "parser_failed",
        "search_plan_failed",
        "unsupported_search_constraint",
        "neo4j_unavailable",
        "rerank_skipped",
        "rerank_partial",
        "graph_predicate_dropped",
        "graph_retrieval_unavailable",
        "graph_retrieval_failed",
        "graph_coverage_partial",
        "verification_capped",
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


class CandidateDeepVerification(BaseModel):
    """Auditable tri-state outcome for one optional qualitative check."""

    application_id: int
    status: Literal["qualified", "not_qualified", "error"]
    reason: Optional[str] = None
    error_code: Optional[str] = None


class SearchRetrievalTrace(BaseModel):
    """Auditable rank contribution for one candidate/application pair."""

    application_id: int
    candidate_id: int
    score: float
    sources: list[Literal["graph", "postgres"]] = Field(default_factory=list)
    # Public traces are one-based even though backend adapters may use
    # zero-based offsets internally.
    graph_rank: Optional[int] = Field(default=None, ge=1)
    postgres_rank: Optional[int] = Field(default=None, ge=1)
    evidence: list[dict] = Field(default_factory=list)


class SearchRetrievalSummary(BaseModel):
    """Backend coverage is separate from evidence qualification coverage."""

    mode: Literal["postgres_only", "graph_only", "hybrid"]
    graph_status: Literal["ok", "unavailable", "error", "not_selected"]
    graph_coverage: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    capped: bool = False
    exhaustive: bool = True
    is_exact_empty: bool = False
    hits: list[SearchRetrievalTrace] = Field(default_factory=list)


class SearchOutput(BaseModel):
    """End-to-end output returned to the route handler."""

    application_ids: list[int]
    parsed_filter: ParsedFilter
    warnings: list[SearchWarning] = Field(default_factory=list)
    rerank_applied: bool = False
    subgraph: Optional[GraphPayload] = None
    # Person-level counts. ``database_matches`` counts the PostgreSQL branch;
    # ``retrieval_matches`` counts the fused GraphDB/PostgreSQL candidate set.
    database_matches: Optional[int] = None
    retrieval_matches: Optional[int] = None
    # Attempted per-candidate checks, including explicit error outcomes.
    deep_checked: int = 0
    # Completed positive/negative checks vs checks that could not produce a
    # decision. A transport/JSON error is never counted as not-qualified.
    evidence_succeeded: int = 0
    evidence_failed: int = 0
    qualified: Optional[int] = None
    verification_results: list[CandidateDeepVerification] = Field(default_factory=list)
    # Auditable backend-independent meaning used by retrieval and offline evals.
    search_plan: Optional[dict] = None
    retrieval: Optional[SearchRetrievalSummary] = None
    capped: bool = False
    exhaustive: bool = True
    # A zero count is safe to describe as "no matches" only when every
    # selected backend completed exhaustively and returned no authorized hit.
    is_exact_empty: bool = False
