"""Shared metered-call primitive for every LLM pipeline.

``one_call`` is the single place that builds the ``metering`` kwarg the
``MeteredAnthropicClient`` reads, fires one ``messages.create``, and
accumulates token usage. Both call shapes use it:

* single-response pipelines (cv_matching, cv_parsing, candidate_search,
  pre_screen, the claude integration) via ``llm.structured.generate_structured``;
* the multi-round agent loop (``agent_runtime.orchestrator``) and chat,
  which keep their own loop but call ``one_call`` once per round with a
  shared ``usage_sink``.

This module is a leaf: it imports nothing from ``app``. The Anthropic
client is always injected, so the metering wrapper, model constants, and
pricing layer stay above it. That dependency direction is what lets the
feature pipelines import the gateway *down* instead of reaching *up* into
``services`` (the function-level-import smell this is meant to retire).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CallUsage:
    """Token tallies accumulated across the calls of one logical operation.

    Lifts the per-round accumulation that ``cv_matching/runner.py`` did on
    ``_RunContext`` and the agent orchestrator did inline on the
    ``AgentRun`` row. ``add_response`` is tolerant of a missing/partial
    ``usage`` object (stub clients, error responses) so callers never have
    to guard it.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def add_response(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self.cache_read_tokens += int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        )
        self.cache_creation_tokens += int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )


@dataclass
class MeteringContext:
    """Typed view over the ``metering`` dict the wrapper consumes.

    ``feature`` is required for every paid round. ``skip=True`` exists only for
    injected non-provider test/eval clients; ``MeteredAnthropicClient`` rejects
    it before any SDK call.
    Retry threading (``retry_attempt`` / ``trace_id``) is added per-call
    by ``as_dict`` so ``claude_call_log`` rows chain across retries.
    """

    feature: Any = ""
    organization_id: Optional[int] = None
    role_id: Optional[int] = None
    entity_id: Optional[str] = None
    candidate_id: Optional[int] = None
    user_id: Optional[int] = None
    trace_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    credit_reservation: Optional[dict[str, Any]] = None
    require_role_authority: bool = False
    skip: bool = False
    metered_by: Optional[str] = None

    @classmethod
    def skipped(cls, *, metered_by: str, trace_id: Optional[str] = None) -> "MeteringContext":
        return cls(skip=True, metered_by=metered_by, trace_id=trace_id)

    @classmethod
    def from_dict(
        cls, meter: Optional[dict[str, Any]], *, default_feature: Any = None
    ) -> "MeteringContext":
        """Adapt a legacy ``metering`` dict to a context.

        Migration aid: pipelines that already thread a ``metering`` dict
        (``{"feature": ..., "organization_id": ..., ...}``) can adopt the
        gateway without changing their public signature. ``default_feature``
        backfills the feature label when the caller passed ``None`` or a
        dict without one (matching the old ``metering or {"feature": X}``
        fallbacks).
        """
        if not meter:
            return cls(feature=default_feature)
        if meter.get("skip"):
            return cls(
                skip=True,
                metered_by=meter.get("metered_by"),
                trace_id=meter.get("trace_id"),
            )
        return cls(
            feature=meter.get("feature", default_feature),
            organization_id=meter.get("organization_id"),
            role_id=meter.get("role_id"),
            entity_id=meter.get("entity_id"),
            candidate_id=meter.get("candidate_id"),
            user_id=meter.get("user_id"),
            trace_id=meter.get("trace_id"),
            metadata=meter.get("metadata"),
            credit_reservation=meter.get("credit_reservation"),
            require_role_authority=meter.get("require_role_authority", False),
        )

    def as_dict(self, *, retry_attempt: int = 0) -> dict[str, Any]:
        if self.skip:
            out: dict[str, Any] = {"skip": True}
            if self.metered_by:
                out["metered_by"] = self.metered_by
        else:
            out = {"feature": self.feature}
            for field in (
                "organization_id",
                "role_id",
                "candidate_id",
                "user_id",
            ):
                value = getattr(self, field)
                if value is not None:
                    if type(value) is not int or value <= 0:
                        raise ValueError(f"{field} must be a positive integer")
                    out[field] = value
            if self.entity_id is not None:
                if type(self.entity_id) is not str or not self.entity_id.strip():
                    raise ValueError("entity_id must be a non-empty string")
                out["entity_id"] = self.entity_id
            if self.metadata is not None:
                if type(self.metadata) is not dict:
                    raise ValueError("metering metadata must be an object")
                out["metadata"] = dict(self.metadata)
            if self.credit_reservation is not None:
                if type(self.credit_reservation) is not dict:
                    raise ValueError("credit_reservation must be an object")
                out["credit_reservation"] = dict(self.credit_reservation)
            if type(self.require_role_authority) is not bool:
                raise ValueError("require_role_authority must be boolean")
            if self.require_role_authority:
                out["require_role_authority"] = True
        if self.trace_id:
            out["trace_id"] = str(self.trace_id)
        if retry_attempt:
            out["retry_attempt"] = int(retry_attempt)
        return out


def one_call_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    system: Any = None,
    temperature: float = 0.0,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the exact provider request, excluding local metering context."""

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system is not None:
        kwargs["system"] = system
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return kwargs


def one_call(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    metering: MeteringContext,
    system: Any = None,
    temperature: float = 0.0,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[dict[str, Any]] = None,
    retry_attempt: int = 0,
    usage_sink: Optional[CallUsage] = None,
) -> Any:
    """Fire one metered ``messages.create`` and return the raw response."""

    kwargs = one_call_request(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        system=system,
        temperature=temperature,
        tools=tools,
        tool_choice=tool_choice,
    )
    kwargs["metering"] = metering.as_dict(retry_attempt=retry_attempt)

    response = client.messages.create(**kwargs)
    if usage_sink is not None:
        usage_sink.add_response(response)
    return response
