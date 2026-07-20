"""Unit tests for Workable sync service - formatting, candidate detection, terminal stage logic."""

import pytest

from app.components.integrations.workable.sync_service import (
    _format_job_spec_from_api,
    _strip_html,
    _is_terminal_candidate,
    _is_disqualified,
    _disqualified_at_from_payload,
    _terminal_outcome,
    _candidate_email,
    _candidate_phone,
    _normalize_phone_for_match,
    _normalize_stage_for_terminal,
    WorkableSyncService,
)
from app.components.integrations.workable.service import WorkableService


class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello</p>") == "Hello"
        assert _strip_html("<strong>Bold</strong>") == "**Bold**"

    def test_br_to_newline(self):
        assert "\n" in _strip_html("A<br/>B")

    def test_li_to_markdown(self):
        out = _strip_html("<li>Item</li>")
        assert "Item" in out

    def test_strips_html_and_embedded_dict(self):
        html = "<p>Location: {'country': 'UAE', 'city': 'Dubai'}</p>"
        out = _strip_html(html)
        assert "Dubai" in out
        assert "'country'" not in out

    def test_fixes_literal_backslash_n(self):
        out = _strip_html("Line1\\nLine2")
        assert "\n" in out

    def test_strips_nul_characters(self):
        out = _strip_html("<p>Hello\x00World</p>")
        assert "\x00" not in out
        assert "HelloWorld" in out


class TestFormatJobSpecFromApi:
    def test_empty_input(self):
        assert _format_job_spec_from_api({}) == ""
        assert _format_job_spec_from_api(None) == ""

    def test_flat_dict_with_description(self):
        job = {
            "title": "Backend Engineer",
            "description": "<p>We need a great engineer.</p>",
        }
        out = _format_job_spec_from_api(job)
        assert "# Backend Engineer" in out
        assert "We need a great engineer" in out

    def test_job_wrapper(self):
        job = {"job": {"title": "Frontend Dev", "description": "React expert"}}
        out = _format_job_spec_from_api(job)
        assert "# Frontend Dev" in out
        assert "React expert" in out

    def test_details_nested(self):
        job = {
            "title": "Role",
            "details": {"description": "Main desc", "requirements": "Req 1"},
        }
        out = _format_job_spec_from_api(job)
        assert "Main desc" in out
        assert "Req 1" in out

    def test_job_with_details_both(self):
        job = {
            "job": {
                "title": "Data Engineer",
                "details": {"full_description": "Full text here"},
            }
        }
        out = _format_job_spec_from_api(job)
        assert "# Data Engineer" in out
        assert "Full text here" in out

    def test_description_and_full_description_are_deduped(self):
        job = {
            "title": "Portfolio Lead",
            "description": "<p>DeepLight AI builds enterprise data systems.</p>",
            "full_description": (
                "<p>DeepLight AI builds enterprise data systems.</p>"
                "<p>Lead delivery governance across strategic programs.</p>"
            ),
            "requirements": "<ul><li>Own financial forecasting.</li></ul>",
            "benefits": "<p>Hybrid working.</p>",
        }
        out = _format_job_spec_from_api(job)
        assert "## Description" in out
        assert "## Requirements" in out
        assert "## Benefits" in out
        assert "Full Description" not in out
        assert out.count("DeepLight AI builds enterprise data systems.") == 1

    def test_location_dict_formatted_not_raw(self):
        job = {
            "title": "GenAI Engineer",
            "location": {"country": "United Arab Emirates", "region": "Dubai", "city": "Dubai", "workplace_type": "hybrid"},
            "department": "DeepLight",
        }
        out = _format_job_spec_from_api(job)
        assert "Dubai" in out
        assert "United Arab Emirates" in out
        assert "hybrid" in out
        assert "{'" not in out and "'country'" not in out

    def test_location_as_python_repr_string(self):
        job = {
            "title": "Test",
            "location": "{'country': 'UAE', 'city': 'Dubai', 'workplace_type': 'remote'}",
        }
        out = _format_job_spec_from_api(job)
        assert "Dubai" in out
        assert "UAE" in out
        assert "'country'" not in out


class TestIsTerminalCandidate:
    def test_hired_stage(self):
        assert _is_terminal_candidate({"stage": "hired"}) is True
        assert _is_terminal_candidate({"stage_name": "Hired"}) is True

    def test_rejected_stage(self):
        assert _is_terminal_candidate({"stage": "rejected"}) is True
        assert _is_terminal_candidate({"stage_kind": "rejected"}) is True

    def test_non_terminal(self):
        assert _is_terminal_candidate({"stage": "screening"}) is False
        assert _is_terminal_candidate({"stage": "interview"}) is False
        assert _is_terminal_candidate({}) is False

    def test_offer_is_terminal(self):
        # offer = hiring decision made → terminal/advanced.
        assert _is_terminal_candidate({"stage": "offer"}) is True
        assert _is_terminal_candidate({"stage": "Offer Extended"}) is True

    def test_interview_stages_not_terminal(self):
        # mid-interview stays in Tali's funnel, NOT terminal.
        assert _is_terminal_candidate({"stage": "technical interview"}) is False
        assert _is_terminal_candidate({"stage": "final interview"}) is False
        assert _is_terminal_candidate({"stage": "phone screen"}) is False

    def test_disqualified_flag(self):
        assert _is_terminal_candidate({"disqualified": True}) is True

    def test_hired_at(self):
        assert _is_terminal_candidate({"hired_at": "2024-01-01"}) is True


