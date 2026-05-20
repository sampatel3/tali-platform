"""Pre-screen prompt assembly with the Workable-context block.

Regression guard: when a candidate has questionnaire answers or
recruiter comments containing hard-constraint info (salary, notice
period, location), the rendered prompt must include them so the LLM
can filter on them. The cache key must also be sensitive to the
context so refreshed Workable metadata busts stale cached scores.
"""

from __future__ import annotations

from app.cv_matching.prompts_pre_screen import (
    build_pre_screen_messages,
    build_pre_screen_prompt,
)
from app.cv_matching.runner_pre_screen import compute_pre_screen_cache_key
from app.cv_matching.schemas import Priority, RequirementInput


_REQS = [
    RequirementInput(
        id="r1",
        requirement="Salary expectation below 60,000 GBP",
        priority=Priority.MUST_HAVE,
    ),
]


def test_prompt_collapses_when_workable_context_empty():
    prompt = build_pre_screen_prompt(
        cv_text="Senior engineer.",
        jd_text="Hiring a senior engineer.",
        requirements=_REQS,
        workable_context=None,
    )
    assert "<CANDIDATE_CV>" in prompt
    # No empty WORKABLE_* tags leaking in.
    assert "<WORKABLE_" not in prompt


def test_prompt_includes_workable_context_when_present():
    context = (
        "<WORKABLE_QUESTIONNAIRE_ANSWERS>\n"
        "Q: What is your salary expectation?\nA: 65,000 GBP\n"
        "</WORKABLE_QUESTIONNAIRE_ANSWERS>"
    )
    prompt = build_pre_screen_prompt(
        cv_text="Senior engineer.",
        jd_text="Hiring a senior engineer.",
        requirements=_REQS,
        workable_context=context,
    )
    assert "WORKABLE_QUESTIONNAIRE_ANSWERS" in prompt
    assert "65,000 GBP" in prompt


def test_messages_keep_static_block_clean_for_caching():
    """The static (cached) block must NOT contain per-candidate data."""
    context = "<WORKABLE_QUESTIONNAIRE_ANSWERS>Q: salary?\nA: 65k</WORKABLE_QUESTIONNAIRE_ANSWERS>"
    messages = build_pre_screen_messages(
        cv_text="cv",
        jd_text="jd",
        requirements=_REQS,
        workable_context=context,
    )
    static_block = messages[0]["content"][0]["text"]
    variable_block = messages[0]["content"][1]["text"]
    # The instruction prose references WORKABLE_* tag names — fine. What
    # must NOT leak into the static block is the per-candidate data.
    assert "65k" not in static_block
    assert "65k" in variable_block
    # Static block keeps cache_control for cross-candidate cache hits.
    assert messages[0]["content"][0].get("cache_control")


def test_cache_key_changes_with_workable_context():
    """A new questionnaire answer must invalidate cached pre-screen scores."""
    base = dict(cv_text="cv", jd_text="jd", requirements=_REQS)
    no_ctx = compute_pre_screen_cache_key(**base, workable_context=None)
    with_ctx = compute_pre_screen_cache_key(
        **base, workable_context="<WORKABLE_QUESTIONNAIRE_ANSWERS>x</WORKABLE_QUESTIONNAIRE_ANSWERS>"
    )
    other_ctx = compute_pre_screen_cache_key(
        **base, workable_context="<WORKABLE_QUESTIONNAIRE_ANSWERS>y</WORKABLE_QUESTIONNAIRE_ANSWERS>"
    )
    assert no_ctx != with_ctx
    assert with_ctx != other_ctx
