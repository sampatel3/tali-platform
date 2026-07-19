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
from unittest.mock import MagicMock

import pytest

from app.candidate_graph import episodes as episode_module
from app.candidate_graph import client as graph_client
from app.candidate_graph import ingest_manifest
from app.candidate_graph import sync as sync_module
from app.platform.config import settings


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


def test_manifest_episode_boundaries_are_exact_even_with_structured_profile_data():
    cand = _candidate(
        experience_entries=[
            {"company": f"Co{i}", "title": "Eng", "start_date": f"{i:04d}"}
            for i in range(200)
        ]
    )

    assert len(
        episode_module.build_candidate_profile_episodes(cand, max_episodes=1)
    ) == 1
    assert len(
        episode_module.build_candidate_profile_episodes(cand, max_episodes=101)
    ) == ingest_manifest.MAX_MANIFEST_EPISODES


@pytest.mark.parametrize(
    ("configured_cap", "expected_count"),
    ((1, 1), (40, 40), (ingest_manifest.MAX_MANIFEST_EPISODES + 1, 100)),
)
def test_sync_defensively_reserves_configured_and_manifest_capacity_for_cv(
    monkeypatch,
    configured_cap,
    expected_count,
):
    cand = _candidate(
        cv_text="full cv",
        experience_entries=[
            {"company": f"Co{i}", "title": "Eng", "start_date": f"{i:04d}"}
            for i in range(200)
        ],
    )
    observed = []

    def _capture_dispatch(episodes, **_kwargs):
        observed.extend(episodes)
        return len(episodes)

    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        settings,
        "GRAPHITI_MAX_EPISODES_PER_CANDIDATE",
        configured_cap,
    )
    monkeypatch.setattr(episode_module, "dispatch", _capture_dispatch)

    assert sync_module.sync_candidate(cand, include_cv_text=True) == expected_count
    assert len(observed) == expected_count
    assert any(item.source_description == "candidate.cv_text" for item in observed)


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
        stage=("screening\n\f" * 100),
        source="manual",
        provider=None,
        meeting_date=None,
        transcript_text=None,
        summary=None,
        speakers=None,
    )
    assert episode_module.build_interview_episodes(interview) == []


def test_large_unicode_interview_payload_is_bounded_before_manifest_and_provider(
    monkeypatch,
):
    cand = _candidate()
    interview = SimpleNamespace(
        id=100,
        organization_id=1,
        application=SimpleNamespace(candidate=cand),
        stage="screening",
        source="fireflies",
        provider="fireflies",
        meeting_date=datetime(2024, 5, 1, tzinfo=timezone.utc),
        transcript_text="opening\fcontrol\x00" + "😀" * 18_000,
        summary={
            f"section-{index}": ["😀" * 1_000] * 100
            for index in range(100)
        },
        speakers=None,
    )
    episodes = episode_module.build_interview_episodes(interview)
    summary = next(item for item in episodes if "summary" in item.name)
    transcript = next(item for item in episodes if "transcript" in item.name)
    assert all(
        len(item.body.encode("utf-8"))
        <= ingest_manifest.MAX_EPISODE_PAYLOAD_BYTES
        for item in episodes
    )
    assert summary.body.endswith("graph ingestion byte limit.]")
    assert episode_module.bounded_episode_body(summary.body) == summary.body
    assert "\f" not in transcript.body
    assert "\x00" not in transcript.body
    assert "opening control " in transcript.body
    observed = []

    class _ObservedPayload(RuntimeError):
        pass

    def _capture_manifest(payload):
        payload = list(payload)
        observed.extend(payload)
        ingest_manifest.build_operation_manifest(
            work_kind="interview",
            entity_id=100,
            episodes=payload,
        )
        raise _ObservedPayload

    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    with pytest.raises(_ObservedPayload):
        episode_module.dispatch(
            episodes,
            operation_manifest_callback=_capture_manifest,
        )

    assert len(observed) == 2
    assert all(
        len(item.body.encode("utf-8"))
        <= ingest_manifest.MAX_EPISODE_PAYLOAD_BYTES
        for item in observed
    )
    assert all(
        len(item.name.encode("utf-8")) <= ingest_manifest.MAX_EPISODE_NAME_BYTES
        and len(item.source_description.encode("utf-8"))
        <= ingest_manifest.MAX_EPISODE_NAME_BYTES
        for item in observed
    )
    assert all(
        not any(
            ord(character) < 32 or ord(character) == 127
            for character in item.name + item.source_description
        )
        for item in observed
    )


