"""execute_pre_screen_only must apply the fraud penalty before persisting.

These cover the standalone pre-screen path (the "Pre-screen new" batch
action). The orchestrator's gate path is covered separately.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.pre_screening_service import execute_pre_screen_only

from tests.sub_agents.conftest import make_full_application


_JD_TEXT = (
    "About the role\n"
    "We are hiring a Senior Backend Engineer to lead our payments platform. "
    "The ideal candidate has deep experience designing scalable distributed "
    "systems and is comfortable being on-call for production services.\n\n"
    "Responsibilities\n"
    "  - Own the architecture of our settlement and reconciliation pipeline "
    "    end to end.\n"
    "  - Mentor junior engineers and uplevel team practices.\n"
    "  - Partner with product to scope ambiguous requirements.\n\n"
    "Requirements\n"
    "  - 5+ years of Python in production at scale.\n"
    "  - Experience with event-driven architectures (Kafka, Pulsar).\n"
)


class _StubLLMResult:
    decision = "yes"
    reason = "looks like a strong match"
    score = 82.0
    unverified_claim = False
    cache_hit = False
    prompt_version = "cv_pre_screen_v2.0"
    trace_id = "trace-test"
    input_tokens = 200
    output_tokens = 50
    cache_read_tokens = 0
    cache_creation_tokens = 0


def test_copy_paste_cv_persists_capped_score_and_evidence(db):
    org, role, _, app = make_full_application(db, cv_text=_JD_TEXT, jd_text=_JD_TEXT)
    role.job_spec_text = _JD_TEXT
    db.flush()

    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_StubLLMResult(),
    ):
        result = execute_pre_screen_only(app)

    assert result["status"] == "ok"
    assert result["fraud_capped"] is True
    # Persisted state on the application — capped + tagged + evidence stored.
    assert app.pre_screen_score_100 <= 10.0
    assert app.pre_screen_recommendation == "Below threshold"
    assert "copied verbatim" in (app.pre_screen_evidence["summary"] or "").lower()
    assert app.pre_screen_evidence["fraud_capped"] is True
    assert app.pre_screen_evidence["llm_score_100"] == 82.0
    cp = app.pre_screen_evidence["fraud_signals"]["cv_copy_paste"]
    assert cp["triggered"] is True
    assert cp["evidence"], "expected evidence snippets stored"
    # rank_score follows the capped score so the directory orders correctly.
    assert app.rank_score == app.pre_screen_score_100


def test_legit_cv_persists_llm_score_unchanged(db):
    org, role, _, app = make_full_application(
        db,
        cv_text=(
            "Maya Patel — Backend engineer with 10 years at Stripe and Klarna. "
            "Built the fraud-detection rules engine handling billions of events "
            "per day. Comfortable owning systems end to end and being on-call."
        ),
        jd_text=_JD_TEXT,
    )
    role.job_spec_text = _JD_TEXT
    db.flush()

    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_StubLLMResult(),
    ):
        result = execute_pre_screen_only(app)

    assert result["status"] == "ok"
    assert result["fraud_capped"] is False
    assert app.pre_screen_score_100 == 82.0
    assert app.pre_screen_evidence["fraud_capped"] is False
    assert app.pre_screen_evidence["fraud_signals"]["cv_copy_paste"]["triggered"] is False
