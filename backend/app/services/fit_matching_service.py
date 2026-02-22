"""CV-to-Job-Spec fit matching via a single Claude API call.

This is the ONLY Claude scoring call per assessment — all other scoring
is pure heuristic. See PRODUCT_PLAN.md guardrail: "Single Claude call per
assessment".
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from ..components.integrations.claude.model_fallback import (
    candidate_models_for,
    is_model_not_found_error,
)
from ..platform.config import settings

logger = logging.getLogger("taali.fit_matching")

_TOKENS_PER_MILLION = 1_000_000.0

_MAX_REQUIREMENTS = 16
_MAX_RATIONALE_BULLETS = 6
_REQUIREMENT_EVIDENCE_SNIPPETS = 2

_EVIDENCE_GENERIC_PATTERNS = (
    "matched recruiter requirement",
    "matched recruiter requirements",
    "meets recruiter requirement",
    "requirement met",
    "good fit",
    "strong fit",
    "aligned with requirement",
    "appears to meet",
)

_REQUIREMENT_STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "all",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "via",
    "with",
}

CV_MATCH_PROMPT = """Analyze candidate CV fit for this role.

CV:
{cv_text}

Job Specification:
{job_spec_text}
{additional_requirements_section}

Provide a JSON response with EXACTLY this structure (no markdown, no explanation, ONLY valid JSON):
{{
    "overall_match_score": <0-100>,
    "skills_match_score": <0-100>,
    "experience_relevance_score": <0-100>,
    "requirements_match_score": <0-100>,
    "recommendation": "strong_yes|yes|lean_no|no",
    "requirements_assessment": [
        {{
            "requirement": "string",
            "priority": "must_have|strong_preference|nice_to_have|constraint",
            "status": "met|partially_met|missing|unknown",
            "evidence": "short evidence from CV or lack of evidence",
            "impact": "why this matters for recruiter decision"
        }}
    ],
    "matching_skills": ["skill1", "skill2"],
    "missing_skills": ["skill1", "skill2"],
    "experience_highlights": ["relevant experience 1", "relevant experience 2"],
    "concerns": ["concern 1", "concern 2"],
    "summary": "2-3 sentence summary of fit"
}}

Scoring policy:
1) The score must be tailored to the actual role requirements and recruiter-added criteria.
2) If a must-have requirement is missing or unsupported by evidence, reduce scores materially.
3) Be evidence-based; do not infer experience not present in the CV text.
4) Use the full 0-100 scale with granular precision (do not round to 10-point bands): 50 = neutral baseline, 70+ = good match, 85+ = strong match.
5) Include a requirements_assessment entry for each recruiter-added criterion when provided.
"""


def _extract_recruiter_requirements(additional_requirements: Optional[str]) -> list[str]:
    text = str(additional_requirements or "").strip()
    if not text:
        return []

    parts = re.split(r"[\n;]+", text)
    if len(parts) <= 1:
        sentence_parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
        if len(sentence_parts) > 1:
            parts = sentence_parts

    items: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[\).\-\s])\s*", "", str(raw or "")).strip()
        if not cleaned:
            continue
        compact = re.sub(r"\s+", " ", cleaned)
        lowered = compact.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(compact[:220])
        if len(items) >= _MAX_REQUIREMENTS:
            break

    if not items and text:
        return [re.sub(r"\s+", " ", text)[:220]]
    return items


def _format_additional_requirements_section(additional_requirements: Optional[str]) -> str:
    items = _extract_recruiter_requirements(additional_requirements)
    if not items:
        return ""

    item_lines = "\n".join(f"- {item}" for item in items)
    return f"""

