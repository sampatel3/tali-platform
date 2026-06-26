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
from . import self_score as _ss
from .grounded_evidence import CriterionVerdict, Evidence

logger = logging.getLogger("taali.candidate_search.top_candidates")

# Default shortlist size and the hard cap (each shortlisted candidate may
# cost one Haiku citation call, so the cap bounds spend).
DEFAULT_LIMIT = 10
MAX_LIMIT = 25
# Cap the number of qualitative criteria we ground per query.
MAX_CRITERIA = 5
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

# The parser occasionally splits a numeric constraint into a bare label and a
# bare value ("salary" + "30000 AED") and drops the operator. These detect each
# fragment so we can reassemble one operator-bearing line ("salary <= 30000 AED").
_LABEL_FRAGMENT_RE = re.compile(
    r"(salar(?:y|ies)|compensation|\bpay\b|package|day\s*rate|\brate\b|notice(?:\s+period)?)"
    r"(?:\s+(?:expectation|expected|requirement|req))?",
    re.I,
)
_VALUE_FRAGMENT_RE = re.compile(
    r"(?:<=|>=|<|>|less\s+than|under|below|over|above|at\s+most|at\s+least|"
    r"no\s+more\s+than|up\s+to|max(?:imum)?|min(?:imum)?)?\s*"
    r"\d[\d,\.]*\s*(?:k|m)?\s*"
    r"(?:aed|usd|eur|gbp|sar|inr|dirhams?|dollars?|pounds?|euros?)?\s*"
    r"(?:/\s*(?:year|month|yr|mo)|per\s+(?:year|month|annum)|p\.?a\.?)?",
    re.I,
)
_GEQ_RE = re.compile(r"\b(over|above|more\s+than|greater\s+than|at\s+least|min(?:imum)?|>=?)\b", re.I)
_LEQ_RE = re.compile(
    r"\b(under|below|less\s+than|at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?|<=?)\b", re.I
)

# A salary/currency CAP verdict is ARITHMETIC, not judgement. The grounding
# model extracts + cites the stated figure (which it does well); the
# met/partial/not_met call is then computed deterministically below so the model
# can't mislabel a clear pass (e.g. 18,000 vs a 30,000 cap as "partial"). Cap
# detection must catch a bare "<=" (no leading \b, unlike the word operators).
_CAP_TOLERANCE = 1.25  # mirrors the grounding prompt's "partial within 25%" band
_CAP_CRIT_RE = re.compile(
    r"(<=?|\b(?:under|below|less\s+than|at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?)\b)",
    re.I,
)
_MONEY_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\b", re.I)


def _money_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _MONEY_NUM_RE.finditer(text or ""):
        val = float(m.group(1).replace(",", ""))
        suf = (m.group(2) or "").lower()
        if suf == "k":
            val *= 1_000
        elif suf == "m":
            val *= 1_000_000
        out.append(val)
    return out


def _recompute_currency_cap_verdict(v: CriterionVerdict) -> None:
    """Recompute a salary/currency CAP verdict from the CITED value vs the cap,
    overriding the grounding model's verdict word. No-op unless the criterion is
    a currency/salary cap with a number AND the evidence yields exactly one
    value in a sane band around the cap — otherwise the model's verdict stands
    (so a wrong/year-only citation or an ambiguous range can't flip it)."""
    crit = v.criterion or ""
    if not _CAP_CRIT_RE.search(crit):
        return
    if not (
        _CURRENCY_RE.search(crit)
        or re.search(r"salar|compensation|\bpay\b|wage|package", crit, re.I)
    ):
        return
    caps = _money_in(crit)
    if not caps:
        return
    cap = max(caps)
    in_band = [
        n
        for e in v.evidence
        for n in _money_in(e.quote)
        if 0.1 * cap <= n <= 10 * cap
    ]
    # drop a bare echo of the cap itself if a distinct stated value is present
    distinct = {round(n, 2) for n in in_band if abs(n - cap) > 1e-9} or {
        round(n, 2) for n in in_band
    }
    if len(distinct) != 1:
        return
    stated = next(iter(distinct))
    if stated <= cap:
        v.status = "met"
    elif stated <= _CAP_TOLERANCE * cap:
        v.status = "partially_met"
    else:
        v.status = "not_met"


