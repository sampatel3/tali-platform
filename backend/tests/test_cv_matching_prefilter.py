"""Tests for the embedding pre-filter (Phase 2 / RALPH 2.3).

Mocks the override-protection lookup so the pre-filter exercises the
code path without a DB. Uses the deterministic mock embedding provider
so cosine scores are stable across runs.

Acceptance covered:
- Pre-filter halves the call count on a synthetic batch of 60 (kept ≈ 30).
- An override-tagged candidate is *never* dropped, even if its cosine is
  below the threshold.
- Below-min-batch input is passed through untouched.
- Disabled flag passes everything through.
- Every dropped candidate is logged with cosine + reason.
"""

from __future__ import annotations

import logging

from app.cv_matching.embeddings import clear_cache
from app.cv_matching.prefilter import (
    PrefilterCandidate,
    prefilter,
)


def setup_function(_):
    clear_cache()


def _candidates(n: int, *, label_prefix: str = "c") -> list[PrefilterCandidate]:
    return [
        PrefilterCandidate(
            cv_text=f"{label_prefix}-{i} {label_prefix} text body",
            application_id=i,
            candidate_label=f"{label_prefix}-{i}",
        )
        for i in range(n)
    ]


def test_prefilter_skips_small_batch():
    cands = _candidates(10)
    decisions = prefilter(
        cands,
        jd_text="any",
        requirements=[],
        enabled=True,
        min_batch=30,
        cosine_threshold=0.0,
        override_lookup=lambda _ids: set(),
    )
    assert len(decisions) == 10
    assert all(d.kept for d in decisions)
    assert all(d.reason == "skipped_small_batch" for d in decisions)


def test_prefilter_disabled_passes_all_through():
    cands = _candidates(60)
    decisions = prefilter(
        cands,
        jd_text="any",
        requirements=[],
        enabled=False,
        min_batch=30,
        cosine_threshold=0.0,
        override_lookup=lambda _ids: set(),
    )
    assert len(decisions) == 60
    assert all(d.kept for d in decisions)


def test_prefilter_drops_roughly_half_on_batch_of_60():
    cands = _candidates(60)
    decisions = prefilter(
        cands,
        jd_text="JD text",
        requirements=[],
        enabled=True,
        min_batch=30,
        cosine_threshold=-1.0,  # threshold below the floor; only top-half logic fires
        top_fraction=0.5,
        override_lookup=lambda _ids: set(),
    )
    kept = [d for d in decisions if d.kept]
    dropped = [d for d in decisions if not d.kept]
    # Mock embedding produces scattered cosines — top-half sort keeps ~30.
    assert len(decisions) == 60
    assert 28 <= len(kept) <= 32  # exact half is 30; allow for tie handling
    assert len(dropped) >= 28
    # Every dropped candidate has a real reason and a finite cosine.
    for d in dropped:
        assert d.reason in ("dropped_below_threshold", "dropped_bottom_half")
        assert -1.0 <= d.cosine_score <= 1.0


def test_prefilter_never_drops_override_protected():
    cands = _candidates(60)
    # Mark applications 0, 1, 2 as override-protected.
    protected = {0, 1, 2}

    decisions = prefilter(
        cands,
        jd_text="JD text",
        requirements=[],
        enabled=True,
        min_batch=30,
        cosine_threshold=2.0,  # impossibly high — drops everything not protected
        top_fraction=0.5,
        override_lookup=lambda _ids: protected,
    )
    # Only the protected applications should survive.
    kept_ids = [
        d.candidate.application_id for d in decisions if d.kept
    ]
    assert set(kept_ids) == protected
    for d in decisions:
        if d.candidate.application_id in protected:
            assert d.kept
            assert d.reason == "kept_override_protected"


def test_prefilter_logs_every_dropped_candidate(caplog):
    cands = _candidates(60)
    with caplog.at_level(logging.INFO, logger="taali.cv_match.prefilter"):
        decisions = prefilter(
            cands,
            jd_text="JD",
            requirements=[],
            enabled=True,
            min_batch=30,
            cosine_threshold=2.0,  # drops all
            override_lookup=lambda _ids: set(),
        )
    dropped_count = sum(1 for d in decisions if not d.kept)
    log_count = sum(
        1 for r in caplog.records if "Prefilter dropped candidate" in r.message
    )
    assert dropped_count == 60
    assert log_count == 60


def test_prefilter_preserves_input_order():
    cands = _candidates(40)
    decisions = prefilter(
        cands,
        jd_text="JD",
        requirements=[],
        enabled=True,
        min_batch=30,
        cosine_threshold=-1.0,
        override_lookup=lambda _ids: set(),
    )
    assert [d.candidate.application_id for d in decisions] == list(range(40))
