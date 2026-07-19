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

from ..models.candidate_application import CandidateApplication
from ..mcp.payloads import SCORE_FIELDS, application_summary
from ..services.provider_error_evidence import safe_provider_error_code as _safe_error
from . import constraint_verdicts as _constraint_verdicts
from . import grounded_evidence as _ge
from .candidate_presenters import (
    candidate_blurb as _candidate_blurb,
    scoring_summary as _scoring_summary,
    years_experience as _years_experience,
)
from .constraint_verdicts import (
    is_constraint as _is_constraint,
    merge_constraint_fragments as _merge_constraint_fragments,
    recompute_currency_cap_verdict as _recompute_currency_cap_verdict,
    recompute_self_score_verdict as _recompute_self_score_verdict,
)
from .grounded_evidence import CriterionVerdict, Evidence
from .deadline_pool import run_deadline_pool

_is_self_score_criterion = _constraint_verdicts.is_self_score_criterion
_parse_score_threshold = _constraint_verdicts.parse_score_threshold

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
# When criteria are present we ground a DEEP, score-ranked window — not just
# `limit*3`. Requirements act as a filter (salary over cap is hidden) and the
# soft-criteria re-sort floats criteria-meeting candidates above higher-scored
# partial matches; both only work if the candidate is in the window. A shallow
# window silently drops a mid-scored candidate who meets every criterion. We
# therefore ground the whole viable pool up to this cap (callers restrict the
# pool to in-the-running candidates, so this rarely truncates). Bounds cost +
# latency; stragglers past GROUND_BATCH_DEADLINE_S degrade to "unknown".
GROUND_WINDOW_CAP = 50
GROUND_CONCURRENCY = 12
# Hard wall-clock deadline for the whole grounding batch behind a chat turn.
# Any candidate not grounded by then degrades to "unknown" (missing) rather
# than stalling the response — strangler calls are abandoned, not awaited.
GROUND_BATCH_DEADLINE_S = 45.0

_RANKING_LABELS = {
    "taali": "Taali fit",
    "pre_screen": "pre-screen score",
    "rank": "pairwise rank",
    "cv_match": "CV-match score",
    "workable": "Workable score",
    "assessment": "assessment score",
    "role_fit": "role-fit score",
}

_STOPWORDS = {
    "a", "an", "the", "with", "and", "or", "of", "in", "on", "for", "to",
    "experience", "domain", "background", "knowledge", "skills", "strong",
    "candidate", "candidates", "who", "has", "have", "is", "are", "at",
}
_TOKEN_RE = re.compile(r"[a-z0-9+#]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


# Count / filler fragments that leak from a query's text ("top 5", "candidates",
# "best 3 candidates") — never a real quality to ground against.
_JUNK_CRITERION_RE = re.compile(
    r"(?:(?:the\s+)?(?:top|best|first|latest|show(?:\s+me)?|give\s+me|find|list))?\s*"
    r"\d*\s*(?:candidates?|people|profiles?|results?|matches)?",
    re.I,
)


def _is_junk_criterion(text: str) -> bool:
    return bool(_JUNK_CRITERION_RE.fullmatch((text or "").strip()))


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
        logger.debug("notes context unavailable app=%s error_code=%s", app.id, _safe_error(exc, operation="candidate_notes"))
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
    deadline_monotonic: float | None = None,
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
        deadline_monotonic=deadline_monotonic,
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
    role_id: int | None = None, db: Session | None = None,
) -> list[tuple[CandidateApplication, list[CriterionVerdict]]]:
    """Ground concurrently after snapshotting evidence and releasing ``db``."""
    if not apps:
        return []
    jobs = [(app, int(app.id), *_collect_evidence(app)) for app in apps]
    if db is not None:
        db.rollback()

    def _one(job, deadline):
        _app, application_id, cv, notes = job
        try:
            return _ground(
                cv, notes,
                criteria=criteria,
                client=client,
                organization_id=organization_id,
                role_id=role_id,
                application_id=application_id,
                deadline_monotonic=deadline,
            )
        except Exception as exc:  # noqa: BLE001 — degrade this candidate, not the query
            logger.warning("ground app=%s failed error_code=%s", application_id, _safe_error(exc, operation="candidate_grounding"))
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
    batch = run_deadline_pool(
        jobs,
        _one,
        max_workers=workers,
        timeout_s=GROUND_BATCH_DEADLINE_S,
    )
    if batch.incomplete:
        logger.warning(
            "grounding batch deadline (%.0fs) hit: %d/%d candidates incomplete",
            GROUND_BATCH_DEADLINE_S,
            len(batch.incomplete),
            len(jobs),
        )

    return [
        (jobs[i][0], batch.results.get(i) or [_timed_out(c) for c in criteria])
        for i in range(len(jobs))
    ]