class TestIsDisqualified:
    def test_flag_in_payload(self):
        assert _is_disqualified({"disqualified": True}) is True

    def test_flag_in_ref(self):
        assert _is_disqualified({}, {"disqualified": True}) is True

    def test_not_disqualified(self):
        assert _is_disqualified({"stage": "interview"}) is False
        assert _is_disqualified({"disqualified": False}, {"disqualified": False}) is False
        assert _is_disqualified({}) is False


class TestTerminalOutcome:
    def test_hired_and_rejected(self):
        assert _terminal_outcome({"stage": "hired"}) == "hired"
        assert _terminal_outcome({"hired_at": "2026-01-01"}) == "hired"
        assert _terminal_outcome({"stage": "rejected"}) == "rejected"
        assert _terminal_outcome({}, disqualified=True) == "rejected"

    def test_offer_has_no_outcome(self):
        # offer is terminal/advanced but the candidate isn't hired yet, so the
        # application_outcome stays open (the calibrator labels offer positive
        # via workable_stage, not via application_outcome).
        assert _terminal_outcome({"stage": "offer"}) is None
        assert _terminal_outcome({"stage": "Offer Extended"}) is None

    def test_non_terminal_none(self):
        assert _terminal_outcome({"stage": "technical interview"}) is None


class TestDisqualifiedAt:
    def test_parses_iso_with_z(self):
        out = _disqualified_at_from_payload({"disqualified_at": "2026-05-20T10:00:00Z"})
        assert out is not None
        assert out.year == 2026 and out.month == 5 and out.day == 20

    def test_falls_back_to_ref(self):
        out = _disqualified_at_from_payload({}, {"disqualified_at": "2026-01-02T00:00:00+00:00"})
        assert out is not None and out.month == 1

    def test_none_when_absent_or_garbage(self):
        assert _disqualified_at_from_payload({}) is None
        assert _disqualified_at_from_payload({"disqualified_at": "not-a-date"}) is None


class TestCandidateEmail:
    def test_direct_email(self):
        assert _candidate_email({"email": "a@b.com"}) == "a@b.com"

    def test_contact_nested(self):
        assert _candidate_email({"contact": {"email": "x@y.com"}}) == "x@y.com"

    def test_emails_list(self):
        payload = {"emails": [{"value": "e@f.com"}]}
        assert _candidate_email(payload) == "e@f.com"

    def test_profile_nested(self):
        assert _candidate_email({"profile": {"email": "p@q.com"}}) == "p@q.com"

    def test_no_email(self):
        assert _candidate_email({}) is None
        assert _candidate_email({"name": "John"}) is None


class TestNormalizePhoneForMatch:
    def test_collapses_formatting_and_country_code(self):
        # The real duplicate-profile case: same person, two phone formats.
        assert _normalize_phone_for_match("+971 50 202 2165") == "502022165"
        assert _normalize_phone_for_match("+971 +971 502022165") == "502022165"

    def test_local_prefix_collapses_to_same_key(self):
        assert _normalize_phone_for_match("0502022165") == "502022165"

    def test_too_short_returns_none(self):
        assert _normalize_phone_for_match("12345") is None
        assert _normalize_phone_for_match("") is None
        assert _normalize_phone_for_match(None) is None

    def test_letters_and_symbols_stripped(self):
        assert _normalize_phone_for_match("tel: 123-456-789 ext") == "123456789"


class TestCandidatePhone:
    def test_direct_phone(self):
        assert _candidate_phone({"phone": "+971 50 202 2165"}) == "+971 50 202 2165"

    def test_nested_contact_phone(self):
        assert _candidate_phone({"contact": {"phone": "12345"}}) == "12345"

    def test_no_phone(self):
        assert _candidate_phone({}) is None
        assert _candidate_phone({"phone": ""}) is None


class TestNormalizeStageForTerminal:
    def test_exact_match(self):
        assert _normalize_stage_for_terminal("hired") == "hired"
        assert _normalize_stage_for_terminal("rejected") == "rejected"

    def test_case_insensitive(self):
        assert _normalize_stage_for_terminal("Hired") == "hired"

    def test_non_terminal(self):
        assert _normalize_stage_for_terminal("screening") is None
        assert _normalize_stage_for_terminal("") is None


class TestJobIdentifierPriority:
    def test_shortcode_precedes_numeric_and_id(self):
        service = WorkableSyncService(WorkableService(access_token="x", subdomain="test"))
        job = {
            "id": "50c5fd",
            "shortcode": "120884740D",
            "application_url": "https://jobs.workable.com/jobs/90000123/apply",
        }
        identifiers = service._job_identifiers(job)
        assert identifiers[0] == "120884740D"
        assert "90000123" in identifiers
        assert identifiers[-1] == "50c5fd"


