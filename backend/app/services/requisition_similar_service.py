"""Requisition warm-start, smarter tier: prefill the SUBSTANCE of a new
requisition from the most SIMILAR prior role.

The base warm-start (``requisition_chat_warm_start.warm_start_fields``) fills 5
logistics fields by recency. This adds the high-value part: once the recruiter
has given the role a title, find the closest prior role by title and offer its
spec — summary, requirements (must/preferred/dealbreakers), seniority, and (from
the role's originating requisition, if any) responsibilities / success profile /
salary band — as editable starting points the agent confirms rather than asks
from scratch.

Selection (deterministic, no LLM, no spend), mirroring how a recruiter would
reuse a past spec:
  1. RELEVANCE — title-token overlap (overlap coefficient) must clear a floor, so
     an unrelated role never prefills.
  2. STRONG SPEC — among comparably-similar roles, prefer the one with more
     candidate applications (a proven, well-written spec attracts applicants).
  3. RECENCY — final tie-break toward the most recent.
Ranked lexicographically: (similarity band, applicant count, recency).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..models.candidate_application import CandidateApplication
from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import Role
from ..models.role_brief import RoleBrief

# How many recent roles to score (bounded — scoring is cheap, but the criteria
# selectinload shouldn't be unbounded). Covers "recent" generously; recency is
# only a tie-break, so a similar older role is still found within this window.
_CANDIDATE_LIMIT = 150
# Minimum title overlap to accept a match. 0.4 = the smaller title shares ~40%+
# of its meaningful tokens (e.g. "Data Engineer" vs "Senior Data Engineer" = 1.0;
# "Data Engineer" vs "Data Analyst" = 0.5 — relevant enough as a starting point;
# "Data Engineer" vs "Marketing Manager" = 0.0 — rejected).
_MIN_SCORE = 0.4
# Applicant count at/above which a spec reads as "strong" (for the banner).
_STRONG_APPLICANTS = 15
_SUMMARY_MAX = 400

_STOPWORDS = frozenset({
    "the", "a", "an", "for", "of", "and", "to", "in", "at", "on", "with",
    "role", "roles", "position", "job", "opening", "new", "other", "team", "i",
})

_SENIORITY = {
    "intern": "Intern", "graduate": "Junior", "junior": "Junior",
    "associate": "Associate", "mid": "Mid", "intermediate": "Mid",
    "senior": "Senior", "snr": "Senior", "sr": "Senior", "lead": "Lead",
    "principal": "Principal", "staff": "Staff", "head": "Head",
    "director": "Director", "vp": "VP", "chief": "Chief", "manager": "Manager",
}

# Substance fields offered (columns); ``responsibilities`` rides in custom_fields
# and is handled separately by the applier.
PREFILL_COLUMN_FIELDS = (
    "summary",
    "must_haves",
    "preferred",
    "dealbreakers",
    "success_profile",
    "priorities",
    "seniority",
    "salary_min",
    "salary_max",
)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _tokens(title: Optional[str]) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS}


def _score(a: set[str], b: set[str]) -> float:
    """Overlap coefficient |A∩B| / min(|A|,|B|) — robust to title length
    differences (so "X Engineer" still matches "Senior X Engineer")."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _seniority_from_title(title: Optional[str]) -> Optional[str]:
    for tok in _tokens(title):
        if tok in _SENIORITY:
            return _SENIORITY[tok]
    return None


def _spec_summary(role: Role, linked_brief: Optional[RoleBrief]) -> Optional[str]:
    """A short role summary to seed the new spec's summary line. Prefers the
    originating requisition's (short) summary; else the first paragraph of the
    role's description, truncated — never the whole JD."""
    if linked_brief is not None and isinstance(linked_brief.summary, str) and linked_brief.summary.strip():
        return linked_brief.summary.strip()
    desc = (role.description or "").strip()
    if not desc:
        return None
    para = desc.split("\n\n", 1)[0].strip() or desc
    return (para[:_SUMMARY_MAX].rstrip() + "…") if len(para) > _SUMMARY_MAX else para


