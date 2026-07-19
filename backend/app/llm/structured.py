"""Single-response structured-generation lifecycle.

``generate_structured`` is the shared spine the single-shot pipelines
currently each reimplement (cv_matching, cv_parsing, candidate_search,
pre_screen, the claude integration): cache lookup -> input ceiling ->
call -> parse -> schema-validate -> semantic-validate -> bounded retry ->
cache write. The pipeline-specific parts are injected, NOT baked in:

* ``output_model``      — the Pydantic contract (one schema source).
* ``semantic_validators`` — invariants JSON schema can't express (e.g.
  cv_matching's verbatim evidence grounding + cross-field consistency).
  A validator may mutate ``value`` in place and/or raise
  ``ValidationFailure`` to trigger a retry.
* ``cache_get`` / ``cache_set`` — the existing per-pipeline cache module's
  callables, so no cache-schema migration is required to adopt this.
* ``retry_message_builder`` — how to fold the error back into the prompt;
  cv_matching appends to the dynamic CV block to preserve its cached
  static block, cv_parsing rebuilds the whole prompt.

Two modes:

* **Text** (``use_tool_use=False``, default): the model is asked for JSON
  in the prompt; fences are stripped; ``json.loads`` + ``model_validate``
  run. Kept for pipelines that haven't flipped yet and for stubs.
* **Forced tool-use** (``use_tool_use=True``): the gateway builds a
  synthetic tool whose ``input_schema`` is
  ``output_model.model_json_schema()`` and forces ``tool_choice``; the
  model emits the structured object as the tool's ``.input``. No strip,
  no ``json.loads``, no syntactic retry — the invalid-JSON failure class
  goes away. Semantic validators still apply.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generic, Optional, Sequence, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ..services.provider_error_evidence import safe_anthropic_error_code
from .core import CallUsage, MeteringContext, one_call

logger = logging.getLogger("taali.llm.structured")

TModel = TypeVar("TModel", bound=BaseModel)

# A semantic validator: receives the parsed model, may mutate it in place,
# raises ValidationFailure to force a retry. Return value is ignored.
SemanticValidator = Callable[[Any], Any]
RetryMessageBuilder = Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]
InputTokenEstimator = Callable[[list[dict[str, Any]], Any], int]

class ValidationFailure(RuntimeError):
    """Raised by parsing or a semantic validator to trigger a single retry.

    The shared sibling of ``cv_matching.validation.ValidationFailure``;
    in Phase 1 that module re-exports this one so a single ``except``
    catches both.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "semantic_validation_failed",
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class StructuredResult(Generic[TModel]):
    """Outcome of ``generate_structured``. Never raises to the caller."""

    value: Optional[TModel]
    ok: bool
    error_reason: str = ""
    usage: CallUsage = field(default_factory=CallUsage)
    trace_id: str = ""
    cache_hit: bool = False
    retry_count: int = 0
    validation_failures: int = 0


def strip_json_fences(raw: str) -> str:
    """Pull a JSON object out of a possibly-fenced / chatty response.

    Single shared copy of the ``_strip_json_fences`` helper duplicated in
    cv_matching/runner.py, cv_parsing/runner.py, and candidate_search.
    """
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = obj_match.group(0)
    return text


def default_retry_message_builder(
    messages: list[dict[str, Any]], error: str
) -> list[dict[str, Any]]:
    """Append a correction request to the last user message.

    Handles both content shapes: a plain-string ``content`` (cv_parsing)
    and a list of content blocks (cv_matching's cached-block layout).
    Pipelines that need to preserve a specific cached block inject their
    own builder instead.
    """
    suffix = (
        "\n\nYour previous response failed validation with this error:\n"
        + error
        + "\nReturn a corrected JSON response. Do not include any commentary."
    )
    new_messages = [dict(m) for m in messages]
    last = new_messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = content + suffix
    elif isinstance(content, list):
        last["content"] = list(content) + [{"type": "text", "text": suffix}]
    else:
        last["content"] = suffix
    return new_messages


def _extract_text(response: Any) -> str:
    try:
        return response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return ""


def _extract_tool_input(
    response: Any, tool_name: str
) -> Optional[dict[str, Any]]:
    """Pull the first ``tool_use`` block matching ``tool_name`` and return
    its ``.input`` dict. Returns ``None`` when the model emitted text
    instead of using the tool — a refusal-ish failure mode the gateway
    then turns into a ``ValidationFailure`` and retries.
    """
    content = getattr(response, "content", None) or []
    for block in content:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == tool_name
        ):
            inp = getattr(block, "input", None)
            return inp if isinstance(inp, dict) else None
    return None