Recruiter-added scoring criteria (treat these as explicit decision requirements):
{item_lines}
"""


def _safe_string(value: Any, *, max_chars: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)[:max_chars]


def _safe_string_list(value: Any, *, max_items: int = 20, max_chars: int = 160) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value[:max_items]:
        text = _safe_string(raw, max_chars=max_chars)
        if text:
            out.append(text)
    return out


def _score_to_100(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    if numeric <= 10:
        numeric = numeric * 10.0
    return round(max(0.0, min(100.0, numeric)), 1)


def _clamp_score_100(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


def _weighted_average(scores: list[tuple[float, float]]) -> float | None:
    if not scores:
        return None
    denominator = sum(weight for _, weight in scores if weight > 0)
    if denominator <= 0:
        return None
    numerator = sum(score * weight for score, weight in scores if weight > 0)
    return _clamp_score_100(numerator / denominator)


def _priority_value(priority: str) -> float:
    if priority == "must_have":
        return 2.2
    if priority == "constraint":
        return 2.4
    if priority == "strong_preference":
        return 1.5
    return 1.0


def _status_value(status: str) -> float:
    if status == "met":
        return 1.0
    if status == "partially_met":
        return 0.55
    if status == "unknown":
        return 0.35
    return 0.0


def _normalize_requirement_priority(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"must", "must-have", "must_have", "required", "hard"}:
        return "must_have"
    if raw in {"constraint", "hard_constraint", "blocking"}:
        return "constraint"
    if raw in {"strong_preference", "strong-preference", "preferred", "preference", "important"}:
        return "strong_preference"
    return "nice_to_have"


def _normalize_requirement_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"met", "yes", "satisfied", "present"}:
        return "met"
    if raw in {"partial", "partially_met", "partially-met", "partially"}:
        return "partially_met"
    if raw in {"missing", "not_met", "not-met", "no", "failed"}:
        return "missing"
    return "unknown"


def _normalize_requirements_assessment(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, str]] = []
    for raw in value[:_MAX_REQUIREMENTS]:
        if not isinstance(raw, dict):
            continue
        requirement = _safe_string(
            raw.get("requirement") or raw.get("criterion") or raw.get("name"),
            max_chars=220,
        )
        if not requirement:
            continue
        priority = _normalize_requirement_priority(raw.get("priority"))
        status = _normalize_requirement_status(raw.get("status"))
        evidence = _safe_string(raw.get("evidence"), max_chars=260)
        impact = _safe_string(raw.get("impact"), max_chars=220)
        normalized.append(
            {
                "requirement": requirement,
                "priority": priority,
                "status": status,
                "evidence": evidence,
                "impact": impact,
            }
        )
    return normalized


def _keyword_tokens(text: str, *, max_terms: int = 16) -> list[str]:
    if not text:
        return []
    raw_tokens = re.findall(r"[a-z0-9][a-z0-9+#/.\-]{1,}", text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        if len(token) < 3 or token in _REQUIREMENT_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= max_terms:
            break
    return out


def _is_generic_requirement_evidence(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return True
    if len(normalized) < 18:
        return True
    return any(pattern in normalized for pattern in _EVIDENCE_GENERIC_PATTERNS)


def _best_cv_snippets_for_requirement(requirement: str, cv_text: str, *, max_items: int = _REQUIREMENT_EVIDENCE_SNIPPETS) -> list[str]:
    if not requirement or not cv_text:
        return []
    requirement_tokens = set(_keyword_tokens(requirement, max_terms=12))
    if not requirement_tokens:
        return []

    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    chunks = re.split(r"[\r\n]+|(?<=[.!?])\s+", cv_text)
    for chunk in chunks:
        snippet = _safe_string(chunk, max_chars=140)
        if len(snippet) < 20:
            continue
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)

        snippet_tokens = set(_keyword_tokens(snippet, max_terms=24))
        overlap = requirement_tokens.intersection(snippet_tokens)
        if not overlap:
            continue
        overlap_score = len(overlap)
        length_penalty = max(0, len(snippet) - 90)
        candidates.append((overlap_score, -length_penalty, snippet))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [snippet for _, _, snippet in candidates[:max_items]]


def _select_related_evidence(requirement: str, values: list[str], *, max_items: int, max_chars: int) -> list[str]:
    requirement_tokens = set(_keyword_tokens(requirement, max_terms=12))
    if not values:
        return []

    related: list[str] = []
    fallback: list[str] = []
    for raw in values:
        text = _safe_string(raw, max_chars=max_chars)
        if not text:
            continue
        fallback.append(text)
        if not requirement_tokens:
            continue
        text_tokens = set(_keyword_tokens(text, max_terms=20))
        if requirement_tokens.intersection(text_tokens):
            related.append(text)

    chosen = related or fallback
    return chosen[:max_items]


def _default_requirement_impact(status: str) -> str:
    if status == "met":
        return "Supports confidence that this recruiter requirement is satisfied."
    if status == "partially_met":
        return "Some alignment is present; confirm the exact scope in interview."
    if status == "missing":
        return "Potential blocker if this requirement is non-negotiable."
    return "Evidence is inconclusive; interview validation is recommended."


def _enrich_requirements_assessment(
    *,
    requirements_assessment: list[dict[str, str]],
    recruiter_requirements: list[str],
    cv_text: str,
    matching_skills: list[str],
    experience_highlights: list[str],
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = list(requirements_assessment)
    known = {
        _safe_string(item.get("requirement"), max_chars=220).lower()
        for item in merged
        if _safe_string(item.get("requirement"), max_chars=220)
    }
    for requirement in recruiter_requirements:
        key = requirement.lower()
        if key in known:
            continue
        known.add(key)
        merged.append(
            {
                "requirement": requirement,
                "priority": "must_have",
                "status": "unknown",
                "evidence": "",
                "impact": "",
            }
        )

    enriched: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in merged[:_MAX_REQUIREMENTS]:
        requirement = _safe_string(item.get("requirement"), max_chars=220)
        if not requirement:
            continue
        requirement_key = requirement.lower()
        if requirement_key in seen:
            continue
        seen.add(requirement_key)

        priority = _normalize_requirement_priority(item.get("priority"))
        status = _normalize_requirement_status(item.get("status"))
        evidence = _safe_string(item.get("evidence"), max_chars=260)
        impact = _safe_string(item.get("impact"), max_chars=220)

        if _is_generic_requirement_evidence(evidence):
            cv_snippets = _best_cv_snippets_for_requirement(requirement, cv_text)
            if cv_snippets:
                quoted = "; ".join(f"\"{snippet}\"" for snippet in cv_snippets)
                evidence = _safe_string(f"CV evidence: {quoted}", max_chars=260)
            else:
                related_experience = _select_related_evidence(
                    requirement,
                    experience_highlights,
                    max_items=2,
                    max_chars=140,
                )
                related_skills = _select_related_evidence(
                    requirement,
                    matching_skills,
                    max_items=4,
                    max_chars=80,
                )
                if related_experience:
                    evidence = _safe_string(
                        f"Related CV evidence: {'; '.join(related_experience)}",
                        max_chars=260,
                    )
                elif related_skills:
                    evidence = _safe_string(
                        f"Related CV skills: {', '.join(related_skills)}",
                        max_chars=240,
                    )
                elif status in {"missing", "unknown"}:
                    evidence = "No direct CV evidence was found for this requirement."
                else:
                    evidence = "Model marked this as aligned, but explicit CV evidence is limited."

        if not impact:
            impact = _default_requirement_impact(status)

        enriched.append(
            {
                "requirement": requirement,
                "priority": priority,
                "status": status,
                "evidence": evidence,
                "impact": impact,
            }
        )

    return enriched


def _requirements_coverage(requirements_assessment: list[dict[str, str]]) -> dict[str, Any]:
    total = len(requirements_assessment)
    if total == 0:
        return {
            "total": 0,
            "met": 0,
            "partially_met": 0,
            "missing": 0,
            "unknown": 0,
            "must_have_missing": 0,
            "constraint_missing": 0,
            "coverage_score_100": None,
        }

    met = sum(1 for item in requirements_assessment if item.get("status") == "met")
    partially_met = sum(1 for item in requirements_assessment if item.get("status") == "partially_met")
    missing = sum(1 for item in requirements_assessment if item.get("status") == "missing")
    unknown = sum(1 for item in requirements_assessment if item.get("status") == "unknown")
    must_have_missing = sum(
        1
        for item in requirements_assessment
        if item.get("priority") == "must_have" and item.get("status") == "missing"
    )
    constraint_missing = sum(
        1
        for item in requirements_assessment
        if item.get("priority") == "constraint" and item.get("status") == "missing"
    )

    weighted_total = 0.0
    weighted_met = 0.0
    for item in requirements_assessment:
        w = _priority_value(item.get("priority") or "nice_to_have")
        weighted_total += w
        weighted_met += _status_value(item.get("status") or "unknown") * w
    coverage_score = round((weighted_met / weighted_total) * 100.0, 1) if weighted_total > 0 else None

    return {
        "total": total,
        "met": met,
        "partially_met": partially_met,
        "missing": missing,
        "unknown": unknown,
        "must_have_missing": must_have_missing,
        "constraint_missing": constraint_missing,
        "coverage_score_100": coverage_score,
    }


def _derive_requirements_score_100(requirements_assessment: list[dict[str, str]]) -> float | None:
    coverage = _requirements_coverage(requirements_assessment)
    base = coverage.get("coverage_score_100")
    if base is None:
        return None

    penalty = (
        float(coverage.get("must_have_missing", 0) or 0) * 14.0
        + float(coverage.get("constraint_missing", 0) or 0) * 16.0
    )
    return _clamp_score_100(float(base) - penalty)


def _derive_skills_score_100(
    *,
    matching_skills: list[str],
    missing_skills: list[str],
    concerns: list[str],
) -> float | None:
    if not matching_skills and not missing_skills and not concerns:
        return None

    matched = min(len(matching_skills), 10)
    missing = min(len(missing_skills), 10)
    concern_count = min(len(concerns), 8)

    raw = (
        52.0
        + (matched * 6.8)
        - (missing * 6.2)
        - (concern_count * 2.5)
        + (2.0 if matched > 0 and missing == 0 else 0.0)
    )
    return _clamp_score_100(raw)


def _derive_experience_score_100(*, experience_highlights: list[str], concerns: list[str]) -> float | None:
    if not experience_highlights and not concerns:
        return None

    highlights = min(len(experience_highlights), 8)
    concern_count = min(len(concerns), 8)
    raw = 50.0 + (highlights * 7.5) - (concern_count * 2.8) + (2.5 if highlights >= 3 else 0.0)
    return _clamp_score_100(raw)


def _refine_component_score_100(
    *,
    model_score_100: float | None,
    evidence_score_100: float | None,
    fallback_score_100: float | None = None,
) -> float | None:
    parts: list[tuple[float, float]] = []
    if model_score_100 is not None:
        parts.append((model_score_100, 0.72))
    if evidence_score_100 is not None:
        parts.append((evidence_score_100, 0.28))

    refined = _weighted_average(parts)
    if refined is not None:
        return refined
    if fallback_score_100 is not None:
        return _clamp_score_100(fallback_score_100)
    return None


def _blend_final_score_100(
    *,
    model_overall_score_100: float | None,
    skills_score_100: float | None,
    experience_score_100: float | None,
    requirements_score_100: float | None,
    requirements_assessment: list[dict[str, str]],
    matching_skills: list[str],
    missing_skills: list[str],
    experience_highlights: list[str],
    concerns: list[str],
) -> float | None:
    weighted_parts: list[tuple[float, float]] = []
    if skills_score_100 is not None:
        weighted_parts.append((skills_score_100, 0.35))
    if experience_score_100 is not None:
        weighted_parts.append((experience_score_100, 0.30))
    if requirements_score_100 is not None:
        weighted_parts.append((requirements_score_100, 0.35))

    derived = _weighted_average(weighted_parts)

    if derived is None and model_overall_score_100 is None:
        return None
    if derived is None:
        final_score = float(model_overall_score_100)
    elif model_overall_score_100 is None:
        final_score = float(derived)
    else:
        # Bias toward structured recruiter-requirement scoring while preserving model holistic judgment.
        final_score = (float(derived) * 0.70) + (float(model_overall_score_100) * 0.30)

    coverage = _requirements_coverage(requirements_assessment)
    met = float(coverage.get("met") or 0.0)
    partially_met = float(coverage.get("partially_met") or 0.0)
    unknown = float(coverage.get("unknown") or 0.0)
    coverage_score = coverage.get("coverage_score_100")
    signal_adjustment = (
        min(4.0, len(matching_skills) * 0.65)
        - min(4.5, len(missing_skills) * 0.75)
        + min(3.0, len(experience_highlights) * 0.55)
        - min(3.5, len(concerns) * 0.65)
        + min(2.4, met * 0.35)
        + min(1.2, partially_met * 0.15)
        - min(2.4, unknown * 0.25)
    )
    if coverage_score is not None:
        signal_adjustment += (float(coverage_score) - 50.0) * 0.04
    final_score = final_score + signal_adjustment

    must_have_missing = int(coverage.get("must_have_missing") or 0)
    constraint_missing = int(coverage.get("constraint_missing") or 0)
    if must_have_missing > 0 and final_score > 69.0:
        final_score = 69.0
    if constraint_missing > 0 and final_score > 59.0:
        final_score = 59.0
    return _clamp_score_100(final_score)


def _normalize_recommendation(raw: Any, final_score_100: float | None, requirements_assessment: list[dict[str, str]]) -> str:
    mapped = str(raw or "").strip().lower().replace("-", "_")
    if mapped in {"strong_yes", "yes", "lean_no", "no"}:
        return mapped

    coverage = _requirements_coverage(requirements_assessment)
    must_have_missing = int(coverage.get("must_have_missing") or 0)
    constraint_missing = int(coverage.get("constraint_missing") or 0)
    if constraint_missing > 0:
        return "no"
    if must_have_missing > 0:
        return "lean_no"
    if final_score_100 is None:
        return "lean_no"
    if final_score_100 >= 85:
        return "strong_yes"
    if final_score_100 >= 70:
        return "yes"
    if final_score_100 >= 55:
        return "lean_no"
    return "no"


def _score_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    rounded = round(float(value), 1)
    return f"{int(rounded)}" if rounded.is_integer() else f"{rounded:.1f}"


def _clean_bullet(text: str, *, max_chars: int = 260) -> str:
    cleaned = _safe_string(text, max_chars=max_chars)
    return cleaned.rstrip(".") + "." if cleaned else ""


def _build_score_rationale_bullets(
    *,
    final_score_100: float | None,
    skills_score_100: float | None,
    experience_score_100: float | None,
    requirements_score_100: float | None,
    requirements_assessment: list[dict[str, str]],
    matching_skills: list[str],
    missing_skills: list[str],
    experience_highlights: list[str],
    concerns: list[str],
    recruiter_requirements_used: bool,
) -> list[str]:
    bullets: list[str] = []
    coverage = _requirements_coverage(requirements_assessment)
    total = int(coverage.get("total") or 0)
    met = int(coverage.get("met") or 0)
    partially_met = int(coverage.get("partially_met") or 0)
    missing = int(coverage.get("missing") or 0)
    unknown = int(coverage.get("unknown") or 0)

    component_parts: list[str] = []
    if skills_score_100 is not None:
        component_parts.append(f"skills {_score_text(skills_score_100)}/100")
    if experience_score_100 is not None:
        component_parts.append(f"experience {_score_text(experience_score_100)}/100")
    if requirements_score_100 is not None:
        component_parts.append(f"recruiter requirements {_score_text(requirements_score_100)}/100")
    if component_parts and final_score_100 is not None:
        bullets.append(
            _clean_bullet(
                f"Composite fit {_score_text(final_score_100)}/100 from {', '.join(component_parts)}",
                max_chars=280,
            )
        )

    if matching_skills:
        bullets.append(
            _clean_bullet(
                f"Strong CV-to-role skill evidence: {', '.join(matching_skills[:4])}",
                max_chars=220,
            )
        )

    if experience_highlights:
        bullets.append(
            _clean_bullet(
                f"Relevant experience evidence: {'; '.join(experience_highlights[:2])}",
                max_chars=240,
            )
        )

    if recruiter_requirements_used and total > 0:
        bullets.append(
            _clean_bullet(
                f"Recruiter requirements coverage: {met}/{total} met, {partially_met} partial, {missing} missing, {unknown} unknown",
                max_chars=220,
            )
        )

    missing_critical = [
        item.get("requirement", "")
        for item in requirements_assessment
        if item.get("status") == "missing" and item.get("priority") in {"must_have", "constraint"}
    ]
    if missing_critical:
        bullets.append(
            _clean_bullet(
                f"Critical recruiter requirements missing/unsupported: {', '.join(missing_critical[:2])}",
                max_chars=240,
            )
        )

    if missing_skills:
        bullets.append(
            _clean_bullet(
                f"Skills gaps versus role needs: {', '.join(missing_skills[:4])}",
                max_chars=220,
            )
        )

    if concerns:
        bullets.append(
            _clean_bullet(
                f"Risk signals from CV evidence: {'; '.join(concerns[:2])}",
                max_chars=240,
            )
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for bullet in bullets:
        text = _safe_string(bullet, max_chars=300)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
        if len(deduped) >= _MAX_RATIONALE_BULLETS:
            break
    return deduped


async def calculate_cv_job_match(
    cv_text: str,
    job_spec_text: str,
    api_key: str,
    model: Optional[str] = None,
    additional_requirements: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse CV-to-job-spec fit with a single Claude call.

    Args:
        cv_text: Extracted text from the candidate's CV.
        job_spec_text: Extracted text from the job specification.
        api_key: Anthropic API key.
        model: Optional Claude model override. Uses env-resolved default when unset.
        additional_requirements: Optional free-text criteria (e.g. "large enterprise experience",
            "30 days notice", "XYZ passport") used when scoring; not part of the job spec.

    Returns:
        Dict with 0-100 tailored recruiter fit score and detailed requirement-level evidence.
    """
    if not cv_text or not job_spec_text:
        return {
            "cv_job_match_score": None,
            "skills_match": None,
            "experience_relevance": None,
            "match_details": {"error": "Missing CV or job spec text"},
        }

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)

        # Truncate to manage token costs
        cv_truncated = cv_text[:4000]
        js_truncated = job_spec_text[:2000]
        additional_section = _format_additional_requirements_section(additional_requirements)
        recruiter_requirements = _extract_recruiter_requirements(additional_requirements)

        prompt = CV_MATCH_PROMPT.format(
            cv_text=cv_truncated,
            job_spec_text=js_truncated,
            additional_requirements_section=additional_section,
        )

        resolved_model = (model or settings.resolved_claude_scoring_model).strip()
        model_candidates = candidate_models_for(resolved_model)

        logger.info(
            "Running CV-job match analysis (cv_chars=%d, js_chars=%d, model=%s)",
            len(cv_truncated),
            len(js_truncated),
            resolved_model,
        )

        response = None
        model_used = resolved_model
        last_model_error: Exception | None = None
        for candidate_model in model_candidates:
            try:
                response = client.messages.create(
                    model=candidate_model,
                    max_tokens=1024,
                    system="You are an expert recruiter. Respond ONLY with valid JSON.",
                    messages=[{"role": "user", "content": prompt}],
                )
                model_used = candidate_model
                if candidate_model != resolved_model:
                    logger.warning(
                        "Fell back to Claude model=%s after primary model=%s was unavailable",
                        candidate_model,
                        resolved_model,
                    )
                break
            except Exception as exc:
                if is_model_not_found_error(exc):
                    last_model_error = exc
                    logger.warning(
                        "Claude model unavailable for CV match (model=%s): %s",
                        candidate_model,
                        exc,
                    )
                    continue
                raise
        if response is None:
            if last_model_error is not None:
                raise last_model_error
            raise RuntimeError("Claude call failed before receiving a response")

        usage_ledger = _build_usage_ledger(response=response, model=model_used)

        raw_text = response.content[0].text

        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{[\s\S]*\}", raw_text)
            if json_match:
                result = json.loads(json_match.group())
            else:
                logger.warning("Claude returned non-JSON for CV match analysis")
                return {
                    "cv_job_match_score": None,
                    "skills_match": None,
                    "experience_relevance": None,
                    "match_details": {
                        "error": "Failed to parse Claude response",
                        "_claude_usage": usage_ledger,
                    },
                }

        if not isinstance(result, dict):
            return {
                "cv_job_match_score": None,
                "skills_match": None,
                "experience_relevance": None,
                "match_details": {
                    "error": "Claude response was not a JSON object",
                    "_claude_usage": usage_ledger,
                },
            }

        requirements_assessment_raw = _normalize_requirements_assessment(
            result.get("requirements_assessment")
            or result.get("requirement_assessment")
            or result.get("criteria_assessment")
        )

        matching_skills = _safe_string_list(result.get("matching_skills"), max_items=20, max_chars=120)
        missing_skills = _safe_string_list(result.get("missing_skills"), max_items=20, max_chars=120)
        experience_highlights = _safe_string_list(result.get("experience_highlights"), max_items=12, max_chars=180)
        concerns = _safe_string_list(result.get("concerns"), max_items=12, max_chars=220)
        requirements_assessment = _enrich_requirements_assessment(
            requirements_assessment=requirements_assessment_raw,
            recruiter_requirements=recruiter_requirements,
            cv_text=cv_truncated,
            matching_skills=matching_skills,
            experience_highlights=experience_highlights,
        )

        model_overall_score_100 = _score_to_100(
            result.get("overall_match_score")
            or result.get("overall_match_score_100")
            or result.get("overall_score")
        )
        model_skills_score_100 = _score_to_100(
            result.get("skills_match_score")
            or result.get("skills_alignment_score")
            or result.get("skills_score")
        )
        model_experience_score_100 = _score_to_100(
            result.get("experience_relevance_score")
            or result.get("experience_alignment_score")
            or result.get("experience_score")
        )
        model_requirements_score_100 = _score_to_100(
            result.get("requirements_match_score")
            or result.get("requirement_match_score")
            or result.get("criteria_match_score")
        )
        requirements_score_100 = model_requirements_score_100
        if requirements_score_100 is None:
            requirements_score_100 = _derive_requirements_score_100(requirements_assessment)

        skills_evidence_score_100 = _derive_skills_score_100(
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            concerns=concerns,
        )
        experience_evidence_score_100 = _derive_experience_score_100(
            experience_highlights=experience_highlights,
            concerns=concerns,
        )

        skills_score_100 = _refine_component_score_100(
            model_score_100=model_skills_score_100,
            evidence_score_100=skills_evidence_score_100,
            fallback_score_100=model_overall_score_100,
        )
        experience_score_100 = _refine_component_score_100(
            model_score_100=model_experience_score_100,
            evidence_score_100=experience_evidence_score_100,
            fallback_score_100=model_overall_score_100,
        )

        final_score_100 = _blend_final_score_100(
            model_overall_score_100=model_overall_score_100,
            skills_score_100=skills_score_100,
            experience_score_100=experience_score_100,
            requirements_score_100=requirements_score_100,
            requirements_assessment=requirements_assessment,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            experience_highlights=experience_highlights,
            concerns=concerns,
        )
        recommendation = _normalize_recommendation(
            result.get("recommendation"),
            final_score_100=final_score_100,
            requirements_assessment=requirements_assessment,
        )
        coverage = _requirements_coverage(requirements_assessment)
        rationale_bullets = _build_score_rationale_bullets(
            final_score_100=final_score_100,
            skills_score_100=skills_score_100,
            experience_score_100=experience_score_100,
            requirements_score_100=requirements_score_100,
            requirements_assessment=requirements_assessment,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            experience_highlights=experience_highlights,
            concerns=concerns,
            recruiter_requirements_used=bool(recruiter_requirements),
        )

        logger.info(
            (
                "CV-job match complete: final=%.1f model=%.1f "
                "skills=%.1f experience=%.1f requirements=%.1f must_missing=%d"
            ),
            final_score_100 or 0.0,
            model_overall_score_100 or 0.0,
            skills_score_100 or 0.0,
            experience_score_100 or 0.0,
            requirements_score_100 or 0.0,
            int(coverage.get("must_have_missing") or 0),
        )

        final_score_10 = round((final_score_100 or 0.0) / 10.0, 1) if final_score_100 is not None else None
        skills_score_10 = round((skills_score_100 or 0.0) / 10.0, 1) if skills_score_100 is not None else None
        experience_score_10 = round((experience_score_100 or 0.0) / 10.0, 1) if experience_score_100 is not None else None

        return {
            "cv_job_match_score": final_score_100,
            "cv_job_match_score_10": final_score_10,
            "skills_match": skills_score_100,
            "skills_match_10": skills_score_10,
            "experience_relevance": experience_score_100,
            "experience_relevance_10": experience_score_10,
            "match_details": {
                "score_scale": "0-100",
                "model_overall_score_100": model_overall_score_100,
                "model_skills_score_100": model_skills_score_100,
                "model_experience_relevance_score_100": model_experience_score_100,
                "model_requirements_match_score_100": model_requirements_score_100,
                "skills_match_score_100": skills_score_100,
                "experience_relevance_score_100": experience_score_100,
                "requirements_match_score_100": requirements_score_100,
                "skills_evidence_score_100": skills_evidence_score_100,
                "experience_evidence_score_100": experience_evidence_score_100,
                "matching_skills": matching_skills,
                "missing_skills": missing_skills,
                "experience_highlights": experience_highlights,
                "concerns": concerns,
                "requirements_assessment": requirements_assessment,
                "requirements_coverage": coverage,
                "custom_requirements_used": bool(recruiter_requirements),
                "recommendation": recommendation,
                "score_rationale_bullets": rationale_bullets,
                "scoring_version": "cv_fit_v3_evidence_enriched",
                "summary": _safe_string(result.get("summary"), max_chars=600),
                "_claude_usage": usage_ledger,
            },
        }

    except Exception as e:
        err_msg = str(e)
        logger.error(
            "CV-job match analysis failed: %s (type=%s). Check ANTHROPIC_API_KEY and CLAUDE_MODEL.",
            err_msg,
            type(e).__name__,
        )
        model_hint = model or settings.resolved_claude_scoring_model
        fallback_hint = ", ".join(candidate_models_for(model_hint))
        return {
            "cv_job_match_score": None,
            "skills_match": None,
            "experience_relevance": None,
            "match_details": {
                "error": err_msg[:500],
                "hint": (
                    "Verify ANTHROPIC_API_KEY is set and CLAUDE_MODEL is valid "
                    f"(Haiku fallback chain: {fallback_hint})."
                ),
            },
        }


