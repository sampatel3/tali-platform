import copy
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    field_serializer,
    model_validator,
)


_TASK_UPDATE_NON_NULL_FIELDS = frozenset(
    {
        "name",
        "description",
        "task_type",
        "difficulty",
        "duration_minutes",
        "starter_code",
        "test_code",
        "is_active",
        "proctoring_enabled",
    }
)


def _public_task_extra_data(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    extra = copy.deepcopy(value)
    battle = extra.get("battle_test")
    history = extra.get("battle_test_history")
    reports = [battle]
    if isinstance(history, list):
        reports.extend(history)
    for report in reports:
        if isinstance(report, dict) and report.get("error"):
            report["error"] = "assessment_task_battle_test_failed"
    state = extra.get("battle_test_provisioning")
    if isinstance(state, dict) and state.get("last_error"):
        error = str(state["last_error"]).strip()[:2000]
        is_code = re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error)
        if str(state.get("status") or "") == "repair_pending" or is_code:
            state["last_error"] = error
        else:
            state["last_error"] = "assessment_task_processing_failed"
    return extra


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
    claude_budget_limit_usd: Optional[float] = Field(default=None, gt=0, le=1000)
    task_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("task_key", "task_id"))
    role: Optional[str] = None
    scenario: Optional[str] = None
    repo_structure: Optional[Dict[str, Any]] = None
    evaluation_rubric: Optional[Dict[str, Any]] = None
    extra_data: Optional[Dict[str, Any]] = None
    expected_insights: Optional[List[str]] = None
    valid_solutions: Optional[List[str]] = None
    expected_approaches: Optional[Dict[str, Any]] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, min_length=3, max_length=5000)
    task_type: Optional[str] = Field(default=None, min_length=2, max_length=100)
    difficulty: Optional[str] = Field(default=None, min_length=2, max_length=100)
    duration_minutes: Optional[int] = Field(default=None, ge=15, le=180)
    starter_code: Optional[str] = Field(default=None, min_length=1, max_length=100000)
    test_code: Optional[str] = Field(default=None, min_length=1, max_length=100000)
    sample_data: Optional[Dict[str, Any]] = None
    dependencies: Optional[List[str]] = None
    success_criteria: Optional[Dict[str, Any]] = None
    test_weights: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    calibration_prompt: Optional[str] = None
    score_weights: Optional[Dict[str, Any]] = None
    recruiter_weight_preset: Optional[str] = None
    proctoring_enabled: Optional[bool] = None
    claude_budget_limit_usd: Optional[float] = Field(default=None, gt=0, le=1000)
    task_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("task_key", "task_id"))
    role: Optional[str] = None
    scenario: Optional[str] = None
    repo_structure: Optional[Dict[str, Any]] = None
    evaluation_rubric: Optional[Dict[str, Any]] = None
    extra_data: Optional[Dict[str, Any]] = None
    expected_insights: Optional[List[str]] = None
    valid_solutions: Optional[List[str]] = None
    expected_approaches: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def reject_explicit_null_for_required_fields(self):
        """Keep PATCH omission distinct from clearing required task state."""

        null_fields = sorted(
            field
            for field in _TASK_UPDATE_NON_NULL_FIELDS
            if field in self.model_fields_set and getattr(self, field) is None
        )
        if null_fields:
            fields = ", ".join(null_fields)
            raise ValueError(f"Task fields must not be null: {fields}")
        return self


class TaskResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    task_type: Optional[str] = None
    difficulty: Optional[str] = None
    duration_minutes: int
    starter_code: Optional[str] = None
    test_code: Optional[str] = None
    sample_data: Optional[Dict[str, Any]] = None
    dependencies: Optional[List[str]] = None
    success_criteria: Optional[Dict[str, Any]] = None
    test_weights: Optional[Dict[str, Any]] = None
    is_template: bool
    is_active: bool
    created_at: datetime
    calibration_prompt: Optional[str] = None
    score_weights: Optional[Dict[str, Any]] = None
    recruiter_weight_preset: Optional[str] = None
    proctoring_enabled: bool = False
    claude_budget_limit_usd: Optional[float] = None
    # New fields from task JSON spec
    task_key: Optional[str] = None
    role: Optional[str] = None
    scenario: Optional[str] = None
    repo_structure: Optional[Dict[str, Any]] = None
    evaluation_rubric: Optional[Dict[str, Any]] = None
    extra_data: Optional[Dict[str, Any]] = None
    main_repo_path: Optional[str] = None
    template_repo_url: Optional[str] = None
    repo_file_count: int = 0

    @field_serializer("extra_data")
    def serialize_extra_data(self, value):
        return _public_task_extra_data(value)

    model_config = {"from_attributes": True}
