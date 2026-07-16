"""Anti-stranding tests for the ``workable_writeback`` read-only skip.

The decision-dispatch path wraps Workable write helpers in
``strict_workable_writes()``. In strict mode ``_build_failure_result``
RAISES ``WorkableWritebackError``, which rolls back + re-queues the decision
forever. Read-only mode (``workable_writeback`` False) must therefore SKIP
the write as a benign, non-raising no-op — never routed through the strict
failure path. These tests prove that.
"""

from types import SimpleNamespace

from app.components.integrations.workable.service import WorkableService
from app.services.workable_actions_service import (
    disqualify_candidate_in_workable,
    move_candidate_in_workable,
    strict_workable_writes,
    workable_writeback_enabled,
)


def _org(*, workable_writeback=None, granted_scopes=("r_jobs", "r_candidates", "w_candidates")):
    config = {
        "granted_scopes": list(granted_scopes),
        "workable_actor_member_id": "member-1",
    }
    if workable_writeback is not None:
        config["workable_writeback"] = workable_writeback
    return SimpleNamespace(
        id=1,
        workable_connected=True,
        workable_access_token="token",
        workable_subdomain="acme",
        workable_config=config,
    )


def _app(**overrides):
    payload = {
        "id": 21,
        "workable_candidate_id": "candidate-1",
        "pre_screen_score_100": 47.2,
        "pre_screen_recommendation": "reject",
        "candidate": SimpleNamespace(full_name="Taylor Candidate", email="taylor@example.com"),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _role(**overrides):
    payload = {"id": 11, "name": "Backend Engineer", "workable_actor_member_id": None}
    payload.update(overrides)
    return SimpleNamespace(**payload)


# ---------------------------------------------------------------------------
# workable_writeback_enabled resolution
# ---------------------------------------------------------------------------


def test_writeback_flag_takes_precedence_over_scopes():
    # Explicit False wins even though the token has the w_candidates scope.
    assert workable_writeback_enabled(_org(workable_writeback=False)) is False
    assert workable_writeback_enabled(_org(workable_writeback=True)) is True


def test_writeback_flag_fails_closed_when_absent():
    # Migration 150 backfilled existing rows; missing state must not silently
    # enable writes merely because a token carries the provider scope.
    assert workable_writeback_enabled(_org(granted_scopes=("r_jobs", "r_candidates"))) is False
    assert workable_writeback_enabled(_org()) is False


def test_writeback_disabled_when_not_connected():
    org = _org(workable_writeback=True)
    org.workable_connected = False
    assert workable_writeback_enabled(org) is False


# ---------------------------------------------------------------------------
# Read-only skip is a non-raising no-op, even under strict writes
# ---------------------------------------------------------------------------


def test_disqualify_skips_without_raising_under_strict(monkeypatch):
    def _boom(self, **_):  # pragma: no cover — must never be called
        raise AssertionError("HTTP write attempted in read-only mode")

    monkeypatch.setattr(WorkableService, "disqualify_candidate", _boom)

    org = _org(workable_writeback=False)
    with strict_workable_writes():
        result = disqualify_candidate_in_workable(org=org, app=_app(), role=_role())

    assert result["skipped"] is True
    assert result["success"] is False
    assert result["code"] == "writeback_disabled"


def test_move_skips_without_raising_under_strict(monkeypatch):
    def _boom(self, **_):  # pragma: no cover — must never be called
        raise AssertionError("HTTP write attempted in read-only mode")

    monkeypatch.setattr(WorkableService, "move_candidate", _boom)

    org = _org(workable_writeback=False)
    with strict_workable_writes():
        result = move_candidate_in_workable(
            org=org, candidate_id="candidate-1", target_stage="Review", role=_role()
        )

    assert result["skipped"] is True
    assert result["code"] == "writeback_disabled"


# ---------------------------------------------------------------------------
# Write-back ON → the helpers proceed to attempt the real write
# ---------------------------------------------------------------------------


def test_disqualify_proceeds_when_writeback_enabled(monkeypatch):
    captured = {}

    def fake_disqualify(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "disqualify_candidate", fake_disqualify)

    result = disqualify_candidate_in_workable(
        org=_org(workable_writeback=True), app=_app(), role=_role(), reason="Below threshold"
    )

    assert result.get("skipped") is not True
    assert result["success"] is True
    assert captured["candidate_id"] == "candidate-1"
    assert captured["member_id"] == "member-1"


def test_move_proceeds_when_writeback_enabled(monkeypatch):
    captured = {}

    def fake_move(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "move_candidate", fake_move)

    result = move_candidate_in_workable(
        org=_org(workable_writeback=True),
        candidate_id="candidate-4",
        target_stage="Review",
        role=_role(),
    )

    assert result.get("skipped") is not True
    assert result["success"] is True
    assert captured == {
        "candidate_id": "candidate-4",
        "member_id": "member-1",
        "target_stage": "Review",
    }
