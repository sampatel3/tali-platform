"""V2 scoring placeholders (HuggingFace-based, currently disabled)."""

from __future__ import annotations

from typing import Any, Dict


def v2_placeholder(enabled: bool) -> Dict[str, Any]:
    """Return a stub V2 result payload.

    When V2 is activated this will call HuggingFace models for grammar,
    sentiment, code-complexity, etc.  For now it returns *None* for every
    metric so callers can include the key without blowing up.
    """
    return {
        "enabled": enabled,
        "grammar_score": None,
        "sentiment_trajectory": None,
        "prompt_type_distribution": None,
        "learning_velocity": None,
        "copy_from_stackoverflow": None,
        "copy_from_chatgpt": None,
        "code_complexity": None,
        "linting_score": None,
    }
