"""Unit tests for the deterministic fraud detection service."""

from app.services.fraud_detection import (
    apply_fraud_penalty,
    apply_integrity_penalty,
    apply_unverified_claim_prescreen_penalty,
    build_fraud_signals_payload,
    compute_integrity_penalty,
    detect_cv_copy_paste,
    detect_timeline_inconsistencies,
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


def test_non_latin_copy_paste_is_detected():
    jd = (
        "نبحث عن مهندس برمجيات أول لبناء أنظمة موزعة موثوقة وقابلة للتوسع "
        "مع خبرة عميقة في بايثون وتشغيل خدمات الإنتاج ومراقبتها باستمرار"
    )
    result = detect_cv_copy_paste(jd, jd, threshold=0.05)
    assert result.triggered is True
    assert result.score >= 0.9
    assert result.evidence


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


# ── Pre-screen unverified-claim soft penalty ──────────────────────────────────


def test_prescreen_unverified_penalty_applies_when_flagged():
    score, penalised = apply_unverified_claim_prescreen_penalty(72.0, True, penalty=5.0)
    assert score == 67.0
    assert penalised is True


def test_prescreen_unverified_penalty_skips_when_not_flagged():
    score, penalised = apply_unverified_claim_prescreen_penalty(72.0, False, penalty=5.0)
    assert score == 72.0
    assert penalised is False


def test_prescreen_unverified_penalty_clamps_at_zero():
    score, penalised = apply_unverified_claim_prescreen_penalty(3.0, True, penalty=5.0)
    assert score == 0.0
    assert penalised is True


def test_prescreen_unverified_penalty_handles_none_and_zero_penalty():
    assert apply_unverified_claim_prescreen_penalty(None, True, penalty=5.0) == (None, False)
    assert apply_unverified_claim_prescreen_penalty(80.0, True, penalty=0.0) == (80.0, False)


# ── Timeline inconsistency detection ──────────────────────────────────────────


def _entry(company="Acme", role="Engineer", start=2018, end=2021, current=False) -> dict:
    return {
        "company": company,
        "role": role,
        "start_year": start,
        "end_year": end,
        "is_current": current,
    }


def test_timeline_clean_has_no_issues():
    timeline = [
        _entry("Klarna", start=2021, end=None, current=True),
        _entry("Stripe", start=2017, end=2021),
    ]
    result = detect_timeline_inconsistencies(timeline, now_year=2026)
    assert result.triggered is False
    assert result.issues == []


def test_timeline_year_overlap_is_not_flagged():
    # A mid-year job change legitimately shows two roles sharing a year.
    timeline = [_entry("New Co", start=2020, end=2023), _entry("Old Co", start=2018, end=2020)]
    result = detect_timeline_inconsistencies(timeline, now_year=2026)
    assert result.triggered is False


def test_timeline_future_date_flagged():
    result = detect_timeline_inconsistencies([_entry(end=2099)], now_year=2026)
    assert result.triggered is True
    assert any(i.kind == "future_date" for i in result.issues)


def test_timeline_near_future_start_is_tolerated():
    # CVs list an agreed start date one year out — should not flag.
    result = detect_timeline_inconsistencies(
        [_entry(start=2027, end=None, current=True)], now_year=2026
    )
    assert result.triggered is False


def test_timeline_end_before_start_flagged():
    result = detect_timeline_inconsistencies(
        [_entry(start=2020, end=2015)], now_year=2026
    )
    assert any(i.kind == "end_before_start" for i in result.issues)


def test_timeline_impossible_span_flagged():
    result = detect_timeline_inconsistencies(
        [_entry(start=1900, end=2000)], now_year=2026
    )
    assert any(i.kind == "impossible_span" for i in result.issues)


def test_timeline_excess_concurrent_current_flagged():
    timeline = [
        _entry("A", start=2020, end=None, current=True),
        _entry("B", start=2021, end=None, current=True),
        _entry("C", start=2022, end=None, current=True),
    ]
    result = detect_timeline_inconsistencies(timeline, now_year=2026)
    assert any(i.kind == "excess_current" for i in result.issues)


def test_timeline_two_concurrent_current_is_tolerated():
    timeline = [
        _entry("Day Job", start=2020, end=None, current=True),
        _entry("Advisor", start=2021, end=None, current=True),
    ]
    result = detect_timeline_inconsistencies(timeline, now_year=2026)
    assert not any(i.kind == "excess_current" for i in result.issues)


def test_timeline_empty_and_none_safe():
    assert detect_timeline_inconsistencies(None).triggered is False
    assert detect_timeline_inconsistencies([]).triggered is False


# ── Integrity penalty (claims + timeline) ─────────────────────────────────────


def _claim(corroboration="uncorroborated", familiarity="unknown") -> dict:
    return {
        "claim_text": "1st place, XYZ Global Hackathon 2023",
        "claim_type": "competition",
        "corroboration": corroboration,
        "model_familiarity": familiarity,
    }


def test_integrity_penalty_counts_unverified_claim():
    result = compute_integrity_penalty(
        [_claim()], None, points_per_issue=5.0, max_penalty=15.0
    )
    assert result.unverified_claim_count == 1
    assert result.penalty == 5.0
    assert result.triggered is True


def test_integrity_penalty_fails_open_on_corroborated_or_known_claim():
    # Corroborated OR known → not penalised (both conditions required).
    claims = [
        _claim(corroboration="corroborated", familiarity="unknown"),
        _claim(corroboration="uncorroborated", familiarity="known"),
        _claim(corroboration="weird-model-output", familiarity="???"),
    ]
    result = compute_integrity_penalty(claims, None, points_per_issue=5.0, max_penalty=15.0)
    assert result.unverified_claim_count == 0
    assert result.penalty == 0.0


def test_integrity_penalty_combines_claims_and_timeline():
    # One claim + one timeline issue (end-before-start) = 2 issues × 5pts.
    timeline = detect_timeline_inconsistencies(
        [_entry(start=2020, end=2015)], now_year=2026
    )
    assert timeline.triggered and len(timeline.issues) == 1
    result = compute_integrity_penalty(
        [_claim()], timeline, points_per_issue=5.0, max_penalty=15.0
    )
    assert result.unverified_claim_count == 1
    assert result.timeline_issue_count == 1
    assert result.penalty == 10.0


def test_integrity_penalty_respects_cap():
    claims = [_claim() for _ in range(10)]
    result = compute_integrity_penalty(claims, None, points_per_issue=5.0, max_penalty=15.0)
    assert result.penalty == 15.0
    assert result.capped is True


def test_integrity_penalty_disabled_when_max_zero():
    result = compute_integrity_penalty(
        [_claim()], None, points_per_issue=5.0, max_penalty=0.0
    )
    assert result.penalty == 0.0
    assert result.capped is False


def test_apply_integrity_penalty_subtracts_and_clamps():
    assert apply_integrity_penalty(80.0, 15.0) == 65.0
    assert apply_integrity_penalty(10.0, 15.0) == 0.0
    assert apply_integrity_penalty(80.0, 0.0) == 80.0