def calculate_cv_job_match_sync(
    cv_text: str,
    job_spec_text: str,
    api_key: str,
    model: Optional[str] = None,
    additional_requirements: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper for calculate_cv_job_match."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    calculate_cv_job_match(
                        cv_text, job_spec_text, api_key, model,
                        additional_requirements=additional_requirements,
                    ),
                )
                return future.result()
        else:
            return loop.run_until_complete(
                calculate_cv_job_match(
                    cv_text, job_spec_text, api_key, model,
                    additional_requirements=additional_requirements,
                )
            )
    except RuntimeError:
        return asyncio.run(
            calculate_cv_job_match(
                cv_text, job_spec_text, api_key, model,
                additional_requirements=additional_requirements,
            )
        )


def _build_usage_ledger(*, response: Any, model: str) -> Dict[str, Any]:
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    if input_tokens is None or output_tokens is None:
        raise RuntimeError("Anthropic response is missing usage token metadata")

    safe_input = max(0, int(input_tokens or 0))
    safe_output = max(0, int(output_tokens or 0))
    request_cost_usd = (
        (safe_input / _TOKENS_PER_MILLION) * float(settings.CLAUDE_INPUT_COST_PER_MILLION_USD)
        + (safe_output / _TOKENS_PER_MILLION) * float(settings.CLAUDE_OUTPUT_COST_PER_MILLION_USD)
    )
    return {
        "provider": "anthropic",
        "model": model,
        "input_tokens": safe_input,
        "output_tokens": safe_output,
        "tokens_used": safe_input + safe_output,
        "request_cost_usd": round(float(request_cost_usd), 6),
    }
