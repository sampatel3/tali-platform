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
from ..services.pricing_service import Feature
from ..services.provider_usage_admission import (
    release_provider_usage,
    reserve_provider_usage,
)
from . import MODEL_VERSION, PROMPT_VERSION
from .prompts import build_cv_parse_prompt
from .schemas import ParsedCV, ParsedCVSections

logger = logging.getLogger("taali.cv_parsing.runner")

OUTPUT_TOKEN_CEILING = 4096
MAX_RETRIES = 1
TEMPERATURE = 0.0
# Truncate absurdly long CVs. Most are <8KB; 30KB comfortably covers ~6000
# words and keeps the prompt under Haiku's input budget. Shared with the
# batch path — both must truncate identically or their cache keys diverge.
CV_TEXT_CEILING = 30_000

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

    if len(cv_text) > CV_TEXT_CEILING:
        cv_text = cv_text[:CV_TEXT_CEILING]

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

    def _fail(reason: str) -> ParsedCV:
        failed = ParsedCV.failed(
            reason=reason,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
        # cache.set filters to deterministic failure reasons; transient
        # ones (API errors, client init) pass through uncached.
        if not skip_cache:
            try:
                cache_module.set(cache_key, failed)
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("CV parse failure-cache write failed: %s", exc)
        return failed

    try:
        prompt = build_cv_parse_prompt(cv_text)
    except Exception as exc:
        return _fail(f"prompt_render_failed: {exc}")

    if client is None:
        try:
            client = _resolve_anthropic_client()
        except Exception as exc:
            return _fail(f"client_init_failed: {exc}")

    # Forced tool-use: the model emits ParsedCVSections as the tool's
    # ``.input`` dict — no JSON parsing, no fence stripping, no syntactic
    # retry path. ParsedCVSections's Pydantic JSON schema is the single
    # wire contract; semantic ``model_validate`` still runs server-side.
    metering_context = MeteringContext.from_dict(
        metering, default_feature="cv_parse"
    )
    reservation = None
    if (
        metering_context.organization_id is not None
        and metering_context.role_id is not None
    ):
        try:
            reservation = reserve_provider_usage(
                organization_id=int(metering_context.organization_id),
                role_id=int(metering_context.role_id),
                feature=Feature.CV_PARSE,
                trace_id=(
                    str(metering_context.trace_id)
                    if metering_context.trace_id
                    else f"cv-parse:{metering_context.entity_id or cache_key}"
                ),
                entity_id=(
                    str(metering_context.entity_id)
                    if metering_context.entity_id is not None
                    else None
                ),
                sub_feature="application_cv_parse",
            )
        except Exception as exc:
            # Structured sections are an optional display enhancement; the
            # candidate page already has a raw-text fallback. Never bypass the
            # org balance or role ceiling to create them.
            return _fail(f"usage_admission_failed: {exc}")
        metering_context.credit_reservation = reservation.as_metering_payload()

    result = generate_structured(
        client,
        model=MODEL_VERSION,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        output_model=ParsedCVSections,
        metering=metering_context,
        max_tokens=OUTPUT_TOKEN_CEILING,
        temperature=TEMPERATURE,
        # One hard reservation belongs to exactly one provider call. CV section
        # parsing is optional, so a validation failure falls back to raw text
        # rather than making an unreserved retry that could overshoot the cap.
        max_retries=0 if reservation is not None else MAX_RETRIES,
        use_tool_use=True,
    )

    if not result.ok or result.value is None:
        if (result.error_reason or "").startswith("claude_call_failed"):
            # The real wrapper already releases on SDK failure. This
            # idempotent fallback covers injected clients and failures before
            # the wrapper sees the request.
            release_provider_usage(
                reservation,
                reason="cv_parse_provider_call_failed",
            )
        return _fail(result.error_reason or "parse_failed")

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
