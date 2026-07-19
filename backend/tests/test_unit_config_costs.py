from app.platform.config import Settings
from pathlib import Path

import pytest


def test_resolved_claude_model_defaults_to_haiku_when_empty():
    settings = Settings(CLAUDE_MODEL="")
    assert settings.resolved_claude_model == "claude-haiku-4-5-20251001"


def test_resolved_claude_model_defaults_to_haiku_when_whitespace():
    settings = Settings(CLAUDE_MODEL="   ")
    assert settings.resolved_claude_model == "claude-haiku-4-5-20251001"


def test_resolved_claude_model_uses_explicit_config():
    settings = Settings(CLAUDE_MODEL="claude-custom-override")
    assert settings.resolved_claude_model == "claude-custom-override"


def test_resolved_claude_scoring_model_uses_claude_model():
    settings = Settings(CLAUDE_MODEL="claude-haiku-4-5-20251001", CLAUDE_SCORING_MODEL="")
    assert settings.resolved_claude_scoring_model == "claude-haiku-4-5-20251001"


def test_legacy_scoring_model_mismatch_fails_fast():
    with pytest.raises(ValueError):
        Settings(
            CLAUDE_MODEL="claude-3-5-haiku-latest",
            CLAUDE_SCORING_MODEL="claude-3-5-sonnet-20241022",
        )


def test_resolved_claude_scoring_model_uses_batch_override():
    settings = Settings(
        CLAUDE_MODEL="claude-sonnet-4-5",
        CLAUDE_SCORING_BATCH_MODEL="claude-haiku-4-5-20251001",
    )
    assert settings.resolved_claude_scoring_model == "claude-haiku-4-5-20251001"


def test_resolved_claude_scoring_model_falls_back_to_claude_model_when_batch_empty():
    settings = Settings(
        CLAUDE_MODEL="claude-sonnet-4-5",
        CLAUDE_SCORING_BATCH_MODEL="",
    )
    assert settings.resolved_claude_scoring_model == "claude-sonnet-4-5"


def test_unimplemented_graph_outcome_prior_cannot_be_enabled():
    with pytest.raises(ValueError, match="GRAPH_OUTCOME_PRIOR_ENABLED cannot be enabled"):
        Settings(GRAPH_OUTCOME_PRIOR_ENABLED=True)


@pytest.mark.parametrize("episode_cap", [0, 101])
def test_graphiti_episode_cap_must_fit_the_immutable_manifest(episode_cap):
    with pytest.raises(
        ValueError,
        match="GRAPHITI_MAX_EPISODES_PER_CANDIDATE",
    ):
        Settings(GRAPHITI_MAX_EPISODES_PER_CANDIDATE=episode_cap)


def test_superseded_warmup_calibration_settings_are_not_exposed():
    settings = Settings()
    assert not hasattr(settings, "MVP_DISABLE_CALIBRATION")
    assert not hasattr(settings, "DEFAULT_CALIBRATION_PROMPT")
    assert not hasattr(settings.mvp_flags, "disable_calibration")


@pytest.mark.parametrize("rounds", [3, 32])
def test_bcrypt_rounds_reject_values_outside_algorithm_bounds(rounds):
    with pytest.raises(ValueError, match="BCRYPT_ROUNDS"):
        Settings(BCRYPT_ROUNDS=rounds)


def test_fireflies_legacy_rate_limit_rejects_negative_values():
    with pytest.raises(ValueError, match="FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE"):
        Settings(FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE=-1)


def test_env_example_exposes_current_webhook_limit_without_retired_lemon_keys():
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text()

    assert "FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE=120" in example
    for retired_key in (
        "LEMON_API_KEY",
        "LEMON_STORE_ID",
        "LEMON_WEBHOOK_SECRET",
        "LEMON_TEST_MODE",
        "LEMON_PACKS_JSON",
    ):
        assert retired_key not in example


def test_fraud_actions_are_normalized():
    settings = Settings(
        FRAUD_COPY_PASTE_ACTION=" CAP ",
        FRAUD_HIDDEN_TEXT_ACTION=" FLAG ",
    )
    assert settings.FRAUD_COPY_PASTE_ACTION == "cap"
    assert settings.FRAUD_HIDDEN_TEXT_ACTION == "flag"


@pytest.mark.parametrize(
    "field_name",
    ["FRAUD_COPY_PASTE_ACTION", "FRAUD_HIDDEN_TEXT_ACTION"],
)
def test_invalid_fraud_actions_fail_fast(field_name):
    with pytest.raises(ValueError, match=field_name):
        Settings(**{field_name: "reject"})
