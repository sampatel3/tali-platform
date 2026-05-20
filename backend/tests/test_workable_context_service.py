"""Coverage for the Workable-context formatter.

The pre-screen LLM was historically CV-only. Hard constraints expressed
in Workable (e.g. salary expectation in a LinkedIn-apply questionnaire
answer, or a notice-period recruiter comment) were invisible. The
formatter assembled here is what surfaces those signals so the
pre-screen prompt can filter on them.
"""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.services.workable_context_service import format_workable_context


def _candidate(**fields) -> Candidate:
    base = dict(
        organization_id=1,
        email="c@x.test",
        full_name="Test Candidate",
    )
    base.update(fields)
    return Candidate(**base)


def test_no_candidate_renders_empty_string():
    """When no candidate row is loaded the block collapses cleanly."""
    assert format_workable_context(None, None) == ""


def test_bare_candidate_renders_only_profile_block():
    """A candidate row with just name/email yields only the profile block."""
    cand = _candidate(workable_data=None)
    out = format_workable_context(cand, None)
    assert "<WORKABLE_PROFILE>" in out
    assert "<WORKABLE_QUESTIONNAIRE_ANSWERS>" not in out
    assert "<WORKABLE_RECRUITER_COMMENTS>" not in out
    assert "<WORKABLE_ACTIVITY_LOG>" not in out


def test_questionnaire_answer_with_salary_is_surfaced():
    """The LinkedIn-apply case: salary expectation lives in answers.body."""
    cand = _candidate(
        workable_data={
            "answers": [
                {
                    "question": {"body": "What is your salary expectation?"},
                    "body": "65,000 GBP",
                }
            ]
        }
    )
    out = format_workable_context(cand, None)
    assert "<WORKABLE_QUESTIONNAIRE_ANSWERS>" in out
    assert "salary expectation" in out.lower()
    assert "65,000 GBP" in out


def test_questionnaire_choice_answer_surfaces_selected_options():
    cand = _candidate(
        workable_data={
            "answers": [
                {
                    "question_key": "willing_to_relocate",
                    "choices": [
                        {"body": "Yes", "selected": False},
                        {"body": "No", "selected": True},
                    ],
                }
            ]
        }
    )
    out = format_workable_context(cand, None)
    assert "willing_to_relocate" in out
    assert "Selected: No" in out


def test_questionnaire_boolean_answer_surfaces_checked():
    cand = _candidate(
        workable_data={
            "answers": [
                {
                    "question": {"body": "Do you have a UK work visa?"},
                    "checked": False,
                }
            ]
        }
    )
    out = format_workable_context(cand, None)
    assert "UK work visa" in out
    assert "No" in out


def test_questionnaire_nested_answer_shape_from_production():
    """Workable returns answers in a nested shape in practice:
    ``{"question": {"body": "..."}, "answer": {"body": "25000"}}``.

    Regression: the formatter originally only handled the flat shape
    (``{"body": "...", "question_key": "..."}``) and silently dropped
    every answer that came in the nested form — which meant prod
    candidates whose salary expectation lived in the questionnaire
    were invisible to pre-screen.
    """
    cand = _candidate(
        workable_data={
            "answers": [
                {
                    "answer": {"body": "25000"},
                    "question": {
                        "body": "Please confirm your salary expectation for this role, monthly in AED"
                    },
                },
                {
                    "answer": {"body": "30days"},
                    "question": {"body": "Please confirm your current notice period"},
                },
                {
                    "answer": {"checked": False},
                    "question": {"body": "Do you currently live in the UAE?"},
                },
                {
                    "answer": {"checked": True},
                    "question": {"body": "Do you have 5+ years experience in data engineering roles?"},
                },
            ]
        }
    )
    out = format_workable_context(cand, None)
    assert "<WORKABLE_QUESTIONNAIRE_ANSWERS>" in out
    # The salary signal — the whole point of the change — must be present.
    assert "salary expectation" in out.lower()
    assert "25000" in out
    # Notice period text answer.
    assert "notice period" in out.lower()
    assert "30days" in out
    # Boolean answers render Yes/No.
    assert "UAE" in out
    assert "5+ years" in out


def test_recruiter_comment_with_salary_is_surfaced():
    cand = _candidate(
        workable_comments=[
            {
                "body": "Phone screen — candidate is asking for 70k, may negotiate.",
                "member": {"name": "Alex Recruiter"},
                "created_at": "2026-05-19T10:30:00Z",
            }
        ]
    )
    out = format_workable_context(cand, None)
    assert "<WORKABLE_RECRUITER_COMMENTS>" in out
    assert "70k" in out
    assert "Alex Recruiter" in out