def _collect_criteria(parsed, *, limit: int | None = MAX_CRITERIA) -> list[str]:
    """Qualitative criteria to ground: soft criteria + residual keywords,
    deduped and capped.

    Beyond exact-text dedup, collapse NEAR-duplicates: when one criterion's
    significant tokens are a subset of another's they're asking the same thing
    ("Western company" vs "Western enterprise company", "banking" vs "banking
    domain experience") — keep the more specific (superset) one and drop the
    generic. Without this the parser's two phrasings each ground separately and
    the card shows the same employer evidence twice."""
    raw: list[str] = []
    seen: set[str] = set()
    for c in list(parsed.soft_criteria) + list(parsed.keywords):
        c = (c or "").strip()
        key = c.lower()
        if c and key not in seen and not _is_junk_criterion(c):
            seen.add(key)
            raw.append(c)

    kept: list[str] = []
    for i, c in enumerate(raw):
        ct = _tokens(c)
        if not ct:
            kept.append(c)
            continue
        dominated = False
        for j, other in enumerate(raw):
            if i == j:
                continue
            ot = _tokens(other)
            if not ot:
                continue
            # A strict superset criterion dominates (drop the generic subset);
            # for identical token sets keep only the earliest occurrence.
            if ct < ot or (ct == ot and j < i):
                dominated = True
                break
        if not dominated:
            kept.append(c)

    kept = _merge_constraint_fragments(kept, getattr(parsed, "free_text", None))
    return kept if limit is None else kept[: max(0, int(limit))]


def _criteria_coverage(parsed) -> tuple[list[str], list[str], list[str]]:
    requested = _collect_criteria(parsed, limit=None)
    checked = requested[:MAX_CRITERIA]
    return requested, checked, requested[MAX_CRITERIA:]


_ROLE_PRIORITY_ORDER = {
    "constraint": 0,
    "must_have": 1,
    "strong_preference": 2,
    "nice_to_have": 3,
}


def _stored_role_requirement_verdicts(
    app: CandidateApplication,
    *,
    limit: int = DEFAULT_ROLE_EVIDENCE_LIMIT,
) -> list[CriterionVerdict]:
    """Reuse citation-bearing scorecard rows to explain a bare role top-N.

    These rows were produced by the canonical CV↔role scorer and already carry
    verbatim evidence quotes. They explain *why* the stored role-fit score ranks
    a candidate; they do not re-evaluate the candidate or pretend a fresh search
    evidence pass ran.
    """

    details = getattr(app, "cv_match_details", None)
    rows = details.get("requirements_assessment") if isinstance(details, dict) else None
    if not isinstance(rows, list):
        return []

    indexed = [
        (index, row)
        for index, row in enumerate(rows)
        if isinstance(row, dict)
    ]
    indexed.sort(
        key=lambda item: (
            _ROLE_PRIORITY_ORDER.get(
                str(item[1].get("priority") or "").strip().lower(),
                4,
            ),
            item[0],
        )
    )

    verdicts: list[CriterionVerdict] = []
    for _, row in indexed:
        criterion = str(
            row.get("requirement")
            or row.get("criterion_text")
            or row.get("label")
            or ""
        ).strip()
        if not criterion:
            continue

        raw_quotes = row.get("evidence_quotes")
        if not isinstance(raw_quotes, list):
            raw = row.get("evidence") or row.get("cv_quote")
            raw_quotes = raw if isinstance(raw, list) else ([raw] if raw else [])
        quotes = [
            quote.strip()
            for quote in raw_quotes
            if isinstance(quote, str) and quote.strip()
        ][:3]

        raw_status = str(row.get("status") or "missing").strip().lower().replace(" ", "_")
        status = {
            "partial": "partially_met",
            "partially": "partially_met",
            "unknown": "missing",
        }.get(raw_status, raw_status)
        if status not in {"met", "partially_met", "not_met", "missing", "error"}:
            status = "missing"

        verdicts.append(
            CriterionVerdict(
                criterion=criterion,
                status=status,
                grounded=bool(quotes),
                source="role_requirement" if quotes else "none",
                evidence=[Evidence(quote=quote, source="role_requirement") for quote in quotes],
                note=str(row.get("reasoning") or row.get("impact") or "").strip(),
            )
        )
        if len(verdicts) >= max(1, int(limit)):
            break
    return verdicts


