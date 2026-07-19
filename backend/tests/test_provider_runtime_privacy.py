from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.components.assessments import candidate_chat_runtime_support
from app.candidate_graph import agent_episodes, search
from app.cv_matching import graded, runner_pre_screen
from app.domains.workable_sync import provider_reads
from app.services import (
    document_service,
    fireflies_service,
    pdf_text,
    requisition_chat_attachments,
)


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/agent_runtime/orchestrator.py",
        "app/candidate_graph/episode_outbox_delivery.py",
        "app/components/assessments/interrogation.py",
        "app/components/assessments/rubric_scoring.py",
        "app/components/integrations/workable/sync_service.py",
        "app/domains/assessments_runtime/applications_routes.py",
        "app/main.py",
        "app/tasks/prescreen_tasks.py",
    ],
)
def test_provider_boundaries_never_log_raw_tracebacks(relative_path):
    source = (Path(__file__).resolve().parents[1] / relative_path).read_text(
        encoding="utf-8"
    )

    assert "logger.exception" not in source


def _requirement():
    return SimpleNamespace(id="req-1", priority="must", requirement="Python")


def test_graded_requirement_failures_never_log_provider_or_validation_detail(
    monkeypatch, caplog
):
    provider_secret = "graded-provider-secret-must-not-escape"
    monkeypatch.setattr(
        graded,
        "generate_structured",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(provider_secret)
        ),
    )

    assert graded.grade_requirements(
        cv_text="CV",
        jd_text="JD",
        requirements=[_requirement()],
        client=object(),
    ) == {}
    assert provider_secret not in caplog.text
    assert "graded_requirement_pass:RuntimeError" in caplog.text

    validation_secret = "graded-validation-input-secret-must-not-escape"
    caplog.clear()
    monkeypatch.setattr(
        graded,
        "generate_structured",
        lambda *_args, **_kwargs: SimpleNamespace(
            ok=False,
            value=None,
            error_reason=(
                "validation_failed_after_retry: schema included "
                + validation_secret
            ),
        ),
    )

    assert graded.grade_requirements(
        cv_text="CV",
        jd_text="JD",
        requirements=[_requirement()],
        client=object(),
    ) == {}
    assert validation_secret not in caplog.text
    assert "graded_requirement_pass:validation_failed" in caplog.text


def test_pre_screen_provider_error_is_stable_and_model_reason_is_not_logged(
    monkeypatch, caplog
):
    provider_secret = "prescreen-provider-response-secret-must-not-escape"
    monkeypatch.setattr(
        runner_pre_screen,
        "one_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(provider_secret)
        ),
    )

    failed = runner_pre_screen.run_pre_screen(
        "candidate CV",
        "job spec",
        client=object(),
        skip_cache=True,
    )

    assert failed.reason == "claude_call_failed:RuntimeError"
    assert provider_secret not in caplog.text

    model_reason = "candidate-sensitive-model-reason-must-not-enter-logs"
    caplog.clear()
    monkeypatch.setattr(
        runner_pre_screen,
        "one_call",
        lambda *_args, **_kwargs: SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "score": 80,
                            "reason": model_reason,
                            "unverified_extraordinary_claim": False,
                        }
                    )
                )
            ]
        ),
    )

    completed = runner_pre_screen.run_pre_screen(
        "candidate CV",
        "job spec",
        client=object(),
        skip_cache=True,
    )

    assert completed.reason == model_reason
    assert model_reason not in caplog.text


def test_graph_event_and_prefix_failures_log_only_stable_codes(
    monkeypatch, caplog
):
    provider_secret = "graph-event-provider-secret-must-not-escape"
    monkeypatch.setattr(
        agent_episodes, "build_agent_score_episode", lambda **_kw: object()
    )
    monkeypatch.setattr(
        agent_episodes,
        "_dispatch_metered",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(provider_secret)
        ),
    )

    assert agent_episodes.emit_score_event() is False
    assert provider_secret not in caplog.text
    assert "graph_emit_score:RuntimeError" in caplog.text

    db_secret = "graph-prefix-database-secret-must-not-escape"

    class _Db:
        def query(self, _model):
            raise RuntimeError(db_secret)

    caplog.clear()
    assert search._episode_prefixes_for_candidates(_Db(), [7]) == ["candidate-7-"]
    assert db_secret not in caplog.text
    assert "graph_episode_prefix_expand:RuntimeError" in caplog.text


