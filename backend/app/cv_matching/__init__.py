"""Production CV matching pipeline.

Public surface:

    from app.cv_matching import run_cv_match, CVMatchOutput, RequirementInput

Single scoring path. No version flags. Iterate by bumping ``PROMPT_VERSION``
and (when relevant) ``MODEL_VERSION``.
"""

from ..llm.models import FAST_MODEL

# v18: graded per-requirement scoring. The main call is unchanged; a focused
# second pass (``cv_matching.graded``) grades each requirement 0-100 and the
# aggregation uses that continuous score instead of the binary status × tier
# weighting (which double-penalised strong substitutes and discarded evidence
# the coarse model abstained on). Bumping the version invalidates the score
# cache so live scores pick up the graded layer.
PROMPT_VERSION = "cv_match_v18"
MODEL_VERSION = FAST_MODEL


def __getattr__(name: str):
    """Lazy re-exports so submodule import errors don't crash callers that
    only need a schema or PROMPT_VERSION."""
    if name in {
        "CandidateSnapshot",
        "Category",
        "ClaimToVerify",
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
        "TimelineEntry",
    }:
        from . import schemas

        return getattr(schemas, name)
    if name == "run_cv_match":
        from .runner import run_cv_match

        return run_cv_match
    if name in {
        "BatchJob",
        "BatchSubmission",
        "run_cv_match_batch",
        "submit_cv_match_batch",
        "retrieve_cv_match_batch",
    }:
        from . import runner_batch

        return getattr(runner_batch, name)
    raise AttributeError(f"module 'app.cv_matching' has no attribute {name!r}")


__all__ = [
    "BatchJob",
    "BatchSubmission",
    "CandidateSnapshot",
    "Category",
    "ClaimToVerify",
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
    "TimelineEntry",
    "retrieve_cv_match_batch",
    "run_cv_match",
    "run_cv_match_batch",
    "submit_cv_match_batch",
]