def _default_tool_name(output_model: type[BaseModel]) -> str:
    """Derive a stable snake_case tool name from the Pydantic class name.

    Acronym-aware: ``CVMatchResult`` -> ``emit_cv_match_result`` (not
    ``emit_c_v_match_result``), ``FooBarBaz`` -> ``emit_foo_bar_baz``.
    Stable across calls so the prompt-cached tool definition stays warm.
    """
    name = output_model.__name__
    # Insert _ between a lower->Upper boundary OR an UPPER->Upper+lower
    # boundary (the acronym-followed-by-word case).
    snake = re.sub(
        r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name
    ).lower()
    return f"emit_{snake}"


def _build_structured_tool(
    output_model: type[BaseModel], tool_name: Optional[str]
) -> tuple[dict[str, Any], str]:
    """Build the synthetic tool definition that forces structured output.

    The tool's ``input_schema`` is ``output_model.model_json_schema()`` —
    one schema source for both the wire contract and post-call validation.
    """
    name = tool_name or _default_tool_name(output_model)
    return (
        {
            "name": name,
            "description": (
                f"Emit the structured {output_model.__name__} object as the tool input. "
                "The tool's input IS the response; do not include any other commentary."
            ),
            "input_schema": output_model.model_json_schema(),
        },
        name,
    )


def _validate_parsed_dict(
    parsed: dict[str, Any],
    output_model: type[TModel],
    semantic_validators: Sequence[SemanticValidator],
) -> TModel:
    """Schema-validate ``parsed`` into ``output_model`` then run semantic
    checks. Raises ``ValidationFailure``. Shared by both text mode (after
    JSON parsing) and tool-use mode (the tool_use ``.input`` is already a
    dict).
    """
    try:
        value = output_model.model_validate(parsed)
    except PydanticValidationError as exc:
        raise ValidationFailure(
            f"Response failed schema: {exc}",
            code="schema_validation_failed",
        ) from exc

    for validator in semantic_validators:
        # May mutate ``value`` in place (e.g. drop unverifiable quotes) and/or
        # raise ValidationFailure to force a retry.
        validator(value)
    return value


