"""Rerank unit tests.

Mocks the Anthropic client and DB session; asserts that:
- Empty soft_criteria short-circuits without LLM calls.
- Each application is evaluated once and order is preserved.
- A model returning ``{"match": false}`` drops the candidate.
- API and malformed-JSON failures remain explicit, unclassified outcomes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.candidate_search import rerank as rerank_module


class _FakeClient:
    def __init__(self, decisions: list[object]):
        self._decisions = list(decisions)
        self.calls = 0
        self.requests: list[dict] = []

        class _Messages:
            def __init__(inner_self):
                inner_self._parent = self

            def create(inner_self, **kwargs):
                inner_self._parent.requests.append(kwargs)
                idx = inner_self._parent.calls
                inner_self._parent.calls += 1
                if idx < len(inner_self._parent._decisions):
                    decision = inner_self._parent._decisions[idx]
                    if isinstance(decision, Exception):
                        raise decision
                    if isinstance(decision, bool):
                        body = json.dumps({"match": decision, "reason": "test"})
                    else:
                        body = str(decision)
                else:
                    body = "not json"
                return SimpleNamespace(content=[SimpleNamespace(text=body)])

        self.messages = _Messages()


def _make_app_row(app_id: int, candidate_id: int):
    candidate = SimpleNamespace(
        id=candidate_id,
        headline="Senior Engineer",
        summary="Built lots of things",
        skills=["Python"],
        cv_sections={"experience": [], "skills": [], "summary": ""},
        location_country="United Kingdom",
    )
    application = SimpleNamespace(
        id=app_id,
        candidate=candidate,
        candidate_id=candidate_id,
        cv_match_score=72.5,
        organization_id=1,
    )
    return application


def _make_db(applications):
    """Construct a minimal SQLAlchemy-like session that returns ``applications``."""
    db = MagicMock()
    chain = db.query.return_value.join.return_value.filter.return_value
    chain.all.return_value = applications
    return db


def test_empty_soft_criteria_returns_input_untouched():
    db = _make_db([])
    out = rerank_module.rerank_application_ids(
        db=db,
        organization_id=1,
        application_ids=[1, 2, 3],
        soft_criteria=[],
        client=_FakeClient([]),
    )
    assert out.application_ids == [1, 2, 3]
    assert out.outcomes == []


def test_keeps_only_matched_in_input_order(monkeypatch):
    apps = [_make_app_row(10, 100), _make_app_row(20, 200), _make_app_row(30, 300)]
    db = _make_db(apps)
    fake = _FakeClient([True, False, True])  # keep 10 and 30
    monkeypatch.setattr(
        rerank_module,
        "_build_graph_context",
        lambda **_: None,
    )
    out = rerank_module.rerank_application_ids(
        db=db,
        organization_id=1,
        application_ids=[10, 20, 30],
        soft_criteria=["large enterprise"],
        client=fake,
    )
    assert out.application_ids == [10, 30]
    assert [item.status for item in out.outcomes] == [
        "qualified",
        "not_qualified",
        "qualified",
    ]
    assert out.evidence_succeeded == 3
    assert out.evidence_failed == 0
    assert out.qualified == 2
    assert fake.calls == 3


def test_malformed_response_keeps_candidate_unclassified(monkeypatch):
    apps = [_make_app_row(1, 11)]
    db = _make_db(apps)
    fake = _FakeClient([])  # no canned decisions → malformed reply path
    monkeypatch.setattr(rerank_module, "_build_graph_context", lambda **_: None)
    out = rerank_module.rerank_application_ids(
        db=db,
        organization_id=1,
        application_ids=[1],
        soft_criteria=["in production"],
        client=fake,
    )
    assert out.application_ids == [1]
    assert out.evidence_succeeded == 0
    assert out.evidence_failed == 1
    assert out.qualified == 0
    assert out.outcomes[0].status == "error"
    assert out.outcomes[0].error_code == "invalid_model_response"


def test_api_failure_keeps_candidate_unclassified(monkeypatch):
    apps = [_make_app_row(1, 11)]
    db = _make_db(apps)
    fake = _FakeClient([RuntimeError("provider unavailable")])
    monkeypatch.setattr(rerank_module, "_build_graph_context", lambda **_: None)

    out = rerank_module.rerank_application_ids(
        db=db,
        organization_id=1,
        application_ids=[1],
        soft_criteria=["in production"],
        client=fake,
    )

    assert out.application_ids == [1]
    assert out.evidence_succeeded == 0
    assert out.evidence_failed == 1
    assert out.outcomes[0].status == "error"
    assert out.outcomes[0].error_code == "model_call_failed"


def test_non_boolean_match_is_invalid_not_truthy(monkeypatch):
    apps = [_make_app_row(1, 11)]
    db = _make_db(apps)
    fake = _FakeClient([json.dumps({"match": "false", "reason": "bad type"})])
    monkeypatch.setattr(rerank_module, "_build_graph_context", lambda **_: None)

    out = rerank_module.rerank_application_ids(
        db=db,
        organization_id=1,
        application_ids=[1],
        soft_criteria=["in production"],
        client=fake,
    )

    assert out.application_ids == [1]
    assert out.outcomes[0].status == "error"
    assert out.outcomes[0].error_code == "invalid_model_response"


def test_role_id_is_threaded_into_rerank_admission_and_metering(monkeypatch):
    apps = [_make_app_row(10, 100)]
    db = _make_db(apps)
    fake = _FakeClient([True])
    captured = {}

    def _admit(**kwargs):
        captured.update(kwargs)
        return {
            "feature": "cv_rerank",
            "organization_id": kwargs["organization_id"],
            "role_id": kwargs["role_id"],
            "credit_reservation": {
                "organization_id": kwargs["organization_id"],
                "feature": "cv_rerank",
                "amount": 5_000,
                "external_ref": "test-rerank-hold",
                "live": False,
            },
        }

    monkeypatch.setattr(rerank_module, "admitted_search_metering", _admit)
    graph_context_args = {}

    def _graph_context(**kwargs):
        graph_context_args.update(kwargs)
        return None

    monkeypatch.setattr(rerank_module, "_build_graph_context", _graph_context)

    out = rerank_module.rerank_application_ids(
        db=db,
        organization_id=1,
        role_id=77,
        application_ids=[10],
        soft_criteria=["large enterprise"],
        client=fake,
    )

    assert out.application_ids == [10]
    assert out.outcomes[0].status == "qualified"
    assert captured["organization_id"] == 1
    assert captured["role_id"] == 77
    assert graph_context_args["role_id"] == 77
    assert fake.requests[0]["metering"]["role_id"] == 77
    assert fake.requests[0]["metering"]["credit_reservation"]["amount"] == 5_000


def test_no_api_key_reports_verification_unavailable(monkeypatch):
    monkeypatch.setattr(
        rerank_module,
        "_resolve_anthropic_client",
        lambda **_: (_ for _ in ()).throw(
            RuntimeError("ANTHROPIC_API_KEY is not configured")
        ),
    )
    db = _make_db([_make_app_row(1, 11), _make_app_row(2, 22)])
    with pytest.raises(rerank_module.RerankUnavailable):
        rerank_module.rerank_application_ids(
            db=db,
            organization_id=1,
            application_ids=[1, 2],
            soft_criteria=["in production"],
            client=None,
        )


def test_summary_truncation_caps_long_strings():
    candidate = SimpleNamespace(
        id=1,
        headline="x" * 500,
        summary="y" * 5000,
        skills=["a"] * 200,
        cv_sections={
            "summary": "z" * 5000,
            "skills": ["s"] * 200,
            "experience": [
                {"company": "c" * 200, "title": "t" * 200, "start": "2020", "end": "2024"}
            ] * 50,
        },
        location_country="x" * 500,
    )
    application = SimpleNamespace(cv_match_score=80.0)
    summary = rerank_module._build_candidate_summary(candidate, application)
    assert len(summary["headline"]) <= 160
    assert len(summary["summary"]) <= 600
    assert len(summary["skills_top"]) <= 30
    assert len(summary["experience_top"]) <= 6
