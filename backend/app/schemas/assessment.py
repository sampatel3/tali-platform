from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class AssessmentCreate(BaseModel):
    candidate_email: str
    candidate_name: str
    task_id: int
    duration_minutes: int = 30


class AssessmentResponse(BaseModel):
    id: int
    organization_id: int
    candidate_id: int
    task_id: int
    token: str
    status: str
    duration_minutes: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    score: Optional[float] = None
    tests_passed: Optional[int] = None
    tests_total: Optional[int] = None
    code_quality_score: Optional[float] = None
    time_efficiency_score: Optional[float] = None
    ai_usage_score: Optional[float] = None
    test_results: Optional[Dict[str, Any]] = None
    ai_prompts: Optional[List[Dict[str, Any]]] = None
    timeline: Optional[List[Dict[str, Any]]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AssessmentStart(BaseModel):
    assessment_id: int
    token: str
    sandbox_id: str
    task: Dict[str, Any]
    time_remaining: int


class CodeExecutionRequest(BaseModel):
    code: str


class ClaudeRequest(BaseModel):
    message: str
    conversation_history: List[Dict[str, Any]] = []


class SubmitRequest(BaseModel):
    final_code: str
