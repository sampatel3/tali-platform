"""Search adapters: Graphiti -> the shapes the rest of Tali expects.

Three public callables, signature-compatible with the previous Cypher
implementation so callers (the search runner, the rerank step, the
endpoint) need only re-import:

- ``candidate_ids_matching_all(organization_id, predicates, role_id=None) -> list[int]``
- ``subgraph_for_candidates(organization_id, candidate_ids, db=None) -> GraphPayload``
- ``colleague_neighbourhood(organization_id, candidate_id) -> dict``

When ``subgraph_for_candidates`` is called with a SQLAlchemy ``Session``
it expands the episode prefix list to cover interview transcripts /
summaries and pipeline-event notes, so the graph view shows everything
Graphiti knows about the candidate — not just the profile/CV facts.

All three are sync; they call Graphiti through ``client.run_async``.
``candidate_id`` is the Postgres id stored in the Graphiti node's
``attributes['taali_id']`` field — set by the episode builders.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterable

from . import client as graph_client
from ..services.provider_error_evidence import safe_provider_error_code
from ..candidate_search.schemas import (
    GraphEdge,
    GraphNode,
    GraphPayload,
    GraphPredicate,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("taali.candidate_graph.search")

# How many top-ranked Graphiti nodes/edges we pull per query. Caps blast
# radius if a query is unexpectedly broad ("everyone in tech").
DEFAULT_SEARCH_LIMIT = 50
# Per-candidate subgraph cap. Raised from 200 so a senior candidate's full
# Graphiti footprint (profile + skills + 20 jobs + interview transcripts +
# pipeline events) is not silently clipped in the graph view.
SUBGRAPH_LIMIT = 500
NEIGHBOURHOOD_LIMIT = 60


@contextmanager
def _attribute_search(
    organization_id: int,
    label: str,
    *,
    role_id: int | None = None,
):
    """Set ``graph_metering_ctx`` around a ``graphiti.search`` call so the
    Voyage query-embed (and any Anthropic call) it makes inside the Graphiti
    loop is attributed to the org — propagated onto the loop thread by
    ``run_async``'s copy_context. Without this, search-time embeds land in
    call_log with organization_id=NULL (reconcilable but un-attributed).

    Tagged ``graph_search:<label>`` in metadata so search spend is
    distinguishable from graph_sync indexing in the usage breakdown. A caller
    supplying ``role_id`` opts into role-cap admission; omitting it preserves
    deliberate workspace-level, organization-only search.
    """
    from ..services.metered_async_anthropic_client import (
        GraphMeteringContext,
        graph_metering_ctx,
    )

    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(organization_id),
            role_id=int(role_id) if role_id is not None else None,
            episode_name=f"graph_search:{label}",
            trace_id=(
                f"graph-search:{int(organization_id)}:role:{int(role_id)}:{label}"
                if role_id is not None
                else f"graph-search:{int(organization_id)}:{label}"
            ),
            # Workspace searches remain organization-only. A role-scoped agent
            # search must also consume that role's monthly allowance.
            require_hard_admission=True,
            require_role_admission=role_id is not None,
        )
    )
    try:
        yield
    finally:
        graph_metering_ctx.reset(token)


def _query_for_predicate(predicate: GraphPredicate) -> str:
    """Turn a structured predicate into a natural-language Graphiti query."""
    value = (predicate.value or "").strip()
    if predicate.type == "worked_at":
        return f"candidates who have worked at {value}"
    if predicate.type == "studied_at":
        return f"candidates who studied at {value}"
    if predicate.type == "colleague_of":
        return f"candidates who shared a workplace with candidate {value}"
    if predicate.type == "n_hop_from":
        return f"candidates connected to candidate {value}"
    return value


def candidate_ids_for_predicate(
    *,
    organization_id: int,
    predicate: GraphPredicate,
    role_id: int | None = None,
) -> set[int]:
    """Return the set of Postgres candidate ids matching one predicate.

    Empty set means "no matches" — runner treats this as a hard zero
    and short-circuits. Provider failures propagate as secret-safe errors so
    the runner can drop the graph predicate instead of reporting a false zero.
    """
    if not graph_client.is_configured():
        return set()
    group_id = graph_client.group_id_for_org(organization_id)
    query = _query_for_predicate(predicate)
    failure_code: str | None = None
    try:
        graphiti = graph_client.get_graphiti()
        with _attribute_search(
            organization_id,
            "predicate",
            role_id=role_id,
        ):
            results = graph_client.run_async(
                graphiti.search(query=query, group_ids=[group_id], num_results=DEFAULT_SEARCH_LIMIT)
            )
        candidate_ids = _extract_taali_ids(results)
    except Exception as exc:
        failure_code = safe_provider_error_code(
            exc,
            operation="graphiti_predicate_search",
        )
        logger.warning(
            "Graphiti predicate search failed error_code=%s",
            failure_code,
        )
    if failure_code is not None:
        # Raise after leaving the handler so the secret-bearing provider
        # exception is not retained in ``__context__``. The owning search
        # runner converts this into a stable warning and keeps SQL matches.
        raise RuntimeError(failure_code)
    return candidate_ids


def candidate_ids_matching_all(
    *,
    organization_id: int,
    predicates: list[GraphPredicate],
    role_id: int | None = None,
) -> list[int]:
    """Intersection of candidate-id sets across all predicates.

    Order of the returned list is deterministic (sorted ascending) so
    downstream pagination is stable.
    """
    if not predicates:
        return []
    aggregate: set[int] | None = None
    for predicate in predicates:
        ids = candidate_ids_for_predicate(
            organization_id=organization_id,
            predicate=predicate,
            role_id=role_id,
        )
        if not ids:
            return []
        aggregate = ids if aggregate is None else (aggregate & ids)
        if not aggregate:
            return []
    return sorted(aggregate or set())


def subgraph_for_candidates(
    *,
    organization_id: int,
    candidate_ids: Iterable[int],
    db: Session | None = None,
    episode_selectors: list[str] | None = None,
) -> GraphPayload:
    """Return a graph payload for specific candidates via direct Cypher.

    Pulls every episode that belongs to the candidate, regardless of
    source: profile, CV, skills, experience, **interview transcripts and
    summaries, and pipeline-event notes**. When ``db`` is provided we
    look up the candidate's interview and event ids in Postgres and
    extend the episode-prefix list to cover them; without ``db`` we fall
    back to ``candidate-{id}-*`` only (CV/profile/skills/experience).

    Provider failures propagate so the owning request can distinguish an
    outage from a valid candidate-scoped lookup with no graph evidence.
    """
    ids = list({int(c) for c in candidate_ids})
    if not ids or not graph_client.is_configured():
        return GraphPayload()

    group_id = graph_client.group_id_for_org(organization_id)
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    seen_edge_keys: set[tuple] = set()

    # Cap to 50 candidates per call so the prefix list (and the resulting
    # Cypher) stays bounded even if a caller asks for a huge set.
    capped_ids = ids[:50]
    selectors = (
        list(episode_selectors)
        if episode_selectors is not None
        else _episode_prefixes_for_candidates(db, capped_ids)
    )
    prefixes, exact_names = _split_episode_selectors(selectors)
    failure_code: str | None = None
    try:
        graphiti = graph_client.get_graphiti()
        result = graph_client.run_async(
            _cypher_subgraph_by_prefixes(
                graphiti.driver, group_id, prefixes, exact_names
            ),
            timeout=8.0,
        )
        _merge_neo4j_records(result, nodes, edges, seen_edge_keys=seen_edge_keys)
    except Exception as exc:
        failure_code = safe_provider_error_code(
            exc,
            operation="graphiti_candidate_subgraph",
        )
        logger.warning(
            "candidate subgraph failed error_code=%s",
            failure_code,
        )
    if failure_code is not None:
        # ``from None`` only suppresses display; raising outside the handler
        # also prevents the original provider exception remaining reachable.
        raise RuntimeError(failure_code)

    return GraphPayload(nodes=list(nodes.values())[:SUBGRAPH_LIMIT], edges=edges)


def episode_selectors_for_candidates(
    db: Session,
    candidate_ids: Iterable[int],
) -> list[str]:
    """Snapshot every SQL-backed episode selector before graph provider I/O.

    Candidate search uses this public seam to release its read transaction
    before calling Neo4j while retaining interview and pipeline-event graph
    coverage.  ``subgraph_for_candidates`` still accepts ``db=`` for backward
    compatibility with callers that do not own their session boundary.
    """

    ids = list({int(candidate_id) for candidate_id in candidate_ids})[:50]
    return _episode_prefixes_for_candidates(db, ids)


def _episode_prefixes_for_candidates(
    db: Session | None,
    candidate_ids: list[int],
) -> list[str]:
    """Build the full set of Graphiti episode-name selectors for these
    candidates.

    Always includes ``candidate-{id}-`` (covers profile / skills-education
    / experience / cv). When ``db`` is supplied, also adds:

    - ``interview-{iid}-`` for every ApplicationInterview tied to one of
      the candidates' applications (transcript + summary episodes).
    - ``event-{eid}`` for every CandidateApplicationEvent on those
      applications (pipeline transitions + recruiter notes).

    ``candidate-`` / ``interview-`` selectors carry a trailing ``-`` and
    are matched with ``STARTS WITH``; ``event-`` selectors have no
    terminator and are matched by EXACT name (see
    ``_split_episode_selectors`` / ``_cypher_subgraph_by_prefixes``) so
    ``event-20`` can't bleed into ``event-201`` from another candidate.

    Without ``db`` we degrade gracefully to candidate-only prefixes —
    callers like the runner always have ``db`` so this is just a safety
    net for ad-hoc usage.
    """
    prefixes: list[str] = [f"candidate-{cid}-" for cid in candidate_ids]
    if db is None or not candidate_ids:
        return prefixes
    try:
        # Lazy imports to avoid a hard dep on SQLAlchemy at import time
        # (search.py is also reachable from the worker which uses a thinner
        # bootstrap).
        from ..models.application_interview import ApplicationInterview
        from ..models.candidate_application import CandidateApplication
        from ..models.candidate_application_event import CandidateApplicationEvent

        interview_ids = (
            db.query(ApplicationInterview.id)
            .join(
                CandidateApplication,
                CandidateApplication.id == ApplicationInterview.application_id,
            )
            .filter(CandidateApplication.candidate_id.in_(candidate_ids))
            .all()
        )
        for (iid,) in interview_ids:
            if iid is not None:
                prefixes.append(f"interview-{int(iid)}-")

        event_ids = (
            db.query(CandidateApplicationEvent.id)
            .join(
                CandidateApplication,
                CandidateApplication.id == CandidateApplicationEvent.application_id,
            )
            .filter(CandidateApplication.candidate_id.in_(candidate_ids))
            .all()
        )
        for (eid,) in event_ids:
            if eid is not None:
                # Event episodes are named exactly "event-{id}" (no trailing
                # dash, no suffix). Carried in the same list but matched by
                # EXACT name downstream — a STARTS WITH prefix would let
                # "event-20" also match "event-201" etc., polluting one
                # candidate's subgraph with another's events.
                prefixes.append(f"event-{int(eid)}")
    except Exception as exc:
        code = safe_provider_error_code(
            exc, operation="graph_episode_prefix_expand"
        )
        logger.warning(
            "Could not expand episode prefixes candidate_count=%d error_code=%s",
            len(candidate_ids),
            code,
        )
    return prefixes


def _split_episode_selectors(selectors: list[str]) -> tuple[list[str], list[str]]:
    """Partition raw episode selectors into ``(prefixes, exact_names)``.

    Selectors ending in ``-`` (``candidate-N-``, ``interview-N-``) are
    prefix matches; everything else (``event-N``) is an exact-name match.
    Keeping the split here means callers/tests can still treat the selector
    list as one flat thing while the Cypher applies the right operator.
    """
    prefixes: list[str] = []
    exact_names: list[str] = []
    for selector in selectors:
        if selector.endswith("-"):
            prefixes.append(selector)
        else:
            exact_names.append(selector)
    return prefixes, exact_names


def subgraph_for_query(*, organization_id: int, query: str) -> GraphPayload:
    """Return a graph payload for a free-text query via direct Cypher.

    Searches edge facts by substring match — much faster than the Graphiti
    Python search path because it avoids vector embedding and returns nodes
    with their full data already joined. Provider failures propagate as
    secret-safe errors so callers can distinguish an outage from no matches.
    """
    if not query or not graph_client.is_configured():
        return GraphPayload()

    group_id = graph_client.group_id_for_org(organization_id)
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    failure_code: str | None = None
    try:
        graphiti = graph_client.get_graphiti()
        result = graph_client.run_async(
            _cypher_subgraph_by_query(graphiti.driver, group_id, query, limit=SUBGRAPH_LIMIT),
            timeout=8.0,
        )
        _merge_neo4j_records(result, nodes, edges)
    except Exception as exc:
        failure_code = safe_provider_error_code(
            exc,
            operation="graphiti_subgraph_query",
        )
        logger.warning(
            "subgraph_for_query failed error_code=%s",
            failure_code,
        )
    if failure_code is not None:
        # The graph-only handler needs to distinguish an outage from a real
        # empty result and surface its existing ``neo4j_unavailable`` warning.
        raise RuntimeError(failure_code)

    return GraphPayload(nodes=list(nodes.values())[:SUBGRAPH_LIMIT], edges=edges)


# ---------------------------------------------------------------------------
# Direct Cypher helpers — bypass graphiti.search() which returns EntityEdge
# objects with source_node/target_node = None (not populated on search results).
# ---------------------------------------------------------------------------


async def _cypher_subgraph_by_query(
    driver, group_id: str, query: str, limit: int = 100
):
    """Edges whose fact text contains the query substring, with nodes joined.

    Graphiti's driver accepts Cypher parameters as keyword arguments. Keeping
    user text out of the query string avoids having to reproduce Cypher's
    backslash/quote escaping rules and preserves the exact search substring.
    """
    cypher = """
        MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity)
        WHERE e.group_id = $group_id
          AND toLower(e.fact) CONTAINS toLower($query)
        RETURN
          s.uuid AS s_uuid, s.name AS s_name, properties(s) AS s_props,
          t.uuid AS t_uuid, t.name AS t_name, properties(t) AS t_props,
          e.uuid AS e_uuid, e.name AS e_name, e.fact AS e_fact,
          e.valid_at AS e_valid_at, e.invalid_at AS e_invalid_at
        LIMIT $limit
        """
    return await driver.execute_query(
        cypher,
        group_id=group_id,
        query=query[:200],
        limit=int(limit),
    )


async def _cypher_subgraph_by_prefixes(
    driver, group_id: str, prefixes: list[str], exact_names: list[str] | None = None
):
    """Edges introduced by every episode whose name starts with one of
    ``prefixes`` (covers ``candidate-{id}-*``, ``interview-{iid}-*``) OR
    exactly equals one of ``exact_names`` (``event-{eid}`` for one or more
    candidates).

    Safety:
    - ``prefixes`` are server-built strings of the form ``"candidate-N-"``
      / ``"interview-N-"`` and ``exact_names`` ``"event-N"`` where N is a
      Python int.
    - ``group_id`` is always ``org-{int}`` — no user-controlled chars.

    Event episodes are matched by EXACT name (not prefix) because they are
    named ``event-{id}`` with no terminator, so a prefix match would let
    ``event-20`` also pull ``event-201`` from a different candidate.

    We do NOT filter Episode nodes by group_id property because it may not
    be stored on Episode nodes in this Graphiti version. Instead we rely on:
    - The episode-name selector on ``ep.name`` (candidate-scoped).
    - The ``e.group_id`` filter on the RELATES_TO edge (org-scoped).
    """
    exact_names = exact_names or []
    if not prefixes and not exact_names:
        return None
    # Graphiti's episode nodes carry the label ``:Episodic`` (see
    # graphiti_core.nodes.EpisodicNode). Earlier this query used
    # ``:Episode``, which silently matches nothing and produces a Neo4j
    # UnknownLabelWarning rather than an error — the per-candidate
    # subgraph fetch was a no-op until this was corrected.
    cypher = """
        WITH $prefixes AS prefixes, $exact_names AS exact_names
        MATCH (ep:Episodic)
        WHERE any(prefix IN prefixes WHERE ep.name STARTS WITH prefix)
           OR ep.name IN exact_names
        MATCH (ep)-[:MENTIONS]->(s:Entity)-[e:RELATES_TO]->(t:Entity)
        WHERE e.group_id = $group_id
        RETURN
          s.uuid AS s_uuid, s.name AS s_name, properties(s) AS s_props,
          t.uuid AS t_uuid, t.name AS t_name, properties(t) AS t_props,
          e.uuid AS e_uuid, e.name AS e_name, e.fact AS e_fact,
          e.valid_at AS e_valid_at, e.invalid_at AS e_invalid_at
        LIMIT $limit
        """
    return await driver.execute_query(
        cypher,
        prefixes=prefixes,
        exact_names=exact_names,
        group_id=group_id,
        limit=int(SUBGRAPH_LIMIT),
    )


def _merge_neo4j_records(
    result, nodes: dict, edges: list, *, seen_edge_keys: set | None = None
) -> None:
    """Build GraphNode/GraphEdge objects from raw Cypher records.

    ``seen_edge_keys`` (when provided) deduplicates RELATES_TO edges that
    surface multiple times — e.g. when the same fact is reachable via
    different episodes (a profile episode + an interview episode that
    both mention the same job).
    """
    if result is None:
        return
    for record in result.records or []:
        s_uuid = record.get("s_uuid")
        t_uuid = record.get("t_uuid")
        if not s_uuid or not t_uuid:
            continue
        s_name = record.get("s_name") or "?"
        t_name = record.get("t_name") or "?"
        s_props = dict(record.get("s_props") or {})
        t_props = dict(record.get("t_props") or {})
        e_name = record.get("e_name") or ""
        e_fact = record.get("e_fact") or ""

        # In Graphiti RELATES_TO edges the source is always the "subject"
        # entity (typically a Person) — use edge context to refine labels.
        # e.name is None in current Graphiti schema, so fall back to the fact text.
        edge_label = _edge_label_for(e_name, fact=e_fact)
        s_label = _label_for(s_props, [], s_name, is_source=True)
        t_label = _label_for(t_props, [], t_name, edge_context=edge_label)
        s_id = _node_id_for(s_uuid, s_label, s_props, None)
        t_id = _node_id_for(t_uuid, t_label, t_props, None)

        if s_id not in nodes:
            nodes[s_id] = GraphNode(
                id=s_id,
                label=s_label,
                name=s_name,
                extra={"uuid": s_uuid, "headline": s_props.get("headline")},
            )
        if t_id not in nodes:
            nodes[t_id] = GraphNode(
                id=t_id,
                label=t_label,
                name=t_name,
                extra={"uuid": t_uuid},
            )

        if seen_edge_keys is not None:
            # Edge uuid is the canonical identity; fall back to the
            # (source, target, fact) triple if uuid is missing.
            key = record.get("e_uuid") or (s_id, t_id, e_fact)
            if key in seen_edge_keys:
                continue
            seen_edge_keys.add(key)

        edges.append(
            GraphEdge(
                source=s_id,
                target=t_id,
                label=edge_label,
                extra={
                    "fact": record.get("e_fact"),
                    "valid_at": _stringify(record.get("e_valid_at")),
                    "invalid_at": _stringify(record.get("e_invalid_at")),
                },
            )
        )


def colleague_neighbourhood(
    *,
    organization_id: int,
    candidate_id: int,
    role_id: int | None = None,
    max_companies: int = 10,
    max_colleagues_per_company: int = 5,
) -> dict:
    """Compact neighbourhood used by the rerank prompt.

    Returns ``{"companies": [...], "schools": [...], "skills": [...]}``
    where each company entry has ``{name, title, colleagues: [...]}``.
    Empty dict when Graphiti is unavailable.
    """
    if not graph_client.is_configured():
        return {"companies": [], "schools": [], "skills": []}

    group_id = graph_client.group_id_for_org(organization_id)
    try:
        graphiti = graph_client.get_graphiti()
        with _attribute_search(
            organization_id,
            "neighbourhood",
            role_id=role_id,
        ):
            results = graph_client.run_async(
                graphiti.search(
                    query=f"work history, education, and colleagues of candidate {candidate_id}",
                    group_ids=[group_id],
                    num_results=NEIGHBOURHOOD_LIMIT,
                )
            )
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_neighbourhood")
        logger.warning("colleague_neighbourhood failed error_code=%s", code)
        return {"companies": [], "schools": [], "skills": []}

    companies: dict[str, dict] = {}
    schools: list[str] = []
    skills: list[str] = []

    for fact in _iter_facts(results):
        edge_label = (fact.get("edge_label") or fact.get("name") or "").upper()
        target = fact.get("target_name") or ""
        if not target:
            continue
        if "WORKED_AT" in edge_label or "EMPLOYED" in edge_label:
            entry = companies.setdefault(
                target, {"name": target, "title": fact.get("attributes", {}).get("title", ""), "colleagues": []}
            )
            colleague = fact.get("attributes", {}).get("colleague_name")
            if colleague and colleague not in entry["colleagues"] and len(entry["colleagues"]) < max_colleagues_per_company:
                entry["colleagues"].append(colleague)
        elif "STUDIED" in edge_label or "EDUCATED" in edge_label:
            if target not in schools:
                schools.append(target)
        elif "SKILL" in edge_label or "PROFICIENT" in edge_label:
            if target not in skills:
                skills.append(target)

    return {
        "companies": list(companies.values())[:max_companies],
        "schools": schools[:10],
        "skills": skills[:30],
    }


# ---------------------------------------------------------------------------
# Result shape adapters
# ---------------------------------------------------------------------------


def _iter_facts(results: Any) -> Iterable[dict]:
    """Yield Graphiti search results as plain dicts.

    Graphiti's ``search`` returns a list of ``EntityEdge`` objects (or
    similar). Each has ``.fact``, ``.source_node``, ``.target_node``,
    ``.attributes``. We normalise to a dict so downstream code doesn't
    couple to Graphiti's class hierarchy.
    """
    if results is None:
        return
    seq = results if isinstance(results, (list, tuple)) else getattr(results, "edges", None) or []
    for item in seq:
        if isinstance(item, dict):
            yield item
            continue
        try:
            source = getattr(item, "source_node", None)
            target = getattr(item, "target_node", None)
            yield {
                "uuid": getattr(item, "uuid", None),
                "name": getattr(item, "name", None),
                "fact": getattr(item, "fact", None),
                "edge_label": getattr(item, "name", None),
                "valid_at": getattr(item, "valid_at", None),
                "invalid_at": getattr(item, "invalid_at", None),
                "source_uuid": getattr(source, "uuid", None) if source is not None else None,
                "source_name": getattr(source, "name", None) if source is not None else None,
                "source_labels": list(getattr(source, "labels", []) or []) if source is not None else [],
                "source_attributes": dict(getattr(source, "attributes", {}) or {}) if source is not None else {},
                "target_uuid": getattr(target, "uuid", None) if target is not None else None,
                "target_name": getattr(target, "name", None) if target is not None else None,
                "target_labels": list(getattr(target, "labels", []) or []) if target is not None else [],
                "target_attributes": dict(getattr(target, "attributes", {}) or {}) if target is not None else {},
                "attributes": dict(getattr(item, "attributes", {}) or {}),
            }
        except Exception as exc:
            logger.debug(
                "Skipping unparseable Graphiti result error_type=%s",
                type(exc).__name__,
            )


def _extract_taali_ids(results: Any) -> set[int]:
    """Pull Postgres candidate ids out of Graphiti search results.

    Episode bodies tag each candidate with ``(taali_id={candidate.id})``,
    which Graphiti extracts as a property on the Person entity. We also
    fall back to scanning fact text for the same pattern when the
    attribute isn't promoted.
    """
    import re

    ids: set[int] = set()
    pattern = re.compile(r"taali_id\s*[=:]\s*(\d+)")
    for fact in _iter_facts(results):
        for attrs in (fact.get("source_attributes") or {}, fact.get("target_attributes") or {}, fact.get("attributes") or {}):
            value = attrs.get("taali_id")
            if value is not None:
                try:
                    ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        for text_field in ("fact", "source_name", "target_name", "edge_label"):
            text = fact.get(text_field) or ""
            for m in pattern.finditer(str(text)):
                try:
                    ids.add(int(m.group(1)))
                except ValueError:
                    pass
    return ids


# Definitive company markers — substrings that almost never appear in
# job titles, school names, or country names. Substring match is safe
# here.
_DEFINITIVE_COMPANY_SUFFIXES = (
    " ltd", " limited", " inc", " corp", " llc", " gmbh", " plc", " pvt",
    " holdings", " ventures", " partners", " associates", " agency",
    " studio", " labs", " bank",
)
# Soft company markers — common as substrings of job titles too
# ("Senior Software Engineer" contains " software"). Only treat these as
# Company AFTER ruling out job titles.
_SOFT_COMPANY_SUFFIXES = (
    " group", " technologies", " technology", " tech", " systems",
    " solutions", " services", " consulting", " international", " global",
    " software", " digital", " media", " financial", " capital",
    " investments", " logistics", " aviation", " healthcare", " pharma",
    " energy", " telecom", " networks", " cloud", " ai", " data",
)
# Words that strongly indicate a job title (the entity is a role, not a
# company / school / country). Whole-word match — we split the name on
# whitespace and intersect — so we don't false-positive on e.g.
# "engineering.com" or "Lead Bank".
_JOB_TITLE_WORDS = frozenset({
    "engineer", "engineers", "developer", "developers", "architect",
    "architects", "analyst", "analysts", "scientist", "scientists",
    "manager", "managers", "director", "directors", "designer",
    "designers", "consultant", "consultants", "intern", "interns",
    "founder", "founders", "ceo", "cto", "cfo", "coo", "cio", "vp",
    "lead", "leads", "specialist", "specialists", "officer", "officers",
    "administrator", "associate", "executive", "executives", "president",
    "chief", "researcher", "researchers", "writer", "editor", "producer",
    "recruiter", "recruiters", "accountant", "auditor", "controller",
    "trainee",
})
_SCHOOL_KEYWORDS = (
    "university", "college", "school", "institute", "academy", "polytechnic",
    "iit", "iim", "nit", "faculty", "department", "campus",
)
_COUNTRY_KEYWORDS = (
    "india", "usa", "uk", "uae", "canada", "australia", "germany", "france",
    "singapore", "united states", "united kingdom", "united arab emirates",
)


def _label_for(
    node_attrs: dict,
    fallback_labels: list[str],
    name: str = "",
    *,
    is_source: bool = False,
    edge_context: str = "",
) -> str:
    """Pick the closest match in our GraphNode label vocabulary.

    Priority (most-reliable signal first):
    1. Explicit kind/label on the node.
    2. Definitive company suffix (" inc", " ltd", " corp", …) — substring
       match; these almost never appear inside job titles.
    3. School keyword.
    4. Country.
    5. Job-title word (whole-token match: "engineer", "architect", …) —
       checked BEFORE soft company suffixes so "Senior Software
       Engineer" doesn't get mis-coloured as a Company because of the
       " software" substring.
    6. Soft company suffix (" software", " tech", " data", …) — substring
       match, but only after ruling out job titles.
    7. Edge context: if edge is WORKED_AT the target is likely Company, etc.
    8. Safe default: Skill (renders small and generic).

    For source nodes (the "subject" of a RELATES_TO edge — almost always
    the candidate themselves), we apply the same precedence but default
    to Person rather than Skill at the end.
    """
    raw = " ".join([*(fallback_labels or []), str(node_attrs.get("kind", ""))])
    raw_lower = raw.lower()
    # 1. Explicit label on node
    if "person" in raw_lower or "candidate" in raw_lower:
        return "Person"
    if "company" in raw_lower or "employer" in raw_lower or "organization" in raw_lower:
        return "Company"
    if "school" in raw_lower or "university" in raw_lower or "institution" in raw_lower:
        return "School"
    if "country" in raw_lower or "location" in raw_lower:
        return "Country"
    if "skill" in raw_lower or "technology" in raw_lower:
        return "Skill"

    name_lower = (name or "").lower().strip()
    name_tokens = set(name_lower.split())

    # 2. Definitive company markers
    if any(s in name_lower for s in _DEFINITIVE_COMPANY_SUFFIXES):
        return "Company"

    # 3. School / 4. Country
    if any(kw in name_lower for kw in _SCHOOL_KEYWORDS):
        return "School"
    if name_lower in _COUNTRY_KEYWORDS or (len(name_lower) == 2 and name_lower.isalpha()):
        return "Country"

    # 5. Job-title words (whole-token match) — must come before soft
    # company suffixes so e.g. "Senior Software Engineer" wins on
    # "engineer" before " software" can mis-classify it.
    if name_tokens & _JOB_TITLE_WORDS:
        return "Skill"

    # 6. Soft company suffixes
    if any(s in name_lower for s in _SOFT_COMPANY_SUFFIXES):
        return "Company"

    # Source-side default: the subject of a RELATES_TO edge is almost
    # always a Person.
    if is_source:
        return "Person"

    # 7. Edge context — only use for WORKED_AT/STUDIED_AT/LOCATED_IN where we
    # know the target type; HAS_SKILL default stays Skill.
    if edge_context == "WORKED_AT":
        return "Company"
    if edge_context == "STUDIED_AT":
        return "School"
    if edge_context == "LOCATED_IN":
        return "Country"

    return "Skill"


def _edge_label_for(name: str, fact: str = "") -> str:
    # Graphiti RELATES_TO edges have e.name = None in the current schema.
    # Fall back to the fact text when the edge name is absent.
    text = (name or fact or "").upper()
    if not text:
        return "HAS_SKILL"
    # Education: checked first to avoid "ATTENDED" → WORKED_AT
    if any(kw in text for kw in ("STUDIED", "STUDIES", "EDUCATED", "ATTENDED", "GRADUATED", "ENROLLED", "UNIVERSITY", "COLLEGE")):
        return "STUDIED_AT"
    # "holds the position of X at Company" — the "at Company" means WORKED_AT
    # "holds the position of X" (no "at") — pure title, target is a Skill node
    if any(kw in text for kw in ("HOLDS THE POSITION", "JOB TITLE", "HOLDS THE ROLE", "HOLDS THE JOB")):
        if " AT " in text:
            return "WORKED_AT"
        return "HAS_SKILL"
    # Employment at a company — "works as ... at", "worked at", "employed at"
    if any(kw in text for kw in ("WORKED AT", "WORKS AT", "EMPLOYED AT", "EMPLOYED BY", "HIRED AT", "HIRED BY", "WORKS FOR", "WORKED FOR", "WORKS AS")):
        return "WORKED_AT"
    # Generic employment verbs — broader but after the specific patterns above
    if any(kw in text for kw in ("POSITION", "TITLE", "ROLE")):
        return "HAS_SKILL"
    if any(kw in text for kw in ("WORKED", "WORKS", "EMPLOYED", "EMPLOY", "HIRED")):
        return "WORKED_AT"
    if any(kw in text for kw in ("SKILL", "PROFICIENT", "USES", "USED", "KNOWS", "EXPERTISE")):
        return "HAS_SKILL"
    if any(kw in text for kw in ("LOCATED", "LIVES", "BASED", "RESIDES", "RESIDE")):
        return "LOCATED_IN"
    return "HAS_SKILL"  # safe default


def _node_id_for(uuid: str, label: str, attrs: dict | None, anchor_taali_id: int | None) -> str:
    """Frontend node id format: ``person:<taali_id>`` for Person nodes,
    ``<lowercase_label>:<uuid_short>`` for everything else."""
    if label == "Person":
        attrs = attrs or {}
        taali_id = attrs.get("taali_id") or anchor_taali_id
        if taali_id:
            return f"person:{int(taali_id)}"
    short_uuid = (uuid or "")[:12] or "x"
    return f"{label.lower()}:{short_uuid}"


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
