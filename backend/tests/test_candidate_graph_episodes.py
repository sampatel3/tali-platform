"""Episode builder tests — pure data shaping, no Graphiti / Neo4j.

We construct minimal SimpleNamespace-shaped Candidate / Interview /
Event stand-ins, run the builders, and assert:
- Episode count is correct for typical inputs.
- Every episode body starts with the canonical "Subject candidate" line.
- experience entries are ordered oldest-first.
- skills/education collapse into a single episode.
- max_episodes hard cap works.
- empty/missing data short-circuits cleanly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.candidate_graph import episodes as episode_module
from app.candidate_graph import client as graph_client


@pytest.fixture(autouse=True)
def _force_configured(monkeypatch):
    """Episode builders don't actually call Graphiti, but group_id_for_org
    is always callable. Patch it so tests don't depend on env vars."""
    monkeypatch.setattr(graph_client, "group_id_for_org", lambda org_id: f"org-{org_id}")
    yield


def _candidate(**overrides):
    base = {
        "id": 1,
        "organization_id": 1,
        "full_name": "Alice Example",
        "headline": "Senior Engineer",
        "position": "Backend Lead",
        "summary": "Built distributed systems at scale.",
        "location_city": "London",
        "location_country": "United Kingdom",
        "skills": ["Python", "Postgres"],
        "education_entries": [
            {"school": "MIT", "degree": "BSc", "field": "CS", "start_date": "2010", "end_date": "2014"}
        ],
        "experience_entries": [
            {"company": "Acme", "title": "Engineer", "start_date": "2018", "end_date": "2022", "industry": "fintech"},
            {"company": "Globex", "title": "Senior Engineer", "start_date": "2014", "end_date": "2018"},
        ],
        "cv_sections": {"experience": [], "skills": [], "education": []},
        "cv_text": None,
        "cv_uploaded_at": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_profile_episode_includes_subject_header_and_summary():
    cand = _candidate()
    eps = episode_module.build_candidate_profile_episodes(cand, max_episodes=10)
    assert any("Subject candidate: Alice Example (taali_id=1)" in e.body for e in eps)
    assert any("Built distributed systems" in e.body for e in eps)
    assert all(e.group_id == "org-1" for e in eps)


def test_skills_education_collapsed_into_one_episode():
    cand = _candidate()
    eps = episode_module.build_candidate_profile_episodes(cand, max_episodes=10)
    skill_ep = next(e for e in eps if e.source_description == "candidate.skills_education")
    assert "Python" in skill_ep.body
    assert "MIT" in skill_ep.body


def test_experiences_emitted_oldest_first():
    cand = _candidate()
    eps = episode_module.build_candidate_profile_episodes(cand, max_episodes=10)
    exp_eps = [e for e in eps if e.source_description == "candidate.experience"]
    # Globex (2014) should come before Acme (2018).
    assert "Globex" in exp_eps[0].body
    assert "Acme" in exp_eps[1].body


def test_max_episodes_caps_total():
    cand = _candidate(
        experience_entries=[
            {"company": f"Co{i}", "title": "Eng", "start_date": f"20{i:02d}"}
            for i in range(20)
        ]
    )
    eps = episode_module.build_candidate_profile_episodes(cand, max_episodes=5)
    assert len(eps) == 5


def test_no_experience_or_skills_yields_only_profile_episode():
    cand = _candidate(skills=[], education_entries=[], experience_entries=[], cv_sections={})
    eps = episode_module.build_candidate_profile_episodes(cand, max_episodes=10)
    assert len(eps) == 1
    assert eps[0].source_description == "candidate.profile"


def test_cv_text_episode_truncates_long_text():
    cand = _candidate(cv_text="x" * 50_000)
    ep = episode_module.build_cv_text_episode(cand)
    assert ep is not None
    assert len(ep.body) < 13_000  # subject header + 12k payload + a few framing lines


def test_cv_text_episode_returns_none_when_missing():
    assert episode_module.build_cv_text_episode(_candidate(cv_text=None)) is None


def test_interview_episode_emits_transcript_and_summary():
    cand = _candidate()
    application = SimpleNamespace(candidate=cand)
    interview = SimpleNamespace(
        id=99,
        organization_id=1,
        application=application,
        stage="screening",
        source="fireflies",
        provider="fireflies",
        meeting_date=datetime(2024, 5, 1, tzinfo=timezone.utc),
        transcript_text="Hello world transcript content.",
        summary={"strengths": ["systems design"], "concerns": []},
        speakers=[{"name": "Recruiter"}, {"name": "Alice"}],
    )
    eps = episode_module.build_interview_episodes(interview)
    sources = sorted(e.source_description for e in eps)
    assert sources == ["interview.summary.screening", "interview.transcript.screening"]
    transcript = next(e for e in eps if "transcript" in e.source_description)
    assert "Subject candidate" in transcript.body
    assert "Hello world transcript content." in transcript.body


def test_interview_with_no_text_or_summary_yields_no_episodes():
    cand = _candidate()
    application = SimpleNamespace(candidate=cand)
    interview = SimpleNamespace(
        id=99,
        organization_id=1,
        application=application,
        stage="screening",
        source="manual",
        provider=None,
        meeting_date=None,
        transcript_text=None,
        summary=None,
        speakers=None,
    )
    assert episode_module.build_interview_episodes(interview) == []


def _event(**overrides):
    """Build a SimpleNamespace shaped like the real CandidateApplicationEvent
    SQLAlchemy model: from_stage / to_stage / from_outcome / to_outcome /
    reason are the load-bearing fields. Tests must use these names — using
    `notes`, `from_value`, etc. silently mocks past the production bug
    that this fixture is here to prevent."""
    cand = _candidate()
    application = SimpleNamespace(candidate=cand, organization_id=1, role_id=10)
    base = dict(
        id=1,
        application_id=20,
        application=application,
        role_id=10,
        event_type="pipeline_stage_changed",
        from_stage=None,
        to_stage=None,
        from_outcome=None,
        to_outcome=None,
        reason=None,
        created_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_event_episode_drops_pure_state_transitions():
    # No stage / outcome change, no reason → not worth an LLM call.
    assert episode_module.build_event_episode(_event()) is None


def test_event_episode_keeps_reason_when_present():
    ep = episode_module.build_event_episode(
        _event(
            event_type="application_outcome_changed",
            from_outcome="open",
            to_outcome="rejected",
            reason="Comp expectations did not align.",
        )
    )
    assert ep is not None
    assert "Comp expectations did not align." in ep.body
    # Outcome transition rendered semantically so the LLM can extract it.
    assert "open" in ep.body and "rejected" in ep.body


def test_event_episode_preserves_logical_role_identity_over_transport_owner():
    ep = episode_module.build_event_episode(
        _event(
            role_id=22,
            application=SimpleNamespace(
                candidate=_candidate(), organization_id=1, role_id=10
            ),
            reason="Advanced for this role only",
        )
    )

    assert ep is not None
    assert "Application taali_id=20, role taali_id=22." in ep.body
    assert "role.22" in ep.source_description


def test_event_episode_does_not_infer_legacy_role_from_transport_owner():
    assert episode_module.build_event_episode(
        _event(role_id=None, reason="Ambiguous legacy event")
    ) is None


def test_event_episode_renders_workable_stage_advance():
    # The 646 real Workable advance events in production look like this:
    # event_type='pipeline_stage_changed', from_stage='applied',
    # to_stage='advanced', reason='Recruiter advance'.
    ep = episode_module.build_event_episode(
        _event(
            event_type="pipeline_stage_changed",
            from_stage="applied",
            to_stage="advanced",
            reason="Recruiter advance",
        )
    )
    assert ep is not None
    assert "applied" in ep.body and "advanced" in ep.body
    assert "Recruiter advance" in ep.body


def test_event_episode_renders_hired_outcome():
    # Forward-looking: hired/offered events should land in Graphiti as
    # outcome-change facts the LLM can extract.
    ep = episode_module.build_event_episode(
        _event(
            event_type="application_outcome_changed",
            from_outcome="offered",
            to_outcome="hired",
            reason="Candidate accepted offer",
        )
    )
    assert ep is not None
    assert "hired" in ep.body
    assert "Candidate accepted offer" in ep.body


def test_event_episode_skips_noise_event_types():
    # pipeline_initialized + cv_scored together account for ~99% of events
    # in production. Both carry no facts beyond what's already in Postgres,
    # so we skip them rather than burn LLM calls on them.
    for noisy in ("pipeline_initialized", "cv_scored"):
        ep = episode_module.build_event_episode(
            _event(event_type=noisy, reason="CV scored: scored (46%)")
        )
        assert ep is None, f"expected {noisy} to be skipped"


def test_event_episode_skips_workable_writeback_mechanics():
    # Workable write-back success/failure events are ATS-sync mechanics, not
    # candidate facts. They carry a reason string (so they'd otherwise pass
    # the note gate and cost an LLM extraction each — 242 on 2026-06-07), so
    # they must be filtered as noise.
    for noisy in ("workable_writeback_failed", "workable_writeback_skipped"):
        ep = episode_module.build_event_episode(
            _event(event_type=noisy, reason="Workable 403: job archived")
        )
        assert ep is None, f"expected {noisy} to be skipped"


def test_event_episode_suppresses_no_op_stage_transition():
    # Outcome-only events often have from_stage == to_stage (e.g. an
    # 'applied → applied' stage with outcome going open → rejected).
    # Don't render the redundant stage line in that case.
    ep = episode_module.build_event_episode(
        _event(
            event_type="application_outcome_changed",
            from_stage="applied",
            to_stage="applied",
            from_outcome="open",
            to_outcome="rejected",
            reason="Recruiter reject",
        )
    )
    assert ep is not None
    assert "Pipeline stage:" not in ep.body
    assert "Application outcome:" in ep.body
