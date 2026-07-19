"""Secret-safe provider exception evidence."""

from app.services import provider_error_evidence


def test_safe_anthropic_error_code_includes_controlled_category(monkeypatch):
    marker = "sk-ant-secret-provider-body"
    monkeypatch.setattr(
        provider_error_evidence,
        "classify_anthropic_exception",
        lambda _error: ("rate_limit", 429),
    )

    code = provider_error_evidence.safe_anthropic_error_code(
        RuntimeError(marker),
        operation="pre_screen",
    )

    assert code == "pre_screen:rate_limit:RuntimeError"
    assert marker not in code


def test_safe_anthropic_error_code_omits_unhelpful_other_category(monkeypatch):
    monkeypatch.setattr(
        provider_error_evidence,
        "classify_anthropic_exception",
        lambda _error: ("other", None),
    )

    assert provider_error_evidence.safe_anthropic_error_code(
        ValueError("private response"),
        operation="score",
    ) == "score:ValueError"


def test_safe_structured_error_code_discards_uncontrolled_validation_detail():
    marker = "postgres://private-validation-input"

    code = provider_error_evidence.safe_structured_error_code(
        f"validation_failed_after_retry: Response failed schema: {marker}",
        operation="intake_chat",
    )

    assert code == "intake_chat:validation_failed"
    assert marker not in code


def test_safe_structured_error_code_keeps_controlled_provider_category():
    code = provider_error_evidence.safe_structured_error_code(
        "claude_call_failed:rate_limit:RateLimitError",
        operation="outreach",
    )

    assert code == "outreach:provider_rate_limit"


def test_builtin_transport_errors_get_stable_retry_categories():
    assert provider_error_evidence.classify_anthropic_exception(
        TimeoutError("private timeout detail")
    ) == ("timeout", None)
    assert provider_error_evidence.classify_anthropic_exception(
        ConnectionError("private connection detail")
    ) == ("network", None)
