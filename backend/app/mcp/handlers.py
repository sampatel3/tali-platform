"""Pure-function handlers behind every MCP tool.

Each handler takes ``(db: Session, user: User, **args) -> dict | list``
and is fully self-contained — no Context, no Starlette request. The MCP
tool decorators in ``server.py`` resolve auth then delegate here, and the
in-process copilot orchestrator (``app/copilot/...``) calls the same
functions directly with the User it already authenticated.

Org-scoping is enforced inside every handler via ``user.organization_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone
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

PIPELINE_STAGES = ("applied", "invited", "in_assessment", "review")
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

    apps = query.all()

    sort_column_map = {
        "taali_score": "taali_score_cache_100",
        "pre_screen_score": "pre_screen_score_100",
        "rank_score": "rank_score",
        "cv_match_score": "cv_match_score",
        "created_at": "created_at",
    }
    if sort_by not in sort_column_map:
        raise ValueError(f"sort_by must be one of {sorted(sort_column_map)}, got {sort_by!r}")
    sort_attr = sort_column_map[sort_by]
    reverse = sort_order != "asc"

    def _key(app: CandidateApplication) -> Any:
        value = getattr(app, sort_attr, None)
        if sort_by == "created_at":
            return value or datetime.min.replace(tzinfo=timezone.utc)
        return value if value is not None else float("-inf")

    apps.sort(key=_key, reverse=reverse)
    return [application_summary(a) for a in apps[:limit]]


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
        include_subgraph=False,
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
    }


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
    so an over-broad query can't blow up the React renderer."""
    nodes_out: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []
    for node in (payload.nodes or [])[:60]:
        nodes_out.append(
            {
                "id": node.id,
                "label": node.label,
                "name": node.name,
                "extra": node.extra if isinstance(node.extra, dict) else {},
            }
        )
    for edge in (payload.edges or [])[:100]:
        edges_out.append(
            {
                "source": edge.source,
                "target": edge.target,
                "label": edge.label,
                "fact": (edge.extra or {}).get("fact") if isinstance(edge.extra, dict) else None,
            }
        )
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
