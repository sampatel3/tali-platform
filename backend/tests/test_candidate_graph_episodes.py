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
    monkeypatch.setattr(graph_client, "group_id_for_org", lambda org_id: f"org:{org_id}")
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
    assert all(e.group_id == "org:1" for e in eps)


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


def test_event_episode_drops_pure_state_transitions():
    cand = _candidate()
    application = SimpleNamespace(candidate=cand, organization_id=1)
    event = SimpleNamespace(
        id=1,
        application=application,
        event_type="stage_changed",
        from_value=None,
        to_value=None,
        notes=None,
        comment=None,
        created_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
    )
    assert episode_module.build_event_episode(event) is None


def test_event_episode_keeps_notes_when_present():
    cand = _candidate()
    application = SimpleNamespace(candidate=cand, organization_id=1)
    event = SimpleNamespace(
        id=1,
        application=application,
        event_type="rejected",
        from_value="review",
        to_value="rejected",
        notes="Comp expectations did not align.",
        created_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
    )
    ep = episode_module.build_event_episode(event)
    assert ep is not None
    assert "Comp expectations did not align." in ep.body
