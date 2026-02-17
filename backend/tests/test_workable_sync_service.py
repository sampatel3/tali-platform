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
