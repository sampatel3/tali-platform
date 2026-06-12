"""Grounded "top N candidates with X and Y" query.

The standard procedure for a "give me the best data engineers with banking
domain experience" request, made explicit and inspectable end to end:

1. **Decompose & echo** — parse the query into a population (skills /
   location / years), a set of qualitative criteria, and an explicit
   ranking key, and return that spec so the recruiter can see exactly what
   was searched.
2. **Deterministic prefilter** — reuse ``run_search`` (rerank disabled) to
   get every candidate matching the structured filters. Cheap; runs over
   the whole pool.
3. **Rank before truncating** — order the matches by the chosen score key,
   THEN take the shortlist. (The old NL path truncated in DB order before
   any ranking, so "top N" wasn't actually top.)
4. **Ground the shortlist** — for each qualitative criterion attach a
   verdict backed by *verbatim CV evidence*: reuse the stored
   requirement-assessment quote when the criterion is already a role
   requirement (zero extra LLM cost), else extract a fresh citation from
   the CV. Only the shortlist pays for grounding.
5. **Assemble a cited answer** — ranked candidates, each criterion carrying
   its status + verbatim quote + provenance, plus the spec echo, match
   count, and warnings.

Cost shape: a few cents of parse + the cheap prefilter over the whole pool,
then at most ``shortlist`` single Haiku citation calls (skipped entirely
when a criterion is already covered by stored evidence, or when the query
has no qualitative criteria).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session, joinedload

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..mcp.payloads import SCORE_FIELDS, application_summary
from . import grounded_evidence as _ge
from .grounded_evidence import CriterionVerdict, Evidence

logger = logging.getLogger("taali.candidate_search.top_candidates")

# Default shortlist size and the hard cap (each shortlisted candidate may
# cost one Haiku citation call, so the cap bounds spend).
DEFAULT_LIMIT = 10
MAX_LIMIT = 25
# Cap the number of qualitative criteria we ground per query.
MAX_CRITERIA = 5

_RANKING_LABELS = {
    "taali": "Taali fit",
    "pre_screen": "pre-screen score",
    "rank": "pairwise rank",
    "cv_match": "CV-match score",
}

_STOPWORDS = {
    "a", "an", "the", "with", "and", "or", "of", "in", "on", "for", "to",
    "experience", "domain", "background", "knowledge", "skills", "strong",
    "candidate", "candidates", "who", "has", "have", "is", "are", "at",
}
_TOKEN_RE = re.compile(r"[a-z0-9+#]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def _cv_text(app: CandidateApplication) -> str | None:
    own = getattr(app, "cv_text", None)
    if own and own.strip():
        return own
    cand = app.candidate
    if cand is not None and getattr(cand, "cv_text", None):
        return cand.cv_text
    return None


def _notes_text(app: CandidateApplication) -> str | None:
    """The candidate's Workable evidence corpus (profile, questionnaire
    answers, recruiter comments, activity log) — where constraints like salary
    expectation and notice period are usually stated, not in the CV. Reuses the
    same renderer the scorer uses so the grounding sees the same evidence."""
    try:
        from ..services.workable_context_service import format_workable_context

        text = format_workable_context(app.candidate, app)
        return text or None
    except Exception as exc:  # noqa: BLE001 — notes are best-effort
        logger.debug("notes context unavailable for app=%s: %s", app.id, exc)
        return None


def _stored_assessments(app: CandidateApplication) -> list[dict[str, Any]]:
    details = getattr(app, "cv_match_details", None) or {}
    if not isinstance(details, dict):
        return []
    items = details.get("requirements_assessment") or []
    return [r for r in items if isinstance(r, dict)]


def _reuse_stored(criterion: str, stored: list[dict[str, Any]]) -> CriterionVerdict | None:
    """Reuse a stored requirement assessment when it clearly covers ``criterion``.

    Conservative: the criterion's significant tokens must be a subset of the
    requirement's tokens. We only reuse a *grounded positive* (met/partial
    with verbatim quotes) or a clean *negative* (missing) — an ``unknown`` or
    a quote-less positive falls through to a fresh citation so we never pass
    off an ungrounded claim as grounded.
    """
    crit_tokens = _tokens(criterion)
    if not crit_tokens:
        return None
    for req in stored:
        req_text = str(req.get("requirement") or "")
        if not crit_tokens.issubset(_tokens(req_text)):
            continue
        status = str(req.get("status") or "").lower()
        quotes = [q for q in (req.get("evidence_quotes") or []) if isinstance(q, str) and q.strip()]
        if status in {"met", "partially_met"} and quotes:
            start = req.get("evidence_start_char", -1)
            end = req.get("evidence_end_char", -1)
            evidence = [
                Evidence(
                    quote=q.strip(),
                    start_char=int(start) if i == 0 and isinstance(start, int) else -1,
                    end_char=int(end) if i == 0 and isinstance(end, int) else -1,
                    source="role_requirement",
                )
                for i, q in enumerate(quotes)
            ]
            return CriterionVerdict(
                criterion=criterion,
                status=status,
                grounded=True,
                source="role_requirement",
                evidence=evidence,
                note=str(req.get("reasoning") or "")[:200],
            )
        if status == "missing":
            return CriterionVerdict(
                criterion=criterion,
                status="missing",
                grounded=False,
                source="role_requirement",
                note=str(req.get("reasoning") or "")[:200],
            )
        # met-without-quote / unknown → fall through to a fresh citation.
        return None
    return None


def _ground_application(
    *,
    app: CandidateApplication,
    criteria: list[str],
    client,
    organization_id: int,
) -> list[CriterionVerdict]:
    if not criteria:
        return []
    stored = _stored_assessments(app)
    verdicts: list[CriterionVerdict | None] = []
    remaining: list[str] = []
    for c in criteria:
        reused = _reuse_stored(c, stored)
        verdicts.append(reused)
        if reused is None:
            remaining.append(c)

    if remaining:
        cv = _cv_text(app)
        notes = _notes_text(app)
        if cv or notes:
            fresh = _ge.extract_cv_evidence(
                cv_text=cv,
                notes_text=notes,
                criteria=remaining,
                client=client,
                organization_id=organization_id,
                application_id=int(app.id),
            )
            fresh_by_text = {v.criterion: v for v in fresh}
            for i, c in enumerate(criteria):
                if verdicts[i] is None:
                    verdicts[i] = fresh_by_text.get(
                        c, CriterionVerdict(criterion=c, status="missing")
                    )
        else:
            for i, c in enumerate(criteria):
                if verdicts[i] is None:
                    verdicts[i] = CriterionVerdict(
                        criterion=c, status="missing", note="No CV or notes available."
                    )

    return [v for v in verdicts if v is not None]


def _collect_criteria(parsed) -> list[str]:
    """Qualitative criteria to ground: soft criteria + residual keywords,
    deduped (case-insensitive), capped."""
    seen: set[str] = set()
    out: list[str] = []
    for c in list(parsed.soft_criteria) + list(parsed.keywords):
        c = (c or "").strip()
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            out.append(c)
        if len(out) >= MAX_CRITERIA:
            break
    return out


def _build_spec(parsed, *, query: str, rank_by: str, criteria: list[str]) -> dict[str, Any]:
    locations = list(parsed.locations_country) + list(parsed.locations_region)
    population = {
        "skills_all": list(parsed.skills_all),
        "skills_any": list(parsed.skills_any),
        "locations": locations,
        "min_years_experience": parsed.min_years_experience,
    }
    parts: list[str] = []
    pop_bits = list(parsed.skills_all) + list(parsed.skills_any)
    if pop_bits:
        parts.append(", ".join(pop_bits[:4]))
    if criteria:
        parts.append(" · ".join(criteria))
    if locations:
        parts.append("in " + ", ".join(locations[:3]))
    if parsed.min_years_experience:
        parts.append(f"{parsed.min_years_experience}+ yrs")
    parts.append(f"ranked by {_RANKING_LABELS.get(rank_by, rank_by)}")
    return {
        "query": query,
        "population": population,
        "criteria": [{"text": c, "kind": "qualitative", "grounded": True} for c in criteria],
        "ranking_key": rank_by,
        "echo": " · ".join(p for p in parts if p),
    }


def _candidate_payload(
    app: CandidateApplication,
    *,
    rank: int,
    verdicts: list[CriterionVerdict],
    has_criteria: bool,
) -> dict[str, Any]:
    out = application_summary(app)
    out["rank"] = rank
    out["criteria"] = [v.to_dict() for v in verdicts]
    out["meets_all_criteria"] = (
        all(v.status == "met" and v.grounded for v in verdicts) if has_criteria else None
    )
    return out


def find_top_candidates(
    *,
    db: Session,
    organization_id: int,
    query: str,
    base_query,
    limit: int = DEFAULT_LIMIT,
    rank_by: str = "taali",
    parser_client=None,
    evidence_client=None,
) -> dict[str, Any]:
    """Run the grounded top-N procedure. Never raises — degrades to a ranked
    list with warnings if grounding is unavailable."""
    from .runner import run_search  # local import keeps graph deps lazy

    if rank_by not in SCORE_FIELDS:
        rank_by = "taali"
    limit = max(1, min(int(limit), MAX_LIMIT))

    # 1-2. Parse + deterministic prefilter (no rerank; we rank by score below).
    result = run_search(
        db=db,
        organization_id=organization_id,
        nl_query=query,
        base_query=base_query,
        rerank_enabled=False,
        include_subgraph=False,
        parser_client=parser_client,
        # Keep the prefilter purely structural — qualitative criteria are
        # grounded against the CV below, NOT ILIKE-matched into the pool
        # (a phrase like "banking domain experience" matches ~zero CVs).
        defer_qualitative=True,
    )
    parsed = result.parsed_filter
    criteria = _collect_criteria(parsed)

    # 3. Rank the full match set by the chosen score, THEN shortlist.
    score_col = SCORE_FIELDS[rank_by]
    apps = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(CandidateApplication.id.in_(result.application_ids))
        .all()
        if result.application_ids
        else []
    )
    apps.sort(
        key=lambda a: (
            getattr(a, score_col) is not None,
            getattr(a, score_col) or float("-inf"),
        ),
        reverse=True,
    )
    shortlist = apps[:limit]

    # 4. Ground the shortlist (reuse stored evidence; else fresh CV citation).
    client = evidence_client
    if client is None and criteria and shortlist:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grounding client init failed: %s", exc)

    candidates: list[dict[str, Any]] = []
    for idx, app in enumerate(shortlist, start=1):
        verdicts: list[CriterionVerdict] = []
        if criteria and client is not None:
            verdicts = _ground_application(
                app=app,
                criteria=criteria,
                client=client,
                organization_id=organization_id,
            )
        candidates.append(
            _candidate_payload(
                app, rank=idx, verdicts=verdicts, has_criteria=bool(criteria)
            )
        )

    # 5. Assemble.
    return {
        "spec": _build_spec(parsed, query=query, rank_by=rank_by, criteria=criteria),
        "total_matched": len(result.application_ids),
        "shortlist_size": len(shortlist),
        "candidates": candidates,
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "evidence_model": _ge.GROUNDING_MODEL if criteria else None,
        "rank_by": rank_by,
    }
