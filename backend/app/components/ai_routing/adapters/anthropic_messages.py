"""Anthropic Messages adapter for a precomputed Tali route decision.

This compatibility adapter deliberately reuses the existing metered Anthropic
client.  It does not translate Anthropic message/tool/citation shapes into a
lossy provider-neutral schema.  Its responsibilities are narrower and strict:
enforce the chosen deployment and task limits, attach route provenance to the
existing meter, and bracket every physical create/stream operation with durable
attempt telemetry.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..admission import AttemptAdmission, admit_attempt
from ..anthropic_estimation import estimate_anthropic_messages
from ..contracts import Capability, ExecutionMode
from ..execution import PhysicalAttempt, RouteExecution, RouteExecutionError


class AnthropicRouteContractError(RouteExecutionError):
    """A caller tried to escape the immutable Anthropic route contract."""

    provider_not_called = True


class AnthropicRouteOutcomeError(RouteExecutionError):
    """The provider returned an outcome outside the admitted route contract."""


_ALLOWED_CALL_KWARGS = frozenset(
    {
        "max_tokens",
        "messages",
        "metering",
        "model",
        "system",
        "temperature",
        "timeout",
        "tool_choice",
        "tools",
    }
)


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, key) for item in value)
    return False


def _contains_citations_document(value: Any) -> bool:
    if isinstance(value, dict):
        citations = value.get("citations")
        if (
            value.get("type") == "document"
            and isinstance(citations, dict)
            and citations.get("enabled") is True
        ):
            return True
        return any(_contains_citations_document(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_citations_document(item) for item in value)
    return False


class RoutedAnthropicClient:
    """Transparent Anthropic client proxy bound to one route execution."""

    def __init__(self, inner: Any, execution: RouteExecution) -> None:
        if getattr(inner, "ai_routing_metered_transport", None) is not True:
            raise AnthropicRouteContractError(
                "routed Anthropic calls require the metered transport boundary"
            )
        if getattr(inner, "ai_routing_sdk_max_retries", None) != 0:
            raise AnthropicRouteContractError(
                "routed Anthropic calls require SDK max_retries=0"
            )
        if not hasattr(inner, "organization_id"):
            raise AnthropicRouteContractError(
                "routed Anthropic transports must expose their organization binding"
            )
        bound_org_id = inner.organization_id
        route_org_id = execution.attribution.organization_id
        if bound_org_id is not None and (
            route_org_id is None or int(bound_org_id) != int(route_org_id)
        ):
            raise AnthropicRouteContractError(
                "routed Anthropic transport organization differs from route attribution"
            )
        self._inner = inner
        self.execution = execution
        self.messages = _RoutedMessages(
            inner=inner.messages,
            execution=execution,
        )

class _RoutedMessages:
    def __init__(self, *, inner: Any, execution: RouteExecution) -> None:
        self._inner = inner
        self._execution = execution
        self._seen_reservation_refs: set[str] = set()

    def create(self, **kwargs: Any) -> Any:
        self._validate_call(kwargs, expected_mode=ExecutionMode.SYNC)
        request_estimate = estimate_anthropic_messages(
            messages=kwargs.get("messages"),
            max_tokens=kwargs["max_tokens"],
            system=kwargs.get("system"),
            tools=kwargs.get("tools"),
            tool_choice=kwargs.get("tool_choice"),
        )
        base_kwargs = dict(kwargs)
        start_new_iteration = True
        while True:
            plan = self._execution.plan_next_attempt(
                start_new_iteration=start_new_iteration
            )
            try:
                admission = admit_attempt(
                    self._execution,
                    plan,
                    base_kwargs.get("metering"),
                    request_estimate=request_estimate,
                )
            except BaseException:
                self._execution.cancel_planned_attempt(plan)
                raise
            self._validate_reservation(admission.metering)
            admitted_kwargs = {**base_kwargs, "metering": admission.metering}
            try:
                attempt = self._execution.begin_attempt(
                    plan,
                    admitted_budget=admission.admitted_budget,
                )
            except BaseException:
                admission.release_unstarted(reason="routing_attempt_telemetry_failed")
                self._execution.cancel_planned_attempt(plan)
                raise
            try:
                routed_kwargs = self._route_kwargs(admitted_kwargs, attempt)
            except BaseException as exc:
                admission.release_unstarted(reason="routing_request_render_failed")
                self._execution.finish_error(attempt, exc)
                raise
            try:
                admission.mark_provider_started(
                    provider="anthropic",
                    attempt_ref=f"{self._execution.invocation_id}:{attempt.ordinal}",
                )
            except BaseException as exc:
                admission.release_before_transport(
                    reason="routing_attempt_marker_failed_before_transport"
                )
                self._execution.finish_error(attempt, exc)
                raise
            try:
                response = self._inner.create(**routed_kwargs)
            except BaseException as exc:
                self._remember_reservation(routed_kwargs.get("metering"))
                if bool(getattr(exc, "provider_not_called", False)):
                    admission.release_before_transport(
                        reason="routing_transport_rejected_before_provider"
                    )
                else:
                    admission.release_if_definitely_nonbillable(exc)
                result = self._execution.finish_error(attempt, exc)
                if result.next_attempt is None:
                    raise
                self._sleep_before_retry(exc, attempt.attempt_in_iteration)
                start_new_iteration = False
                continue
            try:
                self._validate_response_contract(response, attempt)
            except BaseException as exc:
                self._remember_reservation(routed_kwargs.get("metering"))
                self._execution.finish_error(attempt, exc)
                raise
            self._remember_reservation(routed_kwargs.get("metering"))
            self._execution.finish_success(attempt, response)
            return response

    def stream(self, **kwargs: Any) -> "_RoutedStreamContext":
        self._validate_call(kwargs, expected_mode=ExecutionMode.STREAM)
        self._validate_reservation(kwargs.get("metering"))
        return _RoutedStreamContext(messages=self, kwargs=dict(kwargs))

    def _validate_call(
        self, kwargs: dict[str, Any], *, expected_mode: ExecutionMode
    ) -> None:
        decision = self._execution.decision
        unknown = sorted(set(kwargs).difference(_ALLOWED_CALL_KWARGS))
        if unknown:
            raise AnthropicRouteContractError(
                "unsupported Anthropic Messages route parameters: "
                + ", ".join(unknown)
            )
        if decision.execution_mode is not expected_mode:
            raise AnthropicRouteContractError(
                f"task {decision.task.value!r} requires "
                f"{decision.execution_mode.value}, not {expected_mode.value}"
            )
        requested_model = kwargs.get("model")
        if not isinstance(requested_model, str) or not requested_model.strip():
            raise AnthropicRouteContractError(
                "Anthropic Messages calls require the routed model identifier"
            )
        if requested_model.strip() != self._execution.selected_model_id:
            raise AnthropicRouteContractError(
                "caller model differs from the selected route deployment"
            )
        requested_output = kwargs.get("max_tokens")
        if isinstance(requested_output, bool) or not isinstance(requested_output, int):
            raise AnthropicRouteContractError(
                "Anthropic Messages calls require an integer max_tokens"
            )
        if (
            requested_output <= 0
            or requested_output > decision.limits.max_output_tokens
        ):
            raise AnthropicRouteContractError(
                "provider call exceeds the route output-token ceiling"
            )
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or any(
            not isinstance(message, dict) for message in messages
        ):
            raise AnthropicRouteContractError(
                "Anthropic Messages calls require a list of message mappings"
            )
        system = kwargs.get("system")
        if system is not None and not isinstance(system, (str, list)):
            raise AnthropicRouteContractError(
                "Anthropic Messages system must be text or content blocks"
            )
        tools = kwargs.get("tools")
        if tools is not None:
            if not isinstance(tools, list) or any(
                not isinstance(tool, dict) for tool in tools
            ):
                raise AnthropicRouteContractError(
                    "Anthropic Messages tools must be a list of mappings"
                )
            if Capability.TOOLS not in decision.required_capabilities:
                raise AnthropicRouteContractError(
                    "the routed task contract does not authorize tools"
                )
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None and (
            not isinstance(tool_choice, dict) or tools is None
        ):
            raise AnthropicRouteContractError(
                "tool_choice requires an authorized tools request"
            )
        shape = decision.request_shape
        if shape.require_tools and not tools:
            raise AnthropicRouteContractError(
                "the routed task requires a non-empty tools request"
            )
        if shape.require_forced_tool_choice:
            selected_tool = (
                tool_choice.get("name") if isinstance(tool_choice, dict) else None
            )
            tool_names = {
                tool.get("name")
                for tool in (tools or [])
                if isinstance(tool.get("name"), str)
            }
            if (
                not isinstance(tool_choice, dict)
                or tool_choice.get("type") != "tool"
                or not isinstance(selected_tool, str)
                or not selected_tool.strip()
                or selected_tool not in tool_names
            ):
                raise AnthropicRouteContractError(
                    "the routed task requires a forced, declared tool choice"
                )
        if shape.require_citations_document and not _contains_citations_document(
            messages
        ):
            raise AnthropicRouteContractError(
                "the routed task requires a citations-enabled document"
            )
        if any(
            _contains_key(value, "cache_control")
            for value in (messages, system, tools)
        ) and Capability.PROMPT_CACHING not in decision.required_capabilities:
            raise AnthropicRouteContractError(
                "the routed task contract does not authorize prompt caching"
            )
        temperature = kwargs.get("temperature", 0.0)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise AnthropicRouteContractError(
                "Anthropic Messages temperature must be numeric"
            )
        if not math.isfinite(float(temperature)) or not 0 <= float(temperature) <= 1:
            raise AnthropicRouteContractError(
                "Anthropic Messages temperature must be finite and between 0 and 1"
            )
        metering = kwargs.get("metering")
        if metering is not None and not isinstance(metering, dict):
            raise AnthropicRouteContractError("metering must be a mapping")
        metadata = (metering or {}).get("metadata", {})
        if metadata is not None and not isinstance(metadata, dict):
            raise AnthropicRouteContractError("metering metadata must be a mapping")
        if isinstance(metadata, dict) and "ai_routing" in metadata:
            raise AnthropicRouteContractError(
                "ai_routing is adapter-owned metering metadata"
            )
        if kwargs.get("timeout") is not None:
            timeout_value = kwargs["timeout"]
            if isinstance(timeout_value, bool) or not isinstance(
                timeout_value, (int, float)
            ):
                raise AnthropicRouteContractError(
                    "Anthropic Messages timeout must be numeric"
                )
            timeout = float(timeout_value)
            if not math.isfinite(timeout) or timeout <= 0:
                raise AnthropicRouteContractError(
                    "Anthropic Messages timeout must be finite and positive"
                )

    def _route_kwargs(
        self, kwargs: dict[str, Any], attempt: PhysicalAttempt
    ) -> dict[str, Any]:
        routed = dict(kwargs)
        routed["model"] = attempt.deployment.model_id
        route_timeout = self._execution.remaining_iteration_timeout_s()
        if route_timeout <= 0:
            raise AnthropicRouteContractError(
                "logical iteration latency ceiling was reached"
            )
        caller_timeout = routed.get("timeout")
        if caller_timeout is not None:
            try:
                route_timeout = min(route_timeout, float(caller_timeout))
            except (TypeError, ValueError) as exc:
                raise AnthropicRouteContractError(
                    "Anthropic Messages timeout must be numeric"
                ) from exc
        routed["timeout"] = route_timeout
        region = (self._execution.request.region or "global").strip().lower()
        if region == "us":
            routed["inference_geo"] = "us"
        meter = dict(routed.get("metering") or {})
        meter["feature"] = self._execution.decision.feature

        attribution = self._execution.attribution
        if attribution.organization_id is not None:
            meter["organization_id"] = attribution.organization_id
        if attribution.user_id is not None:
            meter["user_id"] = attribution.user_id
        else:
            meter.pop("user_id", None)
        if attribution.role_id is not None:
            meter["role_id"] = attribution.role_id
        else:
            meter.pop("role_id", None)
        if attribution.entity_id is not None:
            meter["entity_id"] = attribution.entity_id
        else:
            meter.pop("entity_id", None)

        caller_trace_id = meter.get("trace_id")
        route_metadata = self._execution.routing_metadata(attempt)
        if caller_trace_id:
            route_metadata["caller_trace_id"] = str(caller_trace_id)
        metadata = dict(meter.get("metadata") or {})
        metadata["ai_routing"] = route_metadata
        meter["metadata"] = metadata
        # One unique trace per physical call lets the generic attempt link the
        # independently committed UsageEvent and ClaudeCallLog rows exactly.
        meter["trace_id"] = attempt.trace_id
        meter["retry_attempt"] = attempt.attempt_in_iteration - 1
        routed["metering"] = meter
        return routed

    def _validate_response_contract(
        self, response: Any, attempt: PhysicalAttempt
    ) -> None:
        actual_model = getattr(response, "model", None)
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise AnthropicRouteOutcomeError(
                "provider response omitted its executed model identity"
            )
        if actual_model.strip() != attempt.deployment.model_id:
            raise AnthropicRouteOutcomeError(
                "provider response model differs from the admitted deployment"
            )
        expected = (self._execution.request.region or "global").strip().lower()
        usage = getattr(response, "usage", None)
        actual = getattr(usage, "inference_geo", None)
        if expected != "us" and actual is None:
            return
        if actual is None:
            raise AnthropicRouteOutcomeError(
                "US-routed provider response omitted inference-region evidence"
            )
        normalized = str(actual).strip().lower()
        if normalized != expected:
            raise AnthropicRouteOutcomeError(
                "provider response inference region differs from the admitted route"
            )

    def _sleep_before_retry(self, error: BaseException, attempt_number: int) -> None:
        limits = self._execution.decision.limits
        base_ms = max(int(getattr(limits, "retry_backoff_base_ms", 0)), 0)
        max_ms = max(int(getattr(limits, "retry_backoff_max_ms", base_ms)), base_ms)
        delay_ms = min(base_ms * (2 ** max(attempt_number - 1, 0)), max_ms)
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                retry_after_ms = int(float(headers.get("retry-after", 0)) * 1000)
            except (AttributeError, TypeError, ValueError, OverflowError):
                retry_after_ms = 0
            delay_ms = min(max(delay_ms, retry_after_ms), max_ms)
        if delay_ms:
            remaining_ms = int(
                self._execution.remaining_iteration_timeout_s() * 1000
            )
            if remaining_ms <= 0:
                return
            time.sleep(min(delay_ms, remaining_ms) / 1000.0)

    def _validate_reservation(self, metering: Any) -> None:
        if metering is None:
            return
        if not isinstance(metering, dict):
            raise AnthropicRouteContractError("metering must be a mapping")
        payload = metering.get("credit_reservation")
        if payload is None:
            return
        if not isinstance(payload, dict):
            raise AnthropicRouteContractError(
                "credit_reservation must be a serialized reservation"
            )
        external_ref = str(payload.get("external_ref") or "").strip()
        if not external_ref:
            raise AnthropicRouteContractError(
                "credit_reservation requires an external_ref"
            )
        if external_ref in self._seen_reservation_refs:
            raise AnthropicRouteContractError(
                "one credit reservation cannot fund two physical attempts"
            )

    def _remember_reservation(self, metering: Any) -> None:
        if not isinstance(metering, dict):
            return
        payload = metering.get("credit_reservation")
        if not isinstance(payload, dict):
            return
        external_ref = str(payload.get("external_ref") or "").strip()
        if external_ref:
            self._seen_reservation_refs.add(external_ref)


class _RoutedStreamContext:
    """Start telemetry on context entry and never fail over after acceptance."""

    def __init__(self, *, messages: _RoutedMessages, kwargs: dict[str, Any]) -> None:
        self._messages = messages
        self._kwargs = kwargs
        self._attempt: PhysicalAttempt | None = None
        self._inner_context: Any = None
        self._stream: Any = None
        self._accepted = False
        self._admission: AttemptAdmission | None = None

    def __enter__(self) -> Any:
        request_estimate = estimate_anthropic_messages(
            messages=self._kwargs.get("messages"),
            max_tokens=self._kwargs["max_tokens"],
            system=self._kwargs.get("system"),
            tools=self._kwargs.get("tools"),
            tool_choice=self._kwargs.get("tool_choice"),
        )
        base_kwargs = dict(self._kwargs)
        start_new_iteration = True
        while True:
            plan = self._messages._execution.plan_next_attempt(
                start_new_iteration=start_new_iteration
            )
            try:
                admission = admit_attempt(
                    self._messages._execution,
                    plan,
                    base_kwargs.get("metering"),
                    request_estimate=request_estimate,
                )
            except BaseException:
                self._messages._execution.cancel_planned_attempt(plan)
                raise
            self._messages._validate_reservation(admission.metering)
            admitted_kwargs = {**base_kwargs, "metering": admission.metering}
            self._admission = admission
            try:
                attempt = self._messages._execution.begin_attempt(
                    plan,
                    admitted_budget=admission.admitted_budget,
                )
            except BaseException:
                admission.release_unstarted(reason="routing_stream_telemetry_failed")
                self._messages._execution.cancel_planned_attempt(plan)
                raise
            try:
                routed_kwargs = self._messages._route_kwargs(
                    admitted_kwargs, attempt
                )
            except BaseException as exc:
                admission.release_unstarted(reason="routing_stream_render_failed")
                self._messages._execution.finish_error(attempt, exc)
                raise
            try:
                admission.mark_provider_started(
                    provider="anthropic",
                    attempt_ref=(
                        f"{self._messages._execution.invocation_id}:{attempt.ordinal}"
                    ),
                )
            except BaseException as exc:
                admission.release_before_transport(
                    reason="routing_stream_marker_failed_before_transport"
                )
                self._messages._execution.finish_error(attempt, exc)
                raise
            try:
                inner_context = self._messages._inner.stream(**routed_kwargs)
                stream = inner_context.__enter__()
            except BaseException as exc:
                self._messages._remember_reservation(routed_kwargs.get("metering"))
                if bool(getattr(exc, "provider_not_called", False)):
                    admission.release_before_transport(
                        reason="routing_stream_rejected_before_provider"
                    )
                else:
                    admission.release_if_definitely_nonbillable(exc)
                result = self._messages._execution.finish_error(
                    attempt,
                    exc,
                    stream_accepted=False,
                )
                if result.next_attempt is None:
                    raise
                self._messages._sleep_before_retry(
                    exc, attempt.attempt_in_iteration
                )
                start_new_iteration = False
                continue
            self._attempt = attempt
            self._inner_context = inner_context
            self._stream = stream
            self._kwargs = routed_kwargs
            self._accepted = True
            return stream

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        attempt = self._attempt
        if attempt is None or self._inner_context is None:
            return False
        try:
            suppressed = bool(
                self._inner_context.__exit__(exc_type, exc_value, traceback)
            )
        except BaseException as exit_error:
            self._messages._remember_reservation(self._kwargs.get("metering"))
            self._messages._execution.finish_error(
                attempt,
                exit_error,
                stream_accepted=self._accepted,
            )
            raise

        self._messages._remember_reservation(self._kwargs.get("metering"))
        if exc_type is not None:
            error = exc_value or RuntimeError("stream iteration failed")
            self._messages._execution.finish_error(
                attempt,
                error,
                stream_accepted=self._accepted,
            )
            return suppressed

        try:
            final_message = self._stream.get_final_message()
        except BaseException as final_error:
            self._messages._execution.finish_error(
                attempt,
                final_error,
                stream_accepted=True,
            )
            raise
        try:
            self._messages._validate_response_contract(final_message, attempt)
        except BaseException as region_error:
            self._messages._execution.finish_error(
                attempt,
                region_error,
                stream_accepted=True,
            )
            raise
        self._messages._execution.finish_success(attempt, final_message)
        return suppressed


__all__ = ["AnthropicRouteContractError", "RoutedAnthropicClient"]
