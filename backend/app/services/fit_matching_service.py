"""CV-to-Job-Spec fit matching via a single Claude API call.

This is the ONLY Claude scoring call per assessment — all other scoring
is pure heuristic. See PRODUCT_PLAN.md guardrail: "Single Claude call per
assessment".
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from ..platform.config import settings

logger = logging.getLogger("taali.fit_matching")

CV_MATCH_PROMPT = """Analyze the match between this candidate's CV and the job specification.

CV:
{cv_text}

Job Specification:
{job_spec_text}

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

Be objective and base scores only on evidence in the documents.
Score 5 as average/neutral, 7+ as good match, 9+ as exceptional.
"""


async def calculate_cv_job_match(
    cv_text: str,
    job_spec_text: str,
    api_key: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse CV-to-job-spec fit with a single Claude call.

    Args:
        cv_text: Extracted text from the candidate's CV.
        job_spec_text: Extracted text from the job specification.
        api_key: Anthropic API key.
        model: Optional Claude model override. Uses env-resolved default when unset.

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

        prompt = CV_MATCH_PROMPT.format(
            cv_text=cv_truncated,
            job_spec_text=js_truncated,
        )

        resolved_model = model or settings.resolved_claude_model

        logger.info(
            "Running CV-job match analysis (cv_chars=%d, js_chars=%d, model=%s)",
            len(cv_truncated),
            len(js_truncated),
            resolved_model,
        )

        response = client.messages.create(
            model=resolved_model,
            max_tokens=1024,
            system="You are an expert recruiter. Respond ONLY with valid JSON.",
            messages=[{"role": "user", "content": prompt}],
        )

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
                    "match_details": {"error": "Failed to parse Claude response"},
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
            },
        }

    except Exception as e:
        logger.error("CV-job match analysis failed: %s", e)
        return {
            "cv_job_match_score": None,
            "skills_match": None,
            "experience_relevance": None,
            "match_details": {"error": str(e)},
        }


def calculate_cv_job_match_sync(
    cv_text: str,
    job_spec_text: str,
    api_key: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper for calculate_cv_job_match."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in an async context — run directly
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    calculate_cv_job_match(cv_text, job_spec_text, api_key, model),
                )
                return future.result()
        else:
            return loop.run_until_complete(
                calculate_cv_job_match(cv_text, job_spec_text, api_key, model)
            )
    except RuntimeError:
        return asyncio.run(
            calculate_cv_job_match(cv_text, job_spec_text, api_key, model)
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
