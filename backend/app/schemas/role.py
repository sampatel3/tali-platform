import copy
import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PositiveInt,
    field_serializer,
)

ROLE_DESCRIPTION_MAX_LENGTH = 20000

_ACTIVATION_READINESS_CODES = frozenset(
    {
        "assessment_email_unconfigured",
        "assessment_execution_unconfigured",
        "assessment_repository_unconfigured",
        "assessment_task_ambiguous",
        "assessment_task_approval_required",
        "assessment_task_repository_unready",
        "assessment_worker_unconfigured",
        "billing_credits_insufficient",
        "model_unconfigured",
        "native_apply_disabled",
        "role_monthly_budget_insufficient",
        "usage_meter_not_live",
        "worker_capabilities_unknown",
        "worker_model_probe_failed",
        "worker_model_unconfigured",
        "worker_unready",
        "worker_usage_meter_not_live",
    }
)


def _is_stable_error_code(value: object) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,79}", str(value or "").strip()))


def _public_provisioning_state(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    state = copy.deepcopy(value)

    def scrub(
        container: object, *, default: str, preserve_blocked: bool = False
    ) -> None:
        if not isinstance(container, dict) or not container.get("last_error"):
            return
        error = str(container["last_error"]).strip()[:2000]
        if preserve_blocked and str(container.get("status") or "") == "blocked":
            container["last_error"] = error
        elif _is_stable_error_code(error):
            container["last_error"] = error
        else:
            container["last_error"] = default

    scrub(
        state,
        default="assessment_task_generation_failed",
        preserve_blocked=True,
    )
    activation = state.get("activation_intent")
    if isinstance(activation, dict) and activation.get("last_error"):
        error = str(activation["last_error"]).strip()[:2000]
        readiness_code = error.split(":", 1)[0]
        if (
            str(activation.get("status") or "") == "blocked"
            or _is_stable_error_code(error)
            or readiness_code in _ACTIVATION_READINESS_CODES
        ):
            activation["last_error"] = error
        else:
            activation["last_error"] = "activation_failed"
    scrub(
        state.get("reconfiguration"),
        default="role_reconfiguration_failed",
        preserve_blocked=True,
    )
    scrub(
        state.get("interview_focus_provisioning"),
        default="interview_focus_generation_failed",
    )
    scrub(
        state.get("tech_questions_provisioning"),
        default="tech_question_generation_failed",
    )
    return state


class InterviewFocusQuestion(BaseModel):
    question: str
    what_to_listen_for: list[str] = Field(default_factory=list)
    concerning_signals: list[str] = Field(default_factory=list)


class InterviewFocus(BaseModel):
    role_summary: Optional[str] = None
    manual_screening_triggers: list[str] = Field(default_factory=list)
    questions: list[InterviewFocusQuestion] = Field(default_factory=list)


class InterviewPackQuestion(BaseModel):
    question: str
    why_this_matters: Optional[str] = None
    evidence_anchor: Optional[str] = None
    positive_signals: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    follow_up_probe: Optional[str] = None


class InterviewPack(BaseModel):
    stage: Literal["screening", "tech_stage_2"]
    summary: Optional[str] = None
    source: Optional[str] = None
    generated_at: Optional[datetime] = None
    questions: list[InterviewPackQuestion] = Field(default_factory=list)


class ApplicationInterviewResponse(BaseModel):
    id: int
    application_id: int
    organization_id: int
    stage: Literal["screening", "tech_stage_2"]
    source: Literal["fireflies", "manual"]
    provider: Optional[str] = None
    provider_meeting_id: Optional[str] = None
    provider_url: Optional[str] = None
    status: Optional[str] = None
    transcript_text: Optional[str] = None
    summary: Optional[str] = None
    speakers: list[dict[str, Any]] = Field(default_factory=list)
    provider_payload: Optional[dict[str, Any]] = None
    meeting_date: Optional[datetime] = None
    linked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    # Reject unknown fields outright. Old callers that still POST
    # retired keys (e.g. ``additional_requirements``, dropped in alembic
    # 068) used to silently succeed while the server discarded their
    # input — leaving them with roles missing the criteria they thought
    # they wrote. ``extra='forbid'`` fails the request loud instead.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=ROLE_DESCRIPTION_MAX_LENGTH)
    # ``additional_requirements`` was retired in alembic 068. Use the
    # /roles/{id}/criteria endpoints to author chips after the role is
    # created; new roles also inherit workspace chips at create time.
    screening_pack_template: Optional[InterviewPack] = None
    tech_interview_pack_template: Optional[InterviewPack] = None
    workable_actor_member_id: Optional[str] = Field(default=None, max_length=200)
    monthly_usd_budget_cents: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    score_threshold: Optional[int] = Field(default=None, ge=0, le=100)


class RoleUpdate(BaseModel):
    # Same fail-loud contract as RoleCreate — see comment there.
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=ROLE_DESCRIPTION_MAX_LENGTH)
    screening_pack_template: Optional[InterviewPack] = None
    tech_interview_pack_template: Optional[InterviewPack] = None
    auto_reject_threshold_mode: Optional[Literal["manual", "auto"]] = None
    workable_actor_member_id: Optional[str] = Field(default=None, max_length=200)
    # Agent-native fields
    agentic_mode_enabled: Optional[bool] = None
    agent_action_allowlist: Optional[list[str]] = None
    agent_token_budget_per_cycle: Optional[int] = Field(default=None, ge=1_000, le=500_000)
    agent_decision_budget_per_cycle: Optional[int] = Field(default=None, ge=1, le=200)
    # Autonomy toggles. New roles inherit their workspace policy; untouched
    # workspaces default candidate-facing send/resend/advance actions off and
    # deterministic pre-screen rejection on. ``auto_skip_assessment`` stores
    # configured intent; taskless roles are effectively skipped at runtime.
    # ``auto_reject`` and ``auto_reject_pre_screen`` can automate only
    # deterministic pre-screen failures; full-score/assessment rejects always
    # require confirmation.
    # Sending `null` leaves the existing value unchanged.
    auto_reject: Optional[bool] = None
    auto_reject_pre_screen: Optional[bool] = None
    auto_promote: Optional[bool] = None
    auto_send_assessment: Optional[bool] = None
    auto_resend_assessment: Optional[bool] = None
    auto_advance: Optional[bool] = None
    auto_skip_assessment: Optional[bool] = None
    # One-shot Turn-on choice for the only candidate-facing HITL gate in the
    # requisition bootstrap. The server applies this together with activation,
    # so recruiters never have to discover and complete a separate Tasks-page
    # step before the agent can run.
    activation_assessment_action: Optional[
        Literal["approve_generated_task", "approve_when_ready", "skip_assessment"]
    ] = None
    # Universal monthly USD cap (cents) for ALL Anthropic spend on the role.
    monthly_usd_budget_cents: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    score_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    # Workspace criterion ids the recruiter has explicitly hidden from
    # this role. Editable via PATCH so the chip editor's "Show hidden →
    # Add back" action can clear an entry without going through Sync.
    suppressed_org_criterion_ids: Optional[list[int]] = Field(default=None, max_length=200)