def test_fireflies_client_never_raises_provider_response_text(monkeypatch):
    secret = "Bearer fireflies-secret: provider response body"
    client = fireflies_service.FirefliesService("api-key")
    monkeypatch.setattr(
        fireflies_service.httpx,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(fireflies_service.FirefliesProviderError) as caught:
        client._graphql(query="query Test { test }")

    assert str(caught.value) == "fireflies_graphql:RuntimeError"
    assert caught.value.__context__ is None
    assert secret not in str(caught.value)

    class _GraphQlFailure:
        content = b"provider error"

        def raise_for_status(self):
            return None

        def json(self):
            return {"errors": [{"message": secret}]}

    monkeypatch.setattr(
        fireflies_service.httpx,
        "post",
        lambda *_args, **_kwargs: _GraphQlFailure(),
    )

    with pytest.raises(fireflies_service.FirefliesProviderError) as caught:
        client._graphql(query="query Test { test }")

    assert str(caught.value) == "fireflies_graphql:provider_rejected"
    assert secret not in str(caught.value)


def test_classifier_soft_failure_accepts_only_controlled_codes():
    assert candidate_chat_runtime_support.classifier_error_code(
        "interrogation_classifier_budget_blocked"
    ) == "interrogation_classifier_budget_blocked"
    assert candidate_chat_runtime_support.classifier_error_code(
        "provider response with secret-token"
    ) == "interrogation_classifier_failed"


def test_workable_diagnostic_logs_stable_code_not_provider_body(caplog):
    secret = "workable-token in diagnostic response body"

    class _Client:
        def list_open_jobs(self):
            raise RuntimeError(secret)

    result = provider_reads.run_workable_diagnostic(_Client())

    assert result["api_reachable"] is False
    assert result["error"] == "Workable API diagnostic failed"
    assert "workable_diagnostic:RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_document_parser_logs_exclude_parser_detail_and_attachment_name(
    monkeypatch, caplog
):
    caplog.set_level("INFO")
    parser_detail = "document parser detail that must stay internal"
    attachment_name = "candidate-private-name.pdf"

    monkeypatch.setattr(
        document_service.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(parser_detail)
        ),
    )
    assert document_service.extract_text_from_docx(b"not-a-document") == ""
    assert "DOCX text extraction failed error_type=RuntimeError" in caplog.text
    assert parser_detail not in caplog.text

    caplog.clear()
    monkeypatch.setattr(
        document_service,
        "extract_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(parser_detail)
        ),
    )
    attachment = requisition_chat_attachments.ChatAttachment(
        name=attachment_name,
        content_type="application/pdf",
        content=b"document",
    )
    assert requisition_chat_attachments._decode_document_attachment(attachment) is None
    assert "PDF extraction failed error_type=RuntimeError" in caplog.text
    assert parser_detail not in caplog.text
    assert attachment_name not in caplog.text


def test_pdf_fallback_log_excludes_parser_detail(monkeypatch, caplog):
    parser_detail = "pdf parser detail that must stay internal"
    monkeypatch.setattr(
        pdf_text,
        "_extract_text_from_pdf_columnar",
        lambda _content: (_ for _ in ()).throw(RuntimeError(parser_detail)),
    )
    monkeypatch.setattr(
        pdf_text,
        "_extract_text_from_pdf_with_layout",
        lambda _content: "",
    )
    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda _stream: SimpleNamespace(pages=[]),
    )

    assert pdf_text.extract_text_from_pdf(b"pdf") == ""
    assert "Columnar PDF extraction failed error_type=RuntimeError" in caplog.text
    assert parser_detail not in caplog.text
