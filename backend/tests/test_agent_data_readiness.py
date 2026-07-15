"""Data-readiness guardrails: the agent refuses to run without a job spec and
surfaces CV-less candidates, instead of spending Claude tokens on guesses.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import event

from app.agent_runtime import data_readiness, orchestrator
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role

_BIG_PK = {"agent_runs": 0, "agent_needs_input": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
event.listen(AgentNeedsInput, "before_insert", _assign_big_pk)


def _seed(db, *, job_spec="Requirements\n- 5+ years Python\n", cv="cv text"):
    org = Organization(name="O", slug=f"o-{id(db)}-{_BIG_PK['agent_runs']}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
        job_spec_text=job_spec,
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email=f"c{_BIG_PK['agent_runs']}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text=cv,
    )
    db.add(app)
    db.flush()
    return org, role, app


def _open_kind(db, role, kind):
    return (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == kind,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def test_has_job_spec():
    assert data_readiness.has_job_spec(SimpleNamespace(job_spec_text="x", description=None))
    assert data_readiness.has_job_spec(SimpleNamespace(job_spec_text=None, description="y"))
    assert not data_readiness.has_job_spec(SimpleNamespace(job_spec_text="  ", description=""))


def test_missing_cv_count_and_sync(db):
    org, role, app = _seed(db, cv="has cv")
    assert data_readiness.missing_cv_count(db, role=role) == 0

    app.cv_text = None
    db.add(app)
    db.flush()
    assert data_readiness.missing_cv_count(db, role=role) == 1

    # sync raises a single role-level missing_cv item with the live count.
    data_readiness.sync_cv_readiness(db, role=role)
    db.flush()
    rows = _open_kind(db, role, "missing_cv")
    assert len(rows) == 1
    assert "1 candidate" in rows[0].prompt

    # Once the CV lands, the next sync auto-resolves it.
    app.cv_text = "now has cv"
    db.add(app)
    db.flush()
    data_readiness.sync_cv_readiness(db, role=role)
    db.flush()
    assert _open_kind(db, role, "missing_cv") == []


def test_cv_readiness_split_missing_vs_unreadable(db):
    """A CV *file* present but no extracted text is 'unreadable', not
    'missing' — surfaced on its own card, never as missing_cv."""
    org, role, app = _seed(db, cv="has cv")

    # File on record but no text => unreadable, not missing.
    app.cv_text = None
    app.cv_file_url = "s3://bucket/scan.png"
    db.add(app)
    db.flush()
    assert data_readiness.missing_cv_count(db, role=role) == 0
    assert data_readiness.unreadable_cv_count(db, role=role) == 1

    data_readiness.sync_cv_readiness(db, role=role)
    db.flush()
    assert _open_kind(db, role, "missing_cv") == []
    unreadable = _open_kind(db, role, "cv_unreadable")
    assert len(unreadable) == 1
    assert "couldn't read" in unreadable[0].prompt

    # Drop the file too => now it's genuinely missing; the cards swap over.
    app.cv_file_url = None
    db.add(app)
    db.flush()
    assert data_readiness.missing_cv_count(db, role=role) == 1
    assert data_readiness.unreadable_cv_count(db, role=role) == 0
    data_readiness.sync_cv_readiness(db, role=role)
    db.flush()
    assert len(_open_kind(db, role, "missing_cv")) == 1
    assert _open_kind(db, role, "cv_unreadable") == []


def test_file_less_open_applications_excludes_file_present(db):
    org, role, app = _seed(db, cv="has cv")
    # File-less app => included.
    app.cv_text = None
    app.cv_file_url = None
    db.add(app)
    db.flush()
    file_less = data_readiness.file_less_open_applications(db, role=role)
    assert [a.id for a in file_less] == [app.id]

    # Same app but with a file on record => moves to the unreadable cohort,
    # out of the file-less one (each reject targets only its own cohort).
    app.cv_file_url = "s3://bucket/scan.png"
    db.add(app)
    db.flush()
    assert data_readiness.file_less_open_applications(db, role=role) == []
    unreadable = data_readiness.unreadable_cv_open_applications(db, role=role)
    assert [a.id for a in unreadable] == [app.id]


# ---------------------------------------------------------------------------
# Orchestrator gate
# ---------------------------------------------------------------------------

def test_run_cycle_aborts_without_job_spec_and_never_calls_claude(db):
    org, role, app = _seed(db, job_spec="")  # no spec, no description
    fake_client = MagicMock()

    with patch.object(
        orchestrator, "get_client_for_org", return_value=fake_client
    ) as resolve_client:
        run = orchestrator.run_cycle(db, role=role, trigger="manual")

    assert run.status == "aborted"
    assert run.error == "missing_job_spec"
    resolve_client.assert_not_called()
    # The Anthropic client was never invoked — $0 spent.
    fake_client.messages.create.assert_not_called()
    # A HITL item was raised for the recruiter.
    assert len(_open_kind(db, role, "missing_job_spec")) == 1


def test_run_cycle_resolves_missing_job_spec_when_added(db):
    # Start with no spec → gate raises the item.
    org, role, app = _seed(db, job_spec="")
    with patch.object(orchestrator, "get_client_for_org", return_value=MagicMock()):
        orchestrator.run_cycle(db, role=role, trigger="manual")
    db.commit()
    assert len(_open_kind(db, role, "missing_job_spec")) == 1

    # Recruiter adds a spec; the next cycle clears the item before running.
    role.job_spec_text = "Requirements\n- 5+ years Python\n"
    db.add(role)
    db.commit()

    # Stub the agent loop so the cycle completes immediately after the gate.
    def _complete_first_round(*args, **kwargs):
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(type="tool_use", id="t1", name="agent_run_complete", input={})],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _complete_first_round
    with patch.object(orchestrator, "get_client_for_org", return_value=fake_client):
        run = orchestrator.run_cycle(db, role=role, trigger="manual")
    db.commit()

    assert run.status != "aborted" or run.error != "missing_job_spec"
    assert _open_kind(db, role, "missing_job_spec") == []
