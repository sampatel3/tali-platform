"""Central difficulty-tier model + the CV-claim-consistency tell.

ONE model every task uses. Tiers are declared in the task spec's ``tiers`` block
and scored here from the signals we already compute (test pass-ratio + the
interrogation/design dimension) — no extra test runs. Difficulty here means
JUDGMENT / AMBIGUITY depth, not code complexity (the AI flattens raw code
difficulty): L1 = competency baseline, L2 = core mechanical + a trade-off,
L3 = judgment + stretch.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_TIER_ORDER = ["L1", "L2", "L3"]
# design_decisions dimension score (0-10) at/above which the judgment tier counts
# as "resolved" (candidate committed to / reframed the decisions, didn't dodge).
_DESIGN_RESOLVED_MIN = 7.0


def compute_tier_reached(
    tiers: Optional[Dict[str, Any]],
    *,
    tests_passed: int,
    tests_total: int,
    design_score_10: Optional[float],
) -> Dict[str, Any]:
    """Highest tier the candidate cleared, as a ladder.

    Each tier in ``tiers`` declares ``min_tests_ratio`` and (optionally)
    ``requires_design``. A tier is cleared when the test pass-ratio meets its
    threshold and — for a design-gated tier — the judgment dimension resolved.
    The ladder is monotonic: a higher tier can't be cleared if a lower one
    wasn't. Returns {} when the task declares no tiers.
    """
    if not isinstance(tiers, dict) or not tiers:
        return {}
    ratio = (tests_passed / tests_total) if tests_total else 0.0
    design_resolved = design_score_10 is not None and float(design_score_10) >= _DESIGN_RESOLVED_MIN
    reached = "L0"
    ladder = []
    broke = False
    for tier in _TIER_ORDER:
        cfg = tiers.get(tier)
        if not isinstance(cfg, dict):
            continue
        min_ratio = float(cfg.get("min_tests_ratio", 0.0) or 0.0)
        needs_design = bool(cfg.get("requires_design", False))
        cleared = (not broke) and ratio >= min_ratio and (design_resolved if needs_design else True)
        ladder.append({
            "tier": tier,
            "label": cfg.get("label", tier),
            "cleared": cleared,
            "min_tests_ratio": min_ratio,
            "requires_design": needs_design,
        })
        if cleared:
            reached = tier
        else:
            broke = True
    reached_label = next((row["label"] for row in ladder if row["tier"] == reached), None)
    return {
        "reached": reached,
        "label": reached_label or "Below L1 baseline",
        "tests_ratio": round(ratio, 3),
        "design_resolved": design_resolved,
        "ladder": ladder,
    }


def cv_claim_consistency(
    tier_reached: Optional[Dict[str, Any]],
    *,
    role_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Soft recruiter signal — NEVER a hard reject.

    Fires when the candidate did not clear the L1 competency baseline: a fast
    tell that the basic claimed competency for the role wasn't demonstrated,
    even with the AI assistant, inside the timebox. Surfaced for review against
    the CV; the recruiter decides.
    """
    if not tier_reached:
        return None
    if tier_reached.get("reached") == "L0":
        role = (role_name or "").strip() or "this role"
        return {
            "signal": "below_competency_baseline",
            "severity": "review",  # soft: for recruiter review, never auto-rejects
            "message": (
                f"Did not clear the L1 competency baseline for {role} — the basic primitives a "
                "genuinely-experienced candidate clears quickly (even with the AI assistant) were "
                "not delivered. Worth a closer look against the CV."
            ),
        }
    return None
