"""Unit tests for assessment cost reconciliation.

The reconciler exists to catch a specific class of bug: the metering
ledger and the SDK's own cost number drift apart. That happened twice
in our pilot data:

1. ``entity_id`` was inconsistent between the SDK aggregate writer
   (``"88"``) and the classifier+grader (``"assessment:88"``). Any
   reporting query that filtered by one format silently dropped the
   other and showed a ~50-65% under-count.
2. ``MeteredAnthropicClient`` only persists keys from ``metering[metadata]``
   — the classifier and grader passed ``sub_feature`` at top level so
   it was dropped on the wire. Made the rows un-attributable.

Both are fixed. These tests pin the contract:
- After the fix, all three vantage points (metered total, SDK-reported,
  re-derived) MUST agree within ``RECONCILIATION_TOLERANCE_MICRO`` for
  any assessment whose data passes through the new code path.
- The reconciler still reads BOTH legacy and namespaced entity_id
  formats so historic prod rows reconcile too.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.assessment import Assessment
from app.models.usage_event import UsageEvent
from app.platform.database import Base
from app.services.assessment_cost_reconciler import (
    RECONCILIATION_TOLERANCE_MICRO,
    reconcile_assessment_cost,
)


@pytest.fixture
def db_session():
    # In-memory SQLite (no extension features beyond JSON; UsageEvent
    # uses a plain JSON column so this is safe). Per the metering memo,
    # in-memory sqlite leaks state across files, but pytest gives each
    # test its own engine instance so we're isolated here.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_assessment(db, assessment_id: int = 1) -> Assessment:
    """Create a minimal assessment row with deterministic ai_prompts.

    The chat turn carries 1000/500 input/output Haiku 4.5 tokens — at
    $1/$5 per MTok that's $0.001 + $0.0025 = $0.0035 = 3500 micro-USD.
    Both the metered row and the derived computation should land there.
    """
    a = Assessment(
        id=assessment_id,
        organization_id=1,
        candidate_id=1,
        task_id=1,
        token=f"tok_{assessment_id}",
        status="IN_PROGRESS",
        duration_minutes=30,
        ai_prompts=[
            {
                "message": "first prompt",
                "response": "first reply",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "model": "claude-haiku-4-5",
                "transport": "claude_agent_sdk",
            },
        ],
    )
    db.add(a)
    db.commit()
    return a


def _add_usage_event(
    db, assessment_id: int, *, source: str, sub_feature: str = "",
    cost_micro: int, sdk_total_cost_usd: float | None = None,
    entity_id: str | None = None, model: str = "claude-haiku-4-5",
    input_tokens: int = 1000, output_tokens: int = 500,
) -> UsageEvent:
    metadata: Dict[str, Any] = {}
    if source:
        metadata["source"] = source
    if sub_feature:
        metadata["sub_feature"] = sub_feature
    if sdk_total_cost_usd is not None:
        metadata["sdk_total_cost_usd"] = sdk_total_cost_usd
    ev = UsageEvent(
        organization_id=1,
        user_id=None,
        role_id=None,
        feature="assessment",
        entity_id=entity_id or f"assessment:{assessment_id}",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd_micro=cost_micro,
        markup_multiplier=1.0,
        credits_charged=cost_micro,
        cache_hit=0,
        event_metadata=metadata or None,
    )
    db.add(ev)
    db.commit()
    return ev


class TestReconcileAssessmentCost:
    def test_clean_data_reconciles_within_tolerance(self, db_session):
        # Synthetic clean case: one chat aggregate row + one classifier
        # row + one grader row, all on the namespaced entity_id.
        a = _make_assessment(db_session, assessment_id=42)
        # Chat: 1000 in + 500 out Haiku ≈ $0.0035 = 3500 micro
        _add_usage_event(
            db_session, 42,
            source="claude_agent_sdk_aggregated", sub_feature="agent_sdk_chat",
            cost_micro=3500, sdk_total_cost_usd=0.0035,
        )
        # Classifier
        _add_usage_event(
            db_session, 42,
            source="", sub_feature="interrogation_classifier",
            cost_micro=700, input_tokens=500, output_tokens=40,
        )
        # Grader
        _add_usage_event(
            db_session, 42,
            source="", sub_feature="rubric_scoring",
            cost_micro=4600, input_tokens=2800, output_tokens=200,
        )

        b = reconcile_assessment_cost(db_session, 42)
        assert b.metered_chat_micro == 3500
        assert b.metered_classifier_micro == 700
        assert b.metered_grader_micro == 4600
        assert b.metered_other_micro == 0
        assert b.metered_total_micro == 8800
        assert b.sdk_reported_chat_micro == 3500
        # Derived from ai_prompts (1000 in + 500 out Haiku = 1000 + 2500 = 3500)
        assert b.derived_chat_micro == 3500
        assert b.disagreements() == []

    def test_reads_legacy_entity_id_format(self, db_session):
        """Defensive read of the pre-2026-06-01 SDK aggregate format
        (bare ``"88"`` instead of ``"assessment:88"``). After the
        unification migration this codepath shouldn't fire on fresh
        rows but the reconciler still has to add up historic data."""
        a = _make_assessment(db_session, assessment_id=88)
        _add_usage_event(
            db_session, 88,
            source="claude_agent_sdk_aggregated", sub_feature="agent_sdk_chat",
            cost_micro=8600, sdk_total_cost_usd=0.0086,
            entity_id="88",  # ← legacy bare format
        )
        b = reconcile_assessment_cost(db_session, 88)
        assert b.metered_chat_micro == 8600
        assert b.sdk_reported_chat_micro == 8600

    def test_disagreement_surfaces_in_output(self, db_session):
        """If we record a cost that disagrees with the SDK number,
        the reconciler must surface the gap so the operator can act."""
        a = _make_assessment(db_session, assessment_id=99)
        _add_usage_event(
            db_session, 99,
            source="claude_agent_sdk_aggregated", sub_feature="agent_sdk_chat",
            cost_micro=10_000,  # what we stored
            sdk_total_cost_usd=0.003,  # what the SDK reported (much less)
        )
        b = reconcile_assessment_cost(db_session, 99)
        disagreements = b.disagreements()
        assert disagreements, "must flag the metered/SDK mismatch"
        assert any("sdk_reported_chat" in d for d in disagreements)

    def test_derives_from_prompts_when_no_metered_chat_row(self, db_session):
        """If a session ended before any UsageEvent row was written
        (network blip, crash, etc.) the ai_prompts records still
        carry the SDK-reported token counts. The derived number lets
        the operator see what the session 'should have' cost."""
        a = _make_assessment(db_session, assessment_id=7)
        b = reconcile_assessment_cost(db_session, 7)
        # 1000 input + 500 output Haiku 4.5 = $0.001 + $0.0025 = $0.0035
        assert b.derived_chat_micro == 3500
        assert b.metered_chat_micro == 0
        # And the disagreement is surfaced
        assert any("derived_chat" in d for d in b.disagreements())

    def test_classifier_metadata_correctly_attributed(self, db_session):
        """Pins the fix for the pre-2026-06-01 attribution gap: when
        ``sub_feature`` lives in ``metering[metadata]`` instead of at
        the top level, the row's metadata column carries it through
        and the reconciler buckets it correctly. Drop the sub_feature
        and the row regresses into ``metered_other_micro``."""
        a = _make_assessment(db_session, assessment_id=11)
        # Row WITH sub_feature → classifier bucket
        _add_usage_event(
            db_session, 11,
            source="", sub_feature="interrogation_classifier",
            cost_micro=700,
        )
        # Row WITHOUT sub_feature (the regression shape) → other
        ev = UsageEvent(
            organization_id=1, user_id=None, role_id=None,
            feature="assessment", entity_id="assessment:11",
            model="claude-haiku-4-5",
            input_tokens=500, output_tokens=40,
            cache_read_tokens=0, cache_creation_tokens=0,
            cost_usd_micro=700, markup_multiplier=1.0,
            credits_charged=700, cache_hit=0,
            event_metadata=None,
        )
        db_session.add(ev)
        db_session.commit()
        b = reconcile_assessment_cost(db_session, 11)
        assert b.metered_classifier_micro == 700
        assert b.metered_other_micro == 700  # regression row lands here

    def test_unknown_assessment_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            reconcile_assessment_cost(db_session, 9999)


class TestBreakdownMath:
    def test_total_sums_all_buckets(self, db_session):
        a = _make_assessment(db_session, assessment_id=1)
        _add_usage_event(
            db_session, 1, source="claude_agent_sdk_aggregated",
            sub_feature="agent_sdk_chat", cost_micro=3500, sdk_total_cost_usd=0.0035,
        )
        _add_usage_event(
            db_session, 1, source="", sub_feature="interrogation_classifier",
            cost_micro=700,
        )
        _add_usage_event(
            db_session, 1, source="", sub_feature="rubric_scoring",
            cost_micro=4600,
        )
        b = reconcile_assessment_cost(db_session, 1)
        assert b.metered_total_micro == 3500 + 700 + 4600
        assert abs(b.metered_total_usd() - 0.0088) < 1e-9

    def test_tolerance_constant_is_small(self):
        # 100 micro = $0.0001. Sanity: this should be smaller than any
        # real per-call rounding error but big enough to absorb
        # integer-division drift.
        assert RECONCILIATION_TOLERANCE_MICRO == 100
