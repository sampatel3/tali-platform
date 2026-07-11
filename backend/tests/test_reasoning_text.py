"""humanize_reasoning — display-time cleanup of agent machine-voice."""

from app.domains.agentic._reasoning_text import humanize_reasoning


def test_strips_parenthesized_internal_ids():
    assert humanize_reasoning("Aiazuddin (52407) scores 78.0") == "Aiazuddin scores 78.0"


def test_keeps_legitimate_parenthesized_numbers():
    # Thresholds/scores in parens are meaningful — only bare IDs (4+ digits) go.
    assert humanize_reasoning("well above send threshold (70)") == "well above send threshold (70)"
    assert humanize_reasoning("a perfect pre-screen (100)") == "a perfect pre-screen (100)"


def test_scorer_keys_become_words():
    text = "(role_fit, pre_screen, cv_match all 78.0). Policy fires on role_fit + pre_screen."
    assert humanize_reasoning(text) == (
        "(role fit, pre-screen, CV match all 78.0). Policy triggered on role fit + pre-screen."
    )


def test_workable_stage_key_value_reads_as_sentence():
    text = "Externally advanced (workable_stage=Technical Interview). Minor gaps."
    assert humanize_reasoning(text) == (
        'Externally advanced (already at "Technical Interview" in Workable). Minor gaps.'
    )


def test_pipeline_stage_and_snake_values():
    assert humanize_reasoning('pipeline_stage="advanced" set') == 'pipeline stage "advanced" set'
    assert (
        humanize_reasoning("workable_stage=phone_screen")
        == 'already at "phone screen" in Workable'
    )


def test_clean_text_is_untouched():
    text = "Strong fit for the greenfield Azure platform architect role."
    assert humanize_reasoning(text) == text


def test_empty_and_none_safe():
    assert humanize_reasoning("") == ""