CRITERION_BUCKET_VALUES = ("must", "preferred", "constraint")


class RoleCriterionResponse(BaseModel):
    id: int
    source: Literal[
        "recruiter", "requisition", "derived_from_spec", "recruiter_constraint"
    ]
    ordering: int
    weight: float
    must_have: bool
    bucket: Literal["must", "preferred", "constraint"]
    # Provenance: the workspace criterion this row was copied from, if any.
    # ``null`` = role-only addition.
    org_criterion_id: Optional[int] = None
    customized_at: Optional[datetime] = None
    text: str
    # The criterion lives in a related table but still advances the shared
    # job revision so other open editors can detect it.
    role_version: Optional[int] = None

    model_config = {"from_attributes": True}


class RoleCriterionCreate(BaseModel):
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=220)
    bucket: Literal["must", "preferred", "constraint"] = "preferred"
    ordering: Optional[int] = Field(default=None, ge=0, le=10_000)
    weight: Optional[float] = Field(default=None, ge=0.0, le=10.0)


class RoleCriterionUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    text: Optional[str] = Field(default=None, min_length=1, max_length=220)
    bucket: Optional[Literal["must", "preferred", "constraint"]] = None
    ordering: Optional[int] = Field(default=None, ge=0, le=10_000)
    weight: Optional[float] = Field(default=None, ge=0.0, le=10.0)


