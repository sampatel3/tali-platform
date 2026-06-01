"""Single-call CV parser. Haiku 4.5, temp 0, max_tokens 4096, 1 retry.

Public entry point: ``parse_cv(cv_text) -> ParsedCV``.

Failure shape: returns ``ParsedCV(parse_failed=True, error_reason=...)``.
Never raises to the caller — the parser is best-effort and the candidate
page falls back to raw text when ``parse_failed`` is set.

The call -> parse -> schema-validate -> bounded-retry lifecycle is
delegated to the shared ``app.llm`` gateway. This module owns only the
CV-specific concerns: the prompt, the ``ParsedCVSections`` schema,
caching of the wrapped ``ParsedCV``, and the never-raise failure shape.
"""

from __future__ import annotations

import logging

from ..llm import MeteringContext, generate_structured
from . import MODEL_VERSION, PROMPT_VERSION
from .prompts import build_cv_parse_prompt
from .schemas import ParsedCV, ParsedCVSections

logger = logging.getLogger("taali.cv_parsing.runner")

OUTPUT_TOKEN_CEILING = 4096
MAX_RETRIES = 1
TEMPERATURE = 0.0

_SYSTEM_PROMPT = "You are a CV parser. Respond ONLY with valid JSON."


def _resolve_anthropic_client():
    """Build a metered Anthropic client. Org context is unavailable to the
    parser (it's called from upload paths and assessment routes that may
    not have an org loaded), so the meter falls back to the shared key
    with no organization_id. Callers that DO have an org context should
    pass a pre-built client via the ``client=`` kwarg."""
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client()


def parse_cv(
    cv_text: str,
    *,
    client=None,
    skip_cache: bool = False,
    metering: dict | None = None,
) -> ParsedCV:
    """Parse extracted CV text into structured sections.

    Args:
        cv_text: text extracted from the candidate's CV (PDF/DOCX/TXT).
        client: optional pre-built Anthropic client (used by tests).
        skip_cache: bypass the parse cache (used by re-parse triggers).
        metering: optional metering kwargs forwarded to the Claude call.
            Callers with org context should pass at least
            ``{"feature": Feature.CV_PARSE, "organization_id": org.id,
            "user_id": user.id, "entity_id": str(application_id)}`` so
            the usage_event is attributed correctly. If absent, the call
            still records (default feature ``"cv_parse"``) but without
            per-org / per-application attribution — fine for tests, not
            fine for production.

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

    # Forced tool-use: the model emits ParsedCVSections as the tool's
    # ``.input`` dict — no JSON parsing, no fence stripping, no syntactic
    # retry path. ParsedCVSections's Pydantic JSON schema is the single
    # wire contract; semantic ``model_validate`` still runs server-side.
    result = generate_structured(
        client,
        model=MODEL_VERSION,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        output_model=ParsedCVSections,
        metering=MeteringContext.from_dict(metering, default_feature="cv_parse"),
        max_tokens=OUTPUT_TOKEN_CEILING,
        temperature=TEMPERATURE,
        max_retries=MAX_RETRIES,
        use_tool_use=True,
    )

    if not result.ok or result.value is None:
        return ParsedCV.failed(
            reason=result.error_reason or "parse_failed",
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )

    parsed = ParsedCV.from_sections(
        result.value,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )

    if not skip_cache:
        try:
            cache_module.set(cache_key, parsed)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("CV parse cache write failed: %s", exc)

    return parsed
