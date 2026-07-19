"""Backward-compatible typed views over the heuristic scoring payload.

The scoring runtime continues to return dictionaries from
``calculate_mvp_score``.  These models remain available to integrations that
want validation without introducing a second scoring implementation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FraudResult(BaseModel):
    flags: list[str] = Field(default_factory=list)
    paste_ratio: float = 0.0
    external_paste_detected: bool = False
    solution_dump_detected: bool = False
    injection_attempt: bool = False
    suspiciously_fast: bool = False


class V2Result(BaseModel):
    enabled: bool = False
    grammar_score: float | None = None
    sentiment_trajectory: Any | None = None
    prompt_type_distribution: Any | None = None
    learning_velocity: float | None = None
    copy_from_stackoverflow: float | None = None
    copy_from_chatgpt: float | None = None
    code_complexity: float | None = None
    linting_score: float | None = None


class ScoringResult(BaseModel):
    final_score: float
    component_scores: dict[str, float] = Field(default_factory=dict)
    weights_used: dict[str, float] = Field(default_factory=dict)
    metric_details: dict[str, Any] = Field(default_factory=dict)
    fraud: FraudResult = Field(default_factory=FraudResult)
    soft_signals: dict[str, Any] = Field(default_factory=dict)
    v2: V2Result = Field(default_factory=V2Result)


__all__ = ["FraudResult", "ScoringResult", "V2Result"]