def test_activity_log_renders_stage_transitions():
    cand = _candidate(
        workable_activities=[
            {
                "action": "moved",
                "stage_name": "Applied",
                "to_stage": "Phone Screen",
                "created_at": "2026-05-18T12:00:00Z",
            },
            {
                "action": "comment",
                "body": "Looks promising, schedule a call.",
                "created_at": "2026-05-19T09:00:00Z",
            },
        ]
    )
    out = format_workable_context(cand, None)
    assert "<WORKABLE_ACTIVITY_LOG>" in out
    assert "Applied → Phone Screen" in out
    assert "Looks promising" in out


def test_profile_block_includes_headline_location_and_application_stage():
    cand = _candidate(
        headline="Senior Backend Engineer",
        location_city="Dubai",
        location_country="UAE",
        phone="+971500000000",
    )
    app = CandidateApplication(
        organization_id=1,
        candidate_id=1,
        role_id=1,
        workable_stage="Phone Screen",
        workable_sourced=False,
    )
    out = format_workable_context(cand, app)
    assert "<WORKABLE_PROFILE>" in out
    assert "Senior Backend Engineer" in out
    assert "Dubai, UAE" in out
    assert "Phone Screen" in out
    assert "Inbound application" in out


def test_skills_and_tags_handle_dict_or_string_items():
    """Production stores skills/tags as either plain strings or
    ``{"name": "AWS"}`` dicts depending on the Workable endpoint
    version. Both must render as readable labels, not dict reprs."""
    cand = _candidate(
        skills=[
            {"name": "Amazon Web Services (AWS)"},
            {"name": "Python"},
            "JavaScript",
        ],
        tags=[{"name": "senior"}, "remote"],
    )
    out = format_workable_context(cand, None)
    assert "<WORKABLE_TAGS>" in out
    assert "Amazon Web Services (AWS)" in out
    assert "Python" in out
    assert "JavaScript" in out
    assert "senior" in out
    assert "remote" in out
    # No raw dict reprs leaked into the prompt.
    assert "'name':" not in out
    assert "{'" not in out


def test_skills_rescue_legacy_str_repr_rows():
    """Pre-migration rows stored skills as ``str(dict)`` reprs
    (``["{'name': 'AWS'}", ...]``). The formatter must extract the
    readable label from those legacy strings instead of leaking the
    Python repr into the LLM prompt."""
    cand = _candidate(
        skills=[
            "{'name': 'Amazon Web Services (AWS)'}",
            "{'name': 'Git'}",
            "{'name': 'JavaScript'}",
        ],
        tags=["{'name': 'senior'}"],
    )
    out = format_workable_context(cand, None)
    assert "Amazon Web Services (AWS)" in out
    assert "Git" in out
    assert "JavaScript" in out
    assert "senior" in out
    assert "'name':" not in out
    assert "{'" not in out


def test_education_and_experience_blocks_render():
    cand = _candidate(
        education_entries=[
            {
                "school": "MIT",
                "degree": "BSc",
                "field_of_study": "Computer Science",
                "start_date": "2010",
                "end_date": "2014",
            }
        ],
        experience_entries=[
            {
                "company": "Stripe",
                "title": "Senior Engineer",
                "start_date": "2020",
                "current": True,
                "summary": "Payments platform.",
            }
        ],
    )
    out = format_workable_context(cand, None)
    assert "<WORKABLE_EDUCATION>" in out
    assert "MIT" in out
    assert "<WORKABLE_EXPERIENCE>" in out
    assert "Stripe" in out
    assert "present" in out


def test_caps_protect_against_huge_payloads():
    """The formatter must not let a malicious or noisy payload blow the prompt."""
    cand = _candidate(
        workable_comments=[
            {"body": "spam " * 1000, "member": {"name": "x"}}
        ]
        * 200,  # 200 huge comments
    )
    out = format_workable_context(cand, None)
    # Should be present but bounded.
    assert "<WORKABLE_RECRUITER_COMMENTS>" in out
    # Per-field cap of 1200 chars + ellipsis ⇒ < 200 KB even worst case.
    assert len(out) < 100_000


def test_malformed_payloads_are_ignored_not_crash():
    """Workable's API shapes drift; we must not break the pre-screen path."""
    cand = _candidate(
        workable_data={"answers": "not-a-list"},  # wrong shape
        workable_comments="not-a-list",
        workable_activities=[{"junk": "no body/action"}],
        education_entries=[None, {}],
        experience_entries=[123],
    )
    # Should not raise; should not emit any populated sections from the bad input.
    out = format_workable_context(cand, None)
    assert "<WORKABLE_QUESTIONNAIRE_ANSWERS>" not in out
    assert "<WORKABLE_RECRUITER_COMMENTS>" not in out
    assert "<WORKABLE_ACTIVITY_LOG>" not in out
