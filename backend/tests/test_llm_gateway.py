"""Tests for the shared LLM gateway (app.llm) — Phase 0, text mode.

Proves parity with the parse/validate/retry/cache behaviour the
single-shot pipelines (cv_matching/runner.py, cv_parsing/runner.py)
currently each reimplement, using a stub Anthropic client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from app.llm import (
    CallUsage,
    MeteringContext,
    ProviderAuthorityError,
    StructuredResult,
    ValidationFailure,
    generate_structured,
    one_call,
)


# --------------------------------------------------------------------------- #
# Stub Anthropic client (mirrors tests/test_cv_matching_runner.py)            #
# --------------------------------------------------------------------------- #


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubUsage:
    input_tokens: int = 100
    output_tokens: int = 200
    cache_read_input_tokens: int = 10
    cache_creation_input_tokens: int = 5


@dataclass
class _StubResponse:
    text: str

    @property
    def content(self):
        return [_StubBlock(text=self.text)]

    @property
    def usage(self):
        return _StubUsage()


@dataclass
class _StubMessages:
    responses: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        return _StubResponse(text=self.responses[min(idx, len(self.responses) - 1)])


@dataclass
class _StubClient:
    messages: _StubMessages


def _stub(responses: list[str]) -> _StubClient:
    return _StubClient(messages=_StubMessages(responses=responses))


class _Model(BaseModel):
    score: int
    label: str


def _payload(score: int = 80, label: str = "ok") -> str:
    return json.dumps({"score": score, "label": label})


def _metering() -> MeteringContext:
    return MeteringContext(feature="test", organization_id=7, entity_id="app:1")


# --------------------------------------------------------------------------- #
# one_call                                                                     #
# --------------------------------------------------------------------------- #


def test_one_call_builds_metering_dict_and_accumulates_usage():
    client = _stub([_payload()])
    sink = CallUsage()
    one_call(
        client,
        model="claude-haiku",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=512,
        metering=_metering(),
        retry_attempt=2,
        usage_sink=sink,
    )
    sent = client.messages.calls[0]
    assert sent["metering"]["feature"] == "test"
    assert sent["metering"]["organization_id"] == 7
    assert sent["metering"]["entity_id"] == "app:1"
    assert sent["metering"]["retry_attempt"] == 2
    assert sent["model"] == "claude-haiku"
    assert sent["system"] == "sys"
    # usage accumulated from the stub response
    assert sink.input_tokens == 100
    assert sink.output_tokens == 200
    assert sink.cache_read_tokens == 10
    assert sink.cache_creation_tokens == 5


def test_metering_context_threads_autonomous_authority_requirement():
    context = MeteringContext(
        feature="score",
        organization_id=7,
        role_id=9,
        require_role_authority=True,
    )

    payload = context.as_dict()
    rebuilt = MeteringContext.from_dict(payload)

    assert payload["require_role_authority"] is True
    assert rebuilt.require_role_authority is True


def test_one_call_skip_metering_shape():
    client = _stub([_payload()])
    one_call(
        client,
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10,
        metering=MeteringContext.skipped(metered_by="caller.x"),
    )
    meter = client.messages.calls[0]["metering"]
    assert meter == {"skip": True, "metered_by": "caller.x"}
    # system/tools omitted when not supplied
    assert "system" not in client.messages.calls[0]
    assert "tools" not in client.messages.calls[0]


# --------------------------------------------------------------------------- #
# generate_structured — happy path + caching                                   #
# --------------------------------------------------------------------------- #


def test_generate_structured_happy_path():
    client = _stub([_payload(score=91, label="great")])
    res: StructuredResult[_Model] = generate_structured(
        client,
        model="m",
        system="sys",
        messages=[{"role": "user", "content": "score it"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=256,
    )
    assert res.ok is True
    assert res.value is not None and res.value.score == 91 and res.value.label == "great"
    assert res.retry_count == 0
    assert res.cache_hit is False
    assert res.usage.output_tokens == 200
    assert res.trace_id  # populated


def test_generate_structured_strips_fences():
    client = _stub(["```json\n" + _payload() + "\n```"])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
    )
    assert res.ok is True and res.value.score == 80


def test_generate_structured_cache_hit_short_circuits():
    client = _stub([_payload()])
    cached = _Model(score=5, label="cached")
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        cache_key="k1",
        cache_get=lambda k: cached,
        cache_set=lambda k, v: None,
    )
    assert res.cache_hit is True and res.value.label == "cached"
    assert client.messages.calls == []  # never called the model


def test_generate_structured_writes_cache_on_success():
    client = _stub([_payload(score=42)])
    written: dict[str, Any] = {}
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        cache_key="k2",
        cache_get=lambda k: None,
        cache_set=lambda k, v: written.update({k: v}),
    )
    assert res.ok is True
    assert "k2" in written and written["k2"].score == 42


# --------------------------------------------------------------------------- #
# generate_structured — retry behaviour                                         #
# --------------------------------------------------------------------------- #


def test_generate_structured_retries_invalid_json_then_succeeds():
    client = _stub(["not json at all", _payload(score=77)])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
    )
    assert res.ok is True and res.value.score == 77
    assert res.retry_count == 1 and res.validation_failures == 1
    assert len(client.messages.calls) == 2
    # retry threaded retry_attempt=1 into metering
    assert client.messages.calls[1]["metering"]["retry_attempt"] == 1


def test_generate_structured_rechecks_authority_before_validation_retry():
    client = _stub(["not json at all", _payload(score=77)])
    attempts: list[int] = []

    def authorize(attempt: int) -> None:
        attempts.append(attempt)
        if attempt == 1:
            raise RuntimeError("workspace paused")

    with pytest.raises(RuntimeError, match="workspace paused"):
        generate_structured(
            client,
            model="m",
            messages=[{"role": "user", "content": "x"}],
            output_model=_Model,
            metering=_metering(),
            max_tokens=64,
            before_provider_call=authorize,
        )

    assert attempts == [0, 1]
    # Attempt 1 completed and failed validation; Pause prevented attempt 2.
    assert len(client.messages.calls) == 1


def test_generate_structured_propagates_provider_authority_denial():
    class _DeniedMessages:
        def create(self, **_kwargs):
            raise ProviderAuthorityError("role agent is paused")

    client = _StubClient(messages=_DeniedMessages())  # type: ignore[arg-type]

    with pytest.raises(ProviderAuthorityError, match="paused"):
        generate_structured(
            client,
            model="m",
            messages=[{"role": "user", "content": "x"}],
            output_model=_Model,
            metering=MeteringContext(
                feature="score",
                organization_id=7,
                role_id=9,
                require_role_authority=True,
            ),
            max_tokens=64,
        )


def test_generate_structured_invalid_json_both_attempts_fails():
    client = _stub(["nope", "still nope"])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
    )
    assert res.ok is False and res.value is None
    assert "validation_failed_after_retry" in res.error_reason
    assert res.validation_failures == 2
    # usage still accumulated across both failed attempts
    assert res.usage.output_tokens == 400


def test_generate_structured_schema_failure_retries():
    client = _stub([json.dumps({"score": "not-an-int"}), _payload(score=12)])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
    )
    assert res.ok is True and res.value.score == 12 and res.retry_count == 1


# --------------------------------------------------------------------------- #
# semantic validators (mutate-in-place AND raise-to-retry)                      #
# --------------------------------------------------------------------------- #


def test_semantic_validator_can_mutate_in_place():
    client = _stub([_payload(label="dirty")])

    def _sanitise(value: _Model) -> None:
        value.label = value.label.upper()

    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        semantic_validators=[_sanitise],
    )
    assert res.ok is True and res.value.label == "DIRTY"


def test_semantic_validator_raise_triggers_retry():
    client = _stub([_payload(score=10), _payload(score=90)])

    def _require_high(value: _Model) -> None:
        if value.score < 50:
            raise ValidationFailure("score too low")

    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        semantic_validators=[_require_high],
    )
    assert res.ok is True and res.value.score == 90 and res.retry_count == 1


def test_custom_retry_message_builder_used():
    client = _stub(["bad", _payload()])
    seen: dict[str, Any] = {}

    def _builder(messages, error):
        seen["error"] = error
        return [{"role": "user", "content": "corrected"}]

    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "orig"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        retry_message_builder=_builder,
    )
    assert res.ok is True
    assert "Response was not valid JSON" in seen["error"]
    assert client.messages.calls[1]["messages"] == [{"role": "user", "content": "corrected"}]


# --------------------------------------------------------------------------- #
# input ceiling                                                                 #
# --------------------------------------------------------------------------- #


def test_input_ceiling_blocks_before_call():
    client = _stub([_payload()])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        max_input_tokens=10,
        estimate_input_tokens=lambda messages, system: 999,
    )
    assert res.ok is False
    assert "input_token_ceiling_exceeded" in res.error_reason
    assert client.messages.calls == []


def test_client_exception_returns_failed_result():
    @dataclass
    class _Boom:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("network down")

    res = generate_structured(
        _Boom(),
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
    )
    assert res.ok is False and "claude_call_failed" in res.error_reason


# --------------------------------------------------------------------------- #
# Forced tool-use mode (Phase 2)                                              #
# --------------------------------------------------------------------------- #


@dataclass
class _ToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class _StubToolUseResponse:
    tool_name: str
    tool_input: dict

    @property
    def content(self):
        return [_ToolUseBlock(name=self.tool_name, input=self.tool_input)]

    @property
    def usage(self):
        return _StubUsage()


@dataclass
class _MixedMessages:
    """Stub messages.create that returns pre-built response objects in order.

    Lets a test interleave text responses (``_StubResponse``) and tool_use
    responses (``_StubToolUseResponse``) to exercise the retry recovery path.
    """

    responses: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        return self.responses[min(idx, len(self.responses) - 1)]


@dataclass
class _MixedClient:
    messages: _MixedMessages


def _mixed(responses: list[Any]) -> _MixedClient:
    return _MixedClient(messages=_MixedMessages(responses=responses))


def _tu(input_dict: dict, *, name: str = "emit_my_model") -> _StubToolUseResponse:
    return _StubToolUseResponse(tool_name=name, tool_input=input_dict)


def test_tool_use_happy_path_skips_json_parsing():
    client = _mixed([_tu({"score": 88, "label": "tu_ok"})])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        use_tool_use=True,
        tool_name="emit_my_model",
    )
    assert res.ok is True
    assert res.value is not None
    assert res.value.score == 88 and res.value.label == "tu_ok"
    assert res.retry_count == 0 and res.validation_failures == 0


def test_tool_use_forces_tool_choice_and_passes_pydantic_schema():
    client = _mixed([_tu({"score": 1, "label": "x"})])
    generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        use_tool_use=True,
        tool_name="emit_my_model",
    )
    sent = client.messages.calls[0]
    # tools + tool_choice are present and forced to the synthetic tool
    assert len(sent["tools"]) == 1
    tool = sent["tools"][0]
    assert tool["name"] == "emit_my_model"
    # input_schema is the Pydantic JSON schema — single schema source
    assert tool["input_schema"]["type"] == "object"
    assert set(tool["input_schema"]["properties"]) == {"score", "label"}
    assert sent["tool_choice"] == {"type": "tool", "name": "emit_my_model"}


def test_text_mode_does_not_send_tools():
    """Backward-compat: default ``use_tool_use=False`` must not add tools."""
    client = _stub([_payload()])
    generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
    )
    sent = client.messages.calls[0]
    assert "tools" not in sent and "tool_choice" not in sent


def test_tool_use_missing_block_retries_then_fails():
    """Model returns text instead of using the tool both times → failure
    with a clear error_reason, no half-baked value."""
    client = _mixed([_StubResponse(text=_payload()), _StubResponse(text=_payload())])
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        use_tool_use=True,
        tool_name="emit_my_model",
    )
    assert res.ok is False and res.value is None
    assert "did not emit the expected" in res.error_reason
    assert res.validation_failures == 2 and res.retry_count == 1


def test_tool_use_recovers_on_retry_after_text_response():
    """Stub: text first (model refused the tool), tool_use second → ok=True."""
    client = _mixed(
        [
            _StubResponse(text="some chatter, no tool"),
            _tu({"score": 50, "label": "recovered"}),
        ]
    )
    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        use_tool_use=True,
        tool_name="emit_my_model",
    )
    assert res.ok is True
    assert res.value.score == 50 and res.value.label == "recovered"
    assert res.retry_count == 1 and res.validation_failures == 1


def test_tool_use_semantic_validators_fire():
    client = _mixed([_tu({"score": 10, "label": "low"})])

    def _require_high(value):
        if value.score < 50:
            raise ValidationFailure("score too low")

    res = generate_structured(
        client,
        model="m",
        messages=[{"role": "user", "content": "x"}],
        output_model=_Model,
        metering=_metering(),
        max_tokens=64,
        use_tool_use=True,
        tool_name="emit_my_model",
        semantic_validators=[_require_high],
        max_retries=0,
    )
    assert res.ok is False and "score too low" in res.error_reason


def test_default_tool_name_derives_snake_case_from_class():
    from app.llm.structured import _default_tool_name

    class FooBarBaz(BaseModel):
        x: int = 0

    class CVMatchResult(BaseModel):
        score: int = 0

    assert _default_tool_name(FooBarBaz) == "emit_foo_bar_baz"
    # Acronym handling: "CV" stays together, then split before "Match".
    assert _default_tool_name(CVMatchResult) == "emit_cv_match_result"
