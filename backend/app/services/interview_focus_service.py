"""Generate recruiter interview focus pointers from a role job specification."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from ..platform.config import settings

logger = logging.getLogger("taali.interview_focus")

_TOKENS_PER_MILLION = 1_000_000.0

INTERVIEW_FOCUS_PROMPT = """You are helping a recruiter manually screen candidates before full interviews.

Job specification:
{job_spec_text}

Return valid JSON with EXACTLY this structure (no markdown):
{{
  "role_summary": "2-3 sentences describing what this role truly needs",
  "manual_screening_triggers": ["trigger 1", "trigger 2", "trigger 3"],
  "questions": [
    {{
      "question": "screening question",
      "what_to_listen_for": ["signal 1", "signal 2"],
      "concerning_signals": ["concern 1", "concern 2"]
    }}
  ]
}}

Rules:
- Provide exactly 3 distinct questions.
- Focus on practical verification of experience, scope, ownership, and seniority fit.
- Keep each list concise (2-3 items).
- Avoid illegal or discriminatory questions.
- Do not include any prose outside JSON.
"""

_DEFAULT_QUESTIONS = [
    {
        "question": "Can you walk through a recent project most similar to this role and your exact contribution?",
        "what_to_listen_for": [
            "Clear scope, ownership boundaries, and measurable outcomes",
            "Specific technologies and tradeoffs they made",
        ],
        "concerning_signals": [
            "Only team-level statements with no personal ownership",
            "Vague claims without concrete examples or metrics",
        ],
    },
    {
        "question": "What is one hard decision you made in that project, and why did you choose that approach?",
        "what_to_listen_for": [
            "Structured reasoning with alternatives considered",
            "Awareness of impact on quality, speed, or reliability",
        ],
        "concerning_signals": [
            "No clear decision-making framework",
            "Cannot explain tradeoffs or downstream impact",
        ],
    },
    {
        "question": "How would you ramp up in the first 60 days for this role?",
        "what_to_listen_for": [
            "Prioritized plan tied to role outcomes",
            "Practical communication and stakeholder alignment",
        ],
        "concerning_signals": [
            "Generic onboarding response not tied to the role",
            "No concrete milestones or success indicators",
        ],
    },
]


def generate_interview_focus_sync(
    job_spec_text: str,
    api_key: str,
    model: Optional[str] = None,
) -> Dict[str, Any] | None:
    """Generate structured interview focus guidance from job spec text."""
    if not (job_spec_text or "").strip():
        return None

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        resolved_model = model or settings.resolved_claude_scoring_model
        prompt = INTERVIEW_FOCUS_PROMPT.format(job_spec_text=job_spec_text[:5000])

        logger.info(
            "Generating interview focus (job_spec_chars=%d, model=%s)",
            len(job_spec_text),
            resolved_model,
        )

        response = client.messages.create(
            model=resolved_model,
            max_tokens=1400,
            system="You are an expert recruiter. Respond ONLY with valid JSON.",
            messages=[{"role": "user", "content": prompt}],
        )
        usage_ledger = _build_usage_ledger(response=response, model=resolved_model)

        parsed = _parse_json(response.content[0].text)
        if not isinstance(parsed, dict):
            logger.warning("Interview focus generation returned non-object JSON")
            return None

        normalized = _normalize_focus(parsed)
        normalized["_claude_usage"] = usage_ledger
        if not normalized.get("questions"):
            return None
        return normalized
    except Exception as exc:
        logger.error("Interview focus generation failed: %s", exc)
        return None


def _parse_json(raw_text: str) -> Dict[str, Any] | None:
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _normalize_focus(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = _clip_text(payload.get("role_summary"), max_len=700)
    triggers = _normalize_list(payload.get("manual_screening_triggers"), limit=5, max_len=180)

    normalized_questions: list[Dict[str, Any]] = []
    raw_questions = payload.get("questions")
    if isinstance(raw_questions, list):
        for raw_q in raw_questions:
            if not isinstance(raw_q, dict):
                continue
            question = _clip_text(raw_q.get("question"), max_len=260)
            if not question:
                continue
            listen_for = _normalize_list(raw_q.get("what_to_listen_for"), limit=4, max_len=180)
            concerns = _normalize_list(raw_q.get("concerning_signals"), limit=4, max_len=180)
            normalized_questions.append(
                {
                    "question": question,
                    "what_to_listen_for": listen_for or _DEFAULT_QUESTIONS[0]["what_to_listen_for"],
                    "concerning_signals": concerns or _DEFAULT_QUESTIONS[0]["concerning_signals"],
                }
            )
            if len(normalized_questions) == 3:
                break

    if len(normalized_questions) < 3:
        for item in _DEFAULT_QUESTIONS:
            if len(normalized_questions) == 3:
                break
            if any(q["question"] == item["question"] for q in normalized_questions):
                continue
            normalized_questions.append(item)

    return {
        "role_summary": summary
        or "Focus manual screening on ownership, depth of execution, and demonstrated role fit.",
        "manual_screening_triggers": triggers,
        "questions": normalized_questions[:3],
    }


def _normalize_list(value: Any, *, limit: int, max_len: int) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _clip_text(raw, max_len=max_len)
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) == limit:
            break
    return items


def _clip_text(value: Any, *, max_len: int) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text[:max_len]


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
