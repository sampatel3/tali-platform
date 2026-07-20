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
4. **Ground the shortlist** — EVERY displayed criterion gets a verdict backed
   by *verbatim CV evidence* from one mechanism: the Anthropic Citations pass
   (``grounded_evidence``). This is uniform on purpose — a role-requirement
   criterion is grounded the same citation-grade way as an ad-hoc one, rather
   than borrowing the scorer's paraphrase-tolerant quotes. Cost is bounded by a
   per-(CV+notes, criterion) cache: each pair is grounded at most once, so a
   repeated or refined query is ~free.
5. **Assemble a cited answer** — ranked candidates, each criterion carrying
   its status + verbatim quote + provenance, plus the spec echo, match
   count, and warnings.

Cost shape: a few cents of parse + the cheap prefilter over the whole pool,
then at most one Citations call per shortlisted candidate on the FIRST grounding
(cache misses only; skipped entirely when the query has no qualitative
criteria). Repeated/overlapping queries reuse the cache and pay nothing.
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
from .constraint_policy import (
    _is_constraint,
    _is_junk_criterion,
    _is_self_score_criterion,
    _merge_constraint_fragments,
    _parse_score_threshold,
    _recompute_currency_cap_verdict,
    _recompute_self_score_verdict,
    _tokens,
)
from .criteria_policy import (
    _collect_criteria,
    _evidence_succeeded_count,
    _fully_met_count,
    _partition_required_matches,
    _preferred_criteria,
    _required_criteria,
    _stored_role_requirement_verdicts,
)
from .grounded_evidence import CriterionVerdict

logger = logging.getLogger("taali.candidate_search.top_candidates")

# Default shortlist size and the hard cap (each shortlisted candidate may
# cost one Haiku citation call, so the cap bounds spend).
DEFAULT_LIMIT = 10
MAX_LIMIT = 25
# Cap the number of qualitative criteria we ground per query. The response
# explicitly reports anything beyond the cap; criteria are never silently
# discarded. Eight covers realistic must-have/preference searches while still
# bounding citation cost and latency.
MAX_CRITERIA = 8
# A bare role-scoped top-N still explains the score using the highest-priority
# stored role requirements. This is intentionally smaller than ad-hoc search
# grounding so the default report stays scannable.
DEFAULT_ROLE_EVIDENCE_LIMIT = 3
# When criteria are present we ground a DEEP, query-relevant window — not just
# `limit*3`. Required qualitative evidence is a strict presentation gate, while
# preferences only rank survivors. A shallow or historical-score-first window
# can silently miss a lower-scored candidate who meets every requirement. We
# therefore ground the relevance-ordered viable pool up to this cap. The cap
# bounds cost and latency; stragglers degrade to explicitly unverified results.
GROUND_WINDOW_CAP = 50
GROUND_CONCURRENCY = 12
# Stay below the stream's ~30s idle ceiling; unfinished evidence degrades to
# unknown and straggler calls are abandoned rather than awaited.
GROUND_BATCH_DEADLINE_S = 20.0

_RANKING_LABELS = {
    "taali": "Taali fit",
    "pre_screen": "pre-screen score",
    "rank": "pairwise rank",
    "cv_match": "CV-match score",
    "workable": "Workable score",
    "assessment": "assessment score",
    "role_fit": "role-fit score",
}


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


def _collect_evidence(app: CandidateApplication):
    """Pull the candidate's evidence (CV + notes) off the (already-loaded) ORM
    objects.

    Runs in the MAIN thread before grounding is fanned out, so the parallel
    grounding workers never touch the request's DB session. No stored-assessment
    reuse: every displayed criterion is grounded via Citations (the cache, not
    reuse, is what keeps that cheap), so the only evidence the workers need is
    the raw CV + notes text."""
    return (_cv_text(app), _notes_text(app))


def _ground(
    cv: str | None,
    notes: str | None,
    *,
    criteria: list[str],
    client,
    organization_id: int,
    role_id: int | None,
    application_id: int,
) -> list[CriterionVerdict]:
    """Pure (no DB / no ORM access) — safe to run in a worker thread. Grounds
    every criterion through the cached Citations pass (no stored-assessment
    reuse), then recomputes salary/currency caps from the cited figure rather
    than trusting the model's verdict word. Verdicts come back in criterion
    order; a criterion the check couldn't complete carries ``status="error"``."""
    if not criteria:
        return []
    verdicts = _ge.extract_cv_evidence(
        cv_text=cv,
        notes_text=notes,
        criteria=criteria,
        client=client,
        organization_id=organization_id,
        role_id=role_id,
        application_id=int(application_id),
    )
    for v in verdicts:
        # Salary/currency caps: trust the cited figure, not the model's verdict word.
        _recompute_currency_cap_verdict(v)
    return verdicts


