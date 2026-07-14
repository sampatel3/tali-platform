from app.services.fit_matching_service import (
    CV_MATCH_PROMPT,
    CV_MATCH_V4_PROMPT,
    _safe_summary,
)


def test_all_fit_matching_prompts_request_a_brief_synthesis():
    for prompt in (CV_MATCH_PROMPT, CV_MATCH_V4_PROMPT):
        assert "2-3 concise plain-English sentences" in prompt
        assert "aiming for about 75 words" in prompt
        assert "guidance, not a hard cutoff" in prompt
        assert "summary is a synthesis, not a miniature report" in prompt
        assert "structured fields above carry that detail" in prompt
        assert "Do not state a policy decision" in prompt
        assert "2-3 sentence summary" not in prompt


def test_fit_matching_preserves_the_full_claude_summary():
    generated = (
        "Strong role evidence across production-scale data platforms.\n"
        "The material uncertainty is direct knowledge-graph ownership. "
        + "Additional authored context remains present. " * 20
    ).strip()
    assert len(generated) > 600

    normalised = _safe_summary(generated)

    assert normalised == generated.replace("\n", " ")
    assert not normalised.endswith("\u2026")