class RoleCriteriaSummary(BaseModel):
    """Summary of a role's effective criteria state for the UI."""

    workspace_count: int = 0
    role_added_count: int = 0
    customized_count: int = 0
    suppressed_count: int = 0
    workspace_updated_at: Optional[datetime] = None


class RoleVersionCommand(BaseModel):
    expected_version: int = Field(ge=1)


class RoleResponse(BaseModel):
    id: int
    version: int = 1
    organization_id: int
    name: str
    description: Optional[str] = None
    criteria: list[RoleCriterionResponse] = Field(default_factory=list)
    source: Optional[str] = "manual"
    role_kind: Literal["standard", "sister"] = "standard"
    # Sister roles own an alternate job spec + scores, while this role owns the
    # shared ATS application roster and all Workable write-backs.
    ats_owner_role_id: Optional[int] = None
    ats_owner_role_name: Optional[str] = None
    effective_workable_job_id: Optional[str] = None
    sister_role_count: int = 0
    # Provider-neutral external job contract. New clients should use these
    # fields; the Workable-specific fields below remain for compatibility.
    # ``external_job_live`` is None when this role has no ATS link.
    ats_provider: Optional[Literal["workable", "bullhorn"]] = None
    external_job_id: Optional[str] = None
    external_job_state: Optional[str] = None
    external_job_live: Optional[bool] = None
    workable_job_id: Optional[str] = None
    # Requisition -> Workable job lifecycle: draft | open | filled |
    # filled_external | cancelled. None for legacy / Workable-synced roles
    # (derive display state from workable_job_state). See app.models.role.
    job_status: Optional[str] = None
    # The linked hiring brief's structured spec, when this role originated from
    # (or was linked to) a requisition. Detail-only — the list path leaves it
    # None to avoid a per-row brief lookup. Drives the role's Job Spec tab.
    requisition: Optional[dict[str, Any]] = None
    # The consultancy client this role is for (via its requisition brief). Drives
    # the Jobs list's Client column + filter + the per-client rollups. None for
    # direct (non-consultancy) roles. Internal-only — never the rate/margin.
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    # Workable job lifecycle state: published | draft | archived | closed.
    # None for manual/Taali-created roles. ``published`` == live/recruiting.
    workable_job_state: Optional[str] = None
    # False when the linked Workable job is archived/closed/draft — Workable
    # rejects candidate write-backs (disqualify/move) there, so Taali acts
    # locally only (no sync). True for published jobs and manual roles. The UI
    # uses this to grey out the role + disable Workable-write toggles.
    workable_job_live: bool = True
    # True only when the role's native /job/{token} page is accepting
    # applications and eligible for the careers feed. A published-but-inactive
    # preview deliberately remains False. Distinct from ``workable_job_state``
    # (the external ATS). Drives the Jobs list "Live" badge.
    is_published: bool = False
    job_spec_filename: Optional[str] = None
    job_spec_text: Optional[str] = None
    job_spec_uploaded_at: Optional[datetime] = None
    job_spec_manually_edited_at: Optional[datetime] = None
    job_spec_present: bool = False
    interview_focus: Optional[InterviewFocus] = None
    interview_focus_generated_at: Optional[datetime] = None
    screening_pack_template: Optional[InterviewPack] = None
    tech_interview_pack_template: Optional[InterviewPack] = None
    auto_reject_threshold_mode: Literal["manual", "auto"] = "manual"
    workable_actor_member_id: Optional[str] = None
    starred_for_auto_sync: bool = False
    agentic_mode_enabled: bool = False
    agent_action_allowlist: Optional[list[str]] = None
    agent_token_budget_per_cycle: Optional[int] = None
    agent_decision_budget_per_cycle: Optional[int] = None
    auto_reject: bool = False
    auto_reject_pre_screen: bool = False
    auto_promote: bool = False
    auto_send_assessment: Optional[bool] = None
    auto_resend_assessment: Optional[bool] = None
    auto_advance: Optional[bool] = None
    auto_skip_assessment: bool = False
    # Flattened runtime truth, including legacy fallbacks and permanent HITL
    # rails. Clients should use this for status/copy and the nullable fields
    # above for explicit override editing.
    agent_effective_policy: dict[str, Any] = Field(default_factory=dict)
    monthly_usd_budget_cents: Optional[int] = None
    score_threshold: Optional[int] = None
    agent_paused_at: Optional[datetime] = None
    agent_paused_reason: Optional[str] = None
    agent_last_run_at: Optional[datetime] = None
    agent_bootstrap_status: Optional[Literal["starting", "ready", "failed"]] = None
    agent_bootstrap_error: Optional[str] = None
    agent_bootstrap_started_at: Optional[datetime] = None
    agent_bootstrap_completed_at: Optional[datetime] = None
    # Durable JD -> assessment-authoring + Turn-on command state. The backend
    # owns generation, validation, activation, and recovery; clients may read
    # this for progress but never have to remain open to drive the workflow.
    assessment_task_provisioning: Optional[dict[str, Any]] = None
    tasks_count: int = 0
    applications_count: int = 0
    stage_counts: dict[str, int] = Field(default_factory=dict)
    # Pending agent decisions grouped by decision_type — feeds the role-page
    # funnel's "awaiting your decision" chips (uncapped, unlike the fetched list).
    pending_decisions_by_type: dict[str, int] = Field(default_factory=dict)
    active_candidates_count: int = 0
    last_candidate_activity_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    @field_serializer("assessment_task_provisioning")
    def serialize_provisioning_state(self, value):
        return _public_provisioning_state(value)

    @field_serializer("agent_bootstrap_error")
    def serialize_bootstrap_error(self, value):
        if not value:
            return None
        error = str(value).strip()[:2000]
        if _is_stable_error_code(error):
            return error
        state = self.assessment_task_provisioning or {}
        if str(state.get("status") or "") == "blocked":
            return error
        return "agent_bootstrap_failed"

    model_config = {"from_attributes": True}