def _parse_and_validate(
    raw_text: str,
    output_model: type[TModel],
    semantic_validators: Sequence[SemanticValidator],
) -> TModel:
    """Text mode: JSON -> schema -> semantic validators. Raises ``ValidationFailure``."""
    text = strip_json_fences(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(
            f"Response was not valid JSON: {exc}",
            code="invalid_json",
        ) from exc
    return _validate_parsed_dict(parsed, output_model, semantic_validators)


def structured_tool_params(
    output_model: type[BaseModel], tool_name: Optional[str] = None
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """The ``tools`` / ``tool_choice`` request params forced tool-use mode
    sends, plus the resolved tool name.

    Exposed for callers that render request params without making the call
    themselves — e.g. the Batches API path, which submits requests and
    parses results asynchronously. Using this (rather than hand-building
    the tool) keeps the batch request bit-identical to the sync path's.
    """
    tool_def, name = _build_structured_tool(output_model, tool_name)
    return [tool_def], {"type": "tool", "name": name}, name


def extract_structured_tool_input(
    response: Any,
    output_model: type[TModel],
    *,
    tool_name: str,
    semantic_validators: Sequence[SemanticValidator] = (),
) -> TModel:
    """Pull + validate a forced-tool-use response into ``output_model``.

    The parse/validate half of tool-use mode, for callers that already hold
    the raw message (the Batches API results path). Raises
    ``ValidationFailure`` when the tool_use block is missing or the input
    fails schema / semantic validation.
    """
    tool_input = _extract_tool_input(response, tool_name)
    if tool_input is None:
        raise ValidationFailure(
            f"Model did not emit the expected '{tool_name}' tool_use block",
            code="missing_tool_output",
        )
    return _validate_parsed_dict(tool_input, output_model, semantic_validators)


def parse_structured(
    raw_text: str,
    output_model: type[TModel],
    *,
    semantic_validators: Sequence[SemanticValidator] = (),
) -> TModel:
    """Parse + validate raw model text into ``output_model``.

    The parse/validate half of ``generate_structured``, exposed for callers
    that already hold the model's text and didn't make the call themselves —
    e.g. the Batches API path, which fetches results asynchronously. Raises
    ``ValidationFailure`` on bad JSON, schema failure, or a semantic check.
    """
    return _parse_and_validate(raw_text, output_model, semantic_validators)


def generate_structured(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    output_model: type[TModel],
    metering: MeteringContext,
    max_tokens: int,
    system: Any = None,
    temperature: float = 0.0,
    max_retries: int = 1,
    max_input_tokens: Optional[int] = None,
    estimate_input_tokens: Optional[InputTokenEstimator] = None,
    cache_key: Optional[str] = None,
    cache_get: Optional[Callable[[str], Optional[TModel]]] = None,
    cache_set: Optional[Callable[[str, TModel], None]] = None,
    semantic_validators: Sequence[SemanticValidator] = (),
    retry_message_builder: Optional[RetryMessageBuilder] = None,
    use_tool_use: bool = False,
    tool_name: Optional[str] = None,
    before_provider_call: Optional[Callable[[int], None]] = None,
) -> StructuredResult[TModel]:
    """Run one structured generation end-to-end. Never raises.

    ``use_tool_use=True`` enables forced tool-use structured output: the
    gateway builds a synthetic tool whose ``input_schema`` is
    ``output_model.model_json_schema()`` and forces ``tool_choice``; the
    model emits the response as the tool's ``.input`` dict, bypassing the
    strip/parse step. This eliminates the invalid-JSON failure class.
    Semantic validators still apply. Default ``False`` keeps text mode so
    pipelines opt in one at a time.

    On any provider failure (client error, ceiling exceeded, unrecoverable
    validation) returns ``StructuredResult(ok=False, error_reason=...)``
    with whatever token usage was incurred.  ``before_provider_call`` runs
    immediately before every attempt, including validation retries; its
    exception deliberately propagates so the caller can roll back/defer the
    surrounding unit of work.
    """
    trace_id = metering.trace_id or str(uuid.uuid4())
    metering = replace(metering, trace_id=trace_id)
    usage = CallUsage()

    # Tool-use mode: build the synthetic tool + forced tool_choice once,
    # reuse across retries so the cached tool definition stays warm.
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[dict[str, Any]] = None
    resolved_tool_name: Optional[str] = None
    if use_tool_use:
        tool_def, resolved_tool_name = _build_structured_tool(output_model, tool_name)
        tools = [tool_def]
        tool_choice = {"type": "tool", "name": resolved_tool_name}

    # 1. Cache lookup
    if cache_key and cache_get is not None:
        cached = cache_get(cache_key)
        if cached is not None:
            return StructuredResult(
                value=cached, ok=True, usage=usage, trace_id=trace_id, cache_hit=True
            )

    # 2. Input ceiling (caller supplies the estimator so the gateway stays
    #    free of any tokenizer dependency).
    if max_input_tokens is not None and estimate_input_tokens is not None:
        counted = estimate_input_tokens(messages, system)
        if counted > max_input_tokens:
            return StructuredResult(
                value=None,
                ok=False,
                error_reason=(
                    f"input_token_ceiling_exceeded: counted={counted}, "
                    f"ceiling={max_input_tokens}"
                ),
                usage=usage,
                trace_id=trace_id,
            )

    builder = retry_message_builder or default_retry_message_builder
    current_messages = messages
    last_err = ""
    last_error_code = "semantic_validation_failed"
    value: Optional[TModel] = None
    retry_count = 0
    validation_failures = 0

    # 3. Call with at most ``max_retries`` validation retries.
    for attempt in range(max_retries + 1):
        if before_provider_call is not None:
            # Keep the authority callback outside the provider-error catch.
            # A Pause is a control decision, not a failed model response.
            before_provider_call(attempt)
        try:
            response = one_call(
                client,
                model=model,
                system=system,
                messages=current_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                metering=metering,
                tools=tools,
                tool_choice=tool_choice,
                retry_attempt=attempt,
                usage_sink=usage,
            )
        except Exception as exc:
            error_reason = safe_anthropic_error_code(
                exc,
                operation="claude_call_failed",
            )
            logger.warning(
                "gateway call failed attempt=%d error_code=%s",
                attempt + 1,
                error_reason,
            )
            return StructuredResult(
                value=None,
                ok=False,
                error_reason=error_reason,
                usage=usage,
                trace_id=trace_id,
                retry_count=retry_count,
                validation_failures=validation_failures,
            )

        try:
            if use_tool_use:
                tool_input = _extract_tool_input(response, resolved_tool_name or "")
                if tool_input is None:
                    raise ValidationFailure(
                        f"Model did not emit the expected '{resolved_tool_name}' tool_use block",
                        code="missing_tool_output",
                    )
                value = _validate_parsed_dict(
                    tool_input, output_model, semantic_validators
                )
            else:
                value = _parse_and_validate(
                    _extract_text(response), output_model, semantic_validators
                )
            break
        except ValidationFailure as exc:
            validation_failures += 1
            last_err = str(exc)
            last_error_code = exc.code
            logger.info(
                "gateway validation failed attempt=%d error_code=%s",
                attempt + 1,
                last_error_code,
            )
            if attempt >= max_retries:
                value = None
                break
            retry_count += 1
            current_messages = builder(messages, last_err)

    if value is None:
        return StructuredResult(
            value=None,
            ok=False,
            error_reason=f"validation_failed_after_retry:{last_error_code}",
            usage=usage,
            trace_id=trace_id,
            retry_count=retry_count,
            validation_failures=validation_failures,
        )

    # 4. Cache write
    if cache_key and cache_set is not None:
        try:
            cache_set(cache_key, value)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "gateway cache write failed trace_id=%s error_type=%s",
                trace_id,
                type(exc).__name__,
            )

    return StructuredResult(
        value=value,
        ok=True,
        usage=usage,
        trace_id=trace_id,
        retry_count=retry_count,
        validation_failures=validation_failures,
    )
