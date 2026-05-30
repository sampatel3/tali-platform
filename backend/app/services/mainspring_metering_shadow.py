"""Shadow comparator for the metering convergence (ADR-0010, cut #1b).

Behind a flag (``MAINSPRING_METERING_SHADOW``), every metered Anthropic call is
ALSO priced through mainspring's vendored seam, and a token+cost parity diff vs
tali's own meter is logged. No DB writes, no behaviour change — this is the
at-parity evidence ADR-0010 requires *before* any cutover. The vendored seam
lives under ``backend/vendor/mainspring_metering`` (mirror-vendored from
mainspring master; re-vendor via ``scripts/vendor_mainspring_metering.sh``).

Three outcomes are logged distinctly so the parity log is actionable:
- ``compared``  — both meters priced the call; ``drift_pct`` is the gap to close
- ``unpriced``  — mainspring returned 0 (its pricing table / alias map lacks the
  model tali billed) → a gap to fix in mainspring's pricing, not a real drift
- the comparison never raises — a shadow failure must not affect the live call.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..platform.config import settings

logger = logging.getLogger("taali.metering.shadow")


def shadow_compare(
    *,
    model: str,
    tali_cost_usd_micro: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_creation_1h_tokens: Optional[int] = None,
) -> None:
    """If shadow metering is on, price the same tokens through mainspring's
    seam and log a parity diff vs tali's computed cost. Never raises."""
    if not getattr(settings, "MAINSPRING_METERING_SHADOW", False):
        return
    try:
        from vendor.mainspring_metering.seam import TokenUsage, price_usage

        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_creation_1h_tokens=cache_creation_1h_tokens,
        )
        ms_cost = price_usage(model, usage)

        if ms_cost == 0 and tali_cost_usd_micro > 0:
            # Mainspring couldn't price the model tali billed — its PRICING /
            # ALIASES table lacks this id. A parity gap to close in mainspring,
            # not a real cost drift; flag it as its own status.
            logger.info(
                "mainspring_metering_shadow status=unpriced model=%s tali_micro=%s",
                model, tali_cost_usd_micro,
                extra={
                    "event": "mainspring_metering_shadow",
                    "status": "unpriced",
                    "model": model,
                    "tali_micro": int(tali_cost_usd_micro),
                    "mainspring_micro": 0,
                },
            )
            return

        delta = ms_cost - int(tali_cost_usd_micro)
        drift_pct = (delta / tali_cost_usd_micro * 100) if tali_cost_usd_micro else 0.0
        logger.info(
            "mainspring_metering_shadow status=compared model=%s tali_micro=%s "
            "mainspring_micro=%s delta_micro=%s drift_pct=%.2f",
            model, tali_cost_usd_micro, ms_cost, delta, drift_pct,
            extra={
                "event": "mainspring_metering_shadow",
                "status": "compared",
                "model": model,
                "tali_micro": int(tali_cost_usd_micro),
                "mainspring_micro": int(ms_cost),
                "delta_micro": int(delta),
                "drift_pct": round(drift_pct, 2),
            },
        )
    except Exception:  # pragma: no cover — shadow must never affect the live call
        logger.exception("mainspring_metering_shadow: comparison failed (non-fatal)")
