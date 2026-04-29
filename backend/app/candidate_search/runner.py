"""End-to-end runner for natural-language candidate search.

Steps:
1. Cache lookup on (org_id, normalised query, prompt_version).
2. On miss: parse via Haiku → ``ParsedFilter`` → cache.
3. Apply hard SQL filters to a base query already scoped to the org.
4. Execute graph predicates against Neo4j (when configured) and AND-narrow
   the SQL result set by candidate id.
5. Optional rerank: for ``soft_criteria``, ask Claude to assess the top N
   candidates with their graph-neighbourhood as context.
6. (When view=graph) fetch the subgraph for the matched candidate set.

Returns ``SearchOutput`` with the final application ids, parsed filter,
warnings, and (optionally) the subgraph payload.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from . import cache as cache_module
from .parser import parse_nl_query
from .query_builder_sql import apply_parsed_filter
from .schemas import (
    GraphPayload,
    ParsedFilter,
    SearchOutput,
    SearchWarning,
)

logger = logging.getLogger("taali.candidate_search.runner")

# How many candidates to rerank with Claude in the soft-criteria pass.
RERANK_TOP_N = 50


def _candidate_ids_for_application_ids(
    db: Session, application_ids: Iterable[int]
) -> list[int]:
    """Helper: pull distinct candidate ids out of a set of application ids."""
    if not application_ids:
        return []
    rows = (
        db.query(CandidateApplication.candidate_id)
        .filter(CandidateApplication.id.in_(list(application_ids)))
        .distinct()
        .all()
    )
    return [int(r[0]) for r in rows if r[0] is not None]


def run_search(
    *,
    db: Session,
    organization_id: int,
    nl_query: str,
    base_query,
    rerank_enabled: bool = True,
    include_subgraph: bool = False,
    parser_client=None,
    rerank_client=None,
) -> SearchOutput:
    """Execute one NL search pass.

    ``base_query`` MUST already be filtered by ``organization_id`` and
    ``deleted_at IS NULL``. Caller is responsible for any other base
    constraints (role_ids, source, outcome) — they compose with our
    NL filters.

    Never raises: on any failure we degrade and surface a warning.
    """
    warnings: list[SearchWarning] = []
    cache_key = cache_module.compute_cache_key(
        organization_id=organization_id, query=nl_query
    )

    parsed = cache_module.get(cache_key)
    if parsed is None:
        try:
            parsed = parse_nl_query(nl_query, client=parser_client)
        except Exception as exc:  # pragma: no cover — parser already swallows
            logger.warning("Parser raised: %s", exc)
            parsed = ParsedFilter(keywords=[nl_query.strip()], free_text=nl_query.strip())
            warnings.append(
                SearchWarning(code="parser_failed", message=f"NL parser failed: {exc}")
            )
        if parsed and not parsed.is_empty():
            cache_module.set(cache_key, parsed)

    # Apply hard SQL filters. soft_criteria_as_keywords=False when rerank
    # is enabled — we rely on Claude to evaluate qualitative criteria
    # rather than ILIKE-prefilter and risk false negatives.
    soft_as_keywords = not (rerank_enabled and parsed.soft_criteria)
    sql_query = apply_parsed_filter(
        base_query, parsed, soft_criteria_as_keywords=soft_as_keywords
    )

    # Execute graph predicates: AND-narrow by candidate id set.
    cypher_candidate_ids = _execute_graph_predicates(
        organization_id=organization_id,
        parsed=parsed,
        warnings=warnings,
    )
    if cypher_candidate_ids is not None:
        # cypher_candidate_ids == [] means "no graph match" → empty result set.
        if not cypher_candidate_ids:
            return SearchOutput(
                application_ids=[],
                parsed_filter=parsed,
                warnings=warnings,
                rerank_applied=False,
                subgraph=None,
            )
        sql_query = sql_query.filter(
            CandidateApplication.candidate_id.in_(cypher_candidate_ids)
        )

    # Fetch matching application ids. Caller will paginate / sort downstream.
    application_ids = [
        int(row_id)
        for (row_id,) in sql_query.with_entities(CandidateApplication.id).all()
    ]

    rerank_applied = False
    if rerank_enabled and parsed.soft_criteria and application_ids:
        try:
            from . import rerank as rerank_module

            kept = rerank_module.rerank_application_ids(
                db=db,
                organization_id=organization_id,
                application_ids=application_ids[:RERANK_TOP_N],
                soft_criteria=parsed.soft_criteria,
                client=rerank_client,
            )
            # Preserve original order; drop those rerank rejected.
            kept_set = set(kept)
            application_ids = [aid for aid in application_ids if aid in kept_set]
            rerank_applied = True
        except Exception as exc:
            logger.warning("Rerank failed; passing through SQL results: %s", exc)
            warnings.append(
                SearchWarning(
                    code="rerank_skipped",
                    message=f"Rerank skipped due to error: {exc}",
                )
            )

    subgraph: GraphPayload | None = None
    if include_subgraph and application_ids:
        try:
            from ..candidate_graph import search as graph_search

            candidate_ids = _candidate_ids_for_application_ids(db, application_ids)
            subgraph = graph_search.subgraph_for_candidates(
                organization_id=organization_id,
                candidate_ids=candidate_ids,
            )
            # Fallback: if none of the matched candidates are in the graph yet
            # (partial backfill), do a broad query so the graph view shows
            # something useful rather than "No graph data".
            if not subgraph.nodes:
                subgraph = graph_search.subgraph_for_query(
                    organization_id=organization_id,
                    query=nl_query,
                )
            if subgraph and subgraph.nodes:
                _enrich_graph_scores(db, organization_id, subgraph)
        except Exception as exc:
            logger.warning("Subgraph fetch failed: %s", exc)
            warnings.append(
                SearchWarning(
                    code="neo4j_unavailable",
                    message=f"Graph view unavailable: {exc}",
                )
            )

    return SearchOutput(
        application_ids=application_ids,
        parsed_filter=parsed,
        warnings=warnings,
        rerank_applied=rerank_applied,
        subgraph=subgraph,
    )


def _execute_graph_predicates(
    *,
    organization_id: int,
    parsed: ParsedFilter,
    warnings: list[SearchWarning],
) -> list[int] | None:
    """Run graph predicates against Neo4j.

    Returns:
      - ``None`` when there are no graph predicates (no narrowing).
      - ``[]`` when predicates ran but matched zero candidates.
      - ``list[int]`` of candidate ids matching ALL predicates otherwise.

    On Neo4j unavailability we surface a warning and drop the predicates
    (returns ``None``) so the rest of the search still produces results.
    """
    if not parsed.graph_predicates:
        return None

    try:
        from ..candidate_graph import client as graph_client
        from ..candidate_graph import search as graph_search

        if not graph_client.is_configured():
            warnings.append(
                SearchWarning(
                    code="neo4j_unavailable",
                    message="Neo4j is not configured; graph predicates ignored.",
                )
            )
            return None

        return graph_search.candidate_ids_matching_all(
            organization_id=organization_id,
            predicates=parsed.graph_predicates,
        )
    except Exception as exc:
        logger.warning("Graph predicate execution failed: %s", exc)
        warnings.append(
            SearchWarning(
                code="graph_predicate_dropped",
                message=f"Graph predicates failed: {exc}",
            )
        )
        return None


def _enrich_graph_scores(
    db: Session,
    organization_id: int,
    subgraph: "GraphPayload",
) -> None:
    """Mutate Person nodes in-place: add cv_match_score from Postgres.

    Uses the best (max) score across all applications for that candidate
    within the org, so multi-role candidates get their highest score shown.
    """
    from sqlalchemy import func

    # IDs may be integer-based ("person:12345") or UUID-based ("person:0e83358e-...")
    # depending on whether taali_id was stored on the Graphiti entity node.
    # Only int-based IDs can be joined back to Postgres.
    person_ids = []
    for n in subgraph.nodes:
        if n.label == "Person" and n.id.startswith("person:"):
            raw = n.id.split(":")[1]
            try:
                person_ids.append(int(raw))
            except ValueError:
                pass

    if not person_ids:
        return

    rows = (
        db.query(
            CandidateApplication.candidate_id,
            func.max(CandidateApplication.cv_match_score),
        )
        .filter(
            CandidateApplication.candidate_id.in_(person_ids),
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .group_by(CandidateApplication.candidate_id)
        .all()
    )
    scores_map = {int(r[0]): r[1] for r in rows if r[0] is not None}

    for node in subgraph.nodes:
        if node.label == "Person" and node.id.startswith("person:"):
            raw = node.id.split(":")[1]
            try:
                cid = int(raw)
                node.extra["cv_match_score"] = scores_map.get(cid)
            except ValueError:
                pass
