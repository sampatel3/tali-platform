"""Tests for backend/app/cv_matching/prompts.py.

Specifically asserts that when recruiter requirements are provided, the
prompt forbids the LLM from synthesising additional `jd_req_*`
requirements — the source of the "lots of technical matching, no
recruiter requirement matching" complaint.
"""

from __future__ import annotations

from app.cv_matching.prompts import (
    CV_MATCH_PROMPT_V3,
    build_cv_match_prompt,
    render_additional_requirements,
)
from app.cv_matching.schemas import Priority, RequirementInput


def test_prompt_template_forbids_jd_synthesis_when_recruiter_reqs_present():
    """The hard-coded prompt template (rule 4) must instruct the LLM to
    treat recruiter requirements as the ONLY entries in the assessment
    list. This is the D2 piece of the recruiter-weighting fix."""
    text = CV_MATCH_PROMPT_V3
    # Specific phrases that gate the LLM behaviour:
    assert "MUST contain ONLY those entries" in text
    assert "Do not synthesize additional" in text
    assert "treat the JD as supporting context" in text


def test_render_additional_requirements_includes_disqualifying_marker():
    reqs = [
        RequirementInput(
            id="crit_recruiter_1",
            requirement="5+ years AWS Glue",
            priority=Priority.MUST_HAVE,
            disqualifying_if_missing=True,
        ),
        RequirementInput(
            id="crit_recruiter_2",
            requirement="Bachelor's degree",
            priority=Priority.STRONG_PREFERENCE,
        ),
    ]
    rendered = render_additional_requirements(reqs)
    assert "DISQUALIFYING" in rendered
    assert "5+ years AWS Glue" in rendered
    assert "Bachelor's degree" in rendered
    assert "(id: crit_recruiter_1)" in rendered


def test_build_cv_match_prompt_emits_recruiter_requirements_block():
    reqs = [
        RequirementInput(
            id="crit_recruiter_1",
            requirement="5+ years AWS Glue",
            priority=Priority.MUST_HAVE,
        ),
    ]
    prompt = build_cv_match_prompt(
        cv_text="Senior data engineer",
        jd_text="Hiring an AWS Glue engineer",
        requirements=reqs,
    )
    assert "RECRUITER-ADDED REQUIREMENTS" in prompt
    assert "crit_recruiter_1" in prompt
    # The JD-synthesis ban must reach the rendered prompt too.
    assert "MUST contain ONLY those entries" in prompt