def _extract(role: Role, linked_brief: Optional[RoleBrief]) -> dict[str, Any]:
    """The prefill payload from the matched role + (if present) its originating
    requisition. Role criteria → requirements; the brief adds the richer fields
    a bare role lacks (responsibilities / success profile / priorities / salary)."""
    crit = [c for c in (role.criteria or []) if getattr(c, "deleted_at", None) is None]
    out: dict[str, Any] = {
        "summary": _spec_summary(role, linked_brief),
        "must_haves": [c.text for c in crit if c.bucket == BUCKET_MUST and c.text],
        "preferred": [c.text for c in crit if c.bucket == BUCKET_PREFERRED and c.text],
        "dealbreakers": [c.text for c in crit if c.bucket == BUCKET_CONSTRAINT and c.text],
        "seniority": _seniority_from_title(role.name),
    }
    if linked_brief is not None:
        custom = linked_brief.custom_fields if isinstance(linked_brief.custom_fields, dict) else {}
        # Richer fields a role row doesn't carry — take from the requisition.
        for key, value in (
            ("responsibilities", custom.get("responsibilities")),
            ("success_profile", linked_brief.success_profile),
            ("priorities", linked_brief.priorities),
            ("salary_min", linked_brief.salary_min),
            ("salary_max", linked_brief.salary_max),
        ):
            if not _is_empty(value):
                out[key] = value
        # If the role had no derived criteria, fall back to the brief's lists.
        for key, value in (
            ("must_haves", linked_brief.must_haves),
            ("preferred", linked_brief.preferred),
            ("dealbreakers", linked_brief.dealbreakers),
        ):
            if _is_empty(out.get(key)) and not _is_empty(value):
                out[key] = value
        if _is_empty(out.get("seniority")) and not _is_empty(linked_brief.seniority):
            out["seniority"] = linked_brief.seniority
    return out


def find_similar_prefill(
    db: Session, *, organization_id: int, brief: RoleBrief
) -> Optional[dict[str, Any]]:
    """The most-similar prior role's spec as a prefill suggestion, or None when
    the brief has no title yet or nothing clears the relevance floor.

    Returns ``{"source": {kind, id, name, score, applicants, strong_spec},
    "fields": {<non-empty suggestions>}}``.
    """
    target = _tokens(brief.title)
    if not target:
        return None

    roles = (
        db.query(Role)
        .options(selectinload(Role.criteria))
        .filter(Role.organization_id == organization_id, Role.deleted_at.is_(None))
        .order_by(Role.created_at.desc(), Role.id.desc())
        .limit(_CANDIDATE_LIMIT)
        .all()
    )
    candidates = [r for r in roles if not (brief.role_id and r.id == brief.role_id)]
    if not candidates:
        return None

    # Batched applicant counts for the candidate roles (one query) — the
    # strong-spec signal. Roles with no applications simply count 0.
    counts = dict(
        db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.role_id.in_([r.id for r in candidates]),
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )

    best_key: Optional[tuple] = None
    best: Optional[tuple] = None  # (role, sim, applicants)
    for index, role in enumerate(candidates):
        sim = _score(target, _tokens(role.name))
        if sim < _MIN_SCORE:
            continue
        applicants = int(counts.get(role.id, 0))
        # Lexicographic: similarity band (dominant) → applicants → recency
        # (newest = smaller index = larger -index).
        key = (round(sim, 1), applicants, -index)
        if best_key is None or key > best_key:
            best_key, best = key, (role, sim, applicants)

    if best is None:
        return None
    role, sim, applicants = best

    linked_brief = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.role_id == role.id,
        )
        .order_by(RoleBrief.id.desc())
        .first()
    )
    fields = {k: v for k, v in _extract(role, linked_brief).items() if not _is_empty(v)}
    if not fields:
        return None

    return {
        "source": {
            "kind": "role",
            "id": role.id,
            "name": role.name,
            "score": round(sim, 2),
            "applicants": applicants,
            "strong_spec": applicants >= _STRONG_APPLICANTS,
        },
        "fields": fields,
    }


def apply_prefill_to_empty_fields(
    db: Session, brief: RoleBrief, fields: dict[str, Any]
) -> list[str]:
    """Apply suggested values to ONLY the brief fields that are still empty (never
    overwrite recruiter input). Returns the list of field keys actually filled.
    ``responsibilities`` lands in custom_fields; the rest are columns. Flushes."""
    applied: list[str] = []
    for key, value in fields.items():
        if _is_empty(value):
            continue
        if key == "responsibilities":
            custom = dict(brief.custom_fields or {})
            if _is_empty(custom.get("responsibilities")):
                custom["responsibilities"] = value
                brief.custom_fields = custom
                applied.append(key)
            continue
        if key not in PREFILL_COLUMN_FIELDS:
            continue
        if _is_empty(getattr(brief, key, None)):
            setattr(brief, key, value)
            applied.append(key)
    if applied:
        db.flush()
    return applied
