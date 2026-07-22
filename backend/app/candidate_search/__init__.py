"""Natural-language candidate search.

Public surface:

    from app.candidate_search import run_search, ParsedFilter, SearchOutput

A query like "AWS Glue experience, worked in Europe, 5+ years" is parsed
into a typed ``ParsedFilter``, translated to SQL (skills,
country, years) and Cypher (graph predicates), executed, then optionally
reranked for soft criteria using graph-neighbourhood context.

Bumping ``PROMPT_VERSION`` invalidates parser cache entries cleanly.
"""

PROMPT_VERSION = "candidate_search_v3_required_preferences"


def __getattr__(name: str):
    if name in {
        "CandidateDeepVerification",
        "GraphPredicate",
        "ParsedFilter",
        "SearchOutput",
        "SearchWarning",
    }:
        from . import schemas

        return getattr(schemas, name)
    if name == "run_search":
        from .runner import run_search

        return run_search
    raise AttributeError(f"module 'app.candidate_search' has no attribute {name!r}")


__all__ = [
    "CandidateDeepVerification",
    "GraphPredicate",
    "ParsedFilter",
    "PROMPT_VERSION",
    "SearchOutput",
    "SearchWarning",
    "run_search",
]