def test_sync_includes_candidates_without_email_and_counts_upserts_on_updates(db):
    """Workable list payloads may omit email; sync should still persist by candidate ID."""
    from app.models.organization import Organization
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def list_open_jobs(self):
            return [{"id": "J1", "shortcode": "J1", "title": "Backend Engineer"}]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            return [{"id": "cand_no_email_1", "name": "No Email Candidate", "stage": "Screening"}]

        def get_job_details(self, job_identifier):
            return {}

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org = Organization(
        name="No Email Org",
        slug="no-email-org-workable-sync",
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    service = WorkableSyncService(MockClient())
    first = service.sync_org(db, org)
    assert first["candidates_seen"] == 1
    assert first["candidates_upserted"] == 1
    assert first["applications_upserted"] == 1

    candidate = db.query(Candidate).filter(
        Candidate.organization_id == org.id,
        Candidate.workable_candidate_id == "cand_no_email_1",
    ).first()
    assert candidate is not None
    assert (candidate.email or "") == ""

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.candidate_id == candidate.id,
    ).first()
    assert app is not None

    # Second run updates existing rows; upsert counters should still reflect applied upserts.
    second = service.sync_org(db, org)
    assert second["candidates_seen"] == 1
    assert second["candidates_upserted"] == 1
    assert second["applications_upserted"] == 1


def test_sync_respects_selected_job_shortcodes(db):
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.candidate_application import CandidateApplication

    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def list_open_jobs(self):
            return [
                {"id": "J1", "shortcode": "J1", "title": "Role One"},
                {"id": "J2", "shortcode": "J2", "title": "Role Two"},
            ]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            if str(job_identifier) == "J2":
                return [{"id": "cand_j2", "email": "j2@example.com", "name": "J2 Candidate", "stage": "Screening"}]
            return [{"id": "cand_j1", "email": "j1@example.com", "name": "J1 Candidate", "stage": "Screening"}]

        def get_job_details(self, job_identifier):
            return {}

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org = Organization(
        name="Scoped Role Org",
        slug="scoped-role-org-workable-sync",
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    service = WorkableSyncService(MockClient())
    summary = service.sync_org(db, org, selected_job_shortcodes=["J2"])
    assert summary["jobs_seen"] == 2
    assert summary["jobs_total"] == 1
    assert summary["jobs_processed"] == 1
    assert summary["selected_jobs_count"] == 1
    assert summary["selected_jobs_applied"] == 1

    roles = db.query(Role).filter(Role.organization_id == org.id).all()
    assert len(roles) == 1
    assert roles[0].workable_job_id == "J2"

    apps = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).all()
    assert len(apps) == 1
    assert apps[0].workable_candidate_id == "cand_j2"


def test_sync_yields_mid_job_to_a_pending_op(db):
    """A pending user-facing write must be able to interrupt a sync WITHIN a
    big job, not only at the job boundary.

    Regression: ``should_yield`` was checked only between jobs, so a starred
    role with hundreds of candidates held the per-org Workable mutex for its
    whole candidate walk — long enough that a queued decision approval burned
    its entire lock-wait window and failed with "Workable lock timeout". The
    fix re-checks ``should_yield`` between candidates: the sync stops part-way
    through the role, commits what it did, and releases the lock. The unsynced
    candidates AND the later jobs resync on the next tick (idempotent).
    """
    from app.models.organization import Organization
    from app.models.candidate_application import CandidateApplication

    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def list_open_jobs(self):
            # Two jobs: we should yield inside the first and never reach the second.
            return [
                {"id": "J1", "shortcode": "J1", "title": "Busy Role"},
                {"id": "J2", "shortcode": "J2", "title": "Next Role"},
            ]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            if str(job_identifier) == "J1":
                return [
                    {"id": f"cand_{i}", "email": f"c{i}@example.com", "name": f"C{i}", "stage": "Screening"}
                    for i in range(4)
                ]
            return [{"id": "cand_j2", "email": "j2@example.com", "name": "J2", "stage": "Screening"}]

        def get_job_details(self, job_identifier):
            return {}

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org = Organization(
        name="Yield Org",
        slug="yield-org-workable-sync",
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    # sync_org polls should_yield at three points per job: the job-top boundary,
    # once before the (full-mode) prefetch wave, and once per candidate. Flip the
    # signal on the 4th poll so the first job's first candidate is synced (top +
    # pre-prefetch + candidate-0 = 3 falses) and we then yield on candidate 1 —
    # i.e. part-way through the role, the exact case the fix targets.
    calls = {"n": 0}

    def should_yield():
        calls["n"] += 1
        return calls["n"] >= 4

    service = WorkableSyncService(MockClient())
    summary = service.sync_org(db, org, should_yield=should_yield)

    # Stopped part-way through the first role: some — but not all — candidates synced.
    assert 0 < summary["candidates_seen"] < 4
    # Only the first job was touched; the second never started.
    assert summary["jobs_processed"] == 1
    assert summary["jobs_total"] == 2
    # Surfaced as a partial run with the human-readable pause note.
    assert any("Paused mid-role" in e for e in summary["errors"])

    # The second job's candidate must NOT have been imported (outer loop broke).
    j2 = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_j2",
    ).first()
    assert j2 is None


