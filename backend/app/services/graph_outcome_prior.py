"""Graph OUTCOME priors → Match score (P4 — SHADOW ONLY).

The graph's *positive* use, distinct from its anomaly / corroboration use:
across candidates we've seen, do profiles like this one — same skills, same
employer, same role-family — tend to ADVANCE / get hired here? That's a
suitability prior, not a fraud signal. Per the scoring-engine design it folds
into the Match score as a BOUNDED, BIAS-GATED nudge — but only after a clean
shadow-data review, because outcome-learned signals are the classic bias vector.

This module contains the bounded SHADOW payload math, but the graph fetch is not
yet activated. Configuration rejects ``GRAPH_OUTCOME_PRIOR_ENABLED=true`` so an
operator cannot mistake the scaffold for a working feature. Applying any nudge
requires (a) the autoresearch bias gate and (b) sign-off on the shadow
distribution — see ``docs/TALI_SCORING_ENGINE_DESIGN.md`` §4/§9.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("taali.graph_outcome_prior")


def outcome_prior_nudge(p_advance: Any, confidence: Any, *, max_nudge: float) -> float:
    """Map a graph prior to a bounded score nudge.

    ``p_advance`` in [0,1] is the modelled advance/hire probability; 0.5 is
    neutral. The nudge scales with how far ``p_advance`` sits from neutral AND
    the prior's ``confidence``, capped at +/- ``max_nudge`` so a prior can never
    override the real match::

        nudge = clamp((p_advance - 0.5) * 2 * confidence * max_nudge,
                      -max_nudge, +max_nudge)
    """
    try:
        p = max(0.0, min(1.0, float(p_advance)))
        c = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        return 0.0
    raw = (p - 0.5) * 2.0 * c * float(max_nudge)
    return round(max(-float(max_nudge), min(float(max_nudge), raw)), 2)


def build_outcome_prior_shadow(
    prior: dict[str, Any] | None, *, max_nudge: float
) -> dict[str, Any] | None:
    """Wrap a graph prior (``{p_advance, confidence, components?}``) into the
    persisted SHADOW payload. ``applied`` is ALWAYS False — the nudge is computed
    for review, never added to the score here. None when there's no prior."""
    if not isinstance(prior, dict):
        return None
    p_advance = prior.get("p_advance")
    if p_advance is None:
        return None
    confidence = prior.get("confidence") or 0.0
    nudge = outcome_prior_nudge(p_advance, confidence, max_nudge=max_nudge)
    return {
        "p_advance": round(float(p_advance), 3),
        "confidence": round(float(confidence), 3),
        "would_be_nudge": nudge,
        "max_nudge": float(max_nudge),
        "applied": False,  # SHADOW — never added to the match score
        "components": (prior.get("components") or [])[:6],
    }


def fetch_outcome_prior(application: Any, db: Any) -> dict[str, Any] | None:
    """Best-effort graph prior for one application: ``{p_advance, confidence,
    components}``. FAIL-OPEN (None) on anything — no graph, a cold-start
    candidate, or signature drift. Wiring this to ``GraphPriorsSubAgent`` is the
    deliberate SHADOW-activation step done under review (the flag is off by
    default), so it returns None until then rather than reaching into the
    sub-agent blind from the scoring path."""
    try:
        from ..candidate_graph import client as graph_client

        if not graph_client.is_configured():
            return None
        # TODO(P4 shadow activation): route through GraphPriorsSubAgent.get_priors
        # (brand_id, case_id=candidate_id, role_id, referrer_id, as_of) behind a
        # shadow review + the autoresearch bias gate before returning a live
        # prior. Until then the prior is inert (the flag is off in prod).
        return None
    except Exception:  # pragma: no cover — never raise into scoring
        logger.debug("outcome prior fetch failed", exc_info=True)
        return None