def _fully_met_count(
    rows: list[tuple[CandidateApplication, list[CriterionVerdict]]],
) -> int:
    """Candidates for whom every requested criterion is cited and met."""

    return sum(
        1
        for _app, verdicts in rows
        if verdicts and all(v.status == "met" and v.grounded for v in verdicts)
    )


def _evidence_succeeded_count(
    rows: list[tuple[CandidateApplication, list[CriterionVerdict]]],
) -> int:
    """Candidate checks that completed without a transient error verdict."""

    return sum(
        1
        for _app, verdicts in rows
        if verdicts and all(v.status != "error" for v in verdicts)
    )


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
    return {
        "query": query,
        "population": population,
        # This describes the requested evaluation, not its outcome. Grounding
        # is determined per candidate only after a verbatim citation is
        # attached; degraded searches must never inherit a truthy spec flag.
        "criteria": [
            {"text": c, "kind": "qualitative", "requires_grounding": True}
            for c in criteria
        ],
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
) -> dict[str, Any]:
    """Run the grounded top-N procedure. Never raises — degrades to a ranked
    list with warnings if grounding is unavailable."""
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
    )
    parsed = result.parsed_filter
    requested_criteria, criteria, unchecked_criteria = _criteria_coverage(parsed)

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
        "warnings": warnings,
        "rank_by": rank_by,
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
            "eligible_after_hard_constraints": 0,
            "capped": False,
            "candidates": [],
            "excluded": {"not_met_total": 0, "by_criterion": []},
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
            "excluded": {"not_met_total": 0, "by_criterion": []},
            "evidence_model": None,
        }

    # 2. Grounding client.
    client = evidence_client
    if client is None:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grounding client init failed error_code=%s", _safe_error(exc, operation="grounding_client_init"))

    if client is None:
        # Grounding unavailable → degrade to a ranked list, no filtering.
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
            "capped": matched_count > len(shown),
            "candidates": shown,
            "excluded": {"not_met_total": 0, "by_criterion": []},
            "evidence_model": None,
            "warnings": base_payload["warnings"]
            + [{"code": "rerank_skipped", "message": "Grounding unavailable; not filtered."}],
        }

    # 3. Ground a bounded, score-ranked WINDOW (structural matches first). A
    #    failed HARD CONSTRAINT (salary over cap, …) hides the candidate; a
    #    failed PREFERENCE only ranks lower. Ground the whole pool up to the
    #    cap — NOT just `limit*3` — so a criteria-meeting but mid-scored
    #    candidate is never silently dropped before the soft-criteria re-sort
    #    can float them up. Callers scope the pool to in-the-running candidates
    #    (scored, not below-threshold), so this seldom truncates. The window is
    #    loaded bounded, so even an org-wide pool never materialises in full.
    window_size = min(matched_count, GROUND_WINDOW_CAP)
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
        db=db,
    )

    survivors: list[tuple[CandidateApplication, list[CriterionVerdict]]] = []
    excluded_not_met = 0
    by_criterion: dict[str, int] = {}
    for app, verdicts in grounded:
        failed = [
            v for v in verdicts if v.status == "not_met" and _is_constraint(v.criterion)
        ]
        if failed:
            excluded_not_met += 1
            for v in failed:
                by_criterion[v.criterion] = by_criterion.get(v.criterion, 0) + 1
            continue
        survivors.append((app, verdicts))

    # Rank survivors by CLEAR SIGNAL first: grounded `met` above `partially_met`
    # above unknown/`missing`; a structural match breaks ties next; fit (score)
    # last. So strong, on-target matches lead and the fuzzier ones rank below
    # rather than being hidden.
    def _signal_key(item):
        app, verdicts = item
        met = sum(1 for v in verdicts if v.status == "met" and v.grounded)
        partial = sum(1 for v in verdicts if v.status == "partially_met" and v.grounded)
        matched = bool(matcher_ids) and app.id in matcher_ids
        fit = getattr(app, score_col)
        return (met, partial, matched, fit if fit is not None else float("-inf"))

    survivors.sort(key=_signal_key, reverse=True)
    shown = [
        _candidate_payload(app, rank=i, verdicts=verdicts, has_criteria=True)
        for i, (app, verdicts) in enumerate(survivors[:limit], start=1)
    ]
    evidence_succeeded = _evidence_succeeded_count(grounded)
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
        "qualified": (
            None if unchecked_criteria else _fully_met_count(survivors)
        ),
        "eligible_after_hard_constraints": len(survivors),
        "capped": matched_count > len(grounded),
        "candidates": shown,
        "excluded": {
            "not_met_total": excluded_not_met,
            "by_criterion": [
                {"criterion": c, "count": n} for c, n in by_criterion.items()
            ],
        },
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
    Never raises — degrades to a ranked list with warnings.
    """
    from .runner import run_search  # local import keeps graph deps lazy

    limit = max(1, min(int(limit), MAX_SCREEN_LIMIT))
    offset = max(0, int(offset))
    score_col = SCORE_FIELDS["taali"]

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
        defer_qualitative=False,
    )
    parsed = result.parsed_filter
    requested_criteria, criteria, unchecked_criteria = _criteria_coverage(parsed)
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
            "candidates": shown,
            "excluded": {"not_met_total": 0, "by_criterion": []},
            "evidence_model": None,
            "rescore_candidate_ids": [int(c["application_id"]) for c in shown],
            "warnings": base_payload["warnings"] + [warning],
        }

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

    # 2. Grounding client.
    client = evidence_client
    if client is None:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rediscovery grounding client init failed error_code=%s", _safe_error(exc, operation="rediscovery_client_init"))

    if client is None:
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
        db=db,
    )

    # 4. Hide hard-constraint failures (salary over cap, …); rank the rest by fit
    #    to the NEW requirement (grounded met → partial → structural → score).
    survivors: list[tuple[CandidateApplication, list[CriterionVerdict]]] = []
    excluded_not_met = 0
    by_criterion: dict[str, int] = {}
    for app, verdicts in grounded:
        failed = [
            v for v in verdicts if v.status == "not_met" and _is_constraint(v.criterion)
        ]
        if failed:
            excluded_not_met += 1
            for v in failed:
                by_criterion[v.criterion] = by_criterion.get(v.criterion, 0) + 1
            continue
        survivors.append((app, verdicts))

    def _signal_key(item):
        app, verdicts = item
        met = sum(1 for v in verdicts if v.status == "met" and v.grounded)
        partial = sum(1 for v in verdicts if v.status == "partially_met" and v.grounded)
        matched = bool(matcher_ids) and app.id in matcher_ids
        fit = getattr(app, score_col)
        return (met, partial, matched, fit if fit is not None else float("-inf"))

    survivors.sort(key=_signal_key, reverse=True)
    shown = [
        _candidate_payload(app, rank=i, verdicts=verdicts, has_criteria=True)
        for i, (app, verdicts) in enumerate(survivors[:limit], start=1)
    ]
    evidence_succeeded = _evidence_succeeded_count(grounded)
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
        "capped": matched_count > len(grounded),
        "screen_cap": SCREEN_GROUND_WINDOW,
        "evaluated": len(grounded),
        "deep_checked": len(grounded),
        "evidence_succeeded": evidence_succeeded,
        "shown": len(shown),
        "returned": len(shown),
        "qualified": (
            None if unchecked_criteria else _fully_met_count(survivors)
        ),
        "eligible_after_hard_constraints": len(survivors),
        "candidates": shown,
        "excluded": {
            "not_met_total": excluded_not_met,
            "by_criterion": [
                {"criterion": c, "count": n} for c, n in by_criterion.items()
            ],
        },
        "evidence_model": _ge.GROUNDING_MODEL,
        "warnings": response_warnings,
        "rescore_candidate_ids": [int(c["application_id"]) for c in shown],
    }