@pytest.mark.skip(reason="Uses sync commits; sqlite 'database is locked' when run in parallel")
class TestWorkableSyncIntegration:
    """Integration tests with mocked Workable client; asserts sync creates roles, applications, job_spec."""

    def test_sync_creates_role_and_application_with_mocked_client(self, db, monkeypatch):
        """Sync with realistic Workable payloads creates role, candidate, application, job_spec_text."""
        from app.models.organization import Organization
        from app.models.role import Role
        from app.models.candidate import Candidate
        from app.models.candidate_application import CandidateApplication
        from app.components.integrations.workable.sync_service import WorkableSyncService
        from app.components.integrations.workable.service import WorkableService

        class MockClient(WorkableService):
            def __init__(self):
                super().__init__(access_token="x", subdomain="test")

            def list_open_jobs(self):
                return [
                    {
                        "id": "J1",
                        "shortcode": "J1",
                        "title": "Backend Engineer",
                        "description": "<p>We need a Python expert.</p>",
                    }
                ]

            def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
                return [
                    {
                        "id": "cand_1",
                        "email": "dev@example.com",
                        "name": "Dev Person",
                        "stage": "Screening",
                        "stage_name": "Screening",
                    }
                ]

            def get_job_details(self, job_identifier):
                return {}

            def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
                return 7.5, 7.5, "candidate.score"

        monkeypatch.setattr("app.components.integrations.workable.sync_service.settings.ANTHROPIC_API_KEY", None)
        org = Organization(
            name="Test Org Workable",
            slug="test-org-workable-sync",
            workable_connected=True,
            workable_access_token="x",
            workable_subdomain="test",
        )
        db.add(org)
        db.commit()
        db.refresh(org)

        service = WorkableSyncService(MockClient())
        summary = service.sync_org(db, org)

        assert summary["jobs_seen"] == 1
        assert summary["jobs_upserted"] >= 1
        assert summary["candidates_seen"] == 1
        assert summary["candidates_upserted"] >= 1
        assert summary["applications_upserted"] >= 1

        role = db.query(Role).filter(Role.organization_id == org.id, Role.workable_job_id == "J1").first()
        assert role is not None
        assert (role.job_spec_text or "").strip() != ""
        assert (role.description or "").strip() != ""

        app = db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.workable_candidate_id == "cand_1",
        ).first()
        assert app is not None

        candidate = db.query(Candidate).filter(
            Candidate.organization_id == org.id,
            Candidate.workable_candidate_id == "cand_1",
        ).first()
        assert candidate is not None
        assert candidate.email == "dev@example.com"


def _make_org(db, slug):
    from app.models.organization import Organization

    org = Organization(
        name=slug,
        slug=slug,
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _client_returning(candidates_by_run):
    """MockClient whose candidate list changes per sync run (call count)."""
    state = {"calls": 0}

    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def list_open_jobs(self):
            return [{"id": "J1", "shortcode": "J1", "title": "AI Engineer"}]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            idx = min(state["calls"], len(candidates_by_run) - 1)
            return candidates_by_run[idx]

        def get_job_details(self, job_identifier):
            return {}

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    return MockClient, state


def test_post_handover_workable_stage_does_not_advance_tali(db):
    """A fresh import already at a post-handover Workable stage must land in
    Tali's `applied` bucket, not `advanced`. Advanced is reserved for an
    explicit Tali hand-back decision."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "post-handover-no-advance-org")
    MockClient, _ = _client_returning([
        [{"id": "cand_th", "email": "th@example.com", "name": "Tech Interviewee",
          "stage": "Technical Interview"}],
    ])
    service = WorkableSyncService(MockClient())
    service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_th",
    ).first()
    assert app is not None
    assert app.pipeline_stage == "applied"
    # The real Workable stage is still surfaced for context.
    assert (app.workable_stage or "").lower().startswith("technical")


def test_post_handover_sync_commits_before_reconciliation(db, monkeypatch):
    """Reconciliation starts after releasing the sync's earlier App ownership."""
    from datetime import datetime, timezone

    from sqlalchemy import event

    from app.components.integrations.workable import sync_service
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.models.role import Role

    org = Organization(name="Boundary", slug=f"boundary-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="R", source="workable")
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=int(org.id),
        email="boundary@example.com",
        full_name="Boundary Candidate",
        workable_candidate_id="cand-boundary",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="sync",
        application_outcome="open",
        source="workable",
        workable_candidate_id="cand-boundary",
        cv_match_score=80.0,
    )
    db.add(app)
    db.commit()

    order = []

    def after_commit(_session):
        order.append("commit")

    def observe_reconcile(worker_db, *, app, role):
        order.append("reconcile")
        assert app not in worker_db.dirty
        assert role not in worker_db.dirty
        assert not worker_db.new
        assert app.workable_stage == "Technical Interview"
        return False

    event.listen(db, "after_commit", after_commit)
    monkeypatch.setattr(
        sync_service,
        "reconcile_post_handover_advanced",
        observe_reconcile,
    )
    try:
        service = WorkableSyncService(
            WorkableService(access_token="x", subdomain="test")
        )
        service._sync_candidate_for_role(
            db=db,
            org=org,
            role=role,
            job={"id": "J1", "shortcode": "J1"},
            candidate_ref={
                "id": "cand-boundary",
                "email": "boundary@example.com",
                "name": "Boundary Candidate",
                "stage": "Technical Interview",
            },
            now=datetime.now(timezone.utc),
            mode="metadata",
        )
    finally:
        event.remove(db, "after_commit", after_commit)

    assert order == ["commit", "reconcile"]


def test_sync_stores_per_application_workable_created_at(db):
    """The payload's created_at is per JOB APPLICATION (Workable candidate ids
    are per-application), so the applied date must land on the application row
    — the candidate-level copy is last-sync-wins across a person's roles."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "applied-at-org")
    MockClient, _ = _client_returning([
        [{"id": "cand_aa", "email": "aa@example.com", "name": "Applied At",
          "stage": "Screening", "created_at": "2026-06-12T09:30:00Z"}],
    ])
    service = WorkableSyncService(MockClient())
    service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_aa",
    ).first()
    assert app is not None
    assert app.workable_created_at is not None
    assert app.workable_created_at.strftime("%Y-%m-%d %H:%M") == "2026-06-12 09:30"


def test_disqualified_candidate_is_flagged_and_advanced(db):
    """When Workable disqualifies an existing candidate, sync records the flag,
    refreshes the Workable stage, and parks them in Tali's terminal stage."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "disqualified-update-org")
    MockClient, state = _client_returning([
        # First run: normal candidate in screening.
        [{"id": "cand_dq", "email": "dq@example.com", "name": "Soon Gone",
          "stage": "Screening"}],
        # Second run: same candidate, now disqualified while in Technical Interview.
        [{"id": "cand_dq", "email": "dq@example.com", "name": "Soon Gone",
          "stage": "Technical Interview", "disqualified": True,
          "disqualified_at": "2026-05-20T10:00:00Z"}],
    ])
    service = WorkableSyncService(MockClient())

    service.sync_org(db, org)
    state["calls"] = 1
    service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_dq",
    ).first()
    assert app is not None
    assert app.workable_disqualified is True
    assert app.workable_disqualified_at is not None
    assert app.pipeline_stage == "advanced"
    # Disqualified is a negative final outcome — captured for model training.
    assert app.application_outcome == "rejected"
    assert (app.workable_stage or "").lower().startswith("technical")


