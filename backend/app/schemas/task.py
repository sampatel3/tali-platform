from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=5000)
    task_type: str = Field(min_length=2, max_length=100)
    difficulty: str = Field(min_length=2, max_length=100)
    duration_minutes: int = Field(default=30, ge=15, le=180)
    starter_code: str = Field(min_length=1, max_length=100000)
    test_code: str = Field(min_length=1, max_length=100000)
    sample_data: Optional[Dict[str, Any]] = None
    dependencies: Optional[List[str]] = None
    success_criteria: Optional[Dict[str, Any]] = None
    test_weights: Optional[Dict[str, Any]] = None
    is_template: bool = False
    calibration_prompt: Optional[str] = None
    score_weights: Optional[Dict[str, Any]] = None
    recruiter_weight_preset: Optional[str] = None
    proctoring_enabled: bool = False


class TaskUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, min_length=3, max_length=5000)
    task_type: Optional[str] = Field(default=None, min_length=2, max_length=100)
    difficulty: Optional[str] = Field(default=None, min_length=2, max_length=100)
    duration_minutes: Optional[int] = Field(default=None, ge=15, le=180)
    starter_code: Optional[str] = Field(default=None, min_length=1, max_length=100000)
    test_code: Optional[str] = Field(default=None, min_length=1, max_length=100000)
    is_active: Optional[bool] = None
    calibration_prompt: Optional[str] = None
    score_weights: Optional[Dict[str, Any]] = None
    recruiter_weight_preset: Optional[str] = None
    proctoring_enabled: Optional[bool] = None


class TaskResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    task_type: Optional[str] = None
    difficulty: Optional[str] = None
    duration_minutes: int
    starter_code: Optional[str] = None
    test_code: Optional[str] = None
    is_template: bool
    is_active: bool
    created_at: datetime
    calibration_prompt: Optional[str] = None
    score_weights: Optional[Dict[str, Any]] = None
    recruiter_weight_preset: Optional[str] = None
    proctoring_enabled: bool = False

    model_config = {"from_attributes": True}
