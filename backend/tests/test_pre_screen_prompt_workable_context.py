"""Pre-screen prompt assembly with the Workable-context block.

Regression guard: when a candidate has questionnaire answers or
recruiter comments containing hard-constraint info (salary, notice
period, location), the rendered prompt must include them so the LLM
can filter on them. The cache key must also be sensitive to the
context so refreshed Workable metadata busts stale cached scores.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.cv_matching.prompts_pre_screen import (
    build_pre_screen_messages,
    build_pre_screen_prompt,
)
from app.cv_matching.runner_pre_screen import (
    compute_pre_screen_cache_key,
    run_pre_screen,
)
from app.cv_matching.schemas import Priority, RequirementInput
from app.services.workable_context_contract import StructuredWorkableContext


_REQS = [
    RequirementInput(
        id="r1",
        requirement="Salary expectation below 60,000 GBP",
        priority=Priority.MUST_HAVE,
    ),
]

_REQS_WITH_CONSTRAINT = [
    *_REQS,
    RequirementInput(
        id="r2",
        requirement="Must already have UAE work authorisation",
        priority=Priority.CONSTRAINT,
    ),
    RequirementInput(
        id="r3",
        requirement="Experience with FastAPI",
        priority=Priority.STRONG_PREFERENCE,
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


def test_prompt_labels_and_preserves_recruiter_hard_constraints():
    prompt = build_pre_screen_prompt(
        cv_text="Senior engineer.",
        jd_text="Hiring a senior engineer.",
        requirements=_REQS_WITH_CONSTRAINT,
    )

    assert "[MUST HAVE] Salary expectation below 60,000 GBP" in prompt
    assert "[HARD CONSTRAINT] Must already have UAE work authorisation" in prompt
    assert "Experience with FastAPI" not in prompt


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


def test_cache_key_covers_constraint_text_and_priority():
    base = dict(cv_text="cv", jd_text="jd", workable_context=None)
    constraint_key = compute_pre_screen_cache_key(
        **base,
        requirements=[_REQS_WITH_CONSTRAINT[1]],
    )
    changed_text_key = compute_pre_screen_cache_key(
        **base,
        requirements=[
            RequirementInput(
                id="r2",
                requirement="Must already have UK work authorisation",
                priority=Priority.CONSTRAINT,
            )
        ],
    )
    must_have_key = compute_pre_screen_cache_key(
        **base,
        requirements=[
            RequirementInput(
                id="r2",
                requirement="Must already have UAE work authorisation",
                priority=Priority.MUST_HAVE,
            )
        ],
    )

    assert constraint_key != changed_text_key
    assert constraint_key != must_have_key


def test_pre_screen_rejects_oversized_protected_evidence_before_cache_or_provider():
    context = StructuredWorkableContext(
        [("WORKABLE_RECRUITER_COMMENTS", "constraint " * 4_000)]
    )
    client = MagicMock()

    with patch(
        "app.cv_matching.runner_pre_screen._cache_get",
        side_effect=AssertionError("oversized protected evidence consulted cache"),
    ):
        result = run_pre_screen(
            "Senior engineer",
            "Hiring a senior engineer",
            requirements=_REQS,
            client=client,
            workable_context=context,
        )

    assert result.decision == "error"
    assert result.reason == "protected_workable_evidence_too_large"
    assert result.cache_hit is False
    client.messages.create.assert_not_called()


def test_pre_screen_cache_key_ignores_legacy_context_outside_provider_window():
    base = dict(cv_text="cv", jd_text="jd", requirements=_REQS)
    visible_prefix = "x" * 2_500

    first = compute_pre_screen_cache_key(
        **base,
        workable_context=visible_prefix + "first invisible suffix",
    )
    second = compute_pre_screen_cache_key(
        **base,
        workable_context=visible_prefix + "second invisible suffix",
    )

    assert first == second


# --------------------------------------------------------------------------- #
# System-block prompt caching (2026-05-22)                                     #
# --------------------------------------------------------------------------- #
#
# Pre-screen previously cached via a cache_control'd block at the front of
# the USER message, which produced zero cache hits in production despite a
# byte-identical >2K-token static block. Moving the stable content into a
# cache_control'd SYSTEM block (Anthropic's canonical caching location) is
# the fix. These guard the new build_pre_screen_system /
# build_pre_screen_user_messages split.

from app.cv_matching.prompts_pre_screen import (  # noqa: E402
    build_pre_screen_system,
    build_pre_screen_user_messages,
)


def test_system_block_carries_cache_control_and_jd():
    system = build_pre_screen_system("ACME backend role, Python required", _REQS)
    assert isinstance(system, list) and len(system) == 1
    block = system[0]
    assert block["type"] == "text"
    assert block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    assert "ACME backend role" in block["text"]
    # Must-have requirement text is part of the stable cached prefix.
    assert "Salary expectation" in block["text"]


def test_system_block_identical_across_candidates():
    """The whole point of caching — the system block must be byte-identical
    for every candidate in a role batch so Anthropic reuses the prefix."""
    a = build_pre_screen_system("same JD", _REQS)
    b = build_pre_screen_system("same JD", _REQS)
    assert a[0]["text"] == b[0]["text"]


def test_user_message_holds_only_candidate_cv():
    msgs = build_pre_screen_user_messages("ALICE CV here", workable_context=None)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    content = msgs[0]["content"]
    assert "ALICE CV here" in content
    # No cache_control on the per-candidate message (it's the variable part).
    assert isinstance(content, str)