def test_terminal_outcome_is_captured_for_existing_candidate(db):
    """A candidate already in Tali who later reaches a terminal Workable stage
    (hired / rejected) has that outcome recorded — sync no longer drops it, so
    the calibration loop can learn from the realized result."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "terminal-outcome-capture-org")
    MockClient, state = _client_returning([
        # First run: candidate in review.
        [{"id": "cand_hire", "email": "hire@example.com", "name": "Will Hire",
          "stage": "Review"},
         {"id": "cand_rej", "email": "rej@example.com", "name": "Will Reject",
          "stage": "Review"}],
        # Second run: one hired, one rejected.
        [{"id": "cand_hire", "email": "hire@example.com", "name": "Will Hire",
          "stage": "Hired", "hired_at": "2026-05-22T09:00:00Z"},
         {"id": "cand_rej", "email": "rej@example.com", "name": "Will Reject",
          "stage": "Rejected"}],
    ])
    service = WorkableSyncService(MockClient())

    service.sync_org(db, org)
    state["calls"] = 1
    service.sync_org(db, org)

    hired = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_hire",
    ).first()
    assert hired is not None
    assert hired.application_outcome == "hired"
    assert hired.pipeline_stage == "advanced"

    rejected = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_rej",
    ).first()
    assert rejected is not None
    assert rejected.application_outcome == "rejected"
    assert rejected.pipeline_stage == "advanced"


def test_offer_is_parked_advanced_outcome_open(db):
    """An existing candidate moved to Workable 'Offer' is terminal → parked in
    `advanced`, but the outcome stays `open` (not hired yet). Positive label for
    the calibrator comes via workable_stage."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "offer-terminal-org")
    MockClient, state = _client_returning([
        [{"id": "cand_offer", "email": "offer@example.com", "name": "Offer Person", "stage": "Review"}],
        [{"id": "cand_offer", "email": "offer@example.com", "name": "Offer Person", "stage": "Offer"}],
    ])
    service = WorkableSyncService(MockClient())
    service.sync_org(db, org)
    state["calls"] = 1
    service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_offer",
    ).first()
    assert app is not None
    assert app.pipeline_stage == "advanced"
    assert app.application_outcome == "open"  # offer != hired yet
    assert (app.workable_stage or "").lower() == "offer"


def test_advanced_frozen_candidate_later_rejected_flips_to_rejected(db):
    """Freeze/reject guard regression: a candidate already parked in `advanced`
    (frozen after Tali handed them off — ``is_resolved`` is True) who is LATER
    rejected in Workable must still have their outcome flip to `rejected`. The
    terminal branch runs (and returns) BEFORE the freeze guard, so the freeze
    never blocks the realized-outcome capture. This is the "advances can still
    be rejected downstream" case."""
    from app.domains.assessments_runtime.role_support import is_resolved
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "advanced-then-rejected-org")
    MockClient, state = _client_returning([
        # 1: in review → imported by the normal pipeline (not advanced yet).
        [{"id": "cand_x", "email": "x@example.com", "name": "Ada Adv", "stage": "Review"}],
        # 2: moved to Offer → parked `advanced`, outcome still open → FROZEN.
        [{"id": "cand_x", "email": "x@example.com", "name": "Ada Adv", "stage": "Offer"}],
        # 3: rejected downstream, after the handoff.
        [{"id": "cand_x", "email": "x@example.com", "name": "Ada Adv", "stage": "Rejected"}],
    ])
    service = WorkableSyncService(MockClient())

    def _app():
        return db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.workable_candidate_id == "cand_x",
        ).first()

    service.sync_org(db, org)  # run 0: Review → imported

    # Advance + freeze.
    state["calls"] = 1
    service.sync_org(db, org)  # run 1: Offer
    app = _app()
    assert app is not None
    assert app.pipeline_stage == "advanced"
    assert app.application_outcome == "open"
    assert is_resolved(app)  # genuinely frozen now

    # Rejected after the handoff — the freeze must NOT block this flip.
    state["calls"] = 2
    service.sync_org(db, org)  # run 2: Rejected
    db.refresh(app)
    assert app.application_outcome == "rejected"
    assert app.pipeline_stage == "advanced"


