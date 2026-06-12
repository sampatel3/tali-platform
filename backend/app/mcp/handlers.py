"""Pure-function handlers behind every MCP tool.

Each handler takes ``(db: Session, user: User, **args) -> dict | list``
and is fully self-contained — no Context, no Starlette request. The MCP
tool decorators in ``server.py`` resolve auth then delegate here, and the
in-process copilot orchestrator (``app/copilot/...``) calls the same
functions directly with the User it already authenticated.

Org-scoping is enforced inside every handler via ``user.organization_id``.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.user import User
from .payloads import (
    SCORE_FIELDS,
    application_detail,
    application_summary,
    candidate_detail,
    comparison_row,
    role_detail,
    role_summary,
)

logger = logging.getLogger("taali.mcp.handlers")

PIPELINE_STAGES = ("applied", "invited", "in_assessment", "review", "advanced")
APPLICATION_OUTCOMES = ("open", "rejected", "withdrawn", "hired")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _stage_counts_for_role(db: Session, *, organization_id: int, role_id: int) -> dict[str, int]:
    rows = (
        db.query(CandidateApplication.pipeline_stage, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .group_by(CandidateApplication.pipeline_stage)
        .all()
    )
    counts = {stage: 0 for stage in PIPELINE_STAGES}
    for stage, total in rows:
        counts[str(stage)] = int(total or 0)
    return counts


def _applications_count(db: Session, *, organization_id: int, role_id: int) -> int:
    return (
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )


def _normalize_score_input(value: float | None) -> float | None:
    """Permit either 0-10 or 0-100 thresholds; coerce to 0-100."""
    if value is None:
        return None
    f = float(value)
    if 0 <= f <= 10:
        return f * 10.0
    return f


def _applications_for_ids(
    db: Session, *, organization_id: int, application_ids: Iterable[int]
) -> list[CandidateApplication]:
    """Hydrate a set of application ids with candidate + role joined."""
    ids = [int(a) for a in application_ids]
    if not ids:
        return []
    return (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.id.in_(ids),
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def list_roles(
    db: Session,
    user: User,
    *,
    include_stage_counts: bool = False,
) -> list[dict[str, Any]]:
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == user.organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.created_at.desc())
        .all()
    )
    if not roles:
        return []
    role_ids = [r.id for r in roles]
    count_rows = (
        db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.role_id.in_(role_ids),
            CandidateApplication.deleted_at.is_(None),
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )
    counts = {int(rid): int(total) for rid, total in count_rows}
    out: list[dict[str, Any]] = []
    for role in roles:
        stage_counts = (
            _stage_counts_for_role(db, organization_id=user.organization_id, role_id=role.id)
            if include_stage_counts
            else None
        )
        out.append(
            role_summary(
                role,
                applications_count=counts.get(role.id, 0),
                stage_counts=stage_counts,
            )
        )
    return out


def get_role(db: Session, user: User, *, role_id: int) -> dict[str, Any]:
    role = (
        db.query(Role)
        .options(joinedload(Role.criteria))
        .filter(
            Role.id == role_id,
            Role.organization_id == user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise ValueError(f"role {role_id} not found")
    return role_detail(
        role,
        applications_count=_applications_count(
            db, organization_id=user.organization_id, role_id=role.id
        ),
        stage_counts=_stage_counts_for_role(
            db, organization_id=user.organization_id, role_id=role.id
        ),
    )


def search_applications(
    db: Session,
    user: User,
    *,
    role_id: int | None = None,
    min_score: float | None = None,
    score_type: str = "taali",
    pipeline_stage: str | None = None,
    application_outcome: str | None = "open",
    q: str | None = None,
    sort_by: str = "taali_score",
    sort_order: str = "desc",
    limit: int = 25,
) -> list[dict[str, Any]]:
    if score_type not in SCORE_FIELDS:
        raise ValueError(
            f"score_type must be one of {sorted(SCORE_FIELDS)}, got {score_type!r}"
        )
    if pipeline_stage and pipeline_stage not in PIPELINE_STAGES:
        raise ValueError(
            f"pipeline_stage must be one of {list(PIPELINE_STAGES)}, got {pipeline_stage!r}"
        )
    if application_outcome and application_outcome not in APPLICATION_OUTCOMES:
        raise ValueError(
            f"application_outcome must be one of {list(APPLICATION_OUTCOMES)} or null, "
            f"got {application_outcome!r}"
        )
    limit = max(1, min(int(limit), 100))

    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if role_id is not None:
        query = query.filter(CandidateApplication.role_id == role_id)
    if pipeline_stage:
        query = query.filter(CandidateApplication.pipeline_stage == pipeline_stage)
    if application_outcome:
        query = query.filter(CandidateApplication.application_outcome == application_outcome)
    threshold = _normalize_score_input(min_score)
    if threshold is not None:
        score_col = getattr(CandidateApplication, SCORE_FIELDS[score_type])
        query = query.filter(score_col >= threshold)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(Candidate, CandidateApplication.candidate_id == Candidate.id).filter(
            or_(
                Candidate.full_name.ilike(like),
                Candidate.email.ilike(like),
                Candidate.position.ilike(like),
            )
        )

    sort_column_map = {
        "taali_score": "taali_score_cache_100",
        "pre_screen_score": "pre_screen_score_100",
        "rank_score": "rank_score",
        "cv_match_score": "cv_match_score",
        "created_at": "created_at",
    }
    if sort_by not in sort_column_map:
        raise ValueError(f"sort_by must be one of {sorted(sort_column_map)}, got {sort_by!r}")
    sort_col = getattr(CandidateApplication, sort_column_map[sort_by])
    ascending = sort_order == "asc"

    # Agent should evaluate candidates the recruiter has already moved
    # forward (pipeline_stage='advanced') BEFORE fresh applied rows —
    # those carry hard recruiter signal and tend to be the ones a
    # decision is actually waiting on. Express that ordering in SQL — an
    # "is advanced" flag first, then the chosen sort column — so we can
    # push .limit() to the DB instead of materializing the whole org's
    # filtered set and slicing in Python.
    is_advanced = func.lower(func.coalesce(CandidateApplication.pipeline_stage, "")) == "advanced"
    # NULL scores sort as the smallest value (matches the previous
    # float("-inf") key): last on desc, first on asc.
    score_order = sort_col.asc().nullsfirst() if ascending else sort_col.desc().nullslast()
    query = query.order_by(is_advanced.desc(), score_order)

    apps = query.limit(limit).all()
    return [application_summary(a) for a in apps]


def get_application(
    db: Session,
    user: User,
    *,
    application_id: int,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    app = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if app is None:
        raise ValueError(f"application {application_id} not found")
    return application_detail(app, include_cv_text=include_cv_text)


def get_candidate(db: Session, user: User, *, candidate_id: int) -> dict[str, Any]:
    candidate = (
        db.query(Candidate)
        .options(joinedload(Candidate.applications).joinedload(CandidateApplication.role))
        .filter(
            Candidate.id == candidate_id,
            Candidate.organization_id == user.organization_id,
            Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if candidate is None:
        raise ValueError(f"candidate {candidate_id} not found")
    return candidate_detail(candidate)


def compare_applications(
    db: Session,
    user: User,
    *,
    application_ids: list[int],
) -> dict[str, Any]:
    if not application_ids:
        raise ValueError("application_ids must contain at least one id")
    if len(application_ids) > 5:
        raise ValueError("compare_applications accepts at most 5 ids")

    apps = _applications_for_ids(
        db, organization_id=user.organization_id, application_ids=application_ids
    )
    found_ids = {a.id for a in apps}
    missing = [aid for aid in application_ids if aid not in found_ids]
    rows = [comparison_row(a) for a in apps]
    order = {aid: idx for idx, aid in enumerate(application_ids)}
    rows.sort(key=lambda r: order.get(r["application_id"], len(order)))
    return {
        "applications": rows,
        "missing_ids": missing,
        "score_legend": {
            "taali": "Merged primary score (0-100) — recommended for ranking.",
            "pre_screen": "Cheap LLM gating score (0-100).",
            "rank": "Pairwise ranking against role pool (0-100).",
            "cv_match": "CV-vs-job-spec similarity (0-100).",
            "workable": "External Workable score, if synced.",
            "assessment": "Cached assessment-result score (0-100).",
            "role_fit": "Composite role-fit score (0-100).",
        },
    }


# ---------------------------------------------------------------------------
# v2 tools (semantic search across CV / skills / experience / graph)
# ---------------------------------------------------------------------------


def nl_search_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    role_id: int | None = None,
    rerank: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    """Natural-language search over CV text, skills, experience, and graph.

    Wraps ``app.candidate_search.runner.run_search`` — same parser, same
    SQL/Cypher/rerank pipeline that powers the in-app search box. Returns
    application summaries with the ``parsed_filter`` and any ``warnings``
    so the caller (Claude / UI) can show what it actually searched for.
    """
    from ..candidate_search.runner import run_search

    text = (query or "").strip()
    if not text:
        raise ValueError("query must be non-empty")

    base = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if role_id is not None:
        base = base.filter(CandidateApplication.role_id == int(role_id))

    result = run_search(
        db=db,
        organization_id=int(user.organization_id),
        nl_query=text,
        base_query=base,
        rerank_enabled=bool(rerank),
        # Always pull the matched candidates' Graphiti subgraph so the
        # chat UI can render an inline graph alongside the candidate
        # grid — the user expects "search this person" to surface their
        # graph, not just their card.
        include_subgraph=True,
    )

    capped_ids = result.application_ids[: max(1, min(int(limit), 100))]
    apps = _applications_for_ids(
        db, organization_id=user.organization_id, application_ids=capped_ids
    )
    by_id = {a.id: a for a in apps}
    ordered = [by_id[aid] for aid in capped_ids if aid in by_id]
    return {
        "applications": [application_summary(a) for a in ordered],
        "total_matched": len(result.application_ids),
        "rerank_applied": bool(result.rerank_applied),
        "parsed_filter": result.parsed_filter.model_dump(mode="json"),
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "graph": _graph_topology(result.subgraph) if result.subgraph else None,
    }


def find_top_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    limit: int = 10,
    rank_by: str = "taali",
    role_id: int | None = None,
) -> dict[str, Any]:
    """Grounded "top N candidates with X and Y".

    Ranks the structured-match set by ``rank_by`` (taali by default) and
    returns the top ``limit`` candidates, each carrying per-criterion
    verdicts backed by *verbatim CV evidence* (Anthropic Citations, or a
    reused stored requirement assessment). Use for "best/top N <role/skill>
    with <qualities>" requests — it does the ranking and the grounding so
    the answer is defensible rather than free-form. Returns a ``spec`` echo
    of how the query was interpreted, ``total_matched``, the grounded
    ``candidates``, and any ``warnings``.
    """
    from ..candidate_search.top_candidates import find_top_candidates as _engine

    text = (query or "").strip()
    if not text:
        raise ValueError("query must be non-empty")

    base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == user.organization_id,
        CandidateApplication.deleted_at.is_(None),
        # Only rank candidates still actionable in the pool. Don't recommend
        # ones a decision was already made on: exclude rejected/withdrawn/hired
        # (outcome != open) and ones already advanced out of the funnel.
        CandidateApplication.application_outcome == "open",
        func.lower(func.coalesce(CandidateApplication.pipeline_stage, "")) != "advanced",
    )
    if role_id is not None:
        base = base.filter(CandidateApplication.role_id == int(role_id))

    result = _engine(
        db=db,
        organization_id=int(user.organization_id),
        query=text,
        base_query=base,
        limit=int(limit),
        rank_by=str(rank_by or "taali"),
    )

    # Carry the role onto the result so the shareable report names which job
    # these candidates were ranked for (role-scoped queries only).
    if role_id is not None:
        role = db.query(Role).filter(Role.id == int(role_id)).first()
        if role is not None:
            result["role_name"] = role.name
            result["role_id"] = int(role_id)

    # Persist a shareable snapshot so every grounded top-N is a report the
    # recruiter can hand out as a link. Best-effort — never fail the search.
    try:
        from ..domains.top_reports.service import create_report, report_public_url

        report = create_report(
            db,
            organization_id=int(user.organization_id),
            created_by_user_id=int(getattr(user, "id", 0)) or None,
            role_id=int(role_id) if role_id is not None else None,
            query=text,
            snapshot=result,
        )
        result["report_token"] = report.token
        result["report_url"] = report_public_url(report.token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("top-candidates report persist failed: %s", exc)

    return result


def graph_search_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Knowledge-graph search across the org's Graphiti subgraph.

    Returns candidates whose graph facts mention the query, plus a short
    list of the actual fact strings so the caller can cite specifics
    (e.g. "Sam — 'Senior Engineer at Stripe, 2020-2024'").
    """
    from ..candidate_graph import client as graph_client
    from ..candidate_graph import search as graph_search

    text = (query or "").strip()
    if not text:
        raise ValueError("query must be non-empty")
    if not graph_client.is_configured():
        return {
            "applications": [],
            "graph_facts": [],
            "warnings": [
                {
                    "code": "neo4j_unavailable",
                    "message": "Knowledge graph is not configured for this deployment.",
                }
            ],
        }

    payload = graph_search.subgraph_for_query(
        organization_id=int(user.organization_id), query=text
    )
    # Person nodes carry a ``taali_id`` in extras when synced from candidates.
    candidate_ids: list[int] = []
    seen: set[int] = set()
    for node in payload.nodes:
        if node.label != "Person":
            continue
        raw = node.extra.get("taali_id") if isinstance(node.extra, dict) else None
        try:
            cid = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        candidate_ids.append(cid)

    if not candidate_ids:
        return {
            "applications": [],
            "graph_facts": _facts_from_payload(payload, limit=10),
            "graph": _graph_topology(payload),
            "warnings": [],
        }

    apps = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.candidate_id.in_(candidate_ids),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .all()
    )
    apps.sort(
        key=lambda a: (a.taali_score_cache_100 if a.taali_score_cache_100 is not None else float("-inf")),
        reverse=True,
    )
    capped = apps[: max(1, min(int(limit), 100))]
    return {
        "applications": [application_summary(a) for a in capped],
        "graph_facts": _facts_from_payload(payload, limit=10),
        "graph": _graph_topology(payload),
        "warnings": [],
    }


