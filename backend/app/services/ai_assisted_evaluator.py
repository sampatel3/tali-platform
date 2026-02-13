from __future__ import annotations

from typing import Any, Dict


def generate_ai_suggestions(payload: Dict[str, Any]) -> Dict[str, Any]:
    """V2 placeholder: AI suggests rubric scores/evidence; human reviewer finalizes."""
    rubric = payload.get("evaluation_rubric") or {}
    suggestions = {}
    for category, details in rubric.items():
        suggestions[category] = {
            "suggested_score": "good",
            "weight": (details or {}).get("weight", 0),
            "suggested_evidence": [
                "V2 placeholder suggestion generated from chat + git artifacts.",
            ],
        }
    return {
        "success": True,
        "mode": "placeholder_v2",
        "message": "AI-assisted evaluation is a suggestion-only workflow. Human reviewers make final decisions.",
        "category_suggestions": suggestions,
    }
