"""Requisition warm-start helpers — deterministic, no LLM, no spend.

Two distinct jobs, reflecting that a spec has two kinds of section:

1. ROLE-AGNOSTIC boilerplate (EVP, benefits) is the SAME across an org's roles,
   so ``standardize_agnostic_fields`` lifts it from recent history and a new
   requisition inherits it instead of being re-typed. (The "About the company"
   blurb is NOT auto-derived here — in per-role JD bodies it's entangled with
   role-specific intro text, so it can't be isolated deterministically; it stays
   the set-once Settings boilerplate.)

2. ROLE-SPECIFIC requirements are NOT pre-filled (a copied requirement list just
   gets overwritten). Instead ``similar_requirements_guidance`` hands the intake
   agent the most similar prior role's requirements as a REFERENCE for its
   questions, so they're captured live + confirmed. Matched within the same
   client when one is assigned (tech stack is consistent per client), else
   org-wide.
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
from .spec_normalizer import normalize_spec

# How many recent rows to scan (bounded; scoring/parsing is cheap).
_CANDIDATE_LIMIT = 150
# Minimum title overlap for a role to count as "similar" (the smaller title
# shares ~40%+ of its meaningful tokens).
_MIN_SCORE = 0.4

_STOPWORDS = frozenset({
    "the", "a", "an", "for", "of", "and", "to", "in", "at", "on", "with",
    "role", "roles", "position", "job", "opening", "new", "other", "team", "i",
})


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
    """Overlap coefficient |A∩B| / min(|A|,|B|) — length-tolerant, so
    "X Engineer" still matches "Senior X Engineer"."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# --------------------------------------------------------------------------- #
# 1 · Role-agnostic boilerplate standardisation (EVP + benefits)
# --------------------------------------------------------------------------- #
def standardize_agnostic_fields(db: Session, organization_id: int) -> dict[str, Any]:
    """The reusable boilerplate to seed a new requisition with: ``evp`` (from the
    most recent requisition that set it) and ``benefits`` (from the most recent
    requisition's custom field, else parsed out of the most recent role's job
    spec — the Benefits section is cleanly heading-delimited). Returns only keys
    that resolved."""
    out: dict[str, Any] = {}
    recent_briefs = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == organization_id)
        .order_by(RoleBrief.created_at.desc(), RoleBrief.id.desc())
        .limit(_CANDIDATE_LIMIT)
        .all()
    )
    for brief in recent_briefs:
        if "evp" not in out and not _is_empty(brief.evp):
            out["evp"] = brief.evp
        if "benefits" not in out:
            custom = brief.custom_fields if isinstance(brief.custom_fields, dict) else {}
            if not _is_empty(custom.get("benefits")):
                out["benefits"] = custom["benefits"]
        if "evp" in out and "benefits" in out:
            return out

    if "benefits" not in out:
        recent_roles = (
            db.query(Role)
            .filter(Role.organization_id == organization_id, Role.deleted_at.is_(None))
            .order_by(Role.created_at.desc(), Role.id.desc())
            .limit(_CANDIDATE_LIMIT)
            .all()
        )
        for role in recent_roles:
            benefits = normalize_spec(role.job_spec_text).benefits
            if benefits and benefits.strip():
                # ``benefits`` is a LIST field — split the parsed section into
                # de-bulleted items.
                items = [
                    re.sub(r"^[\s\-\*•·]+", "", line).strip()
                    for line in benefits.splitlines()
                ]
                items = [it for it in items if it]
                if items:
                    out["benefits"] = items
                    break
    return out


def apply_agnostic_fields(db: Session, brief: RoleBrief, fields: dict[str, Any]) -> list[str]:
    """Fill ONLY the still-empty agnostic fields on a fresh brief. ``evp`` is a
    column; ``benefits`` rides in custom_fields. Returns the keys filled."""
    applied: list[str] = []
    if "evp" in fields and not _is_empty(fields["evp"]) and _is_empty(brief.evp):
        brief.evp = fields["evp"]
        applied.append("evp")
    if "benefits" in fields and not _is_empty(fields["benefits"]):
        custom = dict(brief.custom_fields or {})
        if _is_empty(custom.get("benefits")):
            custom["benefits"] = fields["benefits"]
            brief.custom_fields = custom
            applied.append("benefits")
    if applied:
        db.flush()
    return applied


# --------------------------------------------------------------------------- #
# 2 · Requirements GUIDANCE for the intake agent (not a prefill)
# --------------------------------------------------------------------------- #
def _client_role_ids(db: Session, organization_id: int, client_id: int) -> list[int]:
    rows = (
        db.query(RoleBrief.role_id)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.client_id == client_id,
            RoleBrief.role_id.isnot(None),
        )
        .all()
    )
    return [rid for (rid,) in rows if rid is not None]


def _rank_similar_role(
    db: Session, organization_id: int, brief: RoleBrief, *, restrict_ids: Optional[list[int]]
) -> Optional[tuple[Role, float, int]]:
    """Best (role, similarity, applicant_count) among recent roles by title
    similarity → applicant count → recency, or None. ``restrict_ids`` (when given)
    scopes the pool to those role ids (e.g. one client's roles)."""
    target = _tokens(brief.title)
    if not target:
        return None
    if restrict_ids is not None and not restrict_ids:
        return None
    query = (
        db.query(Role)
        .options(selectinload(Role.criteria))
        .filter(Role.organization_id == organization_id, Role.deleted_at.is_(None))
    )
    if restrict_ids is not None:
        query = query.filter(Role.id.in_(restrict_ids))
    roles = query.order_by(Role.created_at.desc(), Role.id.desc()).limit(_CANDIDATE_LIMIT).all()
    candidates = [r for r in roles if not (brief.role_id and r.id == brief.role_id)]
    if not candidates:
        return None
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
    best: Optional[tuple[Role, float, int]] = None
    for index, role in enumerate(candidates):
        sim = _score(target, _tokens(role.name))
        if sim < _MIN_SCORE:
            continue
        applicants = int(counts.get(role.id, 0))
        key = (round(sim, 1), applicants, -index)
        if best_key is None or key > best_key:
            best_key, best = key, (role, sim, applicants)
    return best


def similar_requirements_guidance(
    db: Session, *, organization_id: int, brief: RoleBrief
) -> Optional[dict[str, Any]]:
    """The most similar prior role's requirements, as a REFERENCE for the intake
    agent's questions (never written to the brief). Scoped to the requisition's
    client when one is assigned (tech stack is consistent per client), falling
    back to org-wide if that client has no similar role yet (or no client set).
    Returns ``{role_name, applicants, must_haves, preferred, dealbreakers}`` or
    None when nothing relevant exists."""
    client_id = getattr(brief, "client_id", None)
    best = None
    if client_id:
        best = _rank_similar_role(
            db, organization_id, brief, restrict_ids=_client_role_ids(db, organization_id, client_id)
        )
    if best is None:
        best = _rank_similar_role(db, organization_id, brief, restrict_ids=None)
    if best is None:
        return None
    role, _sim, applicants = best
    crit = [c for c in (role.criteria or []) if getattr(c, "deleted_at", None) is None]
    must = [c.text for c in crit if c.bucket == BUCKET_MUST and c.text]
    preferred = [c.text for c in crit if c.bucket == BUCKET_PREFERRED and c.text]
    dealbreakers = [c.text for c in crit if c.bucket == BUCKET_CONSTRAINT and c.text]
    if not (must or preferred or dealbreakers):
        return None
    return {
        "role_name": role.name,
        "applicants": applicants,
        "must_haves": must,
        "preferred": preferred,
        "dealbreakers": dealbreakers,
    }
