"""Graphiti-backed :class:`KnowledgeGraphBackend` (optional at-scale swap).

The in-process backend (:mod:`.inprocess`) is the default and is
**DB-backed** — it reads/writes Episodes/priors directly through the
platform Postgres, so it is multi-process-safe and fully functional in
production at low/moderate volume. The brand achieves tali-platform
parity on the in-process backend; Graphiti is **not** a parity
requirement.

This Graphiti backend is an opt-in swap for the *at-scale* case
(GraphRAG multi-hop priors over a Neo4j graph instead of the
in-process discriminator-key heuristic), selected via
``MAINSPRING_KG_BACKEND=graphiti``.

GraphRAG read path (ADR-0010 KG cutover). :meth:`get_priors` runs the
four multi-hop Cypher queries (referrer track record, company overlap
with top performers, graph-similar past candidates, skill→role→outcome
paths) at a temporal anchor and synthesises them into a single
calibrated prior. The Cypher + synthesis are a FAITHFUL, CHARACTER-
IDENTICAL port of tali-platform's working GraphRAG
(:mod:`.graphrag.graphrag_queries`), so over the same Neo4j graph they
return byte-identical priors by construction — see
``tests/test_kg_graphrag_synthesis_parity.py``.

Optional dependency: ``graphiti-core[anthropic,voyageai]`` + ``neo4j``
ship only with the ``mainspring[knowledge_graph]`` extra. All imports of
those libraries are lazy (deferred to the first real Graphiti call via
:mod:`.graphrag.client`), so importing this module — and constructing a
``GraphitiBackend`` — never requires the extra. When Graphiti is not
configured (no ``NEO4J_URI`` / ``VOYAGE_API_KEY``) or the graph has not
ingested the case yet, :meth:`get_priors` degrades gracefully to
``Priors.empty(...)``.

The episode *write* path (``write`` / ``replay_as_of``) is still an
at-scale opt-in and not yet implemented here — episodes are written via
the local DB + outbox, and the in-process backend remains the parity
default for those. The cutover in scope is the *read*/priors path only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .base import EpisodePayload, Priors, ReplayResult


# Tunables for the GraphRAG read path (mirror tali's GraphPriorsSubAgent
# defaults). These only affect *which* signals we attempt to read; the
# synthesiser collapses to confidence=0 when no source produces output.
GRAPHRAG_MAX_SIMILAR = 10
GRAPHRAG_MAX_OVERLAP_COMPANIES = 10
GRAPHRAG_MAX_SKILL_PATHS = 15


class GraphitiBackend:
    """Production backend that talks to a Graphiti / Neo4j cluster.

    Read-through GraphRAG priors via :meth:`get_priors`; episode writes
    still flow through the local DB + outbox (the in-process backend is
    the write/replay parity default).

    Configuration is read from the environment by :mod:`.graphrag.client`
    (``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` /
    ``NEO4J_DATABASE`` / ``VOYAGE_API_KEY`` / ``ANTHROPIC_API_KEY`` and
    the ``GRAPHITI_*`` model knobs). Construction is lazy: the class does
    not connect until the first method call.
    """

    name = "graphiti"

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        # url/token kept for back-compat with the prior stub signature;
        # the GraphRAG client is configured from the environment.
        self.url = url
        self.token = token

    # ---- read: GraphRAG priors -------------------------------------------

    def get_priors(
        self,
        *,
        brand_id: int,
        case_id: int,
        role_id: Optional[int] = None,
        referrer_id: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> Priors:
        """Return the GraphRAG outcome prior for cases similar to this one.

        Runs tali's four multi-hop Cypher queries at temporal anchor
        ``as_of`` (default: now) and synthesises them with the ported
        ``synthesise_prior`` — byte-identical to tali, so the same graph
        yields the same ``p_advance`` / ``confidence``.

        Mapping onto the :class:`Priors` contract:

        * ``case_id``        — the case/candidate id passed in.
        * ``p_advance``      — tali's synthesised ``p_advance`` (verbatim).
                               ``None`` (no signal) maps to the empty
                               prior (``p_advance=0.0, confidence=0.0``),
                               so the policy engine collapses the prior's
                               weight cleanly — exactly tali's
                               graceful-degradation contract.
        * ``confidence``     — tali's synthesised ``confidence`` (verbatim).
        * ``neighbour_count``— ``len(similar_rows) + len(overlap_rows)``,
                               the same neighbour count tali's sub-agent
                               attaches to its prior.
        * ``p_positive``     — tali's GraphRAG synthesis does NOT compute a
                               separate positive-outcome probability; it
                               produces a single advance prior. We mirror
                               tali by setting ``p_positive = p_advance``
                               (tali sets ``p_hired = p_advance`` as the
                               same proxy in its sub-agent output). When
                               there is no signal, ``p_positive`` is 0.0
                               via ``Priors.empty``.
        * ``examples``       — the per-source component breakdown, so
                               callers can surface which relational signals
                               moved the prior (tali exposes the same as
                               ``components`` / citations).

        ``brand_id`` is the Graphiti tenancy key (``group_id = org-<id>``),
        matching tali's ``group_id_for_org``. ``role_id`` anchors the
        role-scoped queries; when it is ``None`` the role-scoped signals
        (company overlap, skill→outcome) are skipped and the prior is
        synthesised from the role-agnostic signals only (referrer,
        similar candidates) — same as tali when those queries return
        empty.

        Never raises: an unconfigured graph, an unavailable Graphiti, or a
        Cypher failure all degrade to ``Priors.empty(case_id)``.
        """
        from .graphrag import client as graph_client
        from .graphrag import graphrag_queries

        if not graph_client.is_configured():
            return Priors.empty(case_id)

        group_id = graph_client.group_id_for_org(int(brand_id))
        graph_candidate_id = str(int(case_id))
        graph_role_id = str(int(role_id)) if role_id is not None else None
        t = as_of or datetime.now(timezone.utc)

        # Referrer signal (optional — only when a referrer id is supplied).
        referrer = None
        if referrer_id:
            try:
                referrer = graphrag_queries.referrer_signal(
                    group_id=group_id, referrer_id=str(referrer_id), t=t
                )
            except Exception:
                referrer = None

        overlap_rows: list[dict[str, Any]] = []
        skill_rows: list[dict[str, Any]] = []
        if graph_role_id is not None:
            try:
                overlap_rows = graphrag_queries.company_overlap_with_top_performers(
                    group_id=group_id,
                    candidate_id=graph_candidate_id,
                    role_id=graph_role_id,
                    t=t,
                    limit=GRAPHRAG_MAX_OVERLAP_COMPANIES,
                )
            except Exception:
                overlap_rows = []
            try:
                skill_rows = graphrag_queries.skill_to_outcome_paths(
                    group_id=group_id,
                    candidate_id=graph_candidate_id,
                    role_id=graph_role_id,
                    t=t,
                    limit=GRAPHRAG_MAX_SKILL_PATHS,
                )
            except Exception:
                skill_rows = []

        try:
            similar_rows = graphrag_queries.similar_past_candidates(
                group_id=group_id,
                candidate_id=graph_candidate_id,
                t=t,
                top_n=GRAPHRAG_MAX_SIMILAR,
            )
        except Exception:
            similar_rows = []

        synthesis = graphrag_queries.synthesise_prior(
            referrer=referrer,
            overlap_rows=overlap_rows,
            similar_rows=similar_rows,
            skill_outcome_rows=skill_rows,
        )
        return self._priors_from_synthesis(
            case_id=int(case_id),
            synthesis=synthesis,
            neighbour_count=len(similar_rows) + len(overlap_rows),
        )

    @staticmethod
    def _priors_from_synthesis(
        *, case_id: int, synthesis: dict[str, Any], neighbour_count: int
    ) -> Priors:
        """Project tali's synthesised-prior dict onto the :class:`Priors`
        contract without altering any value tali computed.

        ``p_advance is None`` (the synthesiser's "no graph signal"
        sentinel) maps to ``Priors.empty`` so downstream weight collapses
        cleanly — tali's graceful-degradation behaviour, preserved.
        """
        p_advance = synthesis.get("p_advance")
        if p_advance is None:
            return Priors.empty(case_id)
        confidence = float(synthesis.get("confidence") or 0.0)
        p_advance = float(p_advance)
        return Priors(
            case_id=case_id,
            neighbour_count=int(neighbour_count),
            p_positive=p_advance,  # tali uses p_advance as the same proxy (p_hired)
            p_advance=p_advance,
            confidence=confidence,
            examples=list(synthesis.get("components") or []),
        )

    # ---- write / replay: still an at-scale opt-in ------------------------

    def write(self, episode: EpisodePayload) -> None:
        raise NotImplementedError(
            "GraphitiBackend.write is an at-scale opt-in and not yet "
            "implemented. Episodes are written via the local DB + outbox; "
            "use the DB-backed in-process backend for write/replay "
            "(unset MAINSPRING_KG_BACKEND or set it to 'inprocess')."
        )

    def replay_as_of(
        self, *, brand_id: int, case_id: int, as_of: datetime,
    ) -> ReplayResult:
        raise NotImplementedError(
            "GraphitiBackend.replay_as_of is an at-scale opt-in and not yet "
            "implemented. Use the DB-backed in-process backend."
        )

    def healthcheck(self) -> bool:
        """Reflect graph configuration: True iff Neo4j + Voyage are set.

        Mirrors tali's ``is_configured()`` gate. A configured-but-
        unreachable graph still degrades gracefully on read (priors fall
        back to empty), so configuration is the meaningful health signal
        here.
        """
        from .graphrag import client as graph_client

        return bool(graph_client.is_configured())


__all__ = ["GraphitiBackend"]
