"""Display-time cleanup for agent-written decision reasoning.

Older prompt versions (pre agent.v11) let the agent write reasoning in its
own working vocabulary — internal IDs, scorer keys, key=value dumps. Those
rows are already stored and re-generating them costs real money, so the
serializer runs this deterministic humanizer instead. New rows are written
recruiter-first by the prompt itself; these substitutions are no-ops on
clean text.
"""

from __future__ import annotations

import re

# Scorer/decision keys the agent used to cite verbatim → plain words.
_TERM_MAP = [
    (re.compile(r"\brole_fit\b"), "role fit"),
    (re.compile(r"\bpre_screen\b"), "pre-screen"),
    (re.compile(r"\bcv_match\b"), "CV match"),
    (re.compile(r"\bskip_assessment_reject\b"), "reject at pre-screen"),
    (re.compile(r"\bPolicy fires\b"), "Policy triggered"),
]

# "Aiazuddin (52407) scores…" — parenthesized internal application IDs.
# 4+ digits: scores/thresholds top out at 100 and must survive, e.g. "(100)".
_PAREN_ID = re.compile(r"\s*\(\d{4,}\)")

# "workable_stage=Technical Interview" / 'pipeline_stage="advanced"' —
# value runs to the next delimiter so multi-word stages survive.
_WORKABLE_STAGE = re.compile(r"workable_stage\s*=\s*\"?([^\",).;\n]+)\"?")
_PIPELINE_STAGE = re.compile(r"pipeline_stage\s*=\s*\"?([^\",).;\n]+)\"?")


def _stage_words(value: str) -> str:
    return value.strip().replace("_", " ")


def humanize_reasoning(text: str) -> str:
    """Rewrite machine-voice fragments in stored reasoning to plain English."""
    if not text:
        return text
    out = _PAREN_ID.sub("", text)
    out = _WORKABLE_STAGE.sub(lambda m: f'already at "{_stage_words(m.group(1))}" in Workable', out)
    out = _PIPELINE_STAGE.sub(lambda m: f'pipeline stage "{_stage_words(m.group(1))}"', out)
    for pattern, replacement in _TERM_MAP:
        out = pattern.sub(replacement, out)
    return out
