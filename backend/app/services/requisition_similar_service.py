"""Requisition warm-start, smarter tier: prefill the SUBSTANCE of a new
requisition from the most SIMILAR prior role/brief.

The base warm-start (``requisition_chat_service.warm_start_fields``) fills 5
logistics fields by recency. This adds the high-value part: once the recruiter
has given the role a title, find the closest prior role (or brief) by title and
offer its requirements / responsibilities / seniority / salary band as editable
starting points — so the agent confirms rather than asks from scratch.

Deterministic, no LLM, no spend — consistent with the base warm-start. Matching
is title-token overlap (overlap coefficient) with a high-precision threshold, so
a weak/unrelated match prefills nothing rather than misleading the recruiter.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy.orm import Session, selectinload

from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import Role
from ..models.role_brief import RoleBrief
from ..models.role_criterion import RoleCriterion

# How many recent candidates to score (bounded — scoring is cheap, but the
# criteria selectinload on roles shouldn't be unbounded).
_CANDIDATE_LIMIT = 300
# Minimum title-overlap to accept a match. 0.6 means the smaller title is almost
# a subset of the other (e.g. "Data Engineer" ⊂ "Senior Data Engineer"); a lone
# shared generic token like "engineer" (0.5 on two 2-token titles) is rejected.
_MIN_SCORE = 0.6

# Tokens that carry no matching signal — dropped before scoring.
_STOPWORDS = frozenset({
    "the", "a", "an", "for", "of", "and", "to", "in", "at", "on", "with",
    "role", "roles", "position", "job", "opening", "new", "other", "team", "i",
})

# Seniority hints we can lift straight out of a title (token -> nice label).
_SENIORITY = {
    "intern": "Intern",
    "graduate": "Junior",
    "junior": "Junior",
    "associate": "Associate",
    "mid": "Mid",
    "intermediate": "Mid",
    "senior": "Senior",
    "snr": "Senior",
    "sr": "Senior",
    "lead": "Lead",
    "principal": "Principal",
    "staff": "Staff",
    "head": "Head",
    "director": "Director",
    "vp": "VP",
    "chief": "Chief",
    "manager": "Manager",
}

# Substance fields we'll offer (columns); ``responsibilities`` rides in
# custom_fields and is handled separately by the applier.
PREFILL_COLUMN_FIELDS = (
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


def _extract_from_role(role: Role) -> dict[str, Any]:
    crit = [c for c in (role.criteria or []) if getattr(c, "deleted_at", None) is None]
    return {
        "must_haves": [c.text for c in crit if c.bucket == BUCKET_MUST and c.text],
        "preferred": [c.text for c in crit if c.bucket == BUCKET_PREFERRED and c.text],
        "dealbreakers": [c.text for c in crit if c.bucket == BUCKET_CONSTRAINT and c.text],
        "seniority": _seniority_from_title(role.name),
    }


def _extract_from_brief(brief: RoleBrief) -> dict[str, Any]:
    custom = brief.custom_fields if isinstance(brief.custom_fields, dict) else {}
    return {
        "must_haves": brief.must_haves,
        "preferred": brief.preferred,
        "dealbreakers": brief.dealbreakers,
        "responsibilities": custom.get("responsibilities"),
        "success_profile": brief.success_profile,
        "priorities": brief.priorities,
        "seniority": brief.seniority or _seniority_from_title(brief.title),
        "salary_min": brief.salary_min,
        "salary_max": brief.salary_max,
    }


def find_similar_prefill(
    db: Session, *, organization_id: int, brief: RoleBrief
) -> Optional[dict[str, Any]]:
    """The most similar prior role/brief's substance, as a prefill suggestion, or
    None when the brief has no title yet or nothing clears the match threshold.

    Briefs are scored first so a similar prior REQUISITION (richer — it carries
    responsibilities / success profile / salary) wins ties over a bare role; a
    higher-scoring role still beats a weaker brief. Returns
    ``{"source": {kind, id, name, score}, "fields": {<non-empty suggestions>}}``.
    """
    target = _tokens(brief.title)
    if not target:
        return None

    best_score = 0.0
    best_kind: Optional[str] = None
    best_obj: Any = None

    briefs = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.id != brief.id,
            RoleBrief.title.isnot(None),
        )
        .order_by(RoleBrief.created_at.desc(), RoleBrief.id.desc())
        .limit(_CANDIDATE_LIMIT)
        .all()
    )
    for cand in briefs:
        score = _score(target, _tokens(cand.title))
        if score >= _MIN_SCORE and score > best_score:
            best_score, best_kind, best_obj = score, "brief", cand

    roles = (
        db.query(Role)
        .options(selectinload(Role.criteria))
        .filter(Role.organization_id == organization_id, Role.deleted_at.is_(None))
        .order_by(Role.created_at.desc(), Role.id.desc())
        .limit(_CANDIDATE_LIMIT)
        .all()
    )
    for cand in roles:
        if brief.role_id and cand.id == brief.role_id:
            continue
        score = _score(target, _tokens(cand.name))
        # strict > keeps a tie with an equally-scored brief on the (richer) brief
        if score >= _MIN_SCORE and score > best_score:
            best_score, best_kind, best_obj = score, "role", cand

    if best_obj is None:
        return None

    raw = _extract_from_brief(best_obj) if best_kind == "brief" else _extract_from_role(best_obj)
    fields = {k: v for k, v in raw.items() if not _is_empty(v)}
    if not fields:
        return None

    name = best_obj.title if best_kind == "brief" else best_obj.name
    return {
        "source": {
            "kind": best_kind,
            "id": best_obj.id,
            "name": name,
            "score": round(best_score, 2),
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
