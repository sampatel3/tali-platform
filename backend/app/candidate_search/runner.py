"""End-to-end runner for natural-language candidate search.

Steps:
1. Cache lookup on (org_id, normalised query, prompt_version).
2. On miss: parse via Haiku → ``ParsedFilter`` → cache.
3. Compile a backend-independent ``SearchPlan``.
4. Run semantic GraphDB recall plus ranked PostgreSQL retrieval, fused behind
   the PostgreSQL tenant/role/lifecycle authorization boundary.
5. Optional deep verification: assess a
   relevance-ordered, explicitly bounded candidate window.
6. (When view=graph) fetch the subgraph for the matched candidate set.

Returns ``SearchOutput`` with the final application ids, parsed filter,
warnings, and (optionally) the subgraph payload.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import case
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from . import cache as cache_module
from .criteria_policy import DEFAULT_MAX_CRITERIA, _collect_criteria, _required_criteria
from .hybrid import retrieve_graph_backend, run_hybrid_retrieval
from .parser import ProviderCallsForbiddenError, parse_nl_query
from .person_retrieval import MAX_PERSON_RETRIEVAL_LIMIT, bounded_person_rows
from .plan_adapter import parsed_filter_to_search_plan
from .plan_evidence import graph_evidence_requirements
from .population import (
    apply_searchable_candidate_scope,
    application_map_from_rows,
    population_filter,
)
from .query_builder_sql import (
    apply_parsed_filter,
    apply_relevance_order,
    needs_candidate_join,
)
from .retrieval import BackendHit, BackendResult, BackendStatus, RetrievalMode
from .retrieval_reporting import append_retrieval_warnings, retrieval_summary
from .runtime_capabilities import unsupported_runtime_requirements
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
DEFAULT_RETRIEVAL_LIMIT = 200
MAX_RETRIEVAL_LIMIT = MAX_PERSON_RETRIEVAL_LIMIT


def _assert_provider_free_filter(parsed: ParsedFilter) -> None:
    """Reject every parsed shape that could select a provider-backed branch."""

    if (
        parsed.is_empty()
        or parsed.parse_degraded
        or parsed.soft_criteria
        or parsed.preferred_criteria
        or parsed.keywords
        or parsed.graph_predicates
    ):
        raise ProviderCallsForbiddenError(
            "This query requires semantic retrieval and cannot run with providers forbidden."
        )


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
    inherited_titles_all: list[str] | None = None,
    inherited_titles_any: list[str] | None = None,
    retrieval_limit: int = DEFAULT_RETRIEVAL_LIMIT,
    require_role_authority: bool = False,
    provider_mode: str = "auto",
) -> SearchOutput:
    """Execute one NL search pass.

    ``base_query`` MUST already be filtered by ``organization_id`` and
    ``deleted_at IS NULL``. Caller is responsible for any other base
    constraints (role_ids, source, outcome) — they compose with our
    NL filters.

    Normal execution degrades and surfaces warnings. ``provider_mode=forbid``
    instead raises ``ProviderCallsForbiddenError`` before a provider-backed
    path can be selected.
    """
    if provider_mode not in {"auto", "forbid"}:
        raise ValueError("provider_mode must be 'auto' or 'forbid'")
    if provider_mode == "forbid" and (rerank_enabled or include_subgraph):
        raise ProviderCallsForbiddenError(
            "Reranking and graph views cannot run with providers forbidden."
        )

    base_query = apply_searchable_candidate_scope(
        base_query,
        organization_id=organization_id,
    )
    warnings: list[SearchWarning] = []
    cache_key = cache_module.compute_cache_key(
        organization_id=organization_id, query=nl_query
    )

    # Provider-forbidden execution bypasses the model-derived cache. It must
    # prove that this exact request is understood by today's deterministic
    # parser rather than trusting a structure produced by an earlier model
    # call or an older parser version.
    parsed = None if provider_mode == "forbid" else cache_module.get(cache_key)
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
                require_role_authority=bool(require_role_authority),
                provider_mode=provider_mode,
            )
        except ProviderCallsForbiddenError:
            raise
        except Exception as exc:  # pragma: no cover — parser already swallows
            logger.warning("Parser raised: %s", exc)
            parsed = ParsedFilter(
                keywords=[nl_query.strip()],
                free_text=nl_query.strip(),
                parse_degraded=True,
            )
            warnings.append(
                SearchWarning(
                    code="parser_failed",
                    message="The search request could not be parsed reliably.",
                )
            )
        if parsed and parsed.parse_degraded and not any(
            warning.code == "parser_failed" for warning in warnings
        ):
            warnings.append(
                SearchWarning(
                    code="parser_failed",
                    message=(
                        "The search request could not be parsed reliably; only a "
                        "lexical fallback is available."
                    ),
                )
            )
        if (
            provider_mode != "forbid"
            and parsed
            and not parsed.is_empty()
            and not parsed.parse_degraded
        ):
            cache_module.set(cache_key, parsed)
    elif parsed.parse_degraded:
        warnings.append(
            SearchWarning(
                code="parser_failed",
                message=(
                    "The search request could not be parsed reliably; only a "
                    "lexical fallback is available."
                ),
            )
        )

    if provider_mode == "forbid":
        _assert_provider_free_filter(parsed)

    if not parsed.titles_all and not parsed.titles_any:
        inherited_all = [
            str(title).strip() for title in (inherited_titles_all or []) if str(title).strip()
        ]
        inherited_any = [
            str(title).strip() for title in (inherited_titles_any or []) if str(title).strip()
        ]
        if inherited_all or inherited_any:
            parsed = parsed.model_copy(
                update={
                    "titles_all": inherited_all,
                    "titles_any": inherited_any,
                }
            )

    plan = None
    plan_failed = False
    try:
        safe_limit = max(1, min(int(retrieval_limit), MAX_RETRIEVAL_LIMIT))
        plan = parsed_filter_to_search_plan(parsed, query=nl_query, limit=safe_limit)
    except Exception as exc:
        plan_failed = True
        logger.warning("Search plan compilation failed: %s", exc)
        warnings.append(
            SearchWarning(
                code="search_plan_failed",
                message="Search planning failed; PostgreSQL fallback was used.",
            )
        )

    unsupported = unsupported_runtime_requirements(parsed)
    if unsupported:
        warnings.append(
            SearchWarning(
                code="unsupported_search_constraint",
                message=(
                    "The request contains search semantics that cannot yet be "
                    "verified exactly: " + "; ".join(unsupported) + ". No candidates "
                    "were returned, and this is not an exact zero."
                ),
            )
        )
        return SearchOutput(
            application_ids=[],
            parsed_filter=parsed,
            warnings=warnings,
            database_matches=0,
            retrieval_matches=0,
            search_plan=plan.model_dump(mode="json") if plan is not None else None,
            capped=True,
            exhaustive=False,
            is_exact_empty=False,
        )

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
        base_query,
        parsed_for_sql,
        soft_criteria_as_keywords=soft_as_keywords,
    )
    # ``defer_qualitative`` may strip a keywords-only fallback from the SQL
    # filter, but relevance ordering still reads Candidate profile fields.
    if needs_candidate_join(parsed) and not needs_candidate_join(parsed_for_sql):
        sql_query = sql_query.join(
            Candidate, Candidate.id == CandidateApplication.candidate_id
        )

    # Rank PostgreSQL recall first. Semantic/evidence clauses also get an
    # independently scoped GraphDB recall pass; exact structured-only searches
    # stay PostgreSQL-only and incur no graph embedding call.
    sql_query = apply_relevance_order(sql_query, parsed)
    safe_limit = plan.limit if plan is not None else DEFAULT_RETRIEVAL_LIMIT
    postgres_rows, postgres_capped = bounded_person_rows(
        sql_query,
        application_id_column=CandidateApplication.id,
        candidate_id_column=CandidateApplication.candidate_id,
        person_limit=safe_limit,
    )
    semantic_recall = bool(
        parsed.soft_criteria
        or parsed.preferred_criteria
        or parsed.keywords
        or parsed.graph_predicates
    )
    mode = (
        RetrievalMode.HYBRID
        if semantic_recall and plan
        else RetrievalMode.POSTGRES_ONLY
    )
    graph_backend = None
    scoped_capped = False
    if mode is RetrievalMode.HYBRID:
        scoped_query = apply_parsed_filter(
            base_query,
            population_filter(parsed),
            soft_criteria_as_keywords=False,
        )
        # A graph hit can never cross the canonical PostgreSQL authorization
        # boundary. When both strict retrieval and the relaxed population are
        # empty, that boundary proves the result is empty without an embedding
        # or GraphDB call.
        population_empty = not postgres_rows and (
            scoped_query.with_entities(CandidateApplication.id)
            .limit(1)
            .first()
            is None
        )
        if population_empty:
            graph_backend = BackendResult(
                backend="graph",
                status=BackendStatus.OK,
                exhaustive=True,
            )
        else:
            graph_kwargs = dict(
                query=nl_query,
                organization_id=organization_id,
                role_id=role_id,
                graph_coverage=None,
                graph_coverage_authoritative=False,
                graph_requirements=graph_evidence_requirements(parsed, plan),
                graph_limit=min(safe_limit, 50),
            )
            if require_role_authority:
                graph_kwargs["require_role_authority"] = True
            graph_backend = retrieve_graph_backend(**graph_kwargs)
        candidate_ids = {
            *(
                int(row[1])
                for row in postgres_rows
                if len(row) > 1 and row[1] is not None
            ),
            *(hit.candidate_id for hit in graph_backend.hits),
        }
        if candidate_ids:
            scoped_query = scoped_query.filter(
                CandidateApplication.candidate_id.in_(candidate_ids)
            )
            scoped_query = (
                scoped_query.order_by(None)
                .order_by(
                    case(
                        (CandidateApplication.application_outcome == "open", 0),
                        else_=1,
                    ).asc(),
                    CandidateApplication.updated_at.desc().nullslast(),
                    CandidateApplication.created_at.desc().nullslast(),
                    CandidateApplication.id.desc(),
                )
            )
            scoped_rows, scoped_capped = bounded_person_rows(
                scoped_query,
                application_id_column=CandidateApplication.id,
                candidate_id_column=CandidateApplication.candidate_id,
                person_limit=len(candidate_ids),
            )
        else:
            scoped_rows = []
    else:
        scoped_rows = postgres_rows
    postgres_capped = postgres_capped or scoped_capped
    postgres_backend = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=tuple(BackendHit(candidate_id=int(row[1])) for row in postgres_rows),
        capped=postgres_capped,
        # A planner failure means the fallback did not evaluate the complete
        # request.  Keep any useful PostgreSQL hits, but never turn an empty
        # fallback into a definitive zero.
        exhaustive=not postgres_capped and not plan_failed,
    )
    # The independently scoped rows are ordered active-first. They choose the
    # representative application for both PostgreSQL and graph-recalled people;
    # ranked PostgreSQL rows remain a fallback if scoped hydration is partial.
    allowed_applications = application_map_from_rows([*scoped_rows, *postgres_rows])
    graph_coverage = None
    retrieval_result = run_hybrid_retrieval(
        query=nl_query,
        organization_id=organization_id,
        role_id=role_id,
        allowed_applications=allowed_applications,
        postgres=postgres_backend,
        graph_result=graph_backend,
        graph_coverage=graph_coverage,
        # GraphSyncState is useful coverage telemetry, not an authoritative
        # proof that every later note/event has been indexed.
        graph_coverage_authoritative=False,
        mode=mode,
    )
    append_retrieval_warnings(warnings, retrieval_result, graph_coverage)
    retrieval = retrieval_summary(retrieval_result, graph_coverage)
    application_ids = list(retrieval_result.application_ids)
    database_matches = len({hit.candidate_id for hit in postgres_backend.hits})
    retrieval_matches = len(application_ids)

    rerank_applied = False
    deep_checked = 0
    evidence_succeeded = 0
    evidence_failed = 0
    verification_results: list[CandidateDeepVerification] = []
    # `qualified` is reserved for the subset that passed an actual evidence
    # verification pass. Exact database matches are described separately by
    # `database_matches`; calling them qualified would overstate what ran.
    qualified = None
    capped = retrieval_result.capped
    retrieval_exhaustive = retrieval_result.exhaustive
    requested_rerank_criteria = _required_criteria(
        parsed,
        _collect_criteria(parsed, limit=None),
    )
    rerank_criteria = requested_rerank_criteria[:DEFAULT_MAX_CRITERIA]
    if rerank_enabled and len(requested_rerank_criteria) > len(rerank_criteria):
        capped = True
        rerank_criteria = []
        warnings.append(
            SearchWarning(
                code="verification_capped",
                message=(
                    "Required criteria exceed the verification limit; no "
                    "candidate was marked qualified."
                ),
            )
        )
    if rerank_enabled and rerank_criteria and application_ids:
        try:
            from . import rerank as rerank_module

            checked_ids = application_ids[:RERANK_TOP_N]
            batch = rerank_module.rerank_application_ids(
                db=db,
                organization_id=organization_id,
                role_id=role_id,
                application_ids=checked_ids,
                soft_criteria=rerank_criteria,
                client=rerank_client,
                require_role_authority=bool(require_role_authority),
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
            capped = capped or evidence_succeeded < retrieval_matches
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
            if retrieval_matches > deep_checked:
                warnings.append(
                    SearchWarning(
                        code="verification_capped",
                        message=(
                            f"Attempted evidence checks for {deep_checked} of "
                            f"{retrieval_matches} retrieval matches; "
                            f"{evidence_succeeded} completed successfully. "
                            "Unchecked candidates were not classified as failures."
                        ),
                    )
                )
        except Exception as exc:
            logger.warning("Rerank failed; passing through SQL results: %s", exc)
            # The deterministic retrieval remains intact, but evidence coverage
            # is not exhaustive when a requested pass could not start.
            capped = capped or retrieval_matches > 0
            warnings.append(
                SearchWarning(
                    code="rerank_skipped",
                    message="Evidence reranking was unavailable for this search.",
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
                db=db,
            )
            # Never substitute a broad query graph when the matched candidates
            # have not been indexed yet.  That legacy fallback could render
            # unrelated people beside an otherwise grounded result set.
            if not subgraph.nodes:
                warnings.append(
                    SearchWarning(
                        code="graph_coverage_partial",
                        message=(
                            "Matched candidates have no indexed graph topology; "
                            "no unrelated graph context was substituted."
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
                    message="Graph context was unavailable for this search.",
                )
            )

    return SearchOutput(
        application_ids=application_ids,
        parsed_filter=parsed,
        warnings=warnings,
        rerank_applied=rerank_applied,
        subgraph=subgraph,
        database_matches=database_matches,
        retrieval_matches=retrieval_matches,
        deep_checked=deep_checked,
        evidence_succeeded=evidence_succeeded,
        evidence_failed=evidence_failed,
        qualified=qualified,
        verification_results=verification_results,
        search_plan=plan.model_dump(mode="json") if plan is not None else None,
        retrieval=retrieval,
        capped=capped,
        exhaustive=retrieval_exhaustive and not capped,
        is_exact_empty=retrieval_result.is_exact_empty,
    )


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
