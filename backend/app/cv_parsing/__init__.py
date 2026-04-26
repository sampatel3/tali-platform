"""CV parsing — Haiku 4.5 + strict JSON schema, content-hash cached.

Public surface:

    from app.cv_parsing import parse_cv, ParsedCV

Called from ``_try_fetch_cv_from_workable`` (and the candidate-CV upload
path) right after text extraction. The parsed result lands in
``application.cv_sections`` (and ``candidate.cv_sections``) so the
candidate page can render structured experience/education/skills blocks.

Per memory: all Claude calls use ``settings.ANTHROPIC_API_KEY``.
"""

from .schemas import (
    EducationEntry,
    ExperienceEntry,
    ParsedCV,
)

PROMPT_VERSION = "cv_parse_v1.0"
MODEL_VERSION = "claude-haiku-4-5-20251001"


def __getattr__(name: str):
    """Lazy re-export so importing the package doesn't pull in anthropic
    or DB models for a simple schema check."""
    if name == "parse_cv":
        from .runner import parse_cv

        return parse_cv
    if name in {"EducationEntry", "ExperienceEntry", "ParsedCV"}:
        from . import schemas

        return getattr(schemas, name)
    raise AttributeError(f"module 'app.cv_parsing' has no attribute {name!r}")


__all__ = [
    "EducationEntry",
    "ExperienceEntry",
    "MODEL_VERSION",
    "PROMPT_VERSION",
    "ParsedCV",
    "parse_cv",
]
