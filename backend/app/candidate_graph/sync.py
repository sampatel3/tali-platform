"""Idempotent Postgres → Neo4j sync for candidates.

Reads ``Candidate.experience_entries`` (Workable shape:
``{company,title,start_date,end_date,...}``), ``Candidate.cv_sections``
(cv_parsing shape: ``{company,title,location,start,end,bullets}``),
``Candidate.skills`` (list[str]), ``Candidate.education_entries``, and
the candidate's own location, then upserts the corresponding nodes and
edges into Neo4j. ``MERGE`` makes re-runs free.

Multi-tenancy: every node and edge created by sync carries the
candidate's ``organization_id``; query-time tenancy filters are in
``queries.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from . import client as graph_client
from ..models.candidate import Candidate

logger = logging.getLogger("taali.candidate_graph.sync")


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _safe_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _experience_iter(candidate: Candidate) -> Iterable[dict]:
    """Yield normalised experience dicts from BOTH possible sources.

    Output shape: ``{company, title, location, start_date, end_date}``.
    Empty companies are skipped.
    """
    seen: set[tuple[str, str]] = set()

    # Workable shape
    for entry in (candidate.experience_entries or []):
        if not isinstance(entry, dict):
            continue
        company = _safe_str(entry.get("company"))
        if not company:
            continue
        title = _safe_str(entry.get("title"))
        start = _safe_str(entry.get("start_date"))
        end = _safe_str(entry.get("end_date"))
        key = (_norm(company), start)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "company": company,
            "title": title,
            "location": _safe_str(entry.get("location")),
            "start_date": start,
            "end_date": end,
        }

    # cv_parsing shape (different field names)
    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for entry in (cv_sections.get("experience") or []):
            if not isinstance(entry, dict):
                continue
            company = _safe_str(entry.get("company"))
            if not company:
                continue
            start = _safe_str(entry.get("start"))
            key = (_norm(company), start)
            if key in seen:
                continue
            seen.add(key)
            yield {
                "company": company,
                "title": _safe_str(entry.get("title")),
                "location": _safe_str(entry.get("location")),
                "start_date": start,
                "end_date": _safe_str(entry.get("end")),
            }


def _education_iter(candidate: Candidate) -> Iterable[dict]:
    """Yield normalised school dicts. Output: ``{school}``."""
    seen: set[str] = set()
    for entry in (candidate.education_entries or []):
        if not isinstance(entry, dict):
            continue
        school = _safe_str(entry.get("school") or entry.get("institution"))
        if not school or _norm(school) in seen:
            continue
        seen.add(_norm(school))
        yield {"school": school}

    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for entry in (cv_sections.get("education") or []):
            if not isinstance(entry, dict):
                continue
            school = _safe_str(entry.get("institution") or entry.get("school"))
            if not school or _norm(school) in seen:
                continue
            seen.add(_norm(school))
            yield {"school": school}


def _skills_list(candidate: Candidate) -> list[str]:
    """Combine skills from both candidate.skills and cv_sections.skills."""
    out: list[str] = []
    seen: set[str] = set()
    raw_skills = candidate.skills or []
    if isinstance(raw_skills, list):
        for item in raw_skills:
            name = _safe_str(item)
            if name and _norm(name) not in seen:
                seen.add(_norm(name))
                out.append(name)
    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for item in (cv_sections.get("skills") or []):
            name = _safe_str(item)
            if name and _norm(name) not in seen:
                seen.add(_norm(name))
                out.append(name)
    return out


_UPSERT_PERSON_CYPHER = """
MERGE (p:Person {id: $person_id})
SET p.organization_id = $org_id,
    p.full_name = $full_name,
    p.headline = $headline,
    p.last_synced_at = $synced_at
"""


_REPLACE_RELS_CYPHER = """
MATCH (p:Person {id: $person_id})
WHERE p.organization_id = $org_id
OPTIONAL MATCH (p)-[r]->()
DELETE r
"""


_UPSERT_WORKED_AT_CYPHER = """
MATCH (p:Person {id: $person_id, organization_id: $org_id})
MERGE (c:Company {organization_id: $org_id, name_normalized: $company_norm})
ON CREATE SET c.name_display = $company_display
SET c.name_display = $company_display
MERGE (p)-[r:WORKED_AT]->(c)
SET r.organization_id = $org_id,
    r.title = $title,
    r.location = $location,
    r.start_date = $start_date,
    r.end_date = $end_date
