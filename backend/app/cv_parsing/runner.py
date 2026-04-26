"""Single-call CV parser. Haiku 4.5, temp 0, max_tokens 4096, 1 retry.

Public entry point: ``parse_cv(cv_text) -> ParsedCV``.

Failure shape: returns ``ParsedCV(parse_failed=True, error_reason=...)``.
Never raises to the caller — the parser is best-effort and the candidate
page falls back to raw text when ``parse_failed`` is set.
"""

from __future__ import annotations

import json
import logging
import re
from pydantic import ValidationError

from . import MODEL_VERSION, PROMPT_VERSION
from .prompts import build_cv_parse_prompt
from .schemas import ParsedCV, ParsedCVSections

logger = logging.getLogger("taali.cv_parsing.runner")

OUTPUT_TOKEN_CEILING = 4096
MAX_RETRIES = 1
TEMPERATURE = 0.0
INPUT_TOKEN_CEILING = 8000  # CVs can be longer than scoring prompts


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = obj_match.group(0)
    return text


def _resolve_anthropic_client():
    """Build an Anthropic client. Per memory, key is settings.ANTHROPIC_API_KEY."""
    from anthropic import Anthropic

    from ..platform.config import settings

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return Anthropic(api_key=api_key)


def _call_claude(client, *, prompt: str) -> str:
    system = "You are a CV parser. Respond ONLY with valid JSON."
    response = client.messages.create(
        model=MODEL_VERSION,
        max_tokens=OUTPUT_TOKEN_CEILING,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return ""


def parse_cv(
    cv_text: str,
    *,
    client=None,
    skip_cache: bool = False,
) -> ParsedCV:
    """Parse extracted CV text into structured sections.

    Args:
        cv_text: text extracted from the candidate's CV (PDF/DOCX/TXT).
        client: optional pre-built Anthropic client (used by tests).
        skip_cache: bypass the parse cache (used by re-parse triggers).

    Returns:
        ParsedCV. Always returns; never raises. On failure the result has
        ``parse_failed=True`` and ``error_reason`` populated.
    """
    cv_text = (cv_text or "").strip()
    if not cv_text:
        return ParsedCV.failed(
            reason="empty_cv_text",
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )

    # Truncate if absurdly long. Most CVs are <8KB; cap at 30KB which
    # comfortably covers ~6000 words and keeps the prompt under Haiku's
    # input budget.
    if len(cv_text) > 30_000:
        cv_text = cv_text[:30_000]

    from . import cache as cache_module

    cache_key = cache_module.compute_cache_key(
        cv_text=cv_text,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    if not skip_cache:
        cached = cache_module.get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"cache_hit": True})

    try:
        prompt = build_cv_parse_prompt(cv_text)
    except Exception as exc:
        return ParsedCV.failed(
            reason=f"prompt_render_failed: {exc}",
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )

    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            return ParsedCV.failed(
                reason=f"client_init_failed: {exc}",
                prompt_version=PROMPT_VERSION,
                model_version=MODEL_VERSION,
            )

    last_err = ""
    parsed_sections: ParsedCVSections | None = None
    current_prompt = prompt
    for attempt in range(MAX_RETRIES + 1):
        try:
            raw = _call_claude(client, prompt=current_prompt)
        except Exception as exc:
            logger.exception("CV parse Claude call failed (attempt %d)", attempt + 1)
            return ParsedCV.failed(
                reason=f"claude_call_failed: {exc}",
                prompt_version=PROMPT_VERSION,
                model_version=MODEL_VERSION,
            )

        text = _strip_json_fences(raw)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            last_err = f"invalid_json: {exc}"
            logger.warning("CV parse attempt %d failed: %s", attempt + 1, last_err)
            if attempt < MAX_RETRIES:
                current_prompt = (
                    prompt
                    + "\n\nYour previous response was not valid JSON. "
                    + "Return a corrected JSON response. Do not include any commentary."
                )
                continue
            break

        try:
            parsed_sections = ParsedCVSections.model_validate(payload)
            break
        except ValidationError as exc:
            last_err = f"schema_validation: {exc}"
            logger.warning("CV parse attempt %d failed: %s", attempt + 1, last_err)
            if attempt < MAX_RETRIES:
                current_prompt = (
                    prompt
                    + f"\n\nYour previous response failed schema validation: {last_err}\n"
                    + "Return a corrected JSON response. Do not include any commentary."
                )
                continue
            break

    if parsed_sections is None:
        return ParsedCV.failed(
            reason=f"validation_failed_after_retry: {last_err}",
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )

    parsed = ParsedCV.from_sections(
        parsed_sections,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )

    if not skip_cache:
        try:
            cache_module.set(cache_key, parsed)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("CV parse cache write failed: %s", exc)

    return parsed
