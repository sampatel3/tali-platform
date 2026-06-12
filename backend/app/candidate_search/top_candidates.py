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
# When criteria are present we must ground MORE than `limit` candidates,
# because requirements act as a filter — some of the top-by-fit will fail
# (e.g. salary above the cap) and get hidden, so we need a window deep enough
# to still surface `limit` who qualify. Bounds cost + latency.
GROUND_WINDOW_CAP = 15
GROUND_CONCURRENCY = 8
# Hard wall-clock deadline for the whole grounding batch behind a chat turn.
# Any candidate not grounded by then degrades to "unknown" (missing) rather
# than stalling the response — strangler calls are abandoned, not awaited.
GROUND_BATCH_DEADLINE_S = 45.0

_RANKING_LABELS = {
    "taali": "Taali fit",
    "pre_screen": "pre-screen score",
    "rank": "pairwise rank",
    "cv_match": "CV-match score",
}

# A criterion is a HARD CONSTRAINT (failing it HIDES the candidate) only when
# it's a stated-value cap/threshold — salary, notice period, a years/months
# threshold, location, or work authorisation. Everything else (company type,
# domain, skills) is a PREFERENCE: failing it ranks the candidate lower but
# never removes them. So "salary < 30k" filters; "ideally a Western company"
# does not.
_CONSTRAINT_KW_RE = re.compile(
    r"\b(salar(?:y|ies)|compensation|\bpay\b|wage|notice period|visa|"
    r"work auth\w*|right to work|work permit|relocat\w*|based in|located in|"
    r"\blocation\b|nationality|citizen\w*)\b",
    re.I,
)
_THRESHOLD_RE = re.compile(
    r"\b(less than|under|below|at most|no more than|max(?:imum)?|at least|"
    r"min(?:imum)?|over|above|fewer than|more than|<=?|>=?)\b",
    re.I,
)
_UNIT_RE = re.compile(r"\b(aed|usd|eur|gbp|sar|inr|years?|yrs?|months?|days?|\d{3,})\b", re.I)
_CURRENCY_RE = re.compile(r"\b(aed|usd|eur|gbp|sar|inr)\b", re.I)


def _is_constraint(criterion: str) -> bool:
    c = criterion or ""
    if _CONSTRAINT_KW_RE.search(c):
        return True
    if _THRESHOLD_RE.search(c) and _UNIT_RE.search(c):
        return True
    if _CURRENCY_RE.search(c) and re.search(r"\d", c):
        return True
    return False

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


def _collect_evidence(app: CandidateApplication):
    """Pull the candidate's evidence off the (already-loaded) ORM objects.

    Runs in the MAIN thread before grounding is fanned out, so the parallel
    grounding workers never touch the request's DB session."""
    return (_cv_text(app), _notes_text(app), _stored_assessments(app))


