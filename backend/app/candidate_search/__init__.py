"""Natural-language candidate search.

Public surface:

    from app.candidate_search import run_search, ParsedFilter, SearchOutput

A query like "AWS Glue experience, worked in Europe, 5+ years" is parsed
by Haiku into a typed ``ParsedFilter``, translated to SQL (skills,
country, years) and Cypher (graph predicates), executed, then optionally
reranked by Claude for soft criteria using graph-neighbourhood context.

Bumping ``PROMPT_VERSION`` invalidates parser cache entries cleanly.
"""

from ..llm.models import FAST_MODEL

PROMPT_VERSION = "candidate_search_v2"
MODEL_VERSION = FAST_MODEL


def __getattr__(name: str):
    if name in {
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
    "GraphPredicate",
    "MODEL_VERSION",
    "ParsedFilter",
    "PROMPT_VERSION",
    "SearchOutput",
    "SearchWarning",
    "run_search",
]