def test_score_advanced_for_training_selects_unscored_advanced(db):
    """The calibration scorer targets ALL `advanced` candidates lacking a score
    (any stage/outcome incl. rejects), skips already-scored ones, and honors
    the limit. Dry-run, so no Anthropic calls."""
    from app.scripts.score_advanced_for_training import score_advanced_for_training
    from app.models.role import Role
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "calib-select-org")
    role = Role(organization_id=org.id, name="Calib Role", job_spec_text="Need an engineer.")
    db.add(role)
    db.flush()

    def _mk(email, stage, outcome, scored, cv):
        c = Candidate(organization_id=org.id, email=email, full_name="X", cv_text=cv)
        db.add(c)
        db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            pipeline_stage=stage, pipeline_stage_source="sync",
            application_outcome=outcome, cv_text=cv,
            cv_match_score=(70.0 if scored else None),
        )
        db.add(a)
        db.flush()
        return a

    _mk("a@x.com", "advanced", "open", scored=False, cv="cv text")        # match (offer-ish)
    _mk("b@x.com", "advanced", "rejected", scored=False, cv=None)         # match (reject negative, needs CV)
    _mk("c@x.com", "advanced", "open", scored=True, cv="cv text")         # skip — already scored
    _mk("d@x.com", "applied", "open", scored=False, cv="cv text")         # skip — not advanced
    db.commit()

    summary = score_advanced_for_training(db, target_stages=None, apply=False)
    assert summary["matched"] == 2

    summary_limited = score_advanced_for_training(db, target_stages=None, apply=False, limit=1)
    assert summary_limited["matched"] == 1


def test_outcome_flip_back_is_recorded(db):
    """A permanent per-outcome idempotency key would block a legitimate flip
    back to a previously-seen outcome. After rejected -> hired -> rejected the
    final outcome must be `rejected`, not stuck at `hired`."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "outcome-flip-org")
    MockClient, state = _client_returning([
        [{"id": "cand_flip", "email": "flip@example.com", "name": "Flip", "stage": "Review"}],
        [{"id": "cand_flip", "email": "flip@example.com", "name": "Flip", "stage": "Rejected"}],
        [{"id": "cand_flip", "email": "flip@example.com", "name": "Flip",
          "stage": "Hired", "hired_at": "2026-05-22T09:00:00Z"}],
        [{"id": "cand_flip", "email": "flip@example.com", "name": "Flip", "stage": "Rejected"}],
    ])
    service = WorkableSyncService(MockClient())
    for i in range(4):
        state["calls"] = i
        service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_flip",
    ).first()
    assert app is not None
    assert app.application_outcome == "rejected"


def test_email_linked_terminal_app_gets_outcome_captured(db):
    """An app linked only by candidate email (no workable_candidate_id yet) must
    still receive terminal outcome capture, and have its Workable id backfilled."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "email-linked-terminal-org")
    MockClient, state = _client_returning([
        [{"id": "cand_email", "email": "linked@example.com", "name": "Linked", "stage": "Review"}],
        [{"id": "cand_email", "email": "linked@example.com", "name": "Linked", "stage": "Rejected"}],
    ])
    service = WorkableSyncService(MockClient())
    service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_email",
    ).first()
    # Simulate a row linked only by email (Workable id not yet attached).
    app.workable_candidate_id = None
    db.commit()

    state["calls"] = 1
    service.sync_org(db, org)
    db.refresh(app)
    assert app.application_outcome == "rejected"
    assert app.pipeline_stage == "advanced"
    assert app.workable_candidate_id == "cand_email"  # backfilled by the email-fallback lookup


def test_resolved_candidate_is_frozen_except_workable_stage(db):
    """Once a candidate is resolved (advanced/hired/rejected) they are frozen on
    Tali: later syncs must NOT re-enrich their profile, but their Workable stage
    keeps updating so the trail stays accurate for model refinement."""
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "frozen-resolved-org")
    MockClient, state = _client_returning([
        [{"id": "cand_frozen", "email": "frozen@example.com", "name": "Original Name",
          "stage": "Review"}],
        # Recruiter moved them forward in Workable (non-terminal) and the name
        # changed upstream — the name change must be ignored (frozen), the stage
        # must update.
        [{"id": "cand_frozen", "email": "frozen@example.com", "name": "Changed Name",
          "stage": "Offer"}],
    ])
    service = WorkableSyncService(MockClient())
    service.sync_org(db, org)

    # Simulate a Tali hand-back decision putting them in `advanced` (resolved).
    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_frozen",
    ).first()
    app.pipeline_stage = "advanced"
    app.pipeline_stage_source = "recruiter"
    db.commit()

    state["calls"] = 1
    service.sync_org(db, org)

    candidate = db.query(Candidate).filter(
        Candidate.organization_id == org.id,
        Candidate.workable_candidate_id == "cand_frozen",
    ).first()
    db.refresh(app)
    # Profile frozen — name not re-enriched from the new payload.
    assert candidate.full_name == "Original Name"
    # Workable stage still tracked.
    assert (app.workable_stage or "").lower() == "offer"
    assert app.pipeline_stage == "advanced"


