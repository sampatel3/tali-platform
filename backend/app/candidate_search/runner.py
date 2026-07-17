"""End-to-end runner for natural-language candidate search.

Steps:
1. Cache lookup on (org_id, normalised query, prompt_version).
2. On miss: parse via Haiku → ``ParsedFilter`` → cache.
3. Apply hard SQL filters to a base query already scoped to the org.
4. Execute graph predicates against Neo4j (when configured) and AND-narrow
   the SQL result set by candidate id.
5. Optional deep verification: for ``soft_criteria``, ask Claude to assess a
   relevance-ordered, explicitly bounded candidate window.
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
from .query_builder_sql import apply_parsed_filter, apply_relevance_order
from .schemas import (
    CandidateDeepVerification,
    GraphPayload,
    ParsedFilter,
    SearchOutput,
    SearchWarning,
)

logger = logging.getLogger("taali.candidate_search.runner")

# How many candidates to rerank with Claude in the soft-criteria pass.
RERANK_TOP_N = 50


def _dedupe_person_rows(rows) -> list[int]:
    """Choose the first relevance-ordered application for each person."""
    seen: set[int] = set()
    out: list[int] = []
    for row in rows:
        app_id = int(row[0])
        candidate_id = int(row[1]) if len(row) > 1 and row[1] is not None else -app_id
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        out.append(app_id)
    return out


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
    role_id: int | None = None,
    nl_query: str,
    base_query,
    rerank_enabled: bool = False,
    include_subgraph: bool = False,
    parser_client=None,
    rerank_client=None,
    defer_qualitative: bool = False,
) -> SearchOutput:
    """Execute one NL search pass.

    ``base_query`` MUST already be filtered by ``organization_id`` and
    ``deleted_at IS NULL``. Caller is responsible for any other base
    constraints (role_ids, source, outcome) — they compose with our
    NL filters.

    Never raises: on any failure we degrade and surface a warning.
    """
    # All current callers use this as a read-only command.  Authentication or
    # surrounding query construction may already have opened a transaction;
    # release it before the parser or graph predicate provider can run.  The
    # SQLAlchemy Query remains executable after rollback.
    db.rollback()
    warnings: list[SearchWarning] = []
    cache_key = cache_module.compute_cache_key(
        organization_id=organization_id, query=nl_query
    )

    parsed = cache_module.get(cache_key)
    if parsed is None:
        try:
            parsed = parse_nl_query(
                nl_query,
                client=parser_client,
                organization_id=organization_id,
                role_id=role_id,
                metering={
                    "feature": "search_parse",
                    "organization_id": organization_id,
                    **({"role_id": int(role_id)} if role_id is not None else {}),
                },
            )
        except Exception as exc:  # pragma: no cover — parser already swallows
            logger.warning("Parser raised: %s", exc)
            parsed = ParsedFilter(keywords=[nl_query.strip()], free_text=nl_query.strip())
            warnings.append(
                SearchWarning(
                    code="parser_failed",
                    message=(
                        "Natural-language parsing was unavailable; keyword "
                        "search was used."
                    ),
                )
            )
        if parsed and not parsed.is_empty():
            cache_module.set(cache_key, parsed)

    # Apply hard SQL filters. ``defer_qualitative`` (the grounded top-N path,
    # which grounds qualitative criteria itself via CV citations) keeps the
    # SQL prefilter PURELY STRUCTURAL — it must not ILIKE-prefilter
    # soft_criteria or keywords, which phrase-match the pool to near-zero
    # (e.g. no CV literally contains "banking domain experience"). Otherwise
    # soft_criteria_as_keywords=False when rerank will evaluate them, and the
    # keyword ILIKE remains the residual fallback.
    soft_as_keywords = (not defer_qualitative) and not (
        rerank_enabled and parsed.soft_criteria
    )
    parsed_for_sql = parsed
    if defer_qualitative and parsed.keywords:
        # Strip the residual keyword ILIKE for the SQL pass only; the caller
        # grounds these against the CV. The returned parsed_filter keeps them.
        parsed_for_sql = parsed.model_copy(update={"keywords": []})
    sql_query = apply_parsed_filter(
        base_query, parsed_for_sql, soft_criteria_as_keywords=soft_as_keywords
    )

    # Execute graph predicates: AND-narrow by candidate id set.
    cypher_candidate_ids = _execute_graph_predicates(
        organization_id=organization_id,
        role_id=role_id,
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
                database_matches=0,
                qualified=None,
            )
        sql_query = sql_query.filter(
            CandidateApplication.candidate_id.in_(cypher_candidate_ids)
        )

    # Rank the COMPLETE deterministic retrieval set before selecting a bounded
    # verification window, then collapse multiple role applications belonging
    # to the same person. "All candidates" now means people, not application
    # rows, and the first 50 are relevant/stable rather than DB-natural.
    sql_query = apply_relevance_order(sql_query, parsed)
    rows = sql_query.with_entities(
        CandidateApplication.id,
        CandidateApplication.candidate_id,
    ).all()
    application_ids = _dedupe_person_rows(rows)
    database_matches = len(application_ids)

    # Candidate search is read-only.  Release the deterministic SQL phase
    # before optional Neo4j/Anthropic work so a bounded 50-candidate evidence
    # pass cannot pin a request connection for the duration of provider I/O.
    db.rollback()

    rerank_applied = False
    deep_checked = 0
    evidence_succeeded = 0
    evidence_failed = 0
    verification_results: list[CandidateDeepVerification] = []
    # `qualified` is reserved for the subset that passed an actual evidence
    # verification pass. Exact database matches are described separately by
    # `database_matches`; calling them qualified would overstate what ran.
    qualified = None
    capped = False
    if rerank_enabled and parsed.soft_criteria and application_ids:
        try:
            from . import rerank as rerank_module

            checked_ids = application_ids[:RERANK_TOP_N]
            batch = rerank_module.rerank_application_ids(
                db=db,
                organization_id=organization_id,
                role_id=role_id,
                application_ids=checked_ids,
                soft_criteria=parsed.soft_criteria,
                client=rerank_client,
            )
            # Deep verification is an explicit qualified subset. Candidates
            # outside the checked window are not silently called failures and
            # remain represented by database_matches/capped in the response.
            application_ids = list(batch.application_ids)
            verification_results = [
                CandidateDeepVerification(
                    application_id=outcome.application_id,
                    status=outcome.status,
                    reason=outcome.reason,
                    error_code=outcome.error_code,
                )
                for outcome in batch.outcomes
            ]
            deep_checked = len(batch.outcomes)
            evidence_succeeded = int(batch.evidence_succeeded)
            evidence_failed = int(batch.evidence_failed)
            rerank_applied = evidence_succeeded > 0
            qualified = int(batch.qualified) if evidence_succeeded > 0 else None
            # Evidence is exhaustive only when every deterministic match
            # completed successfully. Failed checks are retained as
            # unclassified rows and therefore also make coverage partial.
            capped = evidence_succeeded < database_matches
            if evidence_failed:
                warnings.append(
                    SearchWarning(
                        code="rerank_partial",
                        message=(
                            f"{evidence_failed} of {deep_checked} evidence checks "
                            "failed; affected candidates remain unclassified and "
                            "were not counted as qualified or not qualified."
                        ),
                    )
                )
            if database_matches > deep_checked:
                warnings.append(
                    SearchWarning(
                        code="verification_capped",
                        message=(
                            f"Attempted evidence checks for {deep_checked} of "
                            f"{database_matches} database matches; "
                            f"{evidence_succeeded} completed successfully. "
                            "Unchecked candidates were not classified as failures."
                        ),
                    )
                )
        except Exception as exc:
            logger.warning("Rerank failed; passing through SQL results: %s", exc)
            # The deterministic retrieval remains intact, but evidence coverage
            # is not exhaustive when a requested pass could not start.
            capped = database_matches > 0
            warnings.append(
                SearchWarning(
                    code="rerank_skipped",
                    message=(
                        "Deep verification was unavailable; showing database "
                        "matches instead."
                    ),
                )
            )

    subgraph: GraphPayload | None = None
    if include_subgraph and application_ids:
        try:
            from ..candidate_graph import search as graph_search

            candidate_ids = _candidate_ids_for_application_ids(db, application_ids)
            episode_selectors = graph_search.episode_selectors_for_candidates(
                db,
                candidate_ids,
            )
            db.rollback()
            subgraph = graph_search.subgraph_for_candidates(
                organization_id=organization_id,
                candidate_ids=candidate_ids,
                episode_selectors=episode_selectors,
            )
            if not subgraph.nodes:
                warnings.append(
                    SearchWarning(
                        code="graph_data_missing",
                        message=(
                            "No graph evidence is available for the matched "
                            "candidates."
                        ),
                    )
                )
            if subgraph and subgraph.nodes:
                _enrich_graph_scores(db, organization_id, subgraph)
        except Exception as exc:
            logger.warning("Subgraph fetch failed: %s", exc)
            warnings.append(
                SearchWarning(
                    code="neo4j_unavailable",
                    message="Graph view is temporarily unavailable.",
                )
            )

    return SearchOutput(
        application_ids=application_ids,
        parsed_filter=parsed,
        warnings=warnings,
        rerank_applied=rerank_applied,
        subgraph=subgraph,
        database_matches=database_matches,
        deep_checked=deep_checked,
        evidence_succeeded=evidence_succeeded,
        evidence_failed=evidence_failed,
        qualified=qualified,
        verification_results=verification_results,
        capped=capped,
        exhaustive=not capped,
    )


def _execute_graph_predicates(
    *,
    organization_id: int,
    role_id: int | None,
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
            role_id=role_id,
            predicates=parsed.graph_predicates,
        )
    except Exception as exc:
        logger.warning("Graph predicate execution failed: %s", exc)
        warnings.append(
            SearchWarning(
                code="graph_predicate_dropped",
                message=(
                    "Graph predicates were unavailable and were ignored for "
                    "this search."
                ),
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
