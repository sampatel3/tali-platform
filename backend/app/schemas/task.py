from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class TaskCreate(BaseModel):
    name: str
    description: str
    task_type: str
    difficulty: str
    duration_minutes: int = 30
    starter_code: str
    test_code: str
    sample_data: Optional[Dict[str, Any]] = None
    dependencies: Optional[List[str]] = None
    success_criteria: Optional[Dict[str, Any]] = None
    test_weights: Optional[Dict[str, Any]] = None
    is_template: bool = False


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

    model_config = {"from_attributes": True}
