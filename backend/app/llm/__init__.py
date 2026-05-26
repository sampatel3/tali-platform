"""Shared LLM-call gateway.

A thin, dependency-free spine the feature pipelines plug into instead of
each reimplementing call -> parse -> validate -> retry -> cache -> token
accounting. See ``core`` (the metered ``one_call`` primitive) and
``structured`` (the single-response generation lifecycle).
"""

from __future__ import annotations

from .core import CallUsage, MeteringContext, one_call
from .grounding import FUZZY_THRESHOLD, FUZZY_WINDOW_PAD, fuzzy_locate
from .models import FAST_MODEL
from .structured import (
    StructuredResult,
    ValidationFailure,
    default_retry_message_builder,
    generate_structured,
    parse_structured,
    strip_json_fences,
)

__all__ = [
    "CallUsage",
    "FAST_MODEL",
    "FUZZY_THRESHOLD",
    "FUZZY_WINDOW_PAD",
    "fuzzy_locate",
    "MeteringContext",
    "one_call",
    "StructuredResult",
    "ValidationFailure",
    "default_retry_message_builder",
    "generate_structured",
    "parse_structured",
    "strip_json_fences",
]
