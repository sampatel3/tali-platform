"""Cross-candidate duplicate / mass-apply fraud signals (deterministic, flag-only).

Two independent tells that only exist when you look ACROSS candidates in the
same organisation — invisible to the single-CV detectors in ``fraud_detection``:

  (a) ``duplicate_identity`` — the same person (matched by ``phone_normalized``
      or email) already exists on a DIFFERENT candidate row in the org. Same
      human, multiple identities: a mass-apply / sockpuppet tell.

  (b) ``cv_mill`` — this CV's text is a near-duplicate of another candidate's CV
      in the SAME ROLE, via the existing 4-shingle Jaccard machinery. Catches a
      CV-mill / template farm spraying lightly-reworded CVs at one req.

Bounded by construction — NO O(n²) over the whole DB, NO LLM, NO Graphiti:
  * identity match is a single indexed equality query (``phone_normalized`` /
    ``email`` are both indexed on ``candidates``);
  * near-dup compares only against the most-recent ``_MAX_CV_COMPARISONS`` OTHER
    applications on the same role (per the spec's acceptable inline fallback),
    and short-circuits on the first hit.

Flag-only end to end: the result is persisted under
``pre_screen_evidence.fraud_signals`` and never changes a score.
"""

from __future__ import annotations

import logging
from typing import Any

from .fraud_detection import detect_jd_shingle_similarity

logger = logging.getLogger(__name__)

# Cap on same-role CVs we shingle-compare against, newest first. Keeps the
# per-application pre-screen check O(k) with a small constant, not O(n).
_MAX_CV_COMPARISONS = 200
# Minimum CV length (chars) on both sides before a near-dup comparison is
# meaningful — a two-line stub shingles to noise.
_MIN_CV_CHARS = 400
# CV-mill trigger: fraction of THIS CV's shingles also present in the other CV.
_CV_MILL_THRESHOLD = 0.7


def detect_cross_candidate_signals(db: Any, app: Any) -> dict[str, Any]:
    """Compute the cross-candidate duplicate / mass-apply signals for ``app``.

    Returns a dict with any of ``duplicate_identity`` / ``cv_mill`` that fired
    (empty dict when nothing does). Best-effort: any DB/parse failure returns
    ``{}`` rather than blocking pre-screen. Never changes a score.
    """
    if db is None or app is None:
        return {}
    try:
        from ..models.candidate import Candidate
        from ..models.candidate_application import CandidateApplication
    except Exception:  # pragma: no cover — defensive
        return {}

    org_id = getattr(app, "organization_id", None)
    if not org_id:
        return {}
    signals: dict[str, Any] = {}

    cand = getattr(app, "candidate", None)
    cand_id = getattr(cand, "id", None)
    phone = (getattr(cand, "phone_normalized", None) or "").strip() if cand else ""
    email = (getattr(cand, "email", None) or "").strip().lower() if cand else ""

    # (a) Identity dup — same phone / email on ANOTHER candidate row in the org.
    try:
        clauses = []
        if phone:
            clauses.append(Candidate.phone_normalized == phone)
        if email:
            clauses.append(func_lower_email() == email)
        if clauses:
            from sqlalchemy import or_

            dup_q = (
                db.query(Candidate.id)
                .filter(
                    Candidate.organization_id == org_id,
                    Candidate.deleted_at.is_(None),
                    or_(*clauses),
                )
            )
            if cand_id is not None:
                dup_q = dup_q.filter(Candidate.id != cand_id)
            dup_ids = [row[0] for row in dup_q.limit(20).all()]
            if dup_ids:
                signals["duplicate_identity"] = {
                    "triggered": True,
                    "matched_on": "phone" if phone else "email",
                    "duplicate_candidate_count": len(dup_ids),
                    "duplicate_candidate_ids": dup_ids[:10],
                }
    except Exception:  # pragma: no cover — defensive
        logger.debug("duplicate_identity check failed", exc_info=True)

    # (b) CV-mill — near-duplicate CV text vs other candidates on the SAME ROLE.
    cv_text = (getattr(app, "cv_text", None) or "").strip()
    role_id = getattr(app, "role_id", None)
    if len(cv_text) >= _MIN_CV_CHARS and role_id is not None:
        try:
            others = (
                db.query(CandidateApplication.id, CandidateApplication.cv_text)
                .filter(
                    CandidateApplication.organization_id == org_id,
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.id != getattr(app, "id", None),
                    CandidateApplication.cv_text.isnot(None),
                )
                .order_by(CandidateApplication.id.desc())
                .limit(_MAX_CV_COMPARISONS)
                .all()
            )
            for other_id, other_cv in others:
                other_cv = (other_cv or "").strip()
                if len(other_cv) < _MIN_CV_CHARS:
                    continue
                res = detect_jd_shingle_similarity(
                    cv_text, other_cv, threshold=_CV_MILL_THRESHOLD
                )
                if res.triggered:
                    signals["cv_mill"] = {
                        "triggered": True,
                        "similarity": res.similarity,
                        "matched_application_id": int(other_id),
                        "compared_against": len(others),
                    }
                    break
        except Exception:  # pragma: no cover — defensive
            logger.debug("cv_mill check failed", exc_info=True)

    return signals


def func_lower_email():
    """``lower(candidates.email)`` for a case-insensitive equality match, kept in
    one place so the compared value (already lowercased) stays consistent."""
    from sqlalchemy import func

    from ..models.candidate import Candidate

    return func.lower(Candidate.email)
