"""agent_chat.rescore — scoped, opt-in re-score of OLD-engine candidates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agent_chat import rescore


def test_is_stale_detects_old_engine():
    assert rescore._is_stale({"prompt_version": "cv_match_v16"}) is True
    assert rescore._is_stale({"prompt_version": "holistic_v1"}) is True  # 2.0.0, superseded
    assert rescore._is_stale({"prompt_version": "holistic_v2", "engine_version": "2.1.0"}) is False
    assert rescore._is_stale({}) is False  # unscored / no version → not stale


def _stale_rows(scores):
    return [
        {"application_id": i, "score": float(s), "engine_version": "1.16.0", "_app": SimpleNamespace(id=i)}
        for i, s in enumerate(scores, start=1)
    ]


def test_preview_does_not_spend(monkeypatch):
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: _stale_rows([80, 70, 60, 40]))
    enq = MagicMock()
    monkeypatch.setattr("app.services.cv_score_orchestrator.enqueue_score", enq)

    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="all", confirm=False)
    assert out["type"] == "rescore_preview"
    assert out["selected_count"] == 4
    assert out["est_cost_usd"] == round(4 * 0.083, 2)
    assert enq.call_count == 0  # preview never enqueues


def test_scope_top_n(monkeypatch):
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: _stale_rows([90, 80, 70, 60, 50]))
    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="top_n", limit=2, confirm=False)
    assert out["selected_count"] == 2  # the two highest


def test_scope_below_threshold(monkeypatch):
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: _stale_rows([80, 55, 49, 30]))
    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="below_threshold", threshold=50, confirm=False)
    assert out["selected_count"] == 2  # 49 and 30


def test_below_threshold_requires_threshold(monkeypatch):
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: _stale_rows([80]))
    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="below_threshold", confirm=False)
    assert out.get("ok") is False


def test_confirm_enqueues_each(monkeypatch):
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: _stale_rows([90, 80, 70]))
    enq = MagicMock(return_value=object())  # truthy job
    monkeypatch.setattr("app.services.cv_score_orchestrator.enqueue_score", enq)
    db = MagicMock()

    out = rescore.rescore_candidates(db, SimpleNamespace(id=1), scope="top_n", limit=2, confirm=True)
    assert out["type"] == "rescore_started"
    assert out["rescoring_count"] == 2
    assert enq.call_count == 2
    # force re-enqueue + bypass the cheap gate (recruiter-directed full re-score)
    for c in enq.call_args_list:
        assert c.kwargs["force"] is True
        assert c.kwargs["bypass_pre_screen"] is True
    db.commit.assert_called()


def test_no_stale_is_a_noop(monkeypatch):
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: [])
    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="all", confirm=True)
    assert out["stale_total"] == 0
    assert "nothing to re-score" in out["message"].lower()