def test_episode_line_budget_stops_consuming_source_after_truncation():
    def _lines():
        yield "😀" * ingest_manifest.MAX_EPISODE_PAYLOAD_BYTES
        raise AssertionError("body builder consumed source after reaching its budget")

    body = episode_module._bounded_episode_lines(_lines())

    assert body.endswith("graph ingestion byte limit.]")
    assert len(body.encode("utf-8")) <= ingest_manifest.MAX_EPISODE_PAYLOAD_BYTES


def test_dispatch_hashes_and_sends_the_same_normalized_control_safe_body(
    monkeypatch,
):
    episode = episode_module.Episode(
        name="candidate\ncontrols-" + ("n" * 1_000),
        body="Subject candidate: Alice\fform-feed\x00nul",
        source_description="candidate\tprofile\f" + ("s" * 1_000),
        reference_time=datetime(2024, 5, 1, tzinfo=timezone.utc),
        group_id="org-1",
    )
    manifest_fields = None

    def _capture_manifest(payload):
        nonlocal manifest_fields
        manifest_fields = (
            payload[0].name,
            payload[0].body,
            payload[0].source_description,
        )
        ingest_manifest.build_operation_manifest(
            work_kind="candidate",
            entity_id=1,
            episodes=payload,
        )
        return True

    provider = SimpleNamespace(add_episode=MagicMock())
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(graph_client, "get_graphiti", lambda: provider)
    monkeypatch.setattr(graph_client, "run_async", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(episode_module, "_episode_text_source", lambda: "text")

    assert episode_module.dispatch(
        [episode],
        operation_manifest_callback=_capture_manifest,
    ) == 1
    provider_kwargs = provider.add_episode.call_args.kwargs
    provider_body = provider_kwargs["episode_body"]
    assert provider_kwargs["name"] is manifest_fields[0]
    assert provider_body is manifest_fields[1]
    assert provider_kwargs["source_description"] is manifest_fields[2]
    assert provider_body == "Subject candidate: Alice form-feed nul"
    assert episode_module.bounded_episode_body(provider_body) == provider_body
    assert all(
        len(provider_kwargs[field].encode("utf-8"))
        <= ingest_manifest.MAX_EPISODE_NAME_BYTES
        for field in ("name", "source_description")
    )
    assert "\n" not in provider_kwargs["name"]
    assert "\t" not in provider_kwargs["source_description"]
    assert "\f" not in provider_kwargs["source_description"]


def test_dispatch_never_logs_or_reraises_provider_detail(monkeypatch, caplog):
    secret_marker = "graphiti-provider-response-secret-must-not-escape"
    episode = episode_module.Episode(
        name="candidate-1-profile",
        body="Subject candidate: Alice",
        source_description="candidate.profile",
        reference_time=datetime(2024, 5, 1, tzinfo=timezone.utc),
        group_id="org-1",
    )
    provider = SimpleNamespace(add_episode=MagicMock())
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(graph_client, "get_graphiti", lambda: provider)
    monkeypatch.setattr(episode_module, "_episode_text_source", lambda: "text")
    monkeypatch.setattr(
        graph_client,
        "run_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret_marker)),
    )

    with pytest.raises(episode_module.GraphProviderRuntimeError) as exc_info:
        episode_module.dispatch([episode], raise_on_error=True)

    assert str(exc_info.value) == "graphiti_add_episode:RuntimeError"
    assert exc_info.value.__context__ is None
    assert secret_marker not in str(exc_info.value)
    assert secret_marker not in caplog.text


def _event(**overrides):
    """Build a SimpleNamespace shaped like the real CandidateApplicationEvent
    SQLAlchemy model: from_stage / to_stage / from_outcome / to_outcome /
    reason are the load-bearing fields. Tests must use these names — using
    `notes`, `from_value`, etc. silently mocks past the production bug
    that this fixture is here to prevent."""
    cand = _candidate()
    application = SimpleNamespace(candidate=cand, organization_id=1)
    base = dict(
        id=1,
        application=application,
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
