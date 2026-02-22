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
4) Use the full 0-100 scale: 50 = neutral baseline, 70+ = good match, 85+ = strong match.
5) Include a requirements_assessment entry for each recruiter-added criterion when provided.
"""


def _extract_recruiter_requirements(additional_requirements: Optional[str]) -> list[str]:
    text = str(additional_requirements or "").strip()
    if not text:
        return []

    parts = re.split(r"[\n;]+", text)
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
    return round(max(0.0, min(100.0, float(base) - penalty)), 1)


def _blend_final_score_100(
    *,
    model_overall_score_100: float | None,
    skills_score_100: float | None,
    experience_score_100: float | None,
    requirements_score_100: float | None,
    requirements_assessment: list[dict[str, str]],
) -> float | None:
    weighted_parts: list[tuple[float, float]] = []
    if skills_score_100 is not None:
        weighted_parts.append((skills_score_100, 0.35))
    if experience_score_100 is not None:
        weighted_parts.append((experience_score_100, 0.30))
    if requirements_score_100 is not None:
        weighted_parts.append((requirements_score_100, 0.35))

    derived = None
    if weighted_parts:
        numerator = sum(score * weight for score, weight in weighted_parts)
        denominator = sum(weight for _, weight in weighted_parts)
        derived = round(numerator / denominator, 1) if denominator > 0 else None

    if derived is None and model_overall_score_100 is None:
        return None
    if derived is None:
        final_score = float(model_overall_score_100)
    elif model_overall_score_100 is None:
        final_score = float(derived)
    else:
        # Bias toward structured recruiter-requirement scoring while preserving model holistic judgment.
        final_score = round((float(derived) * 0.65) + (float(model_overall_score_100) * 0.35), 1)

    coverage = _requirements_coverage(requirements_assessment)
    must_have_missing = int(coverage.get("must_have_missing") or 0)
    constraint_missing = int(coverage.get("constraint_missing") or 0)
    if must_have_missing > 0 and final_score > 69.0:
        final_score = 69.0
    if constraint_missing > 0 and final_score > 59.0:
        final_score = 59.0
    return round(max(0.0, min(100.0, final_score)), 1)


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

        requirements_assessment = _normalize_requirements_assessment(
            result.get("requirements_assessment")
            or result.get("requirement_assessment")
            or result.get("criteria_assessment")
        )
        if not requirements_assessment and recruiter_requirements:
            requirements_assessment = [
                {
                    "requirement": requirement,
                    "priority": "must_have",
                    "status": "unknown",
                    "evidence": "",
                    "impact": "No explicit requirement-level verdict returned by model.",
                }
                for requirement in recruiter_requirements[:_MAX_REQUIREMENTS]
            ]

        model_overall_score_100 = _score_to_100(
            result.get("overall_match_score")
            or result.get("overall_match_score_100")
            or result.get("overall_score")
        )
        skills_score_100 = _score_to_100(
            result.get("skills_match_score")
            or result.get("skills_alignment_score")
            or result.get("skills_score")
        )
        experience_score_100 = _score_to_100(
            result.get("experience_relevance_score")
            or result.get("experience_alignment_score")
            or result.get("experience_score")
        )
        requirements_score_100 = _score_to_100(
            result.get("requirements_match_score")
            or result.get("requirement_match_score")
            or result.get("criteria_match_score")
        )
        if requirements_score_100 is None:
            requirements_score_100 = _derive_requirements_score_100(requirements_assessment)

        final_score_100 = _blend_final_score_100(
            model_overall_score_100=model_overall_score_100,
            skills_score_100=skills_score_100,
            experience_score_100=experience_score_100,
            requirements_score_100=requirements_score_100,
            requirements_assessment=requirements_assessment,
        )
        recommendation = _normalize_recommendation(
            result.get("recommendation"),
            final_score_100=final_score_100,
            requirements_assessment=requirements_assessment,
        )
        coverage = _requirements_coverage(requirements_assessment)

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
                "skills_match_score_100": skills_score_100,
                "experience_relevance_score_100": experience_score_100,
                "requirements_match_score_100": requirements_score_100,
                "matching_skills": _safe_string_list(result.get("matching_skills"), max_items=20, max_chars=120),
                "missing_skills": _safe_string_list(result.get("missing_skills"), max_items=20, max_chars=120),
                "experience_highlights": _safe_string_list(result.get("experience_highlights"), max_items=12, max_chars=180),
                "concerns": _safe_string_list(result.get("concerns"), max_items=12, max_chars=220),
                "requirements_assessment": requirements_assessment,
                "requirements_coverage": coverage,
                "custom_requirements_used": bool(recruiter_requirements),
                "recommendation": recommendation,
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
