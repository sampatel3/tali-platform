"""CV-to-Job-Spec fit matching via a single Claude API call.

This is the ONLY Claude scoring call per assessment — all other scoring
is pure heuristic. See PRODUCT_PLAN.md guardrail: "Single Claude call per
assessment".
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from ..components.integrations.claude.model_fallback import (
    candidate_models_for,
    is_model_not_found_error,
)
from ..platform.config import settings

logger = logging.getLogger("taali.fit_matching")

_TOKENS_PER_MILLION = 1_000_000.0

CV_MATCH_PROMPT = """Analyze the match between this candidate's CV and the job specification.

CV:
{cv_text}

Job Specification:
{job_spec_text}
{additional_requirements_section}

Provide a JSON response with EXACTLY this structure (no markdown, no explanation, ONLY valid JSON):
{{
    "overall_match_score": <0-10>,
    "skills_match_score": <0-10>,
    "experience_relevance_score": <0-10>,
    "matching_skills": ["skill1", "skill2"],
    "missing_skills": ["skill1", "skill2"],
    "experience_highlights": ["relevant experience 1", "relevant experience 2"],
    "concerns": ["concern 1", "concern 2"],
    "summary": "2-3 sentence summary of fit"
}}

Scoring: Focus the overall_match_score on how well the CV meets the role requirements.
(1) Job spec: match to the job specification above (skills, experience, role fit).
(2) Additional criteria: if "Additional scoring criteria" is provided, treat those as must-consider requirements (e.g. large enterprise experience, notice period, passport, production experience). Only give high scores (7+) when the CV supports both the job spec and the additional criteria where they apply; otherwise reflect gaps in concerns and lower the overall score.
Be objective and base scores only on evidence in the documents. Score 5 as average/neutral, 7+ as good match, 9+ as exceptional.
"""


def _format_additional_requirements_section(additional_requirements: Optional[str]) -> str:
    if not additional_requirements or not additional_requirements.strip():
        return ""
    text = additional_requirements.strip()[:1500]
    return f"""

Additional scoring criteria (evaluate the CV against these; lower the overall score if the CV does not support them):
{text}
"""


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
        Dict with overall_match_score, skills_match_score,
        experience_relevance_score, and detailed match info.
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
            import re
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

        overall = _clamp_score(result.get("overall_match_score"))
        skills = _clamp_score(result.get("skills_match_score"))
        experience = _clamp_score(result.get("experience_relevance_score"))

        logger.info(
            "CV-job match complete: overall=%.1f skills=%.1f experience=%.1f",
            overall or 0,
            skills or 0,
            experience or 0,
        )

        return {
            "cv_job_match_score": overall,
            "skills_match": skills,
            "experience_relevance": experience,
            "match_details": {
                "matching_skills": result.get("matching_skills", []),
                "missing_skills": result.get("missing_skills", []),
                "experience_highlights": result.get("experience_highlights", []),
                "concerns": result.get("concerns", []),
                "summary": result.get("summary", ""),
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


def _clamp_score(val: Any) -> float | None:
    """Clamp a score value to 0-10 range, or return None."""
    if val is None:
        return None
    try:
        v = float(val)
        return max(0.0, min(10.0, round(v, 1)))
    except (TypeError, ValueError):
        return None


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
