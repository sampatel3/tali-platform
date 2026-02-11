"""Pydantic models describing the scoring result payload."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class FraudResult(BaseModel):
    flags: List[str] = []
    paste_ratio: float = 0.0
    external_paste_detected: bool = False
    solution_dump_detected: bool = False
    injection_attempt: bool = False
    suspiciously_fast: bool = False


class V2Result(BaseModel):
    enabled: bool = False
    grammar_score: Optional[float] = None
    sentiment_trajectory: Optional[Any] = None
    prompt_type_distribution: Optional[Any] = None
    learning_velocity: Optional[float] = None
    copy_from_stackoverflow: Optional[float] = None
    copy_from_chatgpt: Optional[float] = None
    code_complexity: Optional[float] = None
    linting_score: Optional[float] = None


class ScoringResult(BaseModel):
    final_score: float
    component_scores: Dict[str, float]
    weights_used: Dict[str, float]
    metric_details: Dict[str, Any]
    fraud: FraudResult
    soft_signals: Dict[str, Any]
    v2: V2Result