def _ground(
    cv: str | None,
    notes: str | None,
    stored: list[dict[str, Any]],
    *,
    criteria: list[str],
    client,
    organization_id: int,
    application_id: int,
) -> list[CriterionVerdict]:
    """Pure (no DB / no ORM access) — safe to run in a worker thread. Reuses a
    stored requirement assessment where it cleanly covers a criterion, else
    runs one fresh CV+notes citation call for the remainder."""
    if not criteria:
        return []
    verdicts: list[CriterionVerdict | None] = []
    remaining: list[str] = []
    for c in criteria:
        reused = _reuse_stored(c, stored)
        verdicts.append(reused)
        if reused is None:
            remaining.append(c)

    if remaining:
        if cv or notes:
            fresh = _ge.extract_cv_evidence(
                cv_text=cv,
                notes_text=notes,
                criteria=remaining,
                client=client,
                organization_id=organization_id,
                application_id=int(application_id),
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


def _ground_window(
    apps: list[CandidateApplication],
    *,
    criteria: list[str],
    client,
    organization_id: int,
) -> list[tuple[CandidateApplication, list[CriterionVerdict]]]:
    """Ground each app in ``apps`` concurrently (I/O-bound Haiku calls).

    Evidence is collected in this (main) thread; only the pure ``_ground`` runs
    in workers, so the DB session is never touched off-thread. Order preserved.
    """
    import concurrent.futures as cf

    if not apps:
        return []
    jobs = [(app, *_collect_evidence(app)) for app in apps]

    def _one(job):
        app, cv, notes, stored = job
        try:
            return _ground(
                cv, notes, stored,
                criteria=criteria,
                client=client,
                organization_id=organization_id,
                application_id=int(app.id),
            )
        except Exception as exc:  # noqa: BLE001 — degrade this candidate, not the query
            logger.warning("ground app=%s failed: %s", getattr(app, "id", "?"), exc)
            return [CriterionVerdict(criterion=c, status="missing") for c in criteria]

    def _timed_out(c: str) -> CriterionVerdict:
        return CriterionVerdict(criterion=c, status="missing", note="Evidence check timed out.")

    workers = max(1, min(GROUND_CONCURRENCY, len(jobs)))
    results: dict[int, list[CriterionVerdict]] = {}
    ex = cf.ThreadPoolExecutor(max_workers=workers)
    try:
        fut_to_idx = {ex.submit(_one, job): i for i, job in enumerate(jobs)}
        done, not_done = cf.wait(fut_to_idx, timeout=GROUND_BATCH_DEADLINE_S)
        for fut in done:
            try:
                results[fut_to_idx[fut]] = fut.result()
            except Exception:  # noqa: BLE001
                results[fut_to_idx[fut]] = [_timed_out(c) for c in criteria]
        if not_done:
            logger.warning(
                "grounding batch deadline (%.0fs) hit: %d/%d candidates incomplete",
                GROUND_BATCH_DEADLINE_S, len(not_done), len(jobs),
            )
            for fut in not_done:
                results[fut_to_idx[fut]] = [_timed_out(c) for c in criteria]
    finally:
        # Don't block the response on stragglers; cancel anything not started.
        ex.shutdown(wait=False, cancel_futures=True)

    return [(jobs[i][0], results.get(i) or [_timed_out(c) for c in criteria]) for i in range(len(jobs))]


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


_COVER_NOTE_OPENERS = (
    "dear ", "hi ", "hi,", "hello", "i hope this", "i am writing", "to whom",
    "greetings", "i came across", "i recently came across",
)


def _candidate_blurb(cand) -> str | None:
    """A concise professional summary for the shareable report.

    ``candidate.summary`` is often the candidate's Workable COVER NOTE ("Dear
    Hiring Manager...") — not a profile — so we prefer the CV's parsed summary
    section, then synthesise a factual one-liner from headline + most-recent
    role + top skills, and only fall back to ``candidate.summary`` if it doesn't
    read like a cover note."""
    if cand is None:
        return None
    cv_sections = getattr(cand, "cv_sections", None) or {}

    cv_summary = str(cv_sections.get("summary") or "").strip()
    if len(cv_summary) >= 40 and not cv_summary.lower().startswith(_COVER_NOTE_OPENERS):
        return cv_summary[:400]

    parts: list[str] = []
    headline = str(getattr(cand, "headline", "") or "").strip()
    if headline:
        parts.append(headline)
    experience = cv_sections.get("experience") or getattr(cand, "experience_entries", None) or []
    if isinstance(experience, list) and experience and isinstance(experience[0], dict):
        e0 = experience[0]
        recent = " at ".join(
            p for p in [str(e0.get("title") or "").strip(), str(e0.get("company") or "").strip()] if p
        )
        if recent:
            parts.append(f"most recently {recent}")
    skills = [
        str(s).strip()
        for s in (cv_sections.get("skills") or getattr(cand, "skills", None) or [])[:5]
        if str(s).strip()
    ]
    if skills:
        parts.append(", ".join(skills))
    if parts:
        return " · ".join(parts)[:400]

    # Last resort: candidate.summary, but only if it's not a cover note.
    summary = str(getattr(cand, "summary", "") or "").strip()
    if summary and not summary.lower().startswith(_COVER_NOTE_OPENERS):
        return summary[:400]
    return None


_FIRST_SENTENCE_RE = re.compile(r"(.+?[.!?])(\s|$)", re.S)


def _scoring_summary(app: CandidateApplication) -> tuple[str | None, str | None]:
    """The scoring pipeline's candidate report summary (``cv_match_details.
    summary``) split into a one-line headline (its first sentence — a "Partial
    fit: strengths but gaps" verdict line) and the remaining detail."""
    details = getattr(app, "cv_match_details", None) or {}
    if not isinstance(details, dict):
        return None, None
    summary = str(details.get("summary") or "").strip()
    if not summary:
        return None, None
    m = _FIRST_SENTENCE_RE.match(summary)
    if m and m.end() < len(summary):
        return m.group(1).strip()[:200], summary[m.end():].strip()[:700]
    return summary[:200], None


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
    # Prefer the scoring pipeline's candidate report summary (a fit verdict +
    # detail); fall back to a synthesised blurb when a candidate wasn't scored.
    headline, body = _scoring_summary(app)
    if headline:
        out["candidate_headline"] = headline
        out["candidate_summary"] = body
    else:
        out["candidate_headline"] = None
        out["candidate_summary"] = _candidate_blurb(app.candidate)
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

    # 3. Rank the full match set by the chosen score.
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

    base_payload = {
        "spec": _build_spec(parsed, query=query, rank_by=rank_by, criteria=criteria),
        "total_matched": len(result.application_ids),
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "rank_by": rank_by,
    }

    # No qualitative/constraint criteria → nothing to ground or filter; return
    # the top `limit` by score.
    if not criteria:
        shown = [
            _candidate_payload(app, rank=i, verdicts=[], has_criteria=False)
            for i, app in enumerate(apps[:limit], start=1)
        ]
        return {
            **base_payload,
            "evaluated": len(shown),
            "shown": len(shown),
            "candidates": shown,
            "excluded": {"not_met_total": 0, "by_criterion": []},
            "evidence_model": None,
        }

    # 4. Ground a bounded, score-ranked WINDOW (parallel). Requirements act as
    #    a HARD FILTER: a candidate who clearly fails any criterion (NOT_MET —
    #    e.g. salary above the cap) is hidden. met / partial / missing stay
    #    (e.g. salary not stated = negotiable). We ground deeper than `limit`
    #    so enough qualify after filtering.
    client = evidence_client
    if client is None:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grounding client init failed: %s", exc)

    if client is None:
        # Grounding unavailable → degrade to a ranked list, no filtering.
        shown = [
            _candidate_payload(app, rank=i, verdicts=[], has_criteria=True)
            for i, app in enumerate(apps[:limit], start=1)
        ]
        return {
            **base_payload,
            "evaluated": 0,
            "shown": len(shown),
            "candidates": shown,
            "excluded": {"not_met_total": 0, "by_criterion": []},
            "evidence_model": None,
            "warnings": base_payload["warnings"]
            + [{"code": "rerank_skipped", "message": "Grounding unavailable; not filtered."}],
        }

    window_size = min(len(apps), max(limit * 3, 8), GROUND_WINDOW_CAP)
    grounded = _ground_window(
        apps[:window_size], criteria=criteria, client=client, organization_id=organization_id
    )

    survivors: list[tuple[CandidateApplication, list[CriterionVerdict]]] = []
    excluded_not_met = 0
    by_criterion: dict[str, int] = {}
    for app, verdicts in grounded:
        # Only a failed HARD CONSTRAINT (salary cap, location, etc.) hides a
        # candidate. A failed PREFERENCE (e.g. not a Western company) is shown
        # and just ranks lower — see _signal_key below.
        failed = [
            v for v in verdicts if v.status == "not_met" and _is_constraint(v.criterion)
        ]
        if failed:
            excluded_not_met += 1
            for v in failed:
                by_criterion[v.criterion] = by_criterion.get(v.criterion, 0) + 1
            continue
        survivors.append((app, verdicts))

    # Rank the survivors by CLEAR SIGNAL first: candidates who demonstrably meet
    # the criteria (grounded `met`) surface above those with only partial
    # evidence, above those whose data is unknown/`missing` — and fit (score)
    # breaks ties. So strong matches lead; the fuzzier/unknown ones rank below
    # rather than being hidden.
    def _signal_key(item):
        app, verdicts = item
        met = sum(1 for v in verdicts if v.status == "met" and v.grounded)
        partial = sum(1 for v in verdicts if v.status == "partially_met" and v.grounded)
        fit = getattr(app, score_col)
        return (met, partial, fit if fit is not None else float("-inf"))

    survivors.sort(key=_signal_key, reverse=True)
    shown = [
        _candidate_payload(app, rank=i, verdicts=verdicts, has_criteria=True)
        for i, (app, verdicts) in enumerate(survivors[:limit], start=1)
    ]

    # 5. Assemble.
    return {
        **base_payload,
        "evaluated": len(grounded),
        "shown": len(shown),
        "candidates": shown,
        "excluded": {
            "not_met_total": excluded_not_met,
            "by_criterion": [
                {"criterion": c, "count": n} for c, n in by_criterion.items()
            ],
        },
        "evidence_model": _ge.GROUNDING_MODEL,
    }