def _ground_window(
    apps: list[CandidateApplication],
    *,
    criteria: list[str],
    client,
    organization_id: int,
    role_id: int | None = None,
) -> list[tuple[CandidateApplication, list[CriterionVerdict]]]:
    """Ground each app in ``apps`` concurrently (I/O-bound Haiku calls).

    Evidence is collected in this (main) thread; only the pure ``_ground`` runs
    in workers, so the DB session is never touched off-thread. Order preserved.
    """
    import concurrent.futures as cf

    if not apps:
        return []
    jobs = [(app, *_collect_evidence(app)) for app in apps]  # (app, cv, notes)

    def _one(job):
        app, cv, notes = job
        try:
            return _ground(
                cv, notes,
                criteria=criteria,
                client=client,
                organization_id=organization_id,
                role_id=role_id,
                application_id=int(app.id),
            )
        except Exception as exc:  # noqa: BLE001 — degrade this candidate, not the query
            logger.warning("ground app=%s failed: %s", getattr(app, "id", "?"), exc)
            # An exhausted/failed check is NOT "no evidence" — mark it error so the
            # UI shows "couldn't verify" and the candidate isn't falsely blanked.
            return [
                CriterionVerdict(criterion=c, status="error", note="Evidence check failed.")
                for c in criteria
            ]

    def _timed_out(c: str) -> CriterionVerdict:
        return CriterionVerdict(
            criterion=c, status="error", note="Evidence check didn't finish — retrying."
        )

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


def _criteria_coverage(parsed) -> tuple[list[str], list[str], list[str]]:
    requested = _collect_criteria(parsed, limit=None)
    checked = requested[:MAX_CRITERIA]
    return requested, checked, requested[MAX_CRITERIA:]


# Max length of a single criterion in the one-line spec ECHO (the scan header).
# The full criterion text is kept in spec.criteria[].text and shown verbatim on
# every candidate row — this only keeps the header from sprawling. Generic
# truncation, not per-phrase relabelling (which would be brittle).
_ECHO_CRITERION_MAX = 44


def _short_label(text: str) -> str:
    t = (text or "").strip()
    if len(t) <= _ECHO_CRITERION_MAX:
        return t
    cut = t[:_ECHO_CRITERION_MAX].rsplit(" ", 1)[0].rstrip(" ,;·—-")
    return f"{cut or t[:_ECHO_CRITERION_MAX].rstrip()}…"