def test_resolved_candidate_activity_feed_still_refreshes(db, monkeypatch):
    """A resolved (frozen) candidate must still pick up Workable notes added
    AFTER the decision — recruiter comments/ratings posted post-handover. The
    profile stays frozen (no re-enrichment) but the read-only activity feed
    refreshes, debounced so it can't hammer Workable each cycle."""
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.services.workable_context_service import workable_recruiter_comments

    monkeypatch.setattr(
        "app.components.integrations.workable.sync_service.settings.ANTHROPIC_API_KEY", None
    )
    org = _make_org(db, "frozen-notes-refresh-org")

    feed = [{"id": "a1", "action": "applied", "created_at": "2026-05-22T03:00:00Z"}]

    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")
            self.activity_calls = 0

        def list_open_jobs(self):
            return [{"id": "J1", "shortcode": "J1", "title": "AI Engineer"}]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            return [{"id": "cand_note", "email": "note@example.com",
                     "name": "Original Name", "stage": "Interview"}]

        def get_job_details(self, job_identifier):
            return {}

        def get_candidate(self, candidate_id):
            # Upstream name changed — must be ignored while frozen.
            return {"id": candidate_id, "email": "note@example.com",
                    "name": "Changed Name", "stage": "Interview"}

        def download_candidate_resume(self, candidate_payload):
            return None  # no CV → keep the prefetch offline

        def get_candidate_activities(self, candidate_id):
            self.activity_calls += 1
            return list(feed)

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    client = MockClient()
    service = WorkableSyncService(client)

    # First sync creates the application.
    service.sync_org(db, org)
    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_note",
    ).first()
    assert app is not None

    # Tali hands them back → advanced (resolved/frozen).
    app.pipeline_stage = "advanced"
    app.pipeline_stage_source = "recruiter"
    db.commit()

    # A recruiter posts a rating-with-note in Workable AFTER the decision.
    feed.append({
        "id": "a2", "action": "rating",
        "body": "Interviewed very well; concern he's too senior.",
        "member": {"name": "Saniul Islam"},
        "created_at": "2026-06-03T04:43:00Z",
    })

    # Full sync: the frozen branch must still refresh the read-only feed.
    service.sync_org(db, org, mode="full")

    candidate = db.query(Candidate).filter(
        Candidate.organization_id == org.id,
        Candidate.workable_candidate_id == "cand_note",
    ).first()
    db.refresh(app)
    # Profile still frozen…
    assert candidate.full_name == "Original Name"
    # …but the post-decision note landed in the stored feed and surfaces as a note.
    assert "rating" in [a.get("action") for a in (candidate.workable_activities or [])]
    assert any("too senior" in (n["body"] or "") for n in workable_recruiter_comments(candidate))
    assert app.integration_sync_state.get("last_activities_fetch_at")
    assert client.activity_calls == 1

    # Immediate re-sync: debounce prevents another feed fetch.
    service.sync_org(db, org, mode="full")
    assert client.activity_calls == 1


def test_brand_new_disqualified_candidate_is_not_imported(db):
    """A candidate first seen already disqualified has nothing to act on — we
    don't create an application for them."""
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "disqualified-new-skip-org")
    MockClient, _ = _client_returning([
        [{"id": "cand_new_dq", "email": "newdq@example.com", "name": "Already Out",
          "stage": "Technical Interview", "disqualified": True}],
    ])
    service = WorkableSyncService(MockClient())
    service.sync_org(db, org)

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_new_dq",
    ).first()
    assert app is None


