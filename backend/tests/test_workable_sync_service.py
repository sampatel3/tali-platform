"""Unit tests for Workable sync service - formatting, candidate detection, terminal stage logic."""

import pytest

from app.components.integrations.workable.sync_service import (
    _format_job_spec_from_api,
    _strip_html,
    _is_terminal_candidate,
    _candidate_email,
    _normalize_stage_for_terminal,
)


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

    def test_disqualified_flag(self):
        assert _is_terminal_candidate({"disqualified": True}) is True

    def test_hired_at(self):
        assert _is_terminal_candidate({"hired_at": "2024-01-01"}) is True


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


class TestNormalizeStageForTerminal:
    def test_exact_match(self):
        assert _normalize_stage_for_terminal("hired") == "hired"
        assert _normalize_stage_for_terminal("rejected") == "rejected"

    def test_case_insensitive(self):
        assert _normalize_stage_for_terminal("Hired") == "hired"

    def test_non_terminal(self):
        assert _normalize_stage_for_terminal("screening") is None
        assert _normalize_stage_for_terminal("") is None


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
