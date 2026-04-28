"""Production CV matching pipeline.

Public surface:

    from app.cv_matching import run_cv_match, CVMatchOutput, RequirementInput

Single scoring path. No version flags. Iterate by bumping ``PROMPT_VERSION``
and (when relevant) ``MODEL_VERSION``.
"""

PROMPT_VERSION = "cv_match_v11"
MODEL_VERSION = "claude-haiku-4-5-20251001"


def __getattr__(name: str):
    """Lazy re-exports so submodule import errors don't crash callers that
    only need a schema or PROMPT_VERSION."""
    if name in {
        "Category",
        "Confidence",
        "CVMatchOutput",
        "CVMatchResult",
        "DimensionScores",
        "MatchTier",
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
    "DimensionScores",
    "MODEL_VERSION",
    "MatchTier",
    "PROMPT_VERSION",
    "Priority",
    "Recommendation",
    "RequirementAssessment",
    "RequirementInput",
    "ScoringStatus",
    "Status",
    "run_cv_match",
]