class JobStatusUpdate(BaseModel):
    """Recruiter sets the requisition->Workable job lifecycle status. The
    recruiter is the authority, so any valid status may be set (incl. reopening
    a filled role or marking it filled by an outside vendor). ``draft`` and
    ``open`` are "still being worked"; the rest are terminal outcomes."""

    status: Literal["draft", "open", "filled", "filled_external", "cancelled"]
    reason: Optional[str] = Field(default=None, max_length=2000)
    expected_version: int = Field(ge=1)


class RoleClientUpdate(BaseModel):
    """Assign (or clear) the consultancy client a role belongs to. For roles
    with no requisition brief — e.g. Workable-imported jobs created before
    client tagging existed — the assignment is stored on a minimal stub brief so
    the Jobs Client column / filter and per-client rollups pick the role up.
    ``client_id=None`` clears the assignment."""

    client_id: Optional[int] = Field(default=None, gt=0)
    expected_version: int = Field(ge=1)


class RoleTaskLinkRequest(BaseModel):
    task_id: int = Field(gt=0)
    expected_version: int = Field(ge=1)


class RoleJobSpecUpdate(BaseModel):
    """One truthful save contract for the role title, spec and linked tasks.

    ``task_ids`` is a PATCH-like optional field even though the endpoint is a
    PUT: older/editor-only clients can update the spec without accidentally
    clearing assessment configuration they did not load. An explicitly
    supplied empty list still means "unlink every removable task".
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    job_spec_text: str = Field(min_length=60, max_length=100_000)
    task_ids: Optional[list[PositiveInt]] = Field(default=None, max_length=100)


class JobSpecCriteriaDiff(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    criteria_count: int = 0


class JobSpecRescreenEstimate(BaseModel):
    count: int = 0
    est_cost_usd: float = 0.0


class RoleJobSpecUpdateResponse(BaseModel):
    applied: bool = True
    role: RoleResponse
    diff: JobSpecCriteriaDiff
    would_rescreen: JobSpecRescreenEstimate
    scores_invalidated: int = 0
    rescore_dispatch_approved: bool = False


class ApplicationCreate(BaseModel):
    candidate_email: EmailStr
    candidate_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    candidate_position: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = Field(default="applied", max_length=100)
    pipeline_stage: Optional[Literal["applied", "invited", "in_assessment", "review", "advanced"]] = None
    application_outcome: Optional[Literal["open", "rejected", "withdrawn", "hired"]] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


class ApplicationUpdate(BaseModel):
    status: Optional[str] = Field(default=None, max_length=100)
    pipeline_stage: Optional[Literal["applied", "invited", "in_assessment", "review", "advanced"]] = None
    application_outcome: Optional[Literal["open", "rejected", "withdrawn", "hired"]] = None
    expected_version: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = Field(default=None, max_length=4000)
    candidate_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    candidate_position: Optional[str] = Field(default=None, max_length=200)


class ApplicationResponse(BaseModel):
    id: int
    organization_id: int
    candidate_id: int
    role_id: int
    status: str
    pipeline_stage: Literal["sourced", "applied", "invited", "in_assessment", "review", "advanced"] = "applied"
    pipeline_stage_updated_at: Optional[datetime] = None
    pipeline_stage_source: Literal["system", "recruiter", "sync", "agent"] = "system"
    application_outcome: Literal["open", "rejected", "withdrawn", "hired"] = "open"
    application_outcome_updated_at: Optional[datetime] = None
    external_refs: Optional[dict[str, Any]] = None
    external_stage_raw: Optional[str] = None
    external_stage_normalized: Optional[str] = None
    integration_sync_state: Optional[dict[str, Any]] = None
    # Present on a recruiter-initiated asynchronous ATS write (stage move or
    # Bullhorn outcome). Writes are serialized with sync traffic, so the UI must
    # treat them as queued until the tracked run reaches a terminal state.
    ats_writeback_status: Optional[Literal["queued"]] = None
    ats_writeback_job_run_id: Optional[int] = None
    # Set only by the versioned related-role move endpoint. Its presence proves
    # the server-side worker owns provider confirmation and local projection;
    # older rolling-deploy backends cannot silently accept this route.
    ats_related_transition_protocol: Optional[int] = None
    ats_related_stage_managed: Optional[bool] = None
    pipeline_external_drift: bool = False
    version: int = 1
    notes: Optional[str] = None
    # Recruiter's manually recorded decision (advance/hold/reject + rationale,
    # confidence, next steps) with draft/submitted lifecycle, version, author
    # and history. Null until a decision is recorded. Used for the standing
    # report's Evaluate surface when no assessment is linked.
    manual_decision: Optional[dict[str, Any]] = None
    candidate_email: str
    candidate_name: Optional[str] = None
    candidate_position: Optional[str] = None
    # The role this application is for. Populated from the joined Role
    # row so the global search dropdown can show "Senior API Developer"
    # next to the candidate's name (without it the UI fell back to
    # candidate_position, which is the candidate's *current* job — same
    # for every application a candidate makes, leaving duplicate rows
    # visually indistinguishable).
    role_name: Optional[str] = None
    cv_filename: Optional[str] = None
    cv_uploaded_at: Optional[datetime] = None
    # True when the application row has extracted CV text — what scoring and
    # pre-screening actually consume. A CV file can exist while extraction
    # produced nothing; the role page "New CVs" tile needs the distinction to
    # mirror the auto-scorer's cv_text filter.
    has_cv_text: bool = False
    cv_match_score: Optional[float] = None
    cv_match_details: Optional[dict] = None
    cv_match_scored_at: Optional[datetime] = None
    # "cancelled" covers legacy CvScoreJob rows written before the score-
    # invalidation rework. "retry_wait" is the durable related-role state
    # while scoring waits for authority or a transient retry. Rejecting either
    # valid persisted state here 500s every /applications listing that touches it.
    score_status: Optional[Literal[
        "pending",
        "running",
        "retry_wait",
        "done",
        "error",
        "stale",
        "cancelled",
        "unscorable",
        "excluded",
    ]] = None
    # Present when this row is projected into a sister role. ``id`` remains the
    # canonical source application id so every stage/outcome action routes to
    # the ATS-owning application rather than a cloned pipeline record.
    operational_role_id: Optional[int] = None
    operational_role_name: Optional[str] = None
    sister_role_id: Optional[int] = None
    source_role_score: Optional[float] = None
    related_role_availability: Optional[Literal[
        "active", "external_advanced", "disqualified", "closed"
    ]] = None
    source: Optional[str] = "manual"
    workable_candidate_id: Optional[str] = None
    workable_stage: Optional[str] = None
    workable_score_raw: Optional[float] = None
    workable_score: Optional[float] = None
    workable_score_source: Optional[str] = None
    workable_disqualified: Optional[bool] = None
    workable_disqualified_at: Optional[datetime] = None
    rank_score: Optional[float] = None
    # Rich candidate profile fields
    candidate_headline: Optional[str] = None
    candidate_image_url: Optional[str] = None
    candidate_location: Optional[str] = None
    candidate_phone: Optional[str] = None
    candidate_profile_url: Optional[str] = None
    candidate_social_profiles: Optional[list] = None
    candidate_tags: Optional[list] = None
    candidate_skills: Optional[list] = None
    candidate_education: Optional[list] = None
    candidate_experience: Optional[list] = None
    candidate_summary: Optional[str] = None
    candidate_workable_created_at: Optional[datetime] = None
    # When the candidate applied to THIS role: per-application Workable
    # created_at, falling back to the candidate-level copy (legacy rows), then
    # to the local application created_at (manual/non-Workable sources).
    applied_at: Optional[datetime] = None
    workable_sourced: Optional[bool] = None
    workable_profile_url: Optional[str] = None
    workable_enriched: Optional[bool] = None
    pre_screen_score: Optional[float] = None
    requirements_fit_score: Optional[float] = None
    pre_screen_recommendation: Optional[str] = None
    pre_screen_evidence: Optional[dict[str, Any]] = None
    pre_screen_run_at: Optional[datetime] = None
    # Graph sync state — populated when the candidate has a row in
    # graph_sync_state. graph_stale=True iff the CV was uploaded after the
    # last graph sync (so the projection is out-of-date).
    graph_synced_at: Optional[datetime] = None
    graph_stale: Optional[bool] = None
    auto_reject_state: Optional[str] = None
    auto_reject_reason: Optional[str] = None
    auto_reject_triggered_at: Optional[datetime] = None
    # The application's latest pending agent decision (id/decision_type/
    # recommendation/status), resolved per-row by the list endpoint so the
    # candidate-list AGENT column isn't capped by a separate decisions fetch.
    pending_decision: Optional[dict[str, Any]] = None
    screening_pack: Optional[InterviewPack] = None
    tech_interview_pack: Optional[InterviewPack] = None
    screening_interview_summary: Optional[dict[str, Any]] = None
    tech_interview_summary: Optional[dict[str, Any]] = None
    interview_evidence_summary: Optional[dict[str, Any]] = None
    interviews: list[ApplicationInterviewResponse] = Field(default_factory=list)
    taali_score: Optional[float] = None
    score_mode: Optional[str] = None
    valid_assessment_id: Optional[int] = None
    valid_assessment_status: Optional[str] = None
    score_summary: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Most recent moment *any* meaningful activity touched this application —
    # CV upload, any scoring pass, a stage/outcome/notes edit, or a recruiter
    # comment (which lands on the linked assessment's timeline). Computed in
    # ``application_to_response``; drives the pipeline "Last updated" column.
    last_activity_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApplicationDetailResponse(ApplicationResponse):
    """Application with optional full CV text for viewer."""
    cv_text: Optional[str] = None
    cv_sections: Optional[dict[str, Any]] = None
    assessment_preview: Optional[dict[str, Any]] = None
    assessment_history: list[dict[str, Any]] = Field(default_factory=list)
    candidate_interview_kit: Optional[dict[str, Any]] = None
    # Recruiter-internal Workable surfaces, structured for the Notes tab.
    # Detail-only (omitted from list rows) and stripped from client shares.
    workable_comments: list[dict[str, Any]] = Field(default_factory=list)
    workable_questionnaire_answers: list[dict[str, Any]] = Field(default_factory=list)
    workable_activity_log: list[dict[str, Any]] = Field(default_factory=list)
    # Structured recruiter interview feedback, newest-first. Recruiter-internal;
    # None on client shares (stripped in application_detail_payload).
    interview_feedback: Optional[list[dict[str, Any]]] = None


class ApplicationCvUploadResponse(BaseModel):
    success: bool = True
    application_id: int
    filename: str
    text_preview: str
    uploaded_at: datetime


class ApplicationStageUpdate(BaseModel):
    pipeline_stage: Literal["applied", "invited", "in_assessment", "review", "advanced"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=2000)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class WorkableMoveStageRequest(BaseModel):
    """Recruiter hand-back to the active ATS.

    Workable receives its remote stage slug. Bullhorn receives Taali's stage
    intent and resolves it through the organization's explicit stage map.
    ``acting_role_id`` attributes a shared-application move to a related role.
    """

    target_stage: str = Field(min_length=1, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=2000)
    acting_role_id: Optional[int] = Field(default=None, ge=1)


class ApplicationOutcomeUpdate(BaseModel):
    application_outcome: Literal["open", "rejected", "withdrawn", "hired"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=2000)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)
    # Related roles share the owning ATS application. Supplying the acting role
    # authorizes the explicit global outcome against that related roster and
    # preserves the source-role boundary for ordinary callers.
    acting_role_id: Optional[int] = Field(default=None, ge=1)


class ApplicationEventResponse(BaseModel):
    id: int
    application_id: int
    organization_id: int
    event_type: str
    from_stage: Optional[str] = None
    to_stage: Optional[str] = None
    from_outcome: Optional[str] = None
    to_outcome: Optional[str] = None
    actor_type: str
    actor_id: Optional[int] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    created_at: datetime


class ApplicationNoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=5000)
    # Default-visible to the recruiting agent: a per-candidate note is almost
    # always guidance the agent should weigh ("already interviewed — not
    # suitable"). Untick for pure team chatter the agent shouldn't read.
    for_agent: bool = True
    # The "add info" surface stores three flavours of note through this one
    # endpoint. ``note`` (the default) is the freeform note box; ``ranking``
    # carries a 1–5 score + optional comment; ``link`` carries a URL + optional
    # label. The structured bits ride in the event metadata so the FE can
    # differentiate them and the agent-visible payload can read a readable form.
    kind: Literal["note", "ranking", "link"] = "note"
    ranking: Optional[int] = Field(default=None, ge=1, le=5)
    link_url: Optional[str] = Field(default=None, max_length=2000)
    link_label: Optional[str] = Field(default=None, max_length=200)


class AssessmentFromApplicationCreate(BaseModel):
    task_id: int = Field(gt=0)
    duration_minutes: int = Field(default=30, ge=15, le=180)


class AssessmentRetakeCreate(AssessmentFromApplicationCreate):
    void_reason: Optional[str] = Field(default=None, max_length=2000)


class ManualApplicationInterviewCreate(BaseModel):
    stage: Literal["screening", "tech_stage_2"]
    transcript_text: str = Field(min_length=1, max_length=200000)
    provider_url: Optional[str] = Field(default=None, max_length=2000)
    meeting_date: Optional[datetime] = None
    summary: Optional[str] = Field(default=None, max_length=4000)
    speakers: list[dict[str, Any]] = Field(default_factory=list)


class FirefliesInterviewLinkCreate(BaseModel):
    stage: Literal["screening", "tech_stage_2"]
    fireflies_meeting_id: str = Field(min_length=1, max_length=200)
    provider_url: Optional[str] = Field(default=None, max_length=2000)


class RoleFeedbackNoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)


class RoleFeedbackNoteResponse(BaseModel):
    id: int
    role_id: int
    author_user_id: Optional[int] = None
    author_name: Optional[str] = None
    note: str
    created_at: datetime
    role_version: Optional[int] = None

    model_config = {"from_attributes": True}