def _graph_topology(payload) -> dict[str, Any]:
    """Convert a GraphPayload into a thin ``{nodes, edges}`` shape for
    inline visualisation in the chat UI. Hard-cap at 60 nodes / 100 edges
    so an over-broad query can't blow up the React renderer.

    The two slices are NOT independent — slicing nodes and edges by
    position lets through edges that reference nodes outside the kept
    set, and cytoscape throws synchronously when that happens (which
    React then surfaces as the global "Something went wrong" error
    boundary). We guarantee referential integrity here:

    1. Take the first 100 edges.
    2. Collect every node id those edges reference, plus the first 60
       payload nodes, capped at 60 total.
    3. Drop any edge whose source/target isn't in the kept node set.
    """
    raw_nodes = payload.nodes or []
    raw_edges = payload.edges or []

    # Step 1: pick edges first so we know which nodes we MUST keep.
    candidate_edges = list(raw_edges[:100])

    # Step 2: build the kept-nodes set, prioritising endpoints of the
    # chosen edges (so the graph is connected) over the head-of-list
    # fallback nodes.
    nodes_by_id = {n.id: n for n in raw_nodes}
    kept_ids: list[str] = []
    seen_kept: set[str] = set()

    def _try_add(node_id: str) -> None:
        if not node_id or node_id in seen_kept:
            return
        node = nodes_by_id.get(node_id)
        if node is None:
            return
        if len(kept_ids) >= 60:
            return
        seen_kept.add(node_id)
        kept_ids.append(node_id)

    for edge in candidate_edges:
        _try_add(edge.source)
        _try_add(edge.target)
    # Fill remaining capacity with head-of-list nodes so an empty-edge
    # payload still surfaces something.
    for node in raw_nodes:
        if len(kept_ids) >= 60:
            break
        _try_add(node.id)

    nodes_out = [
        {
            "id": nodes_by_id[node_id].id,
            "label": nodes_by_id[node_id].label,
            "name": nodes_by_id[node_id].name,
            "extra": nodes_by_id[node_id].extra if isinstance(nodes_by_id[node_id].extra, dict) else {},
        }
        for node_id in kept_ids
    ]

    # Step 3: keep only edges whose endpoints survived the node cap.
    edges_out = [
        {
            "source": edge.source,
            "target": edge.target,
            "label": edge.label,
            "fact": (edge.extra or {}).get("fact") if isinstance(edge.extra, dict) else None,
        }
        for edge in candidate_edges
        if edge.source in seen_kept and edge.target in seen_kept
    ]
    return {"nodes": nodes_out, "edges": edges_out}


