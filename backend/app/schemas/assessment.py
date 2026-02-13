from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class AssessmentCreate(BaseModel):
    candidate_email: EmailStr
    candidate_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    task_id: int = Field(gt=0)
    duration_minutes: int = Field(default=30, ge=15, le=180)


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
    # Computed for candidate detail UI (snake_case for API; frontend maps to camelCase)
    prompts_list: Optional[List[Dict[str, Any]]] = None
    results: Optional[List[Dict[str, Any]]] = None
    breakdown: Optional[Dict[str, Any]] = None
    # For list/table display (from joined candidate/task)
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    task_name: Optional[str] = None

    # Prompt scoring fields
    prompt_quality_score: Optional[float] = None
    prompt_efficiency_score: Optional[float] = None
    independence_score: Optional[float] = None
    context_utilization_score: Optional[float] = None
    design_thinking_score: Optional[float] = None
    debugging_strategy_score: Optional[float] = None
    written_communication_score: Optional[float] = None
    learning_velocity_score: Optional[float] = None
    error_recovery_score: Optional[float] = None
    requirement_comprehension_score: Optional[float] = None
    calibration_score: Optional[float] = None
    prompt_fraud_flags: Optional[List[Dict[str, Any]]] = None
    prompt_analytics: Optional[Dict[str, Any]] = None
    browser_focus_ratio: Optional[float] = None
    tab_switch_count: Optional[int] = None
    time_to_first_prompt_seconds: Optional[int] = None
    # SECURITY: cv_file_url (server path) never exposed to API; only boolean + filename
    cv_uploaded: Optional[bool] = None
    cv_filename: Optional[str] = None
    cv_uploaded_at: Optional[datetime] = None
    final_score: Optional[float] = None
    score_breakdown: Optional[Dict[str, Any]] = None
    score_weights_used: Optional[Dict[str, float]] = None
    flags: Optional[List[str]] = None
    scored_at: Optional[datetime] = None
    posted_to_workable: Optional[bool] = None
    posted_to_workable_at: Optional[datetime] = None
    candidate_cv_filename: Optional[str] = None
    candidate_job_spec_filename: Optional[str] = None
    candidate_cv_uploaded_at: Optional[datetime] = None
    candidate_job_spec_uploaded_at: Optional[datetime] = None
    total_duration_seconds: Optional[int] = None
    total_prompts: Optional[int] = None
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    tests_run_count: Optional[int] = None
    tests_pass_count: Optional[int] = None
    completed_due_to_timeout: Optional[bool] = None
    final_repo_state: Optional[str] = None
    git_evidence: Optional[Dict[str, Any]] = None
    assessment_repo_url: Optional[str] = None
    assessment_branch: Optional[str] = None
    clone_command: Optional[str] = None
    # Manual evaluator: task rubric + saved category scores/evidence
    evaluation_rubric: Optional[Dict[str, Any]] = None
    manual_evaluation: Optional[Dict[str, Any]] = None
    evaluation_result: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


class AssessmentStart(BaseModel):
    assessment_id: int
    token: str
    sandbox_id: str
    task: Dict[str, Any]
    time_remaining: int


class CodeExecutionRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100000)


class ClaudeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_history: List[Dict[str, Any]] = []
    code_context: Optional[str] = None  # Current editor content at time of prompt
    paste_detected: bool = False  # Whether prompt was pasted
    browser_focused: bool = True  # Whether browser was in focus
    time_since_last_prompt_ms: Optional[int] = None  # Time since previous prompt in ms


class SubmitRequest(BaseModel):
    final_code: str = Field(min_length=1, max_length=100000)
    tab_switch_count: int = 0  # Total tab switches during assessment