# A "Taali score >= 60" criterion is SELF-REFERENTIAL: it gates on the
# platform's own computed score, not on anything in the CV or notes. The
# grounding model only reads the CV + notes, so it can NEVER find evidence for it
# and dutifully marks it "missing" — even though the score sits right there on
# the candidate (the same value the ranking and the "Taali NN" badge use). So we
# decide these ARITHMETICALLY against the candidate's Taali score, the same way
# `_recompute_currency_cap_verdict` decides a salary cap from the cited figure
# rather than trusting the model's verdict word. Detection, threshold parsing,
# and wording live in the shared `self_score` module so the authed candidate page
# (which decides the same criteria over stored requirements_assessment rows)
# can't drift from this report path.
_is_self_score_criterion = _ss.is_self_score_criterion
_parse_score_threshold = _ss.parse_score_threshold


def _recompute_self_score_verdict(v: CriterionVerdict, app: CandidateApplication) -> None:
    """Decide a self-referential "Taali score" criterion against the candidate's
    own Taali score, overriding the (always-empty) CV-evidence verdict. No-op for
    any other criterion, or when the candidate has no score yet — then we leave
    the honest "couldn't find it" rather than assert a pass/fail without data."""
    score = getattr(app, "taali_score_cache_100", None)
    decision = _ss.self_score_decision(v.criterion, score)
    if decision is None:
        return
    meets, op, threshold = decision
    v.status = "met" if meets else "not_met"
    v.grounded = True
    v.source = "taali_score"
    v.evidence = [Evidence(quote=_ss.self_score_evidence_quote(score), source="taali_score")]
    v.note = _ss.self_score_note(meets, op, threshold, score)


def _is_constraint(criterion: str) -> bool:
    c = criterion or ""
    if _CONSTRAINT_KW_RE.search(c):
        return True
    if _THRESHOLD_RE.search(c) and _UNIT_RE.search(c):
        return True
    if _CURRENCY_RE.search(c) and re.search(r"\d", c):
        return True
    return False


def _merge_constraint_fragments(criteria: list[str], free_text: str | None) -> list[str]:
    """Reassemble a numeric constraint the parser split apart.

    A bare label ("salary") plus a bare value ("30000 AED") become one
    operator-bearing line ("salary <= 30000 AED") so the grounder reads it as a
    single cap rather than two meaningless criteria. The operator is taken from
    the value fragment or the original query; it defaults to ``<=`` (the common
    salary / notice-period cap). No-op when no such fragment pair is present —
    so a parser that already emitted one clean phrase is left untouched."""
    label_i = value_i = None
    for i, c in enumerate(criteria):
        s = (c or "").strip()
        if label_i is None and _LABEL_FRAGMENT_RE.fullmatch(s):
            label_i = i
        elif value_i is None and re.search(r"\d", s) and _VALUE_FRAGMENT_RE.fullmatch(s):
            value_i = i
    if label_i is None or value_i is None:
        return criteria

    raw_value = criteria[value_i].strip()
    op_src = raw_value if _THRESHOLD_RE.search(raw_value) else (free_text or "")
    op = ">=" if (_GEQ_RE.search(op_src) and not _LEQ_RE.search(op_src)) else "<="
    value = _THRESHOLD_RE.sub("", raw_value).strip(" \t-–—")
    merged = f"{criteria[label_i].strip()} {op} {value}".strip()

    out = [c for i, c in enumerate(criteria) if i not in (label_i, value_i)]
    out.insert(min(label_i, value_i), merged)
    return out

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