def _facts_from_payload(payload, *, limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for edge in payload.edges or []:
        fact = (edge.extra or {}).get("fact") if isinstance(edge.extra, dict) else None
        if not fact:
            continue
        out.append(
            {
                "fact": str(fact),
                "source": edge.source,
                "target": edge.target,
                "label": str(edge.label),
            }
        )
        if len(out) >= limit:
            break
    return out


def get_candidate_cv(
    db: Session,
    user: User,
    *,
    candidate_id: int,
) -> dict[str, Any]:
    """Parsed CV sections + raw text for a candidate.

    Useful when Claude wants to quote a candidate's CV verbatim — much
    cheaper than embedding the full CV in every search response.
    """
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == candidate_id,
            Candidate.organization_id == user.organization_id,
            Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if candidate is None:
        raise ValueError(f"candidate {candidate_id} not found")
    return {
        "candidate_id": candidate.id,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "cv_filename": candidate.cv_filename,
        "cv_uploaded_at": candidate.cv_uploaded_at.isoformat() if candidate.cv_uploaded_at else None,
        "cv_sections": candidate.cv_sections if isinstance(candidate.cv_sections, dict) else None,
        "cv_text": (candidate.cv_text or "").strip() or None,
        "skills": candidate.skills,
        "experience_entries": candidate.experience_entries,
        "education_entries": candidate.education_entries,
    }


# ---------------------------------------------------------------------------
# Agent-aware tools (used by role-scoped Taali Chat to explain decisions)
# ---------------------------------------------------------------------------


def list_recent_agent_decisions(
    db: Session,
    user: User,
    *,
    role_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent agent decisions visible to the recruiter.

    Used by the role-scoped Taali Chat to answer "why did the agent
    queue Lucas?" and "what did the agent decide today?". Accepts an
    optional status filter (e.g. ``pending`` / ``approved`` /
    ``overridden``) and an optional role_id (defaults to all roles in
    the org when None).
    """
    from ..models.agent_decision import AGENT_DECISION_STATUSES, AgentDecision

    if status and status not in AGENT_DECISION_STATUSES:
        raise ValueError(
            f"status must be one of {list(AGENT_DECISION_STATUSES)} or null, got {status!r}"
        )
    capped = max(1, min(int(limit), 100))
    query = (
        db.query(AgentDecision)
        .filter(AgentDecision.organization_id == user.organization_id)
    )
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    if status:
        query = query.filter(AgentDecision.status == status)

    rows = (
        query.order_by(
            AgentDecision.created_at.desc(),
            AgentDecision.id.desc(),
        )
        .limit(capped)
        .all()
    )
    return [_agent_decision_payload(row) for row in rows]


def list_recent_agent_runs(
    db: Session,
    user: User,
    *,
    role_id: int | None = None,
    trigger: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent autonomous-cycle log entries.

    Each row is one ``AgentRun`` — the cycle's trigger, status, decisions
    emitted, tools called, error if any, model + prompt versions for
    A/B observation. Lets the recruiter ask "what did the agent do
    today?" or "why did the cycle fail this morning?".
    """
    from ..models.agent_run import AGENT_RUN_TRIGGERS, AgentRun

    if trigger and trigger not in AGENT_RUN_TRIGGERS:
        raise ValueError(
            f"trigger must be one of {list(AGENT_RUN_TRIGGERS)} or null, got {trigger!r}"
        )
    capped = max(1, min(int(limit), 100))
    query = (
        db.query(AgentRun)
        .filter(AgentRun.organization_id == user.organization_id)
    )
    if role_id is not None:
        query = query.filter(AgentRun.role_id == int(role_id))
    if trigger:
        query = query.filter(AgentRun.trigger == trigger)

    rows = (
        query.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(capped)
        .all()
    )
    return [_agent_run_payload(row) for row in rows]


def explain_agent_decision(
    db: Session,
    user: User,
    *,
    decision_id: int,
) -> dict[str, Any]:
    """Full reasoning detail for one agent decision.

    Returns the decision (reasoning + evidence + confidence + status)
    plus the linked AgentRun (trigger, model_version, tools_called,
    started_at, finished_at) so the recruiter can drill into "what
    cycle produced this and what evidence did the agent see."
    """
    from ..models.agent_decision import AgentDecision
    from ..models.agent_run import AgentRun

    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == user.organization_id,
        )
        .first()
    )
    if decision is None:
        raise ValueError(f"agent_decision {decision_id} not found")

    run_payload: dict[str, Any] | None = None
    if decision.agent_run_id is not None:
        run_row = (
            db.query(AgentRun)
            .filter(
                AgentRun.id == int(decision.agent_run_id),
                AgentRun.organization_id == user.organization_id,
            )
            .first()
        )
        if run_row is not None:
            run_payload = _agent_run_payload(run_row)

    return {
        "decision": _agent_decision_payload(decision),
        "agent_run": run_payload,
    }


