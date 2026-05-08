"""Unit tests for the deterministic fraud detection service."""

from app.services.fraud_detection import (
    apply_fraud_penalty,
    build_fraud_signals_payload,
    detect_cv_copy_paste,
)


def _legit_cv() -> str:
    # A plausible CV that shares incidental words with a JD but doesn't lift
    # any sentence-length chunks. Names + companies + skills mentioned the
    # way a real candidate would write them.
    return (
        "Maya Patel — Senior Backend Engineer\n"
        "10 years building distributed Python services on AWS. Led the\n"
        "checkout platform rewrite at Klarna, reducing p99 latency by 40%.\n"
        "Comfortable owning systems end-to-end: API design, on-call,\n"
        "incident response, postmortems.\n\n"
        "Experience\n"
        "Klarna — Staff Engineer (2021–present)\n"
        "  · Migrated legacy Java monolith to FastAPI microservices.\n"
        "  · Mentored four junior engineers through promotion.\n"
        "Stripe — Senior Engineer (2017–2021)\n"
        "  · Built fraud-detection rules engine handling 8B events/day.\n"
        "Skills: Python, FastAPI, Postgres, Kafka, Terraform, AWS"
    )


def _job_spec() -> str:
    return (
        "About the role\n"
        "We are hiring a Senior Backend Engineer to lead our payments\n"
        "platform. The ideal candidate has deep experience designing\n"
        "scalable distributed systems and is comfortable being on-call\n"
        "for production services.\n\n"
        "Responsibilities\n"
        "  · Own the architecture of our settlement and reconciliation\n"
        "    pipeline end to end.\n"
        "  · Mentor junior engineers and uplevel team practices.\n"
        "  · Partner with product to scope ambiguous requirements.\n\n"
        "Requirements\n"
        "  · 5+ years of Python in production at scale.\n"
        "  · Experience with event-driven architectures (Kafka, Pulsar).\n"
        "  · Track record of leading cross-team initiatives."
    )


def test_no_overlap_returns_zero_score():
    cv = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
    jd = "We need a senior platform engineer with deep AWS and Kubernetes."
    result = detect_cv_copy_paste(cv, jd, threshold=0.05)
    assert result.score == 0.0
    assert result.matched_chars == 0
    assert result.triggered is False
    assert result.evidence == []


def test_legit_cv_does_not_trigger():
    result = detect_cv_copy_paste(_legit_cv(), _job_spec(), threshold=0.05)
    assert result.triggered is False, (
        f"legit CV scored {result.score:.2%} — false positive"
    )


def test_full_copy_paste_scores_near_one():
    jd = _job_spec()
    cv = jd  # candidate literally pasted the entire JD
    result = detect_cv_copy_paste(cv, jd, threshold=0.05)
    assert result.score >= 0.9
    assert result.triggered is True
    assert result.evidence, "expected at least one evidence snippet"
    # The whole-CV match should collapse into a single contiguous snippet,
    # not dozens of overlapping windows.
    assert len(result.evidence) == 1


def test_partial_paste_triggers_above_threshold():
    pasted_chunk = (
        "Own the architecture of our settlement and reconciliation pipeline "
        "end to end. Mentor junior engineers and uplevel team practices. "
        "Partner with product to scope ambiguous requirements."
    )
    cv = (
        "Sam — Backend Engineer\n"
        f"{pasted_chunk}\n"
        "Worked at small startups for the last few years.\n"
    )
    result = detect_cv_copy_paste(cv, _job_spec(), threshold=0.05)
    assert result.triggered is True
    assert result.score > 0.05
    assert any("settlement and reconciliation" in s.text for s in result.evidence)


def test_evidence_snippet_offsets_are_word_indices():
    pasted_chunk = (
        "the architecture of our settlement and reconciliation pipeline end to end"
    )
    cv = f"Some preamble here. {pasted_chunk} And then more text after."
    result = detect_cv_copy_paste(cv, _job_spec(), threshold=0.05)
    assert result.evidence
    snippet = result.evidence[0]
    assert snippet.cv_word_offset >= 0
    assert snippet.jd_word_offset >= 0
    assert snippet.word_count >= 8


def test_short_inputs_return_zero_safely():
    # Below n-gram window — should not crash and should not trigger.
    result = detect_cv_copy_paste("hi there", "we are hiring", threshold=0.05)
    assert result.score == 0.0
    assert result.triggered is False


def test_empty_inputs_return_zero_safely():
    result = detect_cv_copy_paste("", "", threshold=0.05)
    assert result.score == 0.0
    assert result.cv_chars == 0
    assert result.triggered is False


def test_apply_fraud_penalty_caps_when_triggered():
    fraud = detect_cv_copy_paste(_job_spec(), _job_spec(), threshold=0.05)
    assert fraud.triggered
    adjusted, was_capped = apply_fraud_penalty(85.0, fraud, cap_score=10.0)
    assert adjusted == 10.0
    assert was_capped is True


def test_apply_fraud_penalty_passthrough_when_not_triggered():
    fraud = detect_cv_copy_paste(_legit_cv(), _job_spec(), threshold=0.05)
    assert not fraud.triggered
    adjusted, was_capped = apply_fraud_penalty(72.0, fraud, cap_score=10.0)
    assert adjusted == 72.0
    assert was_capped is False


def test_apply_fraud_penalty_no_double_penalize_already_low_score():
    # If the LLM already gave a sub-cap score, leave it alone — capping
    # would actually *raise* it, which would be wrong.
    fraud = detect_cv_copy_paste(_job_spec(), _job_spec(), threshold=0.05)
    adjusted, was_capped = apply_fraud_penalty(5.0, fraud, cap_score=10.0)
    assert adjusted == 5.0
    assert was_capped is False


def test_apply_fraud_penalty_handles_none_score():
    fraud = detect_cv_copy_paste(_job_spec(), _job_spec(), threshold=0.05)
    adjusted, was_capped = apply_fraud_penalty(None, fraud, cap_score=10.0)
    assert adjusted is None
    assert was_capped is False


def test_signals_payload_shape():
    fraud = detect_cv_copy_paste(_job_spec(), _job_spec(), threshold=0.05)
    payload = build_fraud_signals_payload(fraud)
    assert "cv_copy_paste" in payload
    cp = payload["cv_copy_paste"]
    for key in ("score", "matched_chars", "cv_chars", "triggered", "threshold", "evidence"):
        assert key in cp
    assert isinstance(cp["evidence"], list)
