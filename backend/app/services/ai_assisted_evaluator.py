from __future__ import annotations

from typing import Any, Dict


def generate_ai_suggestions(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AI-assisted rubric suggestions.

    This integration is intentionally hard-disabled until a production evaluator
    service is wired in. Returning placeholder suggestions is not allowed.
    """
    raise RuntimeError(
        "AI-assisted evaluator integration is not configured. "
        "Disable AI_ASSISTED_EVAL_ENABLED or wire a production evaluator provider."
    )