def _agent_decision_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "role_id": int(row.role_id),
        "application_id": int(row.application_id),
        "agent_run_id": (int(row.agent_run_id) if row.agent_run_id is not None else None),
        "decision_type": str(row.decision_type),
        "recommendation": str(row.recommendation),
        "status": str(row.status),
        "reasoning": str(row.reasoning),
        "evidence": row.evidence if isinstance(row.evidence, dict) else None,
        "confidence": (float(row.confidence) if row.confidence is not None else None),
        "model_version": str(row.model_version),
        "prompt_version": str(row.prompt_version),
        "created_at": (row.created_at.isoformat() if row.created_at else None),
        "resolved_at": (row.resolved_at.isoformat() if row.resolved_at else None),
        "resolved_by_user_id": (
            int(row.resolved_by_user_id) if row.resolved_by_user_id is not None else None
        ),
        "resolution_note": row.resolution_note,
        "override_action": row.override_action,
    }


def _agent_run_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "role_id": int(row.role_id),
        "trigger": str(row.trigger),
        "trigger_event_id": (
            int(row.trigger_event_id) if row.trigger_event_id is not None else None
        ),
        "status": str(row.status),
        "started_at": (row.started_at.isoformat() if row.started_at else None),
        "finished_at": (row.finished_at.isoformat() if row.finished_at else None),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cache_read_tokens": int(row.cache_read_tokens or 0),
        "cache_creation_tokens": int(row.cache_creation_tokens or 0),
        "total_cost_micro_usd": int(row.total_cost_micro_usd or 0),
        "decisions_emitted": int(row.decisions_emitted or 0),
        "tools_called": row.tools_called if isinstance(row.tools_called, list) else [],
        "error": row.error,
        "model_version": str(row.model_version),
        "prompt_version": str(row.prompt_version),
    }
