"""Production-grade CV matching pipeline (cv_match_v3.0).

Public surface:

    from app.cv_matching import run_cv_match, CVMatchOutput, RequirementInput

The pipeline is gated by ``settings.USE_CV_MATCH_V3``. When the flag is off,
existing call sites in ``cv_score_orchestrator`` continue to use the legacy
``cv_match_v4`` / ``cv_fit_v3_evidence_enriched`` flows unchanged.

See ``docs/cv_matching_audit.md`` for the legacy system and
``docs/cv_matching_cutover.md`` for the rollout procedure.
"""

PROMPT_VERSION = "cv_match_v3.0"
MODEL_VERSION = "claude-haiku-4-5-20251001"


def __getattr__(name: str):
    """Lazy re-exports so submodule import errors don't crash callers that
    only need a schema or PROMPT_VERSION."""
    if name in {
        "Category",
        "Confidence",
        "CVMatchOutput",
        "CVMatchResult",
        "Priority",
        "Recommendation",
        "RequirementAssessment",
        "RequirementInput",
        "ScoringStatus",
        "Status",
    }:
        from . import schemas

        return getattr(schemas, name)
    if name == "run_cv_match":
        from .runner import run_cv_match

        return run_cv_match
    raise AttributeError(f"module 'app.cv_matching' has no attribute {name!r}")


__all__ = [
    "Category",
    "Confidence",
    "CVMatchOutput",
    "CVMatchResult",
    "MODEL_VERSION",
    "PROMPT_VERSION",
    "Priority",
    "Recommendation",
    "RequirementAssessment",
    "RequirementInput",
    "ScoringStatus",
    "Status",
    "run_cv_match",
]
