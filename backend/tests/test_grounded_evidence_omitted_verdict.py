"""Regression tests for incomplete citation-grounder responses."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.candidate_search import grounded_evidence as ge


@pytest.fixture(autouse=True)
def _unit_route_seam(monkeypatch):
    class _Execution:
        selected_model_id = "test-grounding-model"
        last_attempt_model_id = "test-grounding-model"
        decision = SimpleNamespace(
            limits=SimpleNamespace(max_iterations=3),
            behavior_fingerprint="test-grounding-behavior",
        )

        def finish_workflow(self, *, succeeded: bool) -> None:
            self.succeeded = succeeded

    monkeypatch.setattr(ge, "prepare_route", lambda *_a, **_k: _Execution())


def _text(text: str, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


def _citation(quote: str):
    return SimpleNamespace(
        cited_text=quote,
        start_char_index=0,
        end_char_index=len(quote),
        document_index=0,
    )


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, _ttl, value):
        self.store[key] = value


class _Client:
    def __init__(self, response):
        self.calls = 0
        outer = self

        class _Messages:
            def create(self, **_kwargs):
                outer.calls += 1
                return response

        self.messages = _Messages()

    def __call__(self, _execution):
        return self


def test_parse_marks_criterion_without_explicit_verdict_as_error():
    verdicts = ge.parse_citation_response(
        [
            _text("[[C2]] MET — banking role"),
            _text("banking evidence", citations=[_citation("Commercial banking")]),
        ],
        ["Treasury experience", "banking domain experience"],
    )

    omitted, explicit = verdicts
    assert omitted.status == "error"
    assert omitted.grounded is False
    assert omitted.evidence == []
    assert "omitted" in omitted.note.lower()
    assert explicit.status == "met"
    assert explicit.grounded is True


def test_extract_does_not_cache_omitted_verdict(monkeypatch):
    cache = _FakeRedis()
    monkeypatch.setattr(ge, "_redis", lambda: cache)
    client = _Client(
        SimpleNamespace(
            content=[
                _text("[[C2]] MET — banking role"),
                _text("banking evidence", citations=[_citation("Commercial banking")]),
            ]
        )
    )

    verdicts = ge.extract_cv_evidence(
        cv_text="Project delivery for a commercial bank.",
        criteria=["Treasury experience", "banking domain experience"],
        route_client_factory=client,
        organization_id=1,
        application_id=42,
    )

    assert [verdict.status for verdict in verdicts] == ["error", "met"]
    cached = [json.loads(value) for value in cache.store.values()]
    assert [(item["criterion"], item["status"]) for item in cached] == [
        ("banking domain experience", "met")
    ]


def test_explicit_missing_verdict_remains_cacheable(monkeypatch):
    cache = _FakeRedis()
    monkeypatch.setattr(ge, "_redis", lambda: cache)
    client = _Client(SimpleNamespace(content=[_text("[[C1]] MISSING — no evidence")]))

    kwargs = {
        "cv_text": "Project delivery for a commercial bank.",
        "criteria": ["Treasury experience"],
        "route_client_factory": client,
        "organization_id": 1,
        "application_id": 42,
    }
    first = ge.extract_cv_evidence(**kwargs)
    second = ge.extract_cv_evidence(**kwargs)

    assert first[0].status == second[0].status == "missing"
    assert client.calls == 1
    assert len(cache.store) == 1
