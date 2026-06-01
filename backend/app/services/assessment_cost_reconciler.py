"""Per-assessment cost reconciliation.

The metering invariant ("every Anthropic call writes a UsageEvent via
MeteredAnthropicClient") is necessary but not sufficient — we also need
the numbers to AGREE across the independent vantage points the platform
exposes:

1. ``UsageEvent.cost_usd_micro`` summed across both entity_id formats
   the assessment runtime writes: ``"assessment:{id}"`` (classifier,
   rubric grader) and historically the bare ``"{id}"`` (SDK aggregate
   pre-2026-06-01 — unified going forward but historic rows linger).
2. The SDK-reported ``sdk_total_cost_usd`` carried in each ai_prompts
   record's ``interrogation_state`` AND in ``UsageEvent.event_metadata``
   for ``source=claude_agent_sdk_aggregated`` rows. This is the
   Node-side bundled CLI's own cost number — comes from the subprocess
   that talked to Anthropic, so it's the closest thing to ground truth
   we have without billing-API reconciliation.
3. A re-derivation from token totals: re-run ``pricing_service.raw_cost_usd_micro``
   over the chat-loop tokens (in ai_prompts) and the metered
   classifier+grader tokens (in usage_events) — independent of what's
   stored in the cost columns.

If (1), (2), and (3) agree within rounding, the cost is "nailed down"
for that assessment. Disagreement points at a specific layer: missing
rows → metering gap; mis-priced rows → pricing bug; SDK vs derived
mismatch → model-mismatch in the SDK call (e.g. SDK reported Sonnet
cost but we priced as Haiku).

Used at two call sites:
- ``backend/tests/test_unit_cost_reconciliation.py`` — synthetic
  assessment with known inputs; all three numbers must match to the cent.
- ``scripts/admin/reconcile_assessment_costs.py`` (admin script) —
  prints a per-assessment diff against prod data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from ..models.assessment import Assessment
from ..models.usage_event import UsageEvent
from .pricing_service import raw_cost_usd_micro

logger = logging.getLogger("taali.assessments.cost_reconciler")

# Tolerance for "match" — 100 micro-USD = $0.0001. Smaller than any
# realistic per-call rounding error but big enough to absorb integer-
# division drift in raw_cost_usd_micro's ROUND_UP behaviour. Rows that
# disagree by more than this are real bugs.
RECONCILIATION_TOLERANCE_MICRO = 100


@dataclass(frozen=True)
class AssessmentCostBreakdown:
    """Triple-source cost view for one assessment, in micro-USD.

    All three numbers are computed independently and SHOULD agree
    within ``RECONCILIATION_TOLERANCE_MICRO``. When they don't, the
    specific gap tells you where the bug is.
    """

    assessment_id: int

    # (1) UsageEvent rows — what the metering ledger has stored.
    metered_chat_micro: int           # SDK aggregate rows (source=claude_agent_sdk_aggregated)
    metered_classifier_micro: int     # interrogation_classifier rows
    metered_grader_micro: int         # rubric_scoring rows
    metered_other_micro: int          # anything else that landed on this assessment

    # (2) SDK-reported cost from the same UsageEvent rows' metadata
    # (``sdk_total_cost_usd`` field set by usage_reconciler). Independent
    # of the cost_usd_micro column — comes from the CLI subprocess.
    sdk_reported_chat_micro: int

    # (3) Re-derived cost from raw token counts + current pricing table.
    # Independent of both stored columns. If this disagrees with (1) the
    # pricing table or model attribution drifted; if it disagrees with
    # (2) the SDK used a different model than the metadata reported.
    derived_chat_micro: int

    @property
    def metered_total_micro(self) -> int:
        return (
            self.metered_chat_micro
            + self.metered_classifier_micro
            + self.metered_grader_micro
            + self.metered_other_micro
        )

    def metered_total_usd(self) -> float:
        return self.metered_total_micro / 1_000_000.0

    def disagreements(self) -> List[str]:
        """Return human-readable strings naming each pair of vantage
        points that disagree by more than the tolerance. Empty list
        means "fully reconciled".
        """
        out: List[str] = []
        if abs(self.metered_chat_micro - self.sdk_reported_chat_micro) > RECONCILIATION_TOLERANCE_MICRO:
            out.append(
                f"metered_chat (${self.metered_chat_micro/1e6:.4f}) != "
                f"sdk_reported_chat (${self.sdk_reported_chat_micro/1e6:.4f})"
            )
        if abs(self.metered_chat_micro - self.derived_chat_micro) > RECONCILIATION_TOLERANCE_MICRO:
            out.append(
                f"metered_chat (${self.metered_chat_micro/1e6:.4f}) != "
                f"derived_chat (${self.derived_chat_micro/1e6:.4f})"
            )
        return out


def _sum_usage_events_for_assessment(
    db: Session, assessment_id: int,
) -> Dict[str, int]:
    """Sum cost_usd_micro from usage_events partitioned by sub-feature.

    Reads BOTH entity_id formats — the bare ``"{id}"`` (legacy SDK
    aggregate writer, pre-2026-06-01) AND the namespaced
    ``"assessment:{id}"`` (current unified writer). When the entity_id
    unification migration is applied to prod, the bare format will be
    backfilled but this code stays defensive so historic queries on
    old data still reconcile.
    """
    rows = (
        db.query(UsageEvent)
        .filter(
            (UsageEvent.entity_id == f"assessment:{assessment_id}")
            | (UsageEvent.entity_id == str(assessment_id))
        )
        .all()
    )
    out = {
        "chat_micro": 0,
        "classifier_micro": 0,
        "grader_micro": 0,
        "other_micro": 0,
        "sdk_reported_chat_micro": 0,
    }
    for row in rows:
        meta = row.event_metadata or {}
        source = str((meta.get("source") if isinstance(meta, dict) else "") or "")
        sub_feature = str((meta.get("sub_feature") if isinstance(meta, dict) else "") or "")
        cost = int(row.cost_usd_micro or 0)
        if source == "claude_agent_sdk_aggregated" or sub_feature == "agent_sdk_chat":
            out["chat_micro"] += cost
            sdk_cost_usd = float((meta.get("sdk_total_cost_usd") if isinstance(meta, dict) else 0) or 0)
            out["sdk_reported_chat_micro"] += int(round(max(sdk_cost_usd, 0.0) * 1_000_000))
        elif sub_feature == "interrogation_classifier":
            out["classifier_micro"] += cost
        elif sub_feature == "rubric_scoring":
            out["grader_micro"] += cost
        else:
            out["other_micro"] += cost
    return out


def _derive_chat_cost_from_prompts(
    prompts: Iterable[Dict[str, Any]] | None,
    fallback_model: str,
) -> int:
    """Re-derive chat-loop cost from the per-turn ai_prompts records.

    Independent of both ``cost_usd_micro`` in usage_events AND
    ``sdk_total_cost_usd`` in the metadata — we walk the raw token
    counts on each record and re-price via ``raw_cost_usd_micro``.
    Disagreement with the metered total points at either a model
    mismatch or a pricing-table drift.
    """
    total_micro = 0
    for rec in prompts or []:
        if not isinstance(rec, dict):
            continue
        model = str(rec.get("model") or "").strip() or fallback_model
        total_micro += raw_cost_usd_micro(
            input_tokens=int(rec.get("input_tokens") or 0),
            output_tokens=int(rec.get("output_tokens") or 0),
            cache_read_tokens=int(rec.get("cache_read_input_tokens") or 0),
            cache_creation_tokens=int(rec.get("cache_creation_input_tokens") or 0),
            cache_creation_1h_tokens=None,
            model=model,
        )
    return total_micro


def reconcile_assessment_cost(
    db: Session, assessment: Assessment | int,
    *,
    fallback_model: str = "claude-haiku-4-5",
) -> AssessmentCostBreakdown:
    """Build a triple-source cost view for one assessment.

    ``assessment`` may be an Assessment instance or a bare id. Reads
    ``ai_prompts`` for the chat-loop derivation and ``usage_events``
    for the metered totals + SDK-reported numbers from metadata.

    Pure function — no DB writes; safe to call on prod data from an
    admin script.
    """
    if isinstance(assessment, int):
        loaded = db.get(Assessment, assessment)
        if loaded is None:
            raise ValueError(f"assessment {assessment} not found")
        assessment = loaded
    assessment_id = int(assessment.id)
    metered = _sum_usage_events_for_assessment(db, assessment_id)
    derived_chat = _derive_chat_cost_from_prompts(
        assessment.ai_prompts or [], fallback_model=fallback_model,
    )
    return AssessmentCostBreakdown(
        assessment_id=assessment_id,
        metered_chat_micro=metered["chat_micro"],
        metered_classifier_micro=metered["classifier_micro"],
        metered_grader_micro=metered["grader_micro"],
        metered_other_micro=metered["other_micro"],
        sdk_reported_chat_micro=metered["sdk_reported_chat_micro"],
        derived_chat_micro=derived_chat,
    )


__all__ = [
    "AssessmentCostBreakdown",
    "RECONCILIATION_TOLERANCE_MICRO",
    "reconcile_assessment_cost",
]