def _build_spec(parsed, *, query: str, rank_by: str, criteria: list[str]) -> dict[str, Any]:
    locations = list(parsed.locations_country) + list(parsed.locations_region)
    population = {
        "skills_all": list(parsed.skills_all),
        "skills_any": list(parsed.skills_any),
        "titles_all": list(parsed.titles_all),
        "titles_any": list(parsed.titles_any),
        "locations": locations,
        "min_years_experience": parsed.min_years_experience,
    }
    parts: list[str] = []
    pop_bits = (
        list(parsed.titles_all)
        + list(parsed.titles_any)
        + list(parsed.skills_all)
        + list(parsed.skills_any)
    )
    if pop_bits:
        parts.append(", ".join(pop_bits[:4]))
    if criteria:
        parts.append(" · ".join(_short_label(c) for c in criteria))
    if locations:
        parts.append("in " + ", ".join(locations[:3]))
    if parsed.min_years_experience:
        parts.append(f"{parsed.min_years_experience}+ yrs")
    parts.append(f"ranked by {_RANKING_LABELS.get(rank_by, rank_by)}")
    required = {criterion.lower() for criterion in _required_criteria(parsed, criteria)}
    return {
        "query": query,
        "population": population,
        # This describes the requested evaluation, not its outcome. Grounding
        # is determined per candidate only after a verbatim citation is
        # attached; degraded searches must never inherit a truthy spec flag.
        "criteria": [
            {
                "text": c,
                "kind": "qualitative",
                "priority": "required" if c.lower() in required else "preferred",
                "requires_grounding": True,
            }
            for c in criteria
        ],
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


def _years_experience(app: CandidateApplication) -> float | None:
    """Total professional years from the scoring snapshot
    (``cv_match_details.candidate_snapshot.years_experience``) for the headline.
    ``None`` when the candidate wasn't scored or the CV lacked dates."""
    details = getattr(app, "cv_match_details", None) or {}
    if not isinstance(details, dict):
        return None
    snap = details.get("candidate_snapshot") or {}
    if not isinstance(snap, dict):
        return None
    try:
        y = float(snap.get("years_experience"))
    except (TypeError, ValueError):
        return None
    return round(y * 2) / 2 if y > 0 else None


def _candidate_payload(
    app: CandidateApplication,
    *,
    rank: int,
    verdicts: list[CriterionVerdict],
    has_criteria: bool,
) -> dict[str, Any]:
    # Self-referential "Taali score >= N" criteria can't be grounded against the
    # CV — decide them against the candidate's actual Taali score here so they
    # don't render as a spurious "missing". In place, so the verdict keeps its
    # display position and the `meets_all_criteria` roll-up below sees the
    # corrected status.
    for v in verdicts:
        _recompute_self_score_verdict(v, app)
    out = application_summary(app)
    out["rank"] = rank
    out["criteria"] = [v.to_dict() for v in verdicts]
    # Empty verdicts (the grounding-unavailable degrade path) must NOT read as
    # "all met" — all() of an empty list is True. None = "not assessed".
    out["meets_all_criteria"] = (
        all(v.status == "met" and v.grounded for v in verdicts)
        if (has_criteria and verdicts)
        else None
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
    out["candidate_years"] = _years_experience(app)
    return out


def _has_structural(parsed) -> bool:
    """Did the query carry any HARD structural filter (skills / candidate
    title / location / years / graph)? These define the requested population;
    qualitative criteria are verified within that population."""
    return bool(
        parsed.skills_all
        or parsed.skills_any
        or parsed.titles_all
        or parsed.titles_any
        or parsed.locations_country
        or parsed.locations_region
        or parsed.min_years_experience
        or parsed.graph_predicates
    )


def _pool_count(base_query) -> int:
    """Size of the actionable pool (cheap COUNT, no rows loaded)."""
    try:
        return int(base_query.count())
    except Exception:  # noqa: BLE001 — count is best-effort display
        return 0


def _load_candidates(base_query, *, matcher_ids, score_attr, size: int):
    """Load at most ``size`` apps from the pool — top by score, with any
    structural matches biased to the front — WITHOUT materialising the whole
    pool, so an org-wide query stays cheap. The caller does the final Python
    ordering, so the IN-clause load order here doesn't matter."""
    from sqlalchemy import case

    if size <= 0:
        return []
    order = [score_attr.is_(None), score_attr.desc()]
    if matcher_ids:
        order = [case((CandidateApplication.id.in_(matcher_ids), 0), else_=1)] + order
    ids = [
        row[0]
        for row in base_query.with_entities(CandidateApplication.id)
        .order_by(*order)
        .limit(int(size))
        .all()
    ]
    if not ids:
        return []
    return (
        base_query.filter(CandidateApplication.id.in_(ids))
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .all()
    )


def _load_candidates_by_ids(base_query, application_ids: list[int]):
    """Hydrate a relevance-ordered id list without losing its order."""
    if not application_ids:
        return []
    apps = (
        base_query.filter(CandidateApplication.id.in_(application_ids))
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .all()
    )
    by_id = {int(app.id): app for app in apps}
    return [by_id[app_id] for app_id in application_ids if app_id in by_id]


def find_top_candidates(
    *,
    db: Session,
    organization_id: int,
    role_id: int | None = None,
    query: str,
    base_query,
    limit: int = DEFAULT_LIMIT,
    rank_by: str = "taali",
    parser_client=None,
    evidence_client=None,
    inherited_titles_all: list[str] | None = None,
    inherited_titles_any: list[str] | None = None,
) -> dict[str, Any]:
    """Run the grounded top-N procedure.

    Never raises. Score-only and explicitly optional searches degrade with an
    honest warning; qualitative must-haves fail closed when evidence is
    unavailable so unverified profiles are never presented as matches.
    """
    from .runner import run_search  # local import keeps graph deps lazy

    if rank_by not in SCORE_FIELDS:
        rank_by = "taali"
    limit = max(1, min(int(limit), MAX_LIMIT))

    # 1. Parse. Structural skills/titles/location/years define the population;
    #    grounding makes qualitative met / over-cap / not-met calls inside it.
    result = run_search(
        db=db,
        organization_id=organization_id,
        role_id=role_id,
        nl_query=query,
        base_query=base_query,
        rerank_enabled=False,
        include_subgraph=False,
        parser_client=parser_client,
        defer_qualitative=True,
        inherited_titles_all=inherited_titles_all,
        inherited_titles_any=inherited_titles_any,
    )
    parsed = result.parsed_filter
    requested_criteria, criteria, unchecked_criteria = _criteria_coverage(parsed)
    required_criteria = _required_criteria(parsed, requested_criteria)
    preferred_criteria = _preferred_criteria(parsed, requested_criteria)
    checked_required = _required_criteria(parsed, criteria)
    unchecked_required = [
        criterion
        for criterion in required_criteria
        if criterion.lower() not in {item.lower() for item in checked_required}
    ]

    score_col = SCORE_FIELDS[rank_by]
    score_attr = getattr(CandidateApplication, score_col)
    # Structural matches bias the window; `None` when the query had no structural
    # filter at all (then the whole pool is fair game, ranked by score).
    has_structural = _has_structural(parsed)
    matcher_ids = set(result.application_ids or []) if has_structural else None
    pool_count = _pool_count(base_query)
    matched_count = len(matcher_ids) if matcher_ids is not None else pool_count
    candidate_pool = (
        base_query
        if matcher_ids is None
        else base_query.filter(CandidateApplication.id.in_(matcher_ids))
    )

    warnings = [w.model_dump(mode="json") for w in result.warnings]
    if unchecked_criteria:
        warnings.append(
            {
                "code": "criteria_capped",
                "message": (
                    f"Checked {len(criteria)} of {len(requested_criteria)} qualitative "
                    "criteria; the unchecked criteria are listed separately."
                ),
            }
        )
    base_payload = {
        "spec": _build_spec(parsed, query=query, rank_by=rank_by, criteria=criteria),
        # The population we ranked. With structural filters this is the exact
        # matched subset; without them it is the whole actionable pool. Use
        # `pool_size` to distinguish "no structural matches" from "empty pool".
        "total_matched": matched_count,
        "database_matches": matched_count,
        "pool_size": pool_count,
        "structural_matches": len(matcher_ids) if matcher_ids is not None else None,
        "criteria_requested": requested_criteria,
        "criteria_checked": criteria,
        "criteria_unchecked": unchecked_criteria,
        "required_criteria": required_criteria,
        "preferred_criteria": preferred_criteria,
        "warnings": warnings,
        "rank_by": rank_by,
    }

    if getattr(parsed, "parse_degraded", False):
        return {
            **base_payload,
            "evaluated": 0,
            "deep_checked": 0,
            "evidence_succeeded": 0,
            "shown": 0,
            "returned": 0,
            "qualified": None,
            "qualified_in_checked": 0,
            "qualified_total": None,
            "eligible_after_hard_constraints": 0,
            "search_status": "parser_failed",
            "capped": matched_count > 0,
            "candidates": [],
            "excluded": {
                "required_total": 0,
                "not_met_total": 0,
                "missing_total": 0,
                "partial_total": 0,
                "unverified_total": matched_count,
                "by_criterion": [],
            },
            "evidence_model": None,
        }

    # A hard population request that matched nobody must never be padded with
    # unrelated high scorers.  This was especially damaging for occupations:
    # "project manager" produced zero exact skill-array hits, after which the
    # old path grounded the org's top engineers against only the qualitative
    # criteria and presented them as PM results.  Fail closed and let the
    # caller explain/relax the structural constraint instead.
    if has_structural and not matcher_ids:
        return {
            **base_payload,
            "evaluated": 0,
            "deep_checked": 0,
            "shown": 0,
            "returned": 0,
            "qualified": None,
            "qualified_in_checked": 0,
            "qualified_total": 0,
            "eligible_after_hard_constraints": 0,
            "search_status": "no_structural_matches",
            "capped": False,
            "candidates": [],
            "excluded": {
                "required_total": 0,
                "not_met_total": 0,
                "missing_total": 0,
                "partial_total": 0,
                "unverified_total": 0,
                "by_criterion": [],
            },
            "evidence_model": None,
            "warnings": base_payload["warnings"]
            + [
                {
                    "code": "no_structural_matches",
                    "message": (
                        "No candidates matched the requested skills or titles; "
                        "unrelated candidates were not substituted."
                    ),
                }
            ],
        }

    # Required criteria are evaluated before preferences at the cap. If even
    # that required set exceeds the bounded evidence budget, no candidate can
    # honestly be called a match. Fail closed instead of checking a subset and
    # presenting false positives.
    if unchecked_required:
        return {
            **base_payload,
            "evaluated": 0,
            "deep_checked": 0,
            "evidence_succeeded": 0,
            "shown": 0,
            "returned": 0,
            "qualified": None,
            "qualified_in_checked": 0,
            "qualified_total": None,
            "eligible_after_hard_constraints": 0,
            "search_status": "required_criteria_unchecked",
            "capped": matched_count > 0,
            "candidates": [],
            "excluded": {
                "required_total": 0,
                "not_met_total": 0,
                "missing_total": 0,
                "partial_total": 0,
                "unverified_total": matched_count,
                "by_criterion": [],
            },
            "evidence_model": None,
            "warnings": base_payload["warnings"]
            + [
                {
                    "code": "required_criteria_unchecked",
                    "message": (
                        "Required criteria exceeded the bounded evidence limit; "
                        "no unverified candidates were presented as matches."
                    ),
                }
            ],
        }

    # Final/window ordering: structural matches first, then by score. Applied in
    # Python so it's deterministic regardless of the bounded load order.
    def _rank_key(a):
        return (
            bool(matcher_ids) and a.id in matcher_ids,
            getattr(a, score_col) is not None,
            getattr(a, score_col) or float("-inf"),
        )

    # No ad-hoc qualitative/constraint criteria → top `limit` by score. For a
    # role-scoped request, reuse the canonical scorecard's top requirement
    # quotes so a bare "top 5" report still explains the ranking in evidence.
    if not criteria:
        apps = _load_candidates(
            candidate_pool, matcher_ids=matcher_ids, score_attr=score_attr, size=limit
        )
        apps.sort(key=_rank_key, reverse=True)
        shown = []
        reused = 0
        for i, app in enumerate(apps[:limit], start=1):
            verdicts = (
                _stored_role_requirement_verdicts(app)
                if role_id is not None
                else []
            )
            if any(verdict.grounded for verdict in verdicts):
                reused += 1
            shown.append(
                _candidate_payload(
                    app,
                    rank=i,
                    verdicts=verdicts,
                    has_criteria=bool(verdicts),
                )
            )
        return {
            **base_payload,
            "evaluated": len(shown),
            "shown": len(shown),
            "returned": len(shown),
            "deep_checked": 0,
            "qualified": None,
            "eligible_after_hard_constraints": matched_count,
            "evidence_basis": (
                "stored_role_requirements" if reused else "score_only"
            ),
            "evidence_reused": reused,
            "evidence_succeeded": reused,
            "capped": matched_count > len(shown),
            "candidates": shown,
            "excluded": {
                "required_total": 0,
                "not_met_total": 0,
                "missing_total": 0,
                "partial_total": 0,
                "unverified_total": 0,
                "by_criterion": [],
            },
            "evidence_model": None,
        }

    # 2. Grounding client.
    client = evidence_client
    if client is None:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grounding client init failed: %s", exc)

    if client is None:
        strict_required = [
            criterion for criterion in checked_required if not _is_constraint(criterion)
        ]
        if strict_required:
            return {
                **base_payload,
                "evaluated": 0,
                "shown": 0,
                "returned": 0,
                "deep_checked": 0,
                "evidence_succeeded": 0,
                "qualified": None,
                "qualified_in_checked": 0,
                "qualified_total": None,
                "eligible_after_hard_constraints": 0,
                "search_status": "verification_unavailable",
                "capped": matched_count > 0,
                "candidates": [],
                "excluded": {
                    "required_total": 0,
                    "not_met_total": 0,
                    "missing_total": 0,
                    "partial_total": 0,
                    "unverified_total": matched_count,
                    "by_criterion": [],
                },
                "evidence_model": None,
                "warnings": base_payload["warnings"]
                + [
                    {
                        "code": "rerank_skipped",
                        "message": (
                            "Required evidence could not be checked; no score-ranked "
                            "candidates were presented as matches."
                        ),
                    }
                ],
            }

        # Optional preferences or stated-value constraints retain the legacy
        # degraded list, clearly labelled as unverified by coverage fields.
        apps = _load_candidates(
            candidate_pool, matcher_ids=matcher_ids, score_attr=score_attr, size=limit
        )
        apps.sort(key=_rank_key, reverse=True)
        shown = [
            _candidate_payload(app, rank=i, verdicts=[], has_criteria=True)
            for i, app in enumerate(apps[:limit], start=1)
        ]
        return {
            **base_payload,
            "evaluated": 0,
            "shown": len(shown),
            "returned": len(shown),
            "deep_checked": 0,
            "qualified": None,
            "qualified_in_checked": 0,
            "qualified_total": None,
            "eligible_after_hard_constraints": matched_count,
            "search_status": "verification_unavailable",
            "capped": matched_count > len(shown),
            "candidates": shown,
            "excluded": {
                "required_total": 0,
                "not_met_total": 0,
                "missing_total": 0,
                "partial_total": 0,
                "unverified_total": matched_count if checked_required else 0,
                "by_criterion": [],
            },
            "evidence_model": None,
            "warnings": base_payload["warnings"]
            + [{"code": "rerank_skipped", "message": "Grounding unavailable; not filtered."}],
        }

    # 3. Ground a bounded WINDOW (structural matches first). Required qualitative
    #    evidence gates the shortlist; stated-value constraints exclude a cited
    #    failure; explicit preferences rank the survivors. Ground the whole pool
    #    up to the cap — NOT just `limit*3` — so a relevant mid-scored candidate
    #    is not silently dropped before evidence can be checked. The window is
    #    loaded bounded, so even an org-wide pool never materialises in full.
    window_size = min(matched_count, GROUND_WINDOW_CAP)
    relevance_ids = list(result.application_ids or [])
    if matched_count > window_size and relevance_ids:
        # The runner already produced a person-deduplicated Postgres-FTS order.
        # Use it to choose WHICH bounded profiles deserve evidence calls; the
        # final sort below still uses grounded constraint/preference signals and
        # the query-relevance order.
        apps = _load_candidates_by_ids(candidate_pool, relevance_ids[:window_size])
    else:
        apps = _load_candidates(
            candidate_pool,
            matcher_ids=matcher_ids,
            score_attr=score_attr,
            size=max(window_size, limit),
        )
    apps.sort(key=_rank_key, reverse=True)
    grounded = _ground_window(
        apps[:window_size],
        criteria=criteria,
        client=client,
        organization_id=organization_id,
        role_id=role_id,
    )

    survivors, excluded = _partition_required_matches(grounded, checked_required)

    # Rank survivors by CLEAR SIGNAL first: grounded `met` above `partially_met`
    # above unknown/`missing`; a structural match breaks ties next; fit (score)
    # last. So strong, on-target matches lead and the fuzzier ones rank below
    # rather than being hidden.
    preferred_keys = {criterion.lower() for criterion in preferred_criteria}
    required_constraint_keys = {
        criterion.lower()
        for criterion in checked_required
        if _is_constraint(criterion)
    }
    relevance_position = {
        int(application_id): index
        for index, application_id in enumerate(relevance_ids)
    }

    def _signal_key(item):
        app, verdicts = item
        constraint_met = sum(
            1
            for v in verdicts
            if v.criterion.lower() in required_constraint_keys
            and v.status == "met"
            and v.grounded
        )
        constraint_partial = sum(
            1
            for v in verdicts
            if v.criterion.lower() in required_constraint_keys
            and v.status == "partially_met"
            and v.grounded
        )
        met = sum(
            1
            for v in verdicts
            if v.criterion.lower() in preferred_keys
            and v.status == "met"
            and v.grounded
        )
        partial = sum(
            1
            for v in verdicts
            if v.criterion.lower() in preferred_keys
            and v.status == "partially_met"
            and v.grounded
        )
        relevance = -relevance_position.get(int(app.id), len(relevance_position) + 1)
        fit = getattr(app, score_col)
        return (
            constraint_met,
            constraint_partial,
            met,
            partial,
            relevance,
            fit if fit is not None else float("-inf"),
        )

    survivors.sort(key=_signal_key, reverse=True)
    shown = [
        _candidate_payload(app, rank=i, verdicts=verdicts, has_criteria=True)
        for i, (app, verdicts) in enumerate(survivors[:limit], start=1)
    ]
    evidence_succeeded = _evidence_succeeded_count(grounded)
    qualification_criteria = checked_required or criteria
    qualified_in_checked = _fully_met_count(survivors, qualification_criteria)
    population_capped = matched_count > len(grounded)
    qualified_total = (
        qualified_in_checked
        if not population_capped and evidence_succeeded == len(grounded)
        else None
    )
    response_warnings = list(base_payload["warnings"])
    if evidence_succeeded < len(grounded):
        response_warnings.append(
            {
                "code": "evidence_incomplete",
                "message": (
                    f"Evidence checks completed for {evidence_succeeded} of "
                    f"{len(grounded)} candidates; failed checks remain unverified."
                ),
            }
        )

    # 4. Assemble.
    return {
        **base_payload,
        "evaluated": len(grounded),
        "deep_checked": len(grounded),
        "evidence_succeeded": evidence_succeeded,
        "shown": len(shown),
        "returned": len(shown),
        # A candidate is only fully qualified when *every requested* criterion
        # was checked. A bounded criteria cap therefore makes the count unknown,
        # even if all checked criteria were met.
        "qualified": qualified_in_checked,
        "qualified_in_checked": qualified_in_checked,
        "qualified_total": qualified_total,
        "eligible_after_hard_constraints": len(survivors),
        "search_status": (
            "matches_found" if shown else "no_verified_matches"
        ),
        "capped": population_capped,
        "candidates": shown,
        "excluded": excluded,
        "evidence_model": _ge.GROUNDING_MODEL,
        "warnings": response_warnings,
    }


# ---------------------------------------------------------------------------
# Rediscovery: screen the WHOLE already-scored pool against a NEW requirement.
#
# ``find_top_candidates`` shortlists the CURRENT pipeline, ranked by each
# candidate's existing score. Rediscovery is the inverse: a new requirement
# arrives for *similar* profiles and the recruiter wants who — across everyone
# ever scored, INCLUDING people scored for OTHER roles whose old score says
# nothing about THIS requirement — fits it. Same grounded machinery, retargeted
# at the scored history (the caller scopes ``base_query``) with a wider grounding
# window, ranked by fit to the NEW requirement (grounded met/partial — NOT the
# stale score). A bounded window is deep-checked via the cached Citations pass;
# we report how many were screened vs the pool (``capped``) and hand back
# ``rescore_candidate_ids`` for the opt-in Sonnet re-score that produces a true
# comparable score against the requirement.
# ---------------------------------------------------------------------------

# How many of the scored history get the grounded (Haiku, cache-backed) deep
# check in one pass. Wider than the top-N window — rediscovery wants breadth —
# but bounded for cost + the GROUND_BATCH_DEADLINE_S wall-clock. The structural
# recall biases WHICH candidates land in the window, so a strong fit for the new
# requirement is checked even when their old-role score was low.
SCREEN_GROUND_WINDOW = 30
DEFAULT_SCREEN_LIMIT = 20
MAX_SCREEN_LIMIT = 50


def screen_pool_against_requirement(
    *,
    db: Session,
    organization_id: int,
    role_id: int | None = None,
    requirement: str,
    base_query,
    limit: int = DEFAULT_SCREEN_LIMIT,
    parser_client=None,
    evidence_client=None,
    deep_verify: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    """Screen the already-scored pool (``base_query``) against a NEW free-text
    requirement.

    Returns the same grounded ``candidate_evidence`` payload as
    ``find_top_candidates`` — ranked by fit to THIS requirement, tagged
    ``mode="rediscovery"`` — plus ``screened`` / ``capped`` (how many of the
    pool were deep-checked) and ``rescore_candidate_ids`` (the shortlist to
    re-score against the requirement for a true comparable score).

    ``base_query`` MUST already be org-scoped + ``deleted_at IS NULL`` and
    SHOULD be restricted to scored candidates (``cv_match_details IS NOT NULL``).
    Never raises. Deep verification fails closed for qualitative must-haves if
    their evidence cannot be checked; non-verified preview mode remains clearly
    labelled as such.
    """
    from .runner import run_search  # local import keeps graph deps lazy

    limit = max(1, min(int(limit), MAX_SCREEN_LIMIT))
    offset = max(0, int(offset))
    score_col = SCORE_FIELDS["taali"]
    score_attr = getattr(CandidateApplication, score_col)

    # 1. Parse and run the zero-cost Postgres retrieval across the full scored
    #    pool. This is exhaustive at the person level; model verification is a
    #    separate, opt-in bounded step below.
    result = run_search(
        db=db,
        organization_id=organization_id,
        role_id=role_id,
        nl_query=requirement,
        base_query=base_query,
        rerank_enabled=False,
        include_subgraph=False,
        parser_client=parser_client,
        defer_qualitative=deep_verify,
    )
    parsed = result.parsed_filter
    requested_criteria, criteria, unchecked_criteria = _criteria_coverage(parsed)
    required_criteria = _required_criteria(parsed, requested_criteria)
    preferred_criteria = _preferred_criteria(parsed, requested_criteria)
    checked_required = _required_criteria(parsed, criteria)
    unchecked_required = [
        criterion
        for criterion in required_criteria
        if criterion.lower() not in {item.lower() for item in checked_required}
    ]
    result_ids = list(result.application_ids or [])
    matcher_ids = set(result_ids) if _has_structural(parsed) else None
    pool_count = _pool_count(base_query)
    matched_count = (
        int(result.database_matches)
        if result.database_matches is not None
        else len(result_ids)
    )
    matched_pool = base_query.filter(
        CandidateApplication.id.in_(result_ids or [-1])
    )

    warnings = [w.model_dump(mode="json") for w in result.warnings]
    if unchecked_criteria:
        warnings.append(
            {
                "code": "criteria_capped",
                "message": (
                    f"Checked {len(criteria)} of {len(requested_criteria)} qualitative "
                    "criteria; the unchecked criteria are listed separately."
                ),
            }
        )
    base_payload = {
        "spec": _build_spec(parsed, query=requirement, rank_by="taali", criteria=criteria),
        "mode": "rediscovery",
        "total_matched": matched_count,
        "database_matches": matched_count,
        "pool_size": pool_count,
        "structural_matches": len(matcher_ids) if matcher_ids is not None else None,
        "criteria_requested": requested_criteria,
        "criteria_checked": criteria,
        "criteria_unchecked": unchecked_criteria,
        "required_criteria": required_criteria,
        "preferred_criteria": preferred_criteria,
        "warnings": warnings,
        "rank_by": "taali",
        "offset": offset,
    }

    def _rank_key(a):
        return (
            bool(matcher_ids) and a.id in matcher_ids,
            getattr(a, score_col) is not None,
            getattr(a, score_col) or float("-inf"),
        )

    def _degrade(apps, *, warning):
        """Return the deterministic retrieval order without grounding."""
        shown = [
            _candidate_payload(a, rank=i, verdicts=[], has_criteria=bool(criteria))
            for i, a in enumerate(apps[:limit], start=1)
        ]
        return {
            **base_payload,
            "screened": 0,
            "capped": matched_count > len(shown),
            "screen_cap": SCREEN_GROUND_WINDOW,
            "evaluated": 0,
            "shown": len(shown),
            "returned": len(shown),
            "deep_checked": 0,
            "qualified": None,
            "qualified_in_checked": 0,
            "qualified_total": None,
            "search_status": warning["code"],
            "candidates": shown,
            "excluded": {
                "required_total": 0,
                "not_met_total": 0,
                "missing_total": 0,
                "partial_total": 0,
                "unverified_total": matched_count if required_criteria else 0,
                "by_criterion": [],
            },
            "evidence_model": None,
            "rescore_candidate_ids": [int(c["application_id"]) for c in shown],
            "warnings": base_payload["warnings"] + [warning],
        }

    if deep_verify and getattr(parsed, "parse_degraded", False):
        return _degrade(
            [],
            warning={
                "code": "parser_failed",
                "message": (
                    "The requirement could not be parsed reliably; no candidates "
                    "were presented as verified matches."
                ),
            },
        )

    # Default path: return deterministic database matches with honest coverage
    # and zero per-candidate model calls. A recruiter can explicitly request
    # deep verification for the bounded citations pass.
    if not deep_verify:
        page_ids = result_ids[offset : offset + limit]
        apps = _load_candidates_by_ids(matched_pool, page_ids)
        return _degrade(
            apps,
            warning={
                "code": "verification_not_requested",
                "message": (
                    "Returned exhaustive Postgres matches; deep CV verification "
                    "was not requested."
                ),
            },
        )

    # Structural-only asks are already exact; there is no narrative criterion
    # for a model to verify.
    if not criteria:
        page_ids = result_ids[offset : offset + limit]
        apps = _load_candidates_by_ids(matched_pool, page_ids)
        return _degrade(
            apps,
            warning={
                "code": "no_criteria",
                "message": "No qualitative criteria parsed; returned exact database matches.",
            },
        )

    if unchecked_required:
        return _degrade(
            [],
            warning={
                "code": "required_criteria_unchecked",
                "message": (
                    "Required criteria exceeded the bounded evidence limit; "
                    "no unverified candidates were presented as matches."
                ),
            },
        )

    # 2. Grounding client.
    client = evidence_client
    if client is None:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rediscovery grounding client init failed: %s", exc)

    if client is None:
        if any(not _is_constraint(criterion) for criterion in checked_required):
            return _degrade(
                [],
                warning={
                    "code": "rerank_skipped",
                    "message": (
                        "Required evidence could not be checked; no database-ranked "
                        "candidates were presented as verified matches."
                    ),
                },
            )
        apps = _load_candidates_by_ids(matched_pool, result_ids[:limit])
        return _degrade(
            apps,
            warning={
                "code": "rerank_skipped",
                "message": "Grounding unavailable; ranked by existing fit, not "
                "screened against the requirement.",
            },
        )

    # 3. Ground a bounded WINDOW of the scored history against the new requirement
    #    via the cached Citations pass. WHICH candidates fall in the window:
    #    structural matches first when the requirement carries a hard filter;
    #    otherwise (a purely-qualitative ask) seed by RECENCY, NOT the stale role
    #    score — score-seeding biases rediscovery toward already-high-scorers and
    #    buries exactly the under-scored fits the feature exists to surface.
    window_size = min(matched_count, SCREEN_GROUND_WINDOW)
    apps = _load_candidates_by_ids(
        matched_pool, result_ids[: max(window_size, limit)]
    )
    grounded = _ground_window(
        apps[:window_size],
        criteria=criteria,
        client=client,
        organization_id=organization_id,
        role_id=role_id,
    )

    # 4. Keep only grounded matches for required qualitative criteria; explicit
    #    preferences rank the verified set without becoming exclusion rules.
    survivors, excluded = _partition_required_matches(grounded, checked_required)
    preferred_keys = {criterion.lower() for criterion in preferred_criteria}
    required_constraint_keys = {
        criterion.lower()
        for criterion in checked_required
        if _is_constraint(criterion)
    }
    relevance_position = {
        int(application_id): index
        for index, application_id in enumerate(result_ids)
    }

    def _signal_key(item):
        app, verdicts = item
        constraint_met = sum(
            1
            for v in verdicts
            if v.criterion.lower() in required_constraint_keys
            and v.status == "met"
            and v.grounded
        )
        constraint_partial = sum(
            1
            for v in verdicts
            if v.criterion.lower() in required_constraint_keys
            and v.status == "partially_met"
            and v.grounded
        )
        met = sum(
            1
            for v in verdicts
            if v.criterion.lower() in preferred_keys
            and v.status == "met"
            and v.grounded
        )
        partial = sum(
            1
            for v in verdicts
            if v.criterion.lower() in preferred_keys
            and v.status == "partially_met"
            and v.grounded
        )
        relevance = -relevance_position.get(int(app.id), len(relevance_position) + 1)
        fit = getattr(app, score_col)
        return (
            constraint_met,
            constraint_partial,
            met,
            partial,
            relevance,
            fit if fit is not None else float("-inf"),
        )

    survivors.sort(key=_signal_key, reverse=True)
    shown = [
        _candidate_payload(app, rank=i, verdicts=verdicts, has_criteria=True)
        for i, (app, verdicts) in enumerate(survivors[:limit], start=1)
    ]
    evidence_succeeded = _evidence_succeeded_count(grounded)
    qualification_criteria = checked_required or criteria
    qualified_in_checked = _fully_met_count(survivors, qualification_criteria)
    population_capped = matched_count > len(grounded)
    qualified_total = (
        qualified_in_checked
        if not population_capped and evidence_succeeded == len(grounded)
        else None
    )
    response_warnings = list(base_payload["warnings"])
    if evidence_succeeded < len(grounded):
        response_warnings.append(
            {
                "code": "evidence_incomplete",
                "message": (
                    f"Evidence checks completed for {evidence_succeeded} of "
                    f"{len(grounded)} candidates; failed checks remain unverified."
                ),
            }
        )

    # 5. Assemble. ``rescore_candidate_ids`` = the shortlist worth a true Sonnet
    #    score against the requirement (the opt-in, bounded re-score step).
    return {
        **base_payload,
        "screened": len(grounded),
        "capped": population_capped,
        "screen_cap": SCREEN_GROUND_WINDOW,
        "evaluated": len(grounded),
        "deep_checked": len(grounded),
        "evidence_succeeded": evidence_succeeded,
        "shown": len(shown),
        "returned": len(shown),
        "qualified": qualified_in_checked,
        "qualified_in_checked": qualified_in_checked,
        "qualified_total": qualified_total,
        "eligible_after_hard_constraints": len(survivors),
        "search_status": "matches_found" if shown else "no_verified_matches",
        "candidates": shown,
        "excluded": excluded,
        "evidence_model": _ge.GROUNDING_MODEL,
        "warnings": response_warnings,
        "rescore_candidate_ids": [int(c["application_id"]) for c in shown],
    }