"""


_UPSERT_STUDIED_AT_CYPHER = """
MATCH (p:Person {id: $person_id, organization_id: $org_id})
MERGE (s:School {organization_id: $org_id, name_normalized: $school_norm})
ON CREATE SET s.name_display = $school_display
SET s.name_display = $school_display
MERGE (p)-[r:STUDIED_AT]->(s)
SET r.organization_id = $org_id
"""


_UPSERT_HAS_SKILL_CYPHER = """
MATCH (p:Person {id: $person_id, organization_id: $org_id})
MERGE (sk:Skill {organization_id: $org_id, name_normalized: $skill_norm})
ON CREATE SET sk.name_display = $skill_display
SET sk.name_display = $skill_display
MERGE (p)-[r:HAS_SKILL]->(sk)
SET r.organization_id = $org_id
"""


_UPSERT_LOCATED_IN_CYPHER = """
MATCH (p:Person {id: $person_id, organization_id: $org_id})
MERGE (co:Country {organization_id: $org_id, name_normalized: $country_norm})
ON CREATE SET co.name_display = $country_display
MERGE (p)-[r:LOCATED_IN]->(co)
SET r.organization_id = $org_id
"""


def sync_candidate(candidate: Candidate, *, db: Session | None = None) -> bool:
    """Upsert one candidate's graph projection.

    Returns True on success, False when Neo4j isn't configured (or on any
    handled error). Never raises — graph sync is fire-and-forget; if it
    fails, Postgres is unchanged and the next sync attempt will catch up.
    """
    if not graph_client.is_configured():
        return False
    if candidate.id is None or candidate.organization_id is None:
        return False

    org_id = int(candidate.organization_id)
    person_id = int(candidate.id)
    synced_at = datetime.now(timezone.utc).isoformat()

    try:
        with graph_client.session() as s:
            s.run(
                _UPSERT_PERSON_CYPHER,
                person_id=person_id,
                org_id=org_id,
                full_name=_safe_str(candidate.full_name),
                headline=_safe_str(candidate.headline),
                synced_at=synced_at,
            )
            # Wipe outgoing rels first so deleted experience entries
            # don't linger. Still cheap because the graph is small per
            # candidate.
            s.run(_REPLACE_RELS_CYPHER, person_id=person_id, org_id=org_id)

            for entry in _experience_iter(candidate):
                s.run(
                    _UPSERT_WORKED_AT_CYPHER,
                    person_id=person_id,
                    org_id=org_id,
                    company_norm=_norm(entry["company"]),
                    company_display=entry["company"],
                    title=entry["title"],
                    location=entry["location"],
                    start_date=entry["start_date"],
                    end_date=entry["end_date"],
                )

            for edu in _education_iter(candidate):
                s.run(
                    _UPSERT_STUDIED_AT_CYPHER,
                    person_id=person_id,
                    org_id=org_id,
                    school_norm=_norm(edu["school"]),
                    school_display=edu["school"],
                )

            for skill in _skills_list(candidate):
                s.run(
                    _UPSERT_HAS_SKILL_CYPHER,
                    person_id=person_id,
                    org_id=org_id,
                    skill_norm=_norm(skill),
                    skill_display=skill,
                )

            country = _safe_str(candidate.location_country)
            if country:
                s.run(
                    _UPSERT_LOCATED_IN_CYPHER,
                    person_id=person_id,
                    org_id=org_id,
                    country_norm=_norm(country),
                    country_display=country,
                )

        if db is not None:
            _record_sync_state(db, person_id)

        return True
    except Exception as exc:
        logger.warning("Graph sync failed for candidate=%s: %s", candidate.id, exc)
        return False


def _record_sync_state(db: Session, candidate_id: int) -> None:
    """Stamp ``graph_sync_state.last_synced_at = now()`` for this candidate."""
    try:
        from ..models.graph_sync_state import GraphSyncState

        existing = (
            db.query(GraphSyncState).filter(GraphSyncState.candidate_id == candidate_id).one_or_none()
        )
        now_utc = datetime.now(timezone.utc)
        if existing is None:
            db.add(
                GraphSyncState(
                    candidate_id=candidate_id,
                    last_synced_at=now_utc,
                    sync_version=1,
                )
            )
        else:
            existing.last_synced_at = now_utc
            existing.sync_version = (existing.sync_version or 0) + 1
        db.commit()
    except Exception as exc:
        logger.debug("graph_sync_state write skipped: %s", exc)
        db.rollback()


def sync_organization(db: Session, organization_id: int) -> dict:
    """Backfill: re-sync every candidate for one org.

    Returns ``{total, succeeded, skipped}``. Idempotent — safe to re-run.
    """
    if not graph_client.is_configured():
        return {"total": 0, "succeeded": 0, "skipped": 0, "neo4j": "unconfigured"}

    candidates = (
        db.query(Candidate)
        .filter(Candidate.organization_id == organization_id)
        .filter(Candidate.deleted_at.is_(None))
        .all()
    )
    total = len(candidates)
    succeeded = 0
    skipped = 0
    for candidate in candidates:
        if sync_candidate(candidate, db=db):
            succeeded += 1
        else:
            skipped += 1
    return {"total": total, "succeeded": succeeded, "skipped": skipped}


def sync_all_organizations(db: Session) -> dict:
    """Backfill every org. Used by ``backfill --all-orgs``."""
    if not graph_client.is_configured():
        return {"orgs": 0, "total": 0, "succeeded": 0, "neo4j": "unconfigured"}

    org_ids = [
        int(row[0])
        for row in db.query(Candidate.organization_id)
        .filter(Candidate.organization_id.is_not(None))
        .distinct()
        .all()
    ]
    aggregate = {"orgs": len(org_ids), "total": 0, "succeeded": 0, "skipped": 0}
    for org_id in org_ids:
        result = sync_organization(db, org_id)
        aggregate["total"] += result["total"]
        aggregate["succeeded"] += result["succeeded"]
        aggregate["skipped"] += result["skipped"]
    return aggregate
