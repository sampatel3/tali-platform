"""Validate the ``evidence`` payload an agent attaches to a queued decision.

The agent emits ``evidence`` as free-form JSON on ``queue_advance_decision``
/ ``queue_reject_decision`` / ``queue_skip_assessment_reject_decision``.
Sub-agent outputs (CV scoring, pre-screen) are already grounded against
the CV text; the *agent's* claims about those scores are not. Without
validation, the agent can write any number into ``cv_match_score`` or
quote a phrase that isn't in the CV, and a recruiter clicking "approve"
would have no signal that the cited evidence is fabricated.

This validator runs after ``queue_decision.run`` creates the row.
For each cited field:

- **Numeric scores** (``cv_match_score``, ``pre_screen_score``,
  ``taali_score``, ``assessment_score``): looked up against the actual
  ``CandidateApplication`` / ``Assessment`` row and required to match
  within a small float tolerance.
- **CV excerpts** (``cv_excerpts``): each ``quoted_text`` is fuzzy-
  matched against the candidate's ``cv_text`` using the same matcher
  the CV-scoring sub-agent uses for its own evidence quotes.

The validator does not raise on failure — it returns a structured
outcome that the caller persists to ``AgentDecision.validation_status``
plus ``validation_failures``. Permissive-by-default lets the agent
continue while making the failure visible to recruiters and in audit
queries. A future flip can change "fail-and-warn" to "fail-and-refuse"
once the agent's prompt is tuned to emit clean evidence consistently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..cv_matching.validation import fuzzy_locate_quote
from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication


logger = logging.getLogger("taali.agent_runtime.decision_evidence")


# Tolerance for matching agent-reported scores to the real row.
# CV match / TAALI scores are stored as floats in the 0-100 range with
# ~2 decimal places; the agent may round them. 0.5 is generous enough to
# absorb rounding without letting an outright fabricated number pass.
_SCORE_TOLERANCE = 0.5


VALIDATION_STATUS_PASSED = "passed"
VALIDATION_STATUS_FAILED = "failed"
VALIDATION_STATUS_SKIPPED = "skipped"
VALIDATION_STATUSES = (
    VALIDATION_STATUS_PASSED,
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_SKIPPED,
)


# Mapping from agent-evidence keys → (CandidateApplication attribute, label).
# Each entry is a numeric score the agent might cite that we can verify
# against a real row. Extend cautiously: the gate is permissive on
# unknown keys.
_APP_SCORE_FIELDS: dict[str, tuple[str, str]] = {
    "cv_match_score": ("cv_match_score", "CandidateApplication.cv_match_score"),
    "pre_screen_score": ("pre_screen_score_100", "CandidateApplication.pre_screen_score_100"),
    "taali_score": ("taali_score_cache_100", "CandidateApplication.taali_score_cache_100"),
    "rank_score": ("rank_score", "CandidateApplication.rank_score"),
}


@dataclass
class EvidenceValidationOutcome:
    status: str
    failures: list[str] = field(default_factory=list)
    checks_run: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failures": list(self.failures),
            "checks_run": self.checks_run,
        }


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_score(
    *,
    key: str,
    cited: Any,
    actual: Any,
    failures: list[str],
) -> int:
    """Compare agent-cited score to actual row value. Returns 1 if a
    check was performed, else 0."""
    cited_float = _coerce_float(cited)
    if cited_float is None:
        return 0
    actual_float = _coerce_float(actual)
    if actual_float is None:
        failures.append(
            f"{key}: agent cited {cited_float} but the application has no value recorded"
        )
        return 1
    if abs(cited_float - actual_float) > _SCORE_TOLERANCE:
        failures.append(
            f"{key}: agent cited {cited_float} but the application has {actual_float} "
            f"(tolerance {_SCORE_TOLERANCE})"
        )
    return 1


def _check_cv_excerpt(
    *,
    excerpt: Any,
    cv_text: str,
    failures: list[str],
) -> int:
    """Verify an excerpt entry. Supports two shapes for backward compat:
    - dict ``{"quoted_text": "...", ...}``
    - bare string
    Returns 1 if a check was performed, else 0.
    """
    if isinstance(excerpt, dict):
        quoted = excerpt.get("quoted_text") or excerpt.get("quote") or excerpt.get("text")
    elif isinstance(excerpt, str):
        quoted = excerpt
    else:
        return 0
    quoted = (quoted or "").strip()
    if not quoted:
        return 0
    if not cv_text:
        failures.append(
            f"cv_excerpt: agent cited a quote but the candidate has no CV text on file: "
            f"{quoted[:120]!r}"
        )
        return 1
    if fuzzy_locate_quote(quoted, cv_text) is None:
        failures.append(
            f"cv_excerpt not found in CV (fuzzy match below threshold): {quoted[:120]!r}"
        )
    return 1


def validate_agent_decision_evidence(
    decision: AgentDecision, db: Session
) -> EvidenceValidationOutcome:
    """Run all available checks against the decision's evidence dict.

    Returns ``passed``/``failed``/``skipped``:
    - ``skipped`` when the evidence dict is empty or contains no
      verifiable fields (no scores, no excerpts).
    - ``failed`` when at least one check ran and at least one failed.
    - ``passed`` when at least one check ran and all passed.
    """
    evidence = decision.evidence or {}
    if not isinstance(evidence, dict) or not evidence:
        return EvidenceValidationOutcome(status=VALIDATION_STATUS_SKIPPED)

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == decision.application_id,
            CandidateApplication.organization_id == decision.organization_id,
        )
        .first()
    )

    failures: list[str] = []
    checks_run = 0

    # 1. Scores cited at the top level of evidence, e.g. {"cv_match_score": 78}.
    if app is not None:
        for key, (attr, _label) in _APP_SCORE_FIELDS.items():
            if key in evidence:
                checks_run += _check_score(
                    key=key,
                    cited=evidence.get(key),
                    actual=getattr(app, attr, None),
                    failures=failures,
                )
        # Also support {"cited_scores": {"cv_match_score": 78}}.
        cited_scores = evidence.get("cited_scores")
        if isinstance(cited_scores, dict):
            for key, value in cited_scores.items():
                if key in _APP_SCORE_FIELDS:
                    attr, _label = _APP_SCORE_FIELDS[key]
                    checks_run += _check_score(
                        key=f"cited_scores.{key}",
                        cited=value,
                        actual=getattr(app, attr, None),
                        failures=failures,
                    )

    # 2. assessment_score, if cited, must match the linked Assessment.
    assessment_score = evidence.get("assessment_score")
    assessment_id = evidence.get("assessment_id")
    if assessment_score is not None or assessment_id is not None:
        target_id = assessment_id
        if target_id is None and app is not None:
            latest = (
                db.query(Assessment)
                .filter(
                    Assessment.application_id == decision.application_id,
                    Assessment.organization_id == decision.organization_id,
                )
                .order_by(Assessment.created_at.desc())
                .first()
            )
            target_id = int(latest.id) if latest is not None else None
        if target_id is not None:
            assessment = (
                db.query(Assessment)
                .filter(
                    Assessment.id == target_id,
                    Assessment.organization_id == decision.organization_id,
                )
                .first()
            )
            if assessment is None:
                failures.append(
                    f"assessment_id={target_id} not found for this org"
                )
                checks_run += 1
            elif assessment_score is not None:
                checks_run += _check_score(
                    key="assessment_score",
                    cited=assessment_score,
                    actual=getattr(assessment, "score", None),
                    failures=failures,
                )

    # 3. CV excerpts: fuzzy-match each quoted_text against the candidate's CV.
    excerpts = evidence.get("cv_excerpts") or evidence.get("cv_excerpt")
    cv_excerpt_string = evidence.get("cv_excerpt") if isinstance(evidence.get("cv_excerpt"), str) else None
    if app is not None and (excerpts or cv_excerpt_string):
        cv_text = ""
        candidate = (
            db.query(Candidate)
            .filter(Candidate.id == app.candidate_id)
            .first()
        )
        if candidate is not None:
            cv_text = candidate.cv_text or app.cv_text or ""
        else:
            cv_text = app.cv_text or ""
        items: list[Any] = []
        if isinstance(excerpts, list):
            items.extend(excerpts)
        elif isinstance(excerpts, (str, dict)):
            items.append(excerpts)
        if cv_excerpt_string and cv_excerpt_string not in items:
            items.append(cv_excerpt_string)
        for excerpt in items:
            checks_run += _check_cv_excerpt(
                excerpt=excerpt,
                cv_text=cv_text,
                failures=failures,
            )

    if checks_run == 0:
        return EvidenceValidationOutcome(status=VALIDATION_STATUS_SKIPPED)
    status = VALIDATION_STATUS_FAILED if failures else VALIDATION_STATUS_PASSED
    return EvidenceValidationOutcome(
        status=status, failures=failures, checks_run=checks_run
    )
