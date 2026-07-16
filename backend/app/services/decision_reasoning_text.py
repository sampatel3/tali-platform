"""Deterministic display cleanup for legacy agent decision reasoning."""

from __future__ import annotations

import re

# Scorer/decision keys older agents cited verbatim -> recruiter-facing words.
_TERM_MAP = [
    (re.compile(r"\brole_fit\b"), "role fit"),
    (re.compile(r"\bpre_screen\b"), "pre-screen"),
    (re.compile(r"\bcv_match\b"), "CV match"),
    (re.compile(r"\bskip_assessment_reject\b"), "reject at pre-screen"),
    (re.compile(r"\bPolicy fires\b"), "Policy triggered"),
]

# Parenthesized application IDs. Preserve plausible four-digit years: a
# certification or project date such as ``(2024)`` is candidate evidence, not
# an implementation detail. Non-year four-digit values and every longer value
# retain the legacy internal-ID cleanup.
_PAREN_ID = re.compile(r"\s*\((?!(?:19|20)\d{2}\))\d{4,}\)")

# Preserve quoted and multi-word stage values through the next delimiter.
_WORKABLE_STAGE = re.compile(r"workable_stage\s*=\s*\"?([^\",).;\n]+)\"?")
_PIPELINE_STAGE = re.compile(r"pipeline_stage\s*=\s*\"?([^\",).;\n]+)\"?")


def _stage_words(value: str) -> str:
    return value.strip().replace("_", " ")


def humanize_reasoning(text: str) -> str:
    """Rewrite machine-voice fragments without regenerating or truncating text."""
    if not text:
        return text
    out = _PAREN_ID.sub("", text)
    out = _WORKABLE_STAGE.sub(
        lambda match: f'already at "{_stage_words(match.group(1))}" in Workable',
        out,
    )
    out = _PIPELINE_STAGE.sub(
        lambda match: f'pipeline stage "{_stage_words(match.group(1))}"',
        out,
    )
    for pattern, replacement in _TERM_MAP:
        out = pattern.sub(replacement, out)
    return out


__all__ = ["humanize_reasoning"]
