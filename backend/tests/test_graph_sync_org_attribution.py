"""graph_sync per-org attribution: the live interview/event sync paths must
thread the org to dispatch so the metered wrapper writes a per-org graph_sync
usage_event (instead of an unattributed org=NULL call_log row).

Regression guard for the residual NULL-org leaks the reconciliation audit
surfaced after PR #477 (which only covered the outbox drain + backfill).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.candidate_graph import client as graph_client
from app.candidate_graph import episodes as episode_module
from app.candidate_graph import sync as sync_module


def _capture_dispatch(captured):
    def _fake(eps, **kwargs):
        captured.update(kwargs)
        return 1

    return _fake


def test_sync_event_attributes_explicit_org_and_db():
    ev = MagicMock()
    ev.organization_id = 99
    captured: dict = {}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_event_episode", return_value=MagicMock()
    ), patch.object(
        episode_module, "dispatch", side_effect=_capture_dispatch(captured)
    ):
        sync_module.sync_event(ev, db="DB_SENTINEL", bill_organization_id=99)
    assert captured["bill_organization_id"] == 99
    assert captured["db"] == "DB_SENTINEL"
    assert captured["require_hard_admission"] is True
    assert captured["require_role_admission"] is False


def test_sync_event_falls_back_to_event_org():
    """No explicit org → use the event's own (non-nullable) organization_id."""
    ev = MagicMock()
    ev.organization_id = 77
    captured: dict = {}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_event_episode", return_value=MagicMock()
    ), patch.object(
        episode_module, "dispatch", side_effect=_capture_dispatch(captured)
    ):
        sync_module.sync_event(ev, db="DB")
    assert captured["bill_organization_id"] == 77
    assert captured["db"] == "DB"
    assert captured["require_hard_admission"] is True


def test_sync_interview_attributes_explicit_org():
    iv = MagicMock()
    iv.organization_id = 55
    captured: dict = {}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_interview_episodes", return_value=[MagicMock()]
    ), patch.object(
        episode_module, "dispatch", side_effect=_capture_dispatch(captured)
    ):
        sync_module.sync_interview(iv, db="DB", bill_organization_id=55)
    assert captured["bill_organization_id"] == 55
    assert captured["db"] == "DB"
    assert captured["require_hard_admission"] is True


# ---------------------------------------------------------------------------
# Unchanged-content skip (2026-06-07 cost guard): sync_candidate must not
# re-run the Graphiti extraction when the candidate's episode set is identical
# to the last fully-synced fingerprint — the listeners fire on every
# Candidate AND CandidateApplication update, but a stage change doesn't touch
# the profile episodes.
# ---------------------------------------------------------------------------


def _make_candidate(db):
    from app.models.candidate import Candidate
    from app.models.organization import Organization

    org = Organization(name="Graph Org", slug=f"graph-{id(db)}")
    db.add(org)
    db.flush()
    cand = Candidate(
        organization_id=org.id, email=f"g-{id(db)}@x.test", full_name="Graph Cand"
    )
    db.add(cand)
    db.flush()
    return cand


def test_candidate_billing_role_comes_from_graph_worthy_application(db):
    from app.models.candidate_application import CandidateApplication
    from app.models.role import Role

    cand = _make_candidate(db)
    below = Role(organization_id=cand.organization_id, name="Below Gate")
    eligible = Role(organization_id=cand.organization_id, name="Eligible")
    db.add_all([below, eligible])
    db.flush()
    db.add_all(
        [
            CandidateApplication(
                organization_id=cand.organization_id,
                candidate_id=cand.id,
                role_id=below.id,
                status="applied",
                pipeline_stage="review",
                application_outcome="open",
                source="manual",
            ),
            CandidateApplication(
                organization_id=cand.organization_id,
                candidate_id=cand.id,
                role_id=eligible.id,
                status="applied",
                pipeline_stage="advanced",
                application_outcome="open",
                source="manual",
            ),
        ]
    )
    db.commit()

    assert sync_module.billing_role_id_for_candidate(cand, db) == int(eligible.id)


def _counting_dispatch(counter):
    def _fake(episodes, **kwargs):
        counter["n"] += 1
        return len(list(episodes))

    return _fake


def test_sync_candidate_skips_unchanged_content(db):
    from types import SimpleNamespace

    cand = _make_candidate(db)
    eps = [SimpleNamespace(name="candidate-x-profile", body="hello world")]
    counter = {"n": 0}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_candidate_profile_episodes", return_value=eps
    ), patch.object(
        episode_module, "build_cv_text_episode", return_value=None
    ), patch.object(
        episode_module, "dispatch", side_effect=_counting_dispatch(counter)
    ):
        sent1 = sync_module.sync_candidate(
            cand, db=db, bill_organization_id=cand.organization_id
        )
        sent2 = sync_module.sync_candidate(
            cand, db=db, bill_organization_id=cand.organization_id
        )

    assert sent1 == 1
    assert sent2 == 0  # identical content -> skipped
    assert counter["n"] == 1  # dispatch ran exactly once


def test_sync_candidate_resyncs_when_content_changes(db):
    from types import SimpleNamespace

    cand = _make_candidate(db)
    state = {"body": "v1"}
    counter = {"n": 0}

    def _build(_c, **_k):
        return [SimpleNamespace(name="candidate-x-profile", body=state["body"])]

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_candidate_profile_episodes", side_effect=_build
    ), patch.object(
        episode_module, "build_cv_text_episode", return_value=None
    ), patch.object(
        episode_module, "dispatch", side_effect=_counting_dispatch(counter)
    ):
        sync_module.sync_candidate(
            cand, db=db, bill_organization_id=cand.organization_id
        )
        state["body"] = "v2-changed"
        sent = sync_module.sync_candidate(
            cand, db=db, bill_organization_id=cand.organization_id
        )

    assert sent == 1
    assert counter["n"] == 2  # changed content -> re-dispatched


def test_sync_candidate_force_resync_bypasses_skip(db):
    from types import SimpleNamespace

    cand = _make_candidate(db)
    eps = [SimpleNamespace(name="candidate-x-profile", body="same")]
    counter = {"n": 0}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_candidate_profile_episodes", return_value=eps
    ), patch.object(
        episode_module, "build_cv_text_episode", return_value=None
    ), patch.object(
        episode_module, "dispatch", side_effect=_counting_dispatch(counter)
    ):
        sync_module.sync_candidate(
            cand, db=db, bill_organization_id=cand.organization_id
        )
        sent = sync_module.sync_candidate(
            cand, db=db, bill_organization_id=cand.organization_id, force_resync=True
        )

    assert sent == 1
    assert counter["n"] == 2  # force_resync bypasses the unchanged-skip
