"""Secret-safe durable codes for provider exceptions."""

from __future__ import annotations


_PROVIDER_FAILURE_CATEGORIES = frozenset(
    {
        "bad_request",
        "context_length",
        "credit_exhausted",
        "network",
        "overloaded",
        "rate_limit",
        "server_error",
        "timeout",
    }
)
_STRUCTURED_VALIDATION_CODES = frozenset(
    {
        "invalid_json",
        "missing_tool_output",
        "schema_validation_failed",
        "semantic_validation_failed",
    }
)


def classify_anthropic_exception(
    error: BaseException,
) -> tuple[str | None, int | None]:
    """Bucket Anthropic failures for dashboards without storing their body."""

    try:
        import anthropic  # type: ignore[import-not-found]
    except Exception:
        return (None, None)
    status_code: int | None = None
    for attr in ("status_code", "http_status", "code"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            status_code = value
            break
    if isinstance(error, TimeoutError):
        return ("timeout", status_code)
    if isinstance(error, ConnectionError):
        return ("network", status_code)
    if isinstance(error, getattr(anthropic, "RateLimitError", ())):
        return ("rate_limit", status_code or 429)
    if isinstance(error, getattr(anthropic, "APITimeoutError", ())):
        return ("timeout", status_code)
    if isinstance(error, getattr(anthropic, "APIConnectionError", ())):
        return ("network", status_code)
    if isinstance(error, getattr(anthropic, "InternalServerError", ())):
        return ("server_error", status_code or 500)
    if isinstance(error, getattr(anthropic, "BadRequestError", ())):
        message = str(error).lower()
        if "credit balance is too low" in message:
            return ("credit_exhausted", status_code or 400)
        if "context" in message and ("length" in message or "window" in message):
            return ("context_length", status_code or 400)
        return ("bad_request", status_code or 400)
    if isinstance(error, getattr(anthropic, "APIStatusError", ())):
        if status_code == 529:
            return ("overloaded", 529)
        if status_code and status_code >= 500:
            return ("server_error", status_code)
        if status_code and status_code >= 400:
            return ("bad_request", status_code)
    if "credit balance is too low" in str(error).lower():
        return ("credit_exhausted", 400)
    return ("other", status_code)


def safe_provider_error_code(
    error: BaseException,
    *,
    operation: str,
) -> str:
    """Return a stable code without persisting the exception message/body."""

    return f"{operation}:{type(error).__name__}"[:200]


def safe_anthropic_error_code(
    error: BaseException,
    *,
    operation: str,
) -> str:
    """Return operation/category/type without persisting provider text."""

    category, _status = classify_anthropic_exception(error)
    parts = [str(operation), type(error).__name__]
    if category not in (None, "other"):
        parts.insert(1, category)
    return ":".join(parts)[:200]


def safe_structured_error_code(
    error_reason: object,
    *,
    operation: str,
) -> str:
    """Reduce a structured-generation failure to a controlled category.

    ``StructuredResult.error_reason`` is a caller-facing value, so callers
    must not assume an injected or legacy result contains only safe text.
    """

    reason = str(error_reason or "").strip()
    if reason.startswith("claude_call_failed"):
        tokens = set(reason.split(":"))
        category = next(
            (item for item in _PROVIDER_FAILURE_CATEGORIES if item in tokens),
            None,
        )
        suffix = f"provider_{category}" if category else "provider_failed"
    elif reason.startswith("validation_failed_after_retry"):
        supplied_code = reason.partition(":")[2].strip()
        suffix = (
            supplied_code
            if supplied_code in _STRUCTURED_VALIDATION_CODES
            else "validation_failed"
        )
    elif reason.startswith("input_token_ceiling_exceeded"):
        suffix = "input_token_ceiling_exceeded"
    else:
        suffix = "structured_generation_failed"
    return f"{operation}:{suffix}"[:200]


def public_scoring_failure_code(reason: object) -> str:
    """Map internal/provider scoring failures to recruiter-safe stable codes."""

    value = str(reason or "").strip().lower()
    if not value:
        return "scoring_failed"
    if any(marker in value for marker in ("missing cv", "missing_inputs")):
        return "missing_inputs"
    if "rate limit" in value or "rate_limit" in value or "429" in value:
        return "scoring_rate_limited"
    if "timeout" in value or "timed out" in value:
        return "scoring_timed_out"
    if any(marker in value for marker in ("credit", "monthly budget", "billing")):
        return "scoring_budget_unavailable"
    if any(
        marker in value
        for marker in (
            "client_init",
            "api key",
            "authentication",
            "unauthorized",
            "forbidden",
        )
    ):
        return "scoring_provider_unavailable"
    if "prompt_render" in value:
        return "scoring_input_render_failed"
    if any(
        marker in value
        for marker in ("validation", "invalid", "missing field", "json", "schema")
    ):
        return "scoring_output_invalid"
    return "scoring_provider_failed"


__all__ = [
    "classify_anthropic_exception",
    "public_scoring_failure_code",
    "safe_anthropic_error_code",
    "safe_provider_error_code",
    "safe_structured_error_code",
]