def _collect_criteria(parsed) -> list[str]:
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
    return kept[:MAX_CRITERIA]


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
        "locations": locations,
        "min_years_experience": parsed.min_years_experience,
    }
    parts: list[str] = []
    pop_bits = list(parsed.skills_all) + list(parsed.skills_any)
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
    location / years / graph)? When it did NOT, the whole actionable pool is
    ranked by score; when it did, those matches only BIAS the grounding window
    — they never exclude. So a parser mistake can reorder the queue but cannot
    empty the result; grounding makes the actual met/over-cap/not-met calls."""
    return bool(
        parsed.skills_all
        or parsed.skills_any
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

    # 1. Parse. The parser's STRUCTURAL guesses (skills / candidate location /
    #    years) are used ONLY to bias which candidates get grounded first — they
    #    never exclude anyone. The pool is the whole actionable set; grounding
    #    (evidence-based) makes every met / over-cap / not-met call below.
    result = run_search(
        db=db,
        organization_id=organization_id,
        nl_query=query,
        base_query=base_query,
        rerank_enabled=False,
        include_subgraph=False,
        parser_client=parser_client,
        defer_qualitative=True,
    )
    parsed = result.parsed_filter
    criteria = _collect_criteria(parsed)

    score_col = SCORE_FIELDS[rank_by]
    score_attr = getattr(CandidateApplication, score_col)
    # Structural matches bias the window; `None` when the query had no structural
    # filter at all (then the whole pool is fair game, ranked by score).
    matcher_ids = set(result.application_ids or []) if _has_structural(parsed) else None
    pool_count = _pool_count(base_query)

    base_payload = {
        "spec": _build_spec(parsed, query=query, rank_by=rank_by, criteria=criteria),
        # The pool we ranked over — NOT a structural-filter count. A parse miss
        # can no longer report a misleading "0 matched"; 0 here means the role
        # genuinely has no actionable candidates.
        "total_matched": pool_count,
        "structural_matches": len(matcher_ids) if matcher_ids is not None else None,
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "rank_by": rank_by,
    }

    # Final/window ordering: structural matches first, then by score. Applied in
    # Python so it's deterministic regardless of the bounded load order.
    def _rank_key(a):
        return (
            bool(matcher_ids) and a.id in matcher_ids,
            getattr(a, score_col) is not None,
            getattr(a, score_col) or float("-inf"),
        )

    # No qualitative/constraint criteria → just the top `limit` by score.
    if not criteria:
        apps = _load_candidates(
            base_query, matcher_ids=matcher_ids, score_attr=score_attr, size=limit
        )
        apps.sort(key=_rank_key, reverse=True)
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

    # 2. Grounding client.
    client = evidence_client
    if client is None:
        try:
            from ..services.claude_client_resolver import get_metered_client

            client = get_metered_client(organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grounding client init failed: %s", exc)

    if client is None:
        # Grounding unavailable → degrade to a ranked list, no filtering.
        apps = _load_candidates(
            base_query, matcher_ids=matcher_ids, score_attr=score_attr, size=limit
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
    window_size = min(pool_count, GROUND_WINDOW_CAP)
    apps = _load_candidates(
        base_query,
        matcher_ids=matcher_ids,
        score_attr=score_attr,
        size=max(window_size, limit),
    )
    apps.sort(key=_rank_key, reverse=True)
    grounded = _ground_window(
        apps[:window_size], criteria=criteria, client=client, organization_id=organization_id
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

    # 4. Assemble.
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
    requirement: str,
    base_query,
    limit: int = DEFAULT_SCREEN_LIMIT,
    parser_client=None,
    evidence_client=None,
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
    score_col = SCORE_FIELDS["taali"]
    score_attr = getattr(CandidateApplication, score_col)

    # 1. Parse → population + qualitative criteria. Structural guesses only BIAS
    #    which candidates get grounded first; they never exclude.
    result = run_search(
        db=db,
        organization_id=organization_id,
        nl_query=requirement,
        base_query=base_query,
        rerank_enabled=False,
        include_subgraph=False,
        parser_client=parser_client,
        defer_qualitative=True,
    )
    parsed = result.parsed_filter
    criteria = _collect_criteria(parsed)
    matcher_ids = set(result.application_ids or []) if _has_structural(parsed) else None
    pool_count = _pool_count(base_query)

    base_payload = {
        "spec": _build_spec(parsed, query=requirement, rank_by="taali", criteria=criteria),
        "mode": "rediscovery",
        "total_matched": pool_count,
        "structural_matches": len(matcher_ids) if matcher_ids is not None else None,
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "rank_by": "taali",
    }

    def _rank_key(a):
        return (
            bool(matcher_ids) and a.id in matcher_ids,
            getattr(a, score_col) is not None,
            getattr(a, score_col) or float("-inf"),
        )

    def _degrade(apps, *, warning):
        """Rank by existing fit (no grounding) and return — used when the
        requirement has no qualitative criteria or grounding is unavailable."""
        apps.sort(key=_rank_key, reverse=True)
        shown = [
            _candidate_payload(a, rank=i, verdicts=[], has_criteria=bool(criteria))
            for i, a in enumerate(apps[:limit], start=1)
        ]
        return {
            **base_payload,
            "screened": 0,
            "capped": pool_count > len(shown),
            "screen_cap": SCREEN_GROUND_WINDOW,
            "evaluated": 0,
            "shown": len(shown),
            "candidates": shown,
            "excluded": {"not_met_total": 0, "by_criterion": []},
            "evidence_model": None,
            "rescore_candidate_ids": [int(c["application_id"]) for c in shown],
            "warnings": base_payload["warnings"] + [warning],
        }

    # No qualitative criteria → nothing to ground; rank the recall by fit.
    if not criteria:
        # Structural-only ask (e.g. a bare skill like "Python"): the requirement
        # IS the structural filter, so HARD-restrict to the matches. matcher_ids
        # is only an ordering bias inside _load_candidates, so without this a
        # short match list gets padded with high-scoring candidates that don't
        # match the requirement at all — surfacing unrelated people as finds.
        pool = base_query if matcher_ids is None else base_query.filter(
            CandidateApplication.id.in_(matcher_ids)
        )
        apps = _load_candidates(
            pool, matcher_ids=matcher_ids, score_attr=score_attr, size=limit
        )
        return _degrade(
            apps,
            warning={
                "code": "no_criteria",
                "message": "No qualitative criteria parsed from the requirement; "
                "ranked by existing fit.",
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
        apps = _load_candidates(
            base_query, matcher_ids=matcher_ids, score_attr=score_attr, size=limit
        )
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
    window_size = min(pool_count, SCREEN_GROUND_WINDOW)
    seed_attr = score_attr if matcher_ids else CandidateApplication.created_at
    apps = _load_candidates(
        base_query,
        matcher_ids=matcher_ids,
        score_attr=seed_attr,
        size=max(window_size, limit),
    )
    if matcher_ids:
        apps.sort(key=_rank_key, reverse=True)
    else:
        # Recency order; compare datetimes only (uniform tz from the column),
        # undated rows last — a score-neutral window seed.
        dated = sorted(
            (a for a in apps if a.created_at is not None),
            key=lambda a: a.created_at,
            reverse=True,
        )
        apps = dated + [a for a in apps if a.created_at is None]
    grounded = _ground_window(
        apps[:window_size], criteria=criteria, client=client, organization_id=organization_id
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

    # 5. Assemble. ``rescore_candidate_ids`` = the shortlist worth a true Sonnet
    #    score against the requirement (the opt-in, bounded re-score step).
    return {
        **base_payload,
        "screened": len(grounded),
        "capped": pool_count > len(grounded),
        "screen_cap": SCREEN_GROUND_WINDOW,
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
        "rescore_candidate_ids": [int(c["application_id"]) for c in shown],
    }
