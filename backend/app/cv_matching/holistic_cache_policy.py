"""Deterministic cache-key and Workable-context policy for holistic scoring.

Keeping these pure helpers separate lets the scoring engine focus on provider
orchestration while preserving one exact, testable contract for which inputs
can affect a cached result.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..services.workable_context_contract import (
    PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS,
    PROTECTED_WORKABLE_SECTION_TAGS,
    StructuredWorkableContext,
    WorkableEvidenceSection,
    neutralize_workable_delimiters,
    render_workable_section,
)


_WORKABLE_SECTION_BUDGETS = (
    ("WORKABLE_QUESTIONNAIRE_ANSWERS", 900),
    ("WORKABLE_RECRUITER_COMMENTS", 450),
    ("WORKABLE_PROFILE", 250),
    ("WORKABLE_TAGS", 150),
    ("WORKABLE_EXPERIENCE", 100),
    ("WORKABLE_EDUCATION", 80),
    ("WORKABLE_ACTIVITY_LOG", 80),
    ("WORKABLE_SUMMARY", 50),
)


class ProtectedWorkableEvidenceOverflow(ValueError):
    """Protected evidence cannot be scored safely within the hard ceiling."""


def derive_requirements_cache_key(
    job_spec_text: str,
    *,
    cache_prefix: str,
    jd_chars: int,
    engine_version: str,
    model: str,
    prompt_version: str,
    system_prompt: str,
    user_prompt_template: str,
) -> str:
    """Key a requirements derivation on every provider-visible input."""

    payload = {
        "engine_version": engine_version,
        "job_spec": (job_spec_text or "").strip()[:jd_chars],
        "model": model,
        "prompt_version": prompt_version,
        "system_prompt": system_prompt,
        "user_prompt_template": user_prompt_template,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return cache_prefix + hashlib.sha256(encoded).hexdigest()


def holistic_cache_policy_fingerprint(
    settings_obj: Any,
    *,
    cv_chars: int,
    jd_chars: int,
    workable_context_chars: int,
) -> str:
    """Fingerprint deterministic settings that can change a cached output."""

    payload = {
        "cv_document_hygiene_enabled": bool(
            settings_obj.CV_DOCUMENT_HYGIENE_ENABLED
        ),
        "cv_hidden_text_strip_enabled": bool(
            settings_obj.CV_HIDDEN_TEXT_STRIP_ENABLED
        ),
        "fraud_hidden_text_action": str(settings_obj.FRAUD_HIDDEN_TEXT_ACTION),
        "fraud_penalty_cap_score": float(settings_obj.FRAUD_PENALTY_CAP_SCORE),
        "grounding_coverage_high_match": float(
            settings_obj.GROUNDING_COVERAGE_HIGH_MATCH
        ),
        "grounding_coverage_low": float(settings_obj.GROUNDING_COVERAGE_LOW),
        "grounding_coverage_min_musthaves": int(
            settings_obj.GROUNDING_COVERAGE_MIN_MUSTHAVES
        ),
        "grounding_coverage_discount_enabled": bool(
            settings_obj.GROUNDING_COVERAGE_DISCOUNT_ENABLED
        ),
        "grounding_coverage_max_discount": float(
            settings_obj.GROUNDING_COVERAGE_MAX_DISCOUNT
        ),
        "holistic_integrity_penalty_enabled": bool(
            settings_obj.HOLISTIC_INTEGRITY_PENALTY_ENABLED
        ),
        "fraud_integrity_penalty_points": float(
            settings_obj.FRAUD_INTEGRITY_PENALTY_POINTS
        ),
        "fraud_integrity_penalty_max": float(
            settings_obj.FRAUD_INTEGRITY_PENALTY_MAX
        ),
        "cv_chars": cv_chars,
        "jd_chars": jd_chars,
        "workable_context_chars": workable_context_chars,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _truncate_tagged_section(
    section: WorkableEvidenceSection,
    *,
    max_chars: int,
) -> str:
    rendered = render_workable_section(section)
    if len(rendered) <= max_chars:
        return rendered

    tag = section.tag
    opening = f"<{tag}>\n"
    closing = f"\n</{tag}>"
    marker = "…"
    body = neutralize_workable_delimiters(section.body)
    body_budget = max(0, max_chars - len(opening) - len(closing) - len(marker))
    return f"{opening}{body[:body_budget].rstrip()}{marker}{closing}"


def _minimum_section_chars(section: WorkableEvidenceSection) -> int:
    marker_form = len(f"<{section.tag}>\n…\n</{section.tag}>")
    return min(len(render_workable_section(section)), marker_form)


def _ordered_sections(
    context: StructuredWorkableContext,
) -> list[WorkableEvidenceSection]:
    """Order formatter-trusted sections by evidence value, stably."""

    priority = {tag: index for index, (tag, _) in enumerate(_WORKABLE_SECTION_BUDGETS)}
    return [
        section
        for _, section in sorted(
            enumerate(context.evidence_sections),
            key=lambda item: (priority[item[1].tag], item[0]),
        )
    ]


def _compact_structured_workable_context(
    context: StructuredWorkableContext,
    *,
    target_chars: int,
) -> str:
    """Compact only low-priority sections; never truncate protected evidence."""

    sections = _ordered_sections(context)
    if not sections:
        return ""

    rendered = [render_workable_section(section) for section in sections]
    protected_indexes = [
        index
        for index, section in enumerate(sections)
        if section.tag in PROTECTED_WORKABLE_SECTION_TAGS
    ]
    protected_visible = "\n\n".join(rendered[index] for index in protected_indexes)
    if len(protected_visible) > PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS:
        raise ProtectedWorkableEvidenceOverflow(
            "protected Workable evidence exceeds the 32,000-character safety ceiling"
        )

    # The 2,500-char ordinary target is a cost optimization, not a safety rail.
    # Expand it enough to retain every protected byte.  Lower-priority profile,
    # summary, skills, education, and experience may use only the remaining room.
    output_target = max(
        min(target_chars, PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS),
        len(protected_visible),
    )
    included = set(protected_indexes)
    minimum_total = len(protected_visible)

    for index, section in enumerate(sections):
        if index in included:
            continue
        minimum = _minimum_section_chars(section)
        separator = 2 if included else 0
        if minimum_total + separator + minimum <= output_target:
            included.add(index)
            minimum_total += separator + minimum

    included_indexes = [index for index in range(len(sections)) if index in included]
    if not included_indexes:
        return ""

    separator_chars = 2 * max(0, len(included_indexes) - 1)
    content_budget = output_target - separator_chars
    reservations = dict(_WORKABLE_SECTION_BUDGETS)
    allocations: dict[int, int] = {}
    for index in included_indexes:
        section = sections[index]
        if index in protected_indexes:
            allocations[index] = len(rendered[index])
        else:
            allocations[index] = max(
                _minimum_section_chars(section),
                min(len(rendered[index]), reservations[section.tag]),
            )

    # Reservations are preferences.  If protected evidence consumed most of
    # the target, shrink the lowest-priority unprotected sections first, while
    # retaining valid balanced tags.
    overflow = max(0, sum(allocations.values()) - content_budget)
    for index in reversed(included_indexes):
        if overflow == 0:
            break
        if index in protected_indexes:
            continue
        minimum = _minimum_section_chars(sections[index])
        reduction = min(overflow, allocations[index] - minimum)
        allocations[index] -= reduction
        overflow -= reduction

    remaining = max(0, content_budget - sum(allocations.values()))
    for index in included_indexes:
        if remaining == 0:
            break
        if index in protected_indexes:
            continue
        growth = min(len(rendered[index]) - allocations[index], remaining)
        allocations[index] += growth
        remaining -= growth

    return "\n\n".join(
        rendered[index]
        if index in protected_indexes
        else _truncate_tagged_section(
            sections[index],
            max_chars=allocations[index],
        )
        for index in included_indexes
    )


def compact_workable_context(
    workable_context: str | None,
    *,
    max_chars: int,
) -> str:
    """Bound Workable evidence without trusting tags found inside its text.

    Formatter-created structured contexts retain questionnaire answers,
    recruiter comments, and activity entries in full, even when that expands
    beyond the ordinary target.  An oversized protected corpus fails closed.
    Plain legacy strings are opaque untrusted text: delimiters are neutralized,
    never parsed into higher-trust sections.
    """

    bounded_max = max(0, int(max_chars))
    if isinstance(workable_context, StructuredWorkableContext):
        return _compact_structured_workable_context(
            workable_context,
            target_chars=bounded_max,
        )

    text = (workable_context or "").strip()
    if not text:
        return ""
    if len(text) > PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS:
        raise ProtectedWorkableEvidenceOverflow(
            "unstructured Workable evidence exceeds the 32,000-character safety ceiling"
        )
    if bounded_max == 0:
        return ""
    return neutralize_workable_delimiters(text)[:bounded_max]


__all__ = [
    "ProtectedWorkableEvidenceOverflow",
    "compact_workable_context",
    "derive_requirements_cache_key",
    "holistic_cache_policy_fingerprint",
]