def test_sync_org_yields_to_pending_op_at_job_boundary(db):
    """A periodic sync holds the per-org Workable mutex for its whole run, which
    can starve a waiting user-facing write (decision approval/override). sync_org
    polls ``should_yield`` at each job boundary and, once it fires, stops without
    touching the remaining jobs so the waiting op can take the lock. Regression
    for the lock-starvation incident where a long candidate sync timed approve
    batches out with "Workable lock timeout"."""
    from app.models.organization import Organization

    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def list_open_jobs(self):
            return [
                {"id": "J1", "shortcode": "J1", "title": "Role One"},
                {"id": "J2", "shortcode": "J2", "title": "Role Two"},
            ]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            return [{"id": f"cand_{job_identifier}", "email": "c@example.com", "name": "C", "stage": "Screening"}]

        def get_job_details(self, job_identifier):
            return {}

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org = Organization(
        name="Yield Org",
        slug="yield-org-workable-sync",
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    service = WorkableSyncService(MockClient())

    # An op is already pending: yield at the very first boundary, process no
    # jobs, and surface the reason.
    result = service.sync_org(db, org, mode="full", should_yield=lambda: True)
    assert result["jobs_processed"] == 0
    assert result["candidates_seen"] == 0
    assert any("pending Workable write" in e for e in result["errors"])

    # The op arrives just as the first job finishes: yield at the next job
    # boundary. sync_org now polls should_yield three times per job (job-top,
    # pre-prefetch, per-candidate), so for a 1-candidate job the first job
    # consumes 3 polls; flip on the 4th — the second job's top boundary — to
    # process exactly the first job, then yield before the next.
    # (Yielding part-way through a single big job is covered by
    # test_sync_yields_mid_job_to_a_pending_op.)
    calls = {"n": 0}

    def yield_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    result2 = service.sync_org(db, org, mode="full", should_yield=yield_after_first)
    assert result2["jobs_processed"] == 1
    assert result2["candidates_seen"] == 1

    # No yield signal → the whole sync runs as before (both jobs).
    result3 = service.sync_org(db, org, mode="full", should_yield=lambda: False)
    assert result3["jobs_processed"] == 2
    assert result3["candidates_seen"] == 2


def _two_published_jobs_client():
    class MockClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def list_open_jobs(self):
            return [
                {"id": "J1", "shortcode": "J1", "title": "Existing Role", "state": "published"},
                {"id": "J2", "shortcode": "J2", "title": "Brand New Role", "state": "published"},
            ]

        def list_job_candidates(self, job_identifier, *, paginate=False, max_pages=None):
            return [{"id": f"cand_{job_identifier}", "email": f"{job_identifier}@example.com",
                     "name": "C", "stage": "Screening"}]

        def get_job_details(self, job_identifier):
            return {}

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    return MockClient


def test_discover_new_jobs_creates_role_for_unselected_new_posting(db):
    """A scoped candidate sync (selected_job_shortcodes) with discover_new_jobs=True
    creates a role for a brand-new Workable posting it isn't scoped to — WITHOUT
    fetching that job's candidates. This is the fix for newly-published jobs being
    starved by the 15-min jobs_only sweep losing the per-org mutex race; discovery
    rides the candidate syncs that reliably hold the lock."""
    from app.models.role import Role
    from app.models.candidate_application import CandidateApplication

    org = _make_org(db, "discover-new-jobs-org")
    service = WorkableSyncService(_two_published_jobs_client()())

    # Steady state: J1 already a role from a prior scoped sync; J2 not yet seen.
    service.sync_org(db, org, mode="full", selected_job_shortcodes=["J1"])
    assert db.query(Role).filter(Role.organization_id == org.id, Role.workable_job_id == "J2").first() is None

    # Scoped candidate sync with discovery on: J2 appears as a role, no J2 candidates.
    summary = service.sync_org(
        db, org, mode="full", selected_job_shortcodes=["J1"], discover_new_jobs=True,
    )

    j2 = db.query(Role).filter(Role.organization_id == org.id, Role.workable_job_id == "J2").first()
    assert j2 is not None
    assert "J2" in (summary.get("discovered_new_jobs") or [])
    # Published new job auto-stars so the NEXT candidate tick pulls its applicants.
    assert j2.starred_for_auto_sync is True
    # …but discovery did NOT fetch J2's candidates this run.
    assert db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_J2",
    ).first() is None
    # The scoped job J1 still synced its candidate normally.
    assert db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id,
        CandidateApplication.workable_candidate_id == "cand_J1",
    ).first() is not None


def test_discovery_off_by_default_leaves_unselected_jobs_untouched(db):
    """The user-facing selective sync (discover_new_jobs defaults to False) must stay
    scoped — it must NOT create roles for jobs the user didn't pick."""
    from app.models.role import Role

    org = _make_org(db, "discovery-off-default-org")
    service = WorkableSyncService(_two_published_jobs_client()())
    service.sync_org(db, org, mode="full", selected_job_shortcodes=["J1"])  # discover defaults False

    assert db.query(Role).filter(Role.organization_id == org.id, Role.workable_job_id == "J1").first() is not None
    assert db.query(Role).filter(Role.organization_id == org.id, Role.workable_job_id == "J2").first() is None


def test_discovery_is_create_only_never_resurrects_deleted_role(db):
    """Discovery must not resurrect a role a recruiter soft-deleted: a Workable job
    that already has a (soft-deleted) role row is left alone — only the jobs_only
    sweep / manual full sync restore those."""
    from datetime import datetime, timezone

    from app.models.role import Role

    org = _make_org(db, "discovery-create-only-org")
    service = WorkableSyncService(_two_published_jobs_client()())
    # Create J2 as a role, then soft-delete it (recruiter removed it from Tali).
    service.sync_org(db, org, mode="full", selected_job_shortcodes=["J2"])
    j2 = db.query(Role).filter(Role.organization_id == org.id, Role.workable_job_id == "J2").first()
    assert j2 is not None
    j2.deleted_at = datetime.now(timezone.utc)
    db.commit()

    # A scoped J1 sync with discovery on must NOT un-delete J2.
    service.sync_org(db, org, mode="full", selected_job_shortcodes=["J1"], discover_new_jobs=True)
    db.refresh(j2)
    assert j2.deleted_at is not None  # still soft-deleted; discovery is create-only
