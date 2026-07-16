from __future__ import annotations

from datetime import datetime
from typing import Any

from ...candidate_search.self_score import (
    self_score_decision,
    self_score_evidence_quote,
    self_score_note,
)
from ...models.candidate_application import CandidateApplication
from ...services.taali_scoring import normalize_score_100


def _graph_state_for(app: CandidateApplication) -> tuple[datetime | None, bool | None]:
    """Return ``(last_synced_at, stale)`` for the candidate's graph_sync_state.

    Reads from the relationship if eagerly loaded; otherwise returns
    ``(None, None)``. We deliberately do NOT issue a fresh DB query per row
    because this is called inside the list-applications hot path. Callers
    that want this populated should load it via the join in their query.

    ``stale=True`` iff the CV was uploaded after the last graph sync.
    """
    candidate = getattr(app, "candidate", None)
    if candidate is None:
        return None, None
    state = None
    # graph_sync_state is a 1:1 relationship on Candidate via candidate_id.
    # Access lazily so it works whether the caller eager-loaded it or not;
    # SQLAlchemy will issue one extra SELECT per candidate when not loaded.
    try:
        state = getattr(candidate, "graph_sync_state", None)
    except Exception:
        return None, None
    if state is None or getattr(state, "last_synced_at", None) is None:
        return None, None
    last = state.last_synced_at
    cv_uploaded = candidate.cv_uploaded_at or app.cv_uploaded_at
    stale = bool(cv_uploaded and cv_uploaded > last)
    return last, stale


def _graph_synced_at_for(app: CandidateApplication) -> datetime | None:
    return _graph_state_for(app)[0]


def _graph_stale_for(app: CandidateApplication) -> bool | None:
    return _graph_state_for(app)[1]


def _normalize_cv_match_score_for_response(
    score: float | None, details: dict | None
) -> float | None:
    """Coerce ``app.cv_match_score`` into 0-100 for the response.

    The v3 CV-match runner writes ``cv_match_score`` as the aggregated
    ``role_fit_score`` on a 0-100 scale. Legacy LLM paths only ever emit
    0-100 too. The old fallback "if ``numeric <= 10`` multiply by 10"
    silently inflated *real* weak scores — a candidate with
    ``role_fit_score = 9.6`` displayed as 96, masking a weak-fit
    candidate as a top one. Don't do that. The remaining ``"10" in
    scale`` branch is kept for explicit legacy payloads that tag a
    ``score_scale = "0-10"`` and really do need rescaling.
    """
    if score is None:
        return None
    scale = str((details or {}).get("score_scale") or "").strip().lower()
    if "10" in scale and "100" not in scale:
        try:
            numeric = float(score)
        except (TypeError, ValueError):
            return None
        if numeric < 0:
            return None
        return round(max(0.0, min(100.0, numeric * 10.0)), 1)
    return normalize_score_100(score)


def _normalize_score_100_for_response(value: float | int | None) -> float | None:
    return normalize_score_100(value)


def _apply_self_score_requirements(details: dict, taali_score: Any) -> dict:
    """Decide self-referential "Taali score >= N" requirements at response time.

    A recruiter criterion like "Taali score >= 60" gates on the platform's own
    computed score (``taali_score_cache_100`` — the value behind the "Taali NN"
    badge), not on anything in the CV/notes. But role criteria are fed verbatim
    into the cv-match LLM, which only reads the CV + Workable notes, so it can
    never find evidence and stores the requirement as "missing" even when the
    candidate clearly clears the threshold. We correct that here, at read time,
    so already-scored candidates render correctly without a re-score — the score
    may not even have been computed yet when the requirement was first assessed.

    Decided arithmetically (the score is its own evidence), mirroring the grounded
    report's ``top_candidates._recompute_self_score_verdict`` via the shared
    ``self_score`` helpers. Treated as a preference: the corrected status only
    relabels the row (``met`` / ``missing`` — the in-enum gap value both candidate
    surfaces render), it never hides or re-penalises the candidate.

    Returns a NEW details dict; never mutates the stored ORM JSON (the items are
    shared references with ``app.cv_match_details``). No-op — returns ``details``
    unchanged — when there's no score yet or no such requirement (the common
    case), so the honest "couldn't find it" stands rather than a fabricated pass.
    """
    items = details.get("requirements_assessment")
    if not isinstance(items, list) or not items:
        return details
    recomputed: list[Any] = []
    changed = False
    for item in items:
        decision = (
            self_score_decision(item.get("requirement"), taali_score)
            if isinstance(item, dict)
            else None
        )
        if decision is None:
            recomputed.append(item)
            continue
        meets, op, threshold = decision
        quote = self_score_evidence_quote(taali_score)
        note = self_score_note(meets, op, threshold, taali_score)
        new_item = dict(item)
        # "met" when it clears; "missing" (the in-enum gap status both the
        # CvMatchReview rail and RoleFitEvidenceSections render as an amber
        # "Gap") when it doesn't — the note says exactly why.
        new_item["status"] = "met" if meets else "missing"
        # The score itself is the evidence. Set every field the candidate-page
        # surfaces read for the evidence line: ``evidence``/``evidence_quote``
        # (extractRequirementEvidence + the RoleFit view model), the schema's
        # ``evidence_quotes`` list, and ``impact``/``reasoning`` (the verdict
        # reason). ``source`` tags the provenance like the report path does.
        new_item["evidence"] = quote
        new_item["evidence_quote"] = quote
        new_item["evidence_quotes"] = [quote]
        new_item["impact"] = note
        new_item["reasoning"] = note
        new_item["source"] = "taali_score"
        recomputed.append(new_item)
        changed = True
    if not changed:
        return details
    new_details = dict(details)
    new_details["requirements_assessment"] = recomputed
    return new_details
