"""Constructed-truth gate for candidate state, action, and decision tools.

The fixture deliberately separates three facts that an agent must never blur:

* a candidate's current role-local state;
* a confirmed workflow action that happened at a particular time; and
* an agent recommendation or recruiter resolution.

It runs the same oracle for an ordinary role and a fully independent related
role.  Related-role membership is the live ``SisterRoleEvaluation`` row; the
source/ATS application is evidence and a writeback restriction, not a hidden
owner of the logical role.

No model, graph provider, ATS, or other paid/external boundary is invoked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.agent_chat import tools as agent_chat_tools
from app.agent_runtime import tool_registry as autonomous_tools
from app.candidate_search.role_projection import OWNER_ROLE_JUDGMENT_FIELDS
from app.deps import get_current_user
from app.domains.assessments_runtime.search_canary_auth import (
    get_applications_search_principal,
)
from app.main import app
from app.mcp import server as mcp_server
from app.mcp.auth import Principal
from app.models.agent_decision import AgentDecision
from app.models.api_key import (
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
    SCOPE_ROLES_READ,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SisterRoleEvaluation,
)
from app.models.user import User
from app.platform.database import get_db
from app.taali_chat import tool_registry as taali_tools


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=7)
SEMANTIC_KEYS = (
    "current-and-moved",
    "moved-on",
    "pending-only",
    "before-window",
    "failed-attempt",
    "direct-recruiter",
    "resolved-no-effect",
)
CURRENT_TECHNICAL_INTERVIEW = {
    "current-and-moved",
    "before-window",
    "direct-recruiter",
}
CONFIRMED_TECHNICAL_INTERVIEW_ACTIONS = [
    "current-and-moved",
    "direct-recruiter",
    "moved-on",
]
PENDING_DECISIONS = ["failed-attempt", "pending-only"]
RESOLVED_DECISIONS = [
    "resolved-no-effect",
    "current-and-moved",
    "moved-on",
]


@dataclass(frozen=True)
class RoleTruth:
    role: Role
    applications: dict[str, int]
    candidates: dict[str, int]
    names: dict[str, str]
    decisions: dict[str, int]
    confirmed_technical_event_ids: tuple[int, ...]
    failed_technical_event_id: int
    expected_local_stages: dict[str, str]

    @property
    def key_by_name(self) -> dict[str, str]:
        return {name: key for key, name in self.names.items()}


@dataclass(frozen=True)
class CapabilityWorld:
    user: User
    standard: RoleTruth
    related: RoleTruth
    unrelated_role: Role


class _BorrowedSession:
    """Let FastMCP use the test transaction without closing its owner."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def close(self) -> None:
        return None


def _candidate_application(
    db: Any,
    *,
    organization: Organization,
    role: Role,
    prefix: str,
    key: str,
    local_stage: str,
    ats_stage: str | None,
    score: float,
    deleted_at: datetime | None = None,
) -> tuple[Candidate, CandidateApplication]:
    candidate = Candidate(
        organization_id=int(organization.id),
        email=f"{prefix}-{key}@candidate-truth.test",
        full_name=f"{prefix} {key}",
        position="AI Engineer",
        skills=["Python"],
        cv_text=f"Constructed truth profile for {prefix} {key}.",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        source="workable" if ats_stage else "manual",
        status="applied",
        pipeline_stage=local_stage,
        pipeline_stage_updated_at=NOW - timedelta(hours=6),
        pipeline_stage_source="recruiter",
        application_outcome="open",
        application_outcome_updated_at=NOW - timedelta(hours=6),
        workable_candidate_id=(f"wk-{prefix}-{key}" if ats_stage else None),
        workable_stage=ats_stage,
        external_stage_raw=ats_stage,
        external_stage_normalized=(
            "advanced"
            if ats_stage in {"Technical Interview", "Final Interview"}
            else "applied"
            if ats_stage
            else None
        ),
        taali_score_cache_100=score,
        pre_screen_score_100=score,
        cv_match_score=score,
        deleted_at=deleted_at,
    )
    db.add(application)
    db.flush()
    return candidate, application


def _add_decision(
    db: Any,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    key: str,
    status: str,
    created_at: datetime,
    resolved_at: datetime | None,
    resolved_by_user_id: int | None,
) -> AgentDecision:
    decision = AgentDecision(
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status=status,
        reasoning=f"Constructed recommendation for {key}",
        evidence={"fixture": key},
        confidence=0.9,
        model_version="offline-fixture",
        prompt_version="offline-fixture",
        created_at=created_at,
        resolved_at=resolved_at,
        resolved_by_user_id=resolved_by_user_id,
        human_disposition=("approved" if status == "approved" else None),
        resolution_metadata=(
            {"target_stage": "Technical Interview"}
            if status == "approved"
            else {}
        ),
        idempotency_key=f"capability-matrix:{role_id}:{key}:{status}",
    )
    db.add(decision)
    db.flush()
    return decision


def _add_event(
    db: Any,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    key: str,
    occurred_at: datetime,
    target_stage: str,
    effect_status: str,
    actor_type: str,
    decision_id: int | None,
) -> CandidateApplicationEvent:
    event = CandidateApplicationEvent(
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        agent_decision_id=decision_id,
        # Technical Interview is a provider target.  A local Tali transition
        # to ``advanced`` is separate evidence and cannot certify this fact.
        event_type=(
            "workable_moved"
            if effect_status == "confirmed"
            else "workable_move_stage_failed"
        ),
        from_stage=None,
        to_stage=None,
        actor_type=actor_type,
        actor_id=None,
        reason=f"Constructed {effect_status} action for {key}",
        event_metadata={"fixture": key},
        target_stage=target_stage,
        effect_status=effect_status,
        idempotency_key=f"capability-event:{role_id}:{key}:{target_stage}",
        created_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def _seed_role_truth(
    db: Any,
    *,
    organization: Organization,
    user: User,
    role: Role,
    prefix: str,
    related_owner: Role | None = None,
) -> RoleTruth:
    applications: dict[str, int] = {}
    candidates: dict[str, int] = {}
    names: dict[str, str] = {}
    decisions: dict[str, int] = {}
    event_applications: dict[str, int] = {}
    score = 98.0
    ats_stages = {
        "current-and-moved": "Technical Interview",
        "moved-on": "Final Interview",
        "pending-only": "Applied",
        "before-window": "Technical Interview",
        "failed-attempt": "Applied",
        "direct-recruiter": "Technical Interview",
        "resolved-no-effect": "Applied",
    }
    local_stages = {
        "current-and-moved": "advanced",
        "moved-on": "review" if related_owner is not None else "advanced",
        "pending-only": "review",
        "before-window": "advanced",
        "failed-attempt": "review",
        "direct-recruiter": "advanced",
        "resolved-no-effect": "review",
    }

    for key in SEMANTIC_KEYS:
        # One related member has its own direct application.  Every member,
        # regardless of storage form, still has an explicit live SRE row.
        # The direct-recruiter oracle is a fully independent membership: its
        # source row belongs to the related role, while a different owner-role
        # row carries the optional ATS transport state.  Because this key is
        # included in the Technical Interview filter assertion below, every
        # tool surface must resolve ats_application_id instead of reading the
        # local/source row as ATS authority.
        direct_related_member = related_owner is not None and key == "direct-recruiter"
        persistence_role = role if direct_related_member else related_owner or role
        candidate, application = _candidate_application(
            db,
            organization=organization,
            role=persistence_role,
            prefix=prefix,
            key=key,
            local_stage=(
                "review" if related_owner is not None else local_stages[key]
            ),
            ats_stage=(None if direct_related_member else ats_stages[key]),
            score=score,
            # A live related-role membership survives soft deletion of the
            # evidence/ATS row. The same lifecycle marker would remove an
            # ordinary-role application, so only the related oracle uses it.
            deleted_at=(
                NOW
                if related_owner is not None and key == "pending-only"
                else None
            ),
        )
        score -= 5.0
        applications[key] = int(application.id)
        candidates[key] = int(candidate.id)
        names[key] = str(candidate.full_name)
        if related_owner is not None:
            ats_application_id = int(application.id)
            if direct_related_member:
                ats_transport = CandidateApplication(
                    organization_id=int(organization.id),
                    candidate_id=int(candidate.id),
                    role_id=int(related_owner.id),
                    source="workable",
                    status="applied",
                    pipeline_stage="review",
                    pipeline_stage_updated_at=NOW - timedelta(hours=6),
                    pipeline_stage_source="recruiter",
                    application_outcome="open",
                    application_outcome_updated_at=NOW - timedelta(hours=6),
                    workable_candidate_id=f"wk-{prefix}-{key}",
                    workable_stage=ats_stages[key],
                    external_stage_raw=ats_stages[key],
                    external_stage_normalized="advanced",
                )
                db.add(ats_transport)
                db.flush()
                ats_application_id = int(ats_transport.id)
            db.add(
                SisterRoleEvaluation(
                    organization_id=int(organization.id),
                    role_id=int(role.id),
                    candidate_id=int(candidate.id),
                    source_application_id=int(application.id),
                    ats_application_id=ats_application_id,
                    status=SISTER_EVAL_DONE,
                    pipeline_stage=local_stages[key],
                    pipeline_stage_updated_at=NOW - timedelta(hours=6),
                    pipeline_stage_source="recruiter",
                    application_outcome="open",
                    application_outcome_updated_at=NOW - timedelta(hours=6),
                    application_outcome_source="recruiter",
                    membership_source=(
                        "direct_application"
                        if direct_related_member
                        else "initial_snapshot"
                    ),
                    spec_fingerprint=f"spec-{role.id}",
                    cv_fingerprint=f"cv-{candidate.id}",
                    role_fit_score=score + 5.0,
                    summary=f"Related-role evidence for {key}",
                    details={"fixture": key},
                    model_version="offline-fixture",
                    prompt_version="offline-fixture",
                    scored_at=NOW - timedelta(hours=8),
                    created_at=NOW - timedelta(days=30),
                )
            )
            event_applications[key] = ats_application_id
        else:
            event_applications[key] = int(application.id)
    if related_owner is not None:
        # The direct member previously used its ATS-owner row as role evidence.
        # That deleted lifecycle and the live direct lifecycle intentionally
        # share one transport id. Any last-row-wins mapping will expose the old
        # physical id instead of the current role-owned membership.
        direct_key = "direct-recruiter"
        db.add(
            SisterRoleEvaluation(
                organization_id=int(organization.id),
                role_id=int(role.id),
                candidate_id=candidates[direct_key],
                source_application_id=event_applications[direct_key],
                ats_application_id=event_applications[direct_key],
                status=SISTER_EVAL_DONE,
                pipeline_stage="review",
                pipeline_stage_source="recruiter",
                application_outcome="open",
                application_outcome_source="recruiter",
                membership_source="legacy_compat_shadow",
                spec_fingerprint=f"prior-spec-{role.id}",
                created_at=NOW - timedelta(days=60),
                deleted_at=NOW - timedelta(days=31),
            )
        )
    db.flush()

    decision_specs = {
        "current-and-moved": (
            "approved",
            NOW - timedelta(days=4),
            NOW - timedelta(days=2),
        ),
        "moved-on": (
            "approved",
            NOW - timedelta(days=6),
            NOW - timedelta(days=5),
        ),
        "pending-only": ("pending", NOW - timedelta(days=3), None),
        "failed-attempt": ("pending", NOW - timedelta(days=1), None),
        # Created outside the window, resolved inside it.  This catches readers
        # that incorrectly use created_at for "decisions made last week".
        "resolved-no-effect": (
            "approved",
            NOW - timedelta(days=20),
            NOW - timedelta(days=1),
        ),
    }
    for key, (status, created_at, resolved_at) in decision_specs.items():
        decision = _add_decision(
            db,
            organization_id=int(organization.id),
            role_id=int(role.id),
            application_id=applications[key],
            key=key,
            status=status,
            created_at=created_at,
            resolved_at=resolved_at,
            resolved_by_user_id=(int(user.id) if resolved_at else None),
        )
        decisions[key] = int(decision.id)

    current_event = _add_event(
        db,
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=applications["current-and-moved"],
        key="current-and-moved",
        occurred_at=NOW - timedelta(days=2),
        target_stage="Technical Interview",
        effect_status="confirmed",
        actor_type="agent",
        decision_id=decisions["current-and-moved"],
    )
    moved_on_technical_event = _add_event(
        db,
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=applications["moved-on"],
        key="moved-on-technical",
        occurred_at=NOW - timedelta(days=5),
        target_stage="Technical Interview",
        effect_status="confirmed",
        actor_type="agent",
        decision_id=decisions["moved-on"],
    )
    _add_event(
        db,
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=applications["moved-on"],
        key="moved-on-final",
        occurred_at=NOW - timedelta(days=1),
        target_stage="Final Interview",
        effect_status="confirmed",
        actor_type="recruiter",
        decision_id=None,
    )
    _add_event(
        db,
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=applications["before-window"],
        key="before-window",
        occurred_at=NOW - timedelta(days=8),
        target_stage="Technical Interview",
        effect_status="confirmed",
        actor_type="agent",
        decision_id=None,
    )
    failed_event = _add_event(
        db,
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=applications["failed-attempt"],
        key="failed-attempt",
        occurred_at=NOW - timedelta(days=1),
        target_stage="Technical Interview",
        effect_status="failed",
        actor_type="agent",
        decision_id=decisions["failed-attempt"],
    )
    direct_recruiter_event = _add_event(
        db,
        organization_id=int(organization.id),
        role_id=int(role.id),
        # Related roles may record the immutable provider movement on a
        # separate ATS transport row. All role-bound surfaces must still expose
        # it through the independent role member's canonical application id.
        application_id=event_applications["direct-recruiter"],
        key="direct-recruiter",
        occurred_at=NOW - timedelta(days=4),
        target_stage="Technical Interview",
        effect_status="confirmed",
        actor_type="recruiter",
        decision_id=None,
    )
    if related_owner is not None:
        # Pre-provenance events are immutable and therefore retain a NULL
        # role_id after the expand migration.  Every tool surface must recover
        # the historical logical role from the event metadata plus explicit
        # related-role membership; the physical ATS-owner application must
        # never win merely because it stores the transport record.
        direct_recruiter_event.role_id = None
        direct_recruiter_event.event_metadata = {
            "fixture": "direct-recruiter",
            "acting_role_id": int(role.id),
        }

    return RoleTruth(
        role=role,
        applications=applications,
        candidates=candidates,
        names=names,
        decisions=decisions,
        confirmed_technical_event_ids=(
            int(current_event.id),
            int(direct_recruiter_event.id),
            int(moved_on_technical_event.id),
        ),
        failed_technical_event_id=int(failed_event.id),
        expected_local_stages=local_stages,
    )


def _seed_distractors(
    db: Any,
    *,
    organization: Organization,
    other_organization: Organization,
    unrelated_role: Role,
    related_role: Role,
    related_owner: Role,
) -> None:
    _candidate_application(
        db,
        organization=organization,
        role=unrelated_role,
        prefix="distractor",
        key="other-role",
        local_stage="advanced",
        ats_stage="Technical Interview",
        score=100,
    )
    other_org_role = Role(
        organization_id=int(other_organization.id),
        name="Other tenant role",
        source="manual",
    )
    db.add(other_org_role)
    db.flush()
    _candidate_application(
        db,
        organization=other_organization,
        role=other_org_role,
        prefix="distractor",
        key="other-tenant",
        local_stage="advanced",
        ats_stage="Technical Interview",
        score=100,
    )
    _candidate_application(
        db,
        organization=organization,
        role=related_owner,
        prefix="distractor",
        key="owner-only-no-membership",
        local_stage="advanced",
        ats_stage="Technical Interview",
        score=100,
    )
    deleted_candidate, deleted_application = _candidate_application(
        db,
        organization=organization,
        role=related_owner,
        prefix="distractor",
        key="deleted-membership",
        local_stage="advanced",
        ats_stage="Technical Interview",
        score=100,
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(related_role.id),
            candidate_id=int(deleted_candidate.id),
            source_application_id=int(deleted_application.id),
            ats_application_id=int(deleted_application.id),
            status=SISTER_EVAL_DONE,
            pipeline_stage="advanced",
            pipeline_stage_updated_at=NOW,
            pipeline_stage_source="recruiter",
            application_outcome="open",
            application_outcome_updated_at=NOW,
            application_outcome_source="recruiter",
            membership_source="initial_snapshot",
            spec_fingerprint="deleted-membership",
            role_fit_score=100,
            deleted_at=NOW,
        )
    )


@pytest.fixture
def candidate_capability_world(postgres_search_db: Any) -> CapabilityWorld:
    stamp = str(id(postgres_search_db))
    organization = Organization(
        name=f"Candidate capability truth {stamp}",
        slug=f"candidate-capability-truth-{stamp}",
    )
    other_organization = Organization(
        name=f"Other capability truth {stamp}",
        slug=f"other-capability-truth-{stamp}",
    )
    postgres_search_db.add_all([organization, other_organization])
    postgres_search_db.flush()
    user = User(
        email=f"capability-{stamp}@example.test",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="Capability Truth Tester",
        organization_id=int(organization.id),
        role="owner",
    )
    standard_role = Role(
        organization_id=int(organization.id),
        name="Standard AI Engineer",
        source="manual",
    )
    related_owner = Role(
        organization_id=int(organization.id),
        name="ATS owner evidence role",
        source="workable",
        workable_job_id=f"owner-{stamp}",
    )
    related_role = Role(
        organization_id=int(organization.id),
        name="Independent related AI Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=related_owner,
    )
    unrelated_role = Role(
        organization_id=int(organization.id),
        name="Unrelated role",
        source="manual",
    )
    postgres_search_db.add_all(
        [user, standard_role, related_owner, related_role, unrelated_role]
    )
    postgres_search_db.flush()
    standard = _seed_role_truth(
        postgres_search_db,
        organization=organization,
        user=user,
        role=standard_role,
        prefix="standard",
    )
    related = _seed_role_truth(
        postgres_search_db,
        organization=organization,
        user=user,
        role=related_role,
        prefix="related",
        related_owner=related_owner,
    )
    _seed_distractors(
        postgres_search_db,
        organization=organization,
        other_organization=other_organization,
        unrelated_role=unrelated_role,
        related_role=related_role,
        related_owner=related_owner,
    )
    postgres_search_db.flush()
    return CapabilityWorld(
        user=user,
        standard=standard,
        related=related,
        unrelated_role=unrelated_role,
    )


def _parse_sse_payload(text: str) -> dict[str, Any]:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError(f"MCP response did not contain an SSE data line: {text!r}")


def _mcp_tool_call(
    client: TestClient,
    *,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    response = client.post(
        "/mcp/",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Bearer offline-constructed-truth",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    rpc = _parse_sse_payload(response.text)
    result = rpc.get("result")
    assert isinstance(result, dict), rpc
    assert result.get("isError") is not True, result
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured.get("result", structured)
    [content] = result.get("content") or []
    return json.loads(content["text"])


def _surface_invokers(
    *,
    db: Any,
    user: User,
    role: Role,
    client: TestClient,
) -> dict[str, Callable[[str, dict[str, Any]], Any]]:
    role_id = int(role.id)

    def public_mcp(name: str, arguments: dict[str, Any]) -> Any:
        return _mcp_tool_call(
            client,
            name=name,
            arguments={**arguments, "role_id": role_id},
        )

    def taali_chat(name: str, arguments: dict[str, Any]) -> Any:
        return taali_tools.dispatch_tool(
            name,
            {**arguments, "role_id": role_id},
            db=db,
            user=user,
        )

    def agent_chat(name: str, arguments: dict[str, Any]) -> Any:
        return agent_chat_tools.dispatch_tool(
            name,
            arguments,
            db=db,
            role=role,
            user=user,
        )

    def autonomous(name: str, arguments: dict[str, Any]) -> Any:
        return autonomous_tools.dispatch(
            name,
            arguments,
            db=db,
            agent_run=SimpleNamespace(decisions_emitted=0),
            role=role,
        )

    return {
        "public_mcp": public_mcp,
        "taali_chat": taali_chat,
        "agent_chat": agent_chat,
        "autonomous_agent": autonomous,
    }


def _keys(rows: list[dict[str, Any]], truth: RoleTruth) -> list[str]:
    return [truth.key_by_name[str(row["candidate_name"])] for row in rows]


def _assert_exact_envelope(payload: dict[str, Any], *, total: int) -> None:
    assert payload["total"] == total
    assert payload["total_is_exact"] is True
    assert payload["offset"] == 0
    assert payload["generated_at"]
    assert isinstance(payload["filters"], dict)


def _assert_no_storage_role_leak(
    payload: Any,
    *,
    logical_role_id: int,
    independent_role: bool = False,
) -> None:
    if isinstance(payload, dict):
        assert "operational_role_id" not in payload
        assert "ats_owner_role_id" not in payload
        assert "source_role_id" not in payload
        if independent_role:
            assert OWNER_ROLE_JUDGMENT_FIELDS.isdisjoint(payload)
        if "role_id" in payload:
            assert int(payload["role_id"]) == logical_role_id
        for value in payload.values():
            _assert_no_storage_role_leak(
                value,
                logical_role_id=logical_role_id,
                independent_role=independent_role,
            )
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_storage_role_leak(
                value,
                logical_role_id=logical_role_id,
                independent_role=independent_role,
            )


def _assert_tool_surface_truth(
    invoke: Callable[[str, dict[str, Any]], Any],
    *,
    truth: RoleTruth,
) -> None:
    role_id = int(truth.role.id)
    independent_role = truth.role.role_kind == ROLE_KIND_SISTER
    roster = invoke(
        "search_role_candidates",
        {"application_outcome": "open", "limit": 100, "offset": 0},
    )
    _assert_exact_envelope(roster, total=len(SEMANTIC_KEYS))
    assert set(_keys(roster["items"], truth)) == set(SEMANTIC_KEYS)
    assert int(roster["role"]["id"]) == role_id
    _assert_no_storage_role_leak(
        roster,
        logical_role_id=role_id,
        independent_role=independent_role,
    )

    current = invoke(
        "search_role_candidates",
        {
            "application_outcome": "open",
            "ats_stage": "Technical Interview",
            "limit": 100,
            "offset": 0,
        },
    )
    _assert_exact_envelope(current, total=3)
    assert set(_keys(current["items"], truth)) == CURRENT_TECHNICAL_INTERVIEW

    moved_on_detail = invoke(
        "get_role_candidate",
        {"application_id": truth.applications["moved-on"]},
    )
    assert moved_on_detail["candidate_name"] == truth.names["moved-on"]
    assert moved_on_detail["current_state"]["pipeline_stage"] == (
        truth.expected_local_stages["moved-on"]
    )
    assert moved_on_detail["current_state"]["ats"]["raw_stage"] == "Final Interview"
    if truth.role.role_kind == ROLE_KIND_SISTER:
        restrictions = moved_on_detail["current_state"]["restrictions"]
        assert restrictions["restricted"] is True
        assert restrictions["can_advance_in_ats"] is False
    _assert_no_storage_role_leak(
        moved_on_detail,
        logical_role_id=role_id,
        independent_role=independent_role,
    )
    if independent_role:
        direct_transport_detail = invoke(
            "get_role_candidate",
            {"application_id": truth.applications["direct-recruiter"]},
        )
        assert direct_transport_detail["workable_stage"] == "Technical Interview"
        assert direct_transport_detail["external_stage_raw"] == (
            "Technical Interview"
        )
        assert direct_transport_detail["external_stage_normalized"] == "advanced"
        assert direct_transport_detail["current_state"]["ats"]["raw_stage"] == (
            "Technical Interview"
        )
        soft_deleted_source_detail = invoke(
            "get_role_candidate",
            {"application_id": truth.applications["pending-only"]},
        )
        assert soft_deleted_source_detail["candidate_name"] == truth.names[
            "pending-only"
        ]
        assert soft_deleted_source_detail["current_state"]["restrictions"][
            "codes"
        ] == ["ats_application_deleted"]

    actions = invoke(
        "list_candidate_actions",
        {
            "action": "advanced",
            "target_stage": "Technical Interview",
            "status": "confirmed",
            "occurred_after": WINDOW_START.isoformat(),
            "occurred_before": NOW.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    _assert_exact_envelope(actions, total=3)
    assert _keys(actions["items"], truth) == CONFIRMED_TECHNICAL_INTERVIEW_ACTIONS
    assert all(item["action"] == "advanced" for item in actions["items"])
    assert all(item["status"] == "confirmed" for item in actions["items"])
    assert all(
        item["target_stage"] == "Technical Interview"
        for item in actions["items"]
    )
    assert all(item["occurred_at"] for item in actions["items"])
    assert truth.names["failed-attempt"] not in {
        item["candidate_name"] for item in actions["items"]
    }
    assert truth.names["before-window"] not in {
        item["candidate_name"] for item in actions["items"]
    }
    assert truth.names["direct-recruiter"] in {
        item["candidate_name"] for item in actions["items"]
    }
    direct_recruiter_action = next(
        item
        for item in actions["items"]
        if item["candidate_name"] == truth.names["direct-recruiter"]
    )
    assert direct_recruiter_action["application_id"] == truth.applications[
        "direct-recruiter"
    ]
    assert direct_recruiter_action["in_current_role_pool"] is True
    direct_recruiter_only = invoke(
        "list_candidate_actions",
        {
            "application_id": truth.applications["direct-recruiter"],
            "action": "advanced",
            "target_stage": "Technical Interview",
            "status": "confirmed",
            "occurred_after": WINDOW_START.isoformat(),
            "occurred_before": NOW.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    _assert_exact_envelope(direct_recruiter_only, total=1)
    [filtered_action] = direct_recruiter_only["items"]
    assert filtered_action["event_id"] == direct_recruiter_action["event_id"]
    assert filtered_action["application_id"] == truth.applications[
        "direct-recruiter"
    ]
    _assert_no_storage_role_leak(
        actions,
        logical_role_id=role_id,
        independent_role=independent_role,
    )

    pending = invoke(
        "list_recent_agent_decisions",
        {
            "status": "pending",
            "created_after": WINDOW_START.isoformat(),
            "created_before": NOW.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    _assert_exact_envelope(pending, total=2)
    assert _keys(pending["items"], truth) == PENDING_DECISIONS

    resolved = invoke(
        "list_recent_agent_decisions",
        {
            "resolved_after": WINDOW_START.isoformat(),
            "resolved_before": NOW.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    _assert_exact_envelope(resolved, total=3)
    assert _keys(resolved["items"], truth) == RESOLVED_DECISIONS
    assert truth.names["resolved-no-effect"] in {
        item["candidate_name"] for item in resolved["items"]
    }
    assert truth.names["direct-recruiter"] not in {
        item["candidate_name"] for item in resolved["items"]
    }
    _assert_no_storage_role_leak(
        pending,
        logical_role_id=role_id,
        independent_role=independent_role,
    )
    _assert_no_storage_role_leak(
        resolved,
        logical_role_id=role_id,
        independent_role=independent_role,
    )


def _assert_rest_truth(
    client: TestClient,
    *,
    truth: RoleTruth,
) -> None:
    role_id = int(truth.role.id)
    roster_response = client.get(
        f"/api/v1/roles/{role_id}/applications",
        params={"application_outcome": "open", "limit": 100},
    )
    assert roster_response.status_code == 200, roster_response.text
    roster = roster_response.json()
    assert {truth.key_by_name[row["candidate_name"]] for row in roster} == set(
        SEMANTIC_KEYS
    )
    assert all(int(row["role_id"]) == role_id for row in roster)

    global_response = client.get(
        "/api/v1/applications",
        params={
            "role_ids": str(role_id),
            "application_outcome": "open",
            "limit": 100,
        },
    )
    assert global_response.status_code == 200, global_response.text
    global_payload = global_response.json()
    assert int(global_payload["total"]) == len(SEMANTIC_KEYS)
    assert {
        truth.key_by_name[row["candidate_name"]]
        for row in global_payload["items"]
    } == set(SEMANTIC_KEYS)
    assert all(
        int(row["role_id"]) == role_id
        and int(row["logical_role_id"]) == role_id
        and row["pipeline_stage"]
        == truth.expected_local_stages[truth.key_by_name[row["candidate_name"]]]
        for row in global_payload["items"]
    )

    pipeline_response = client.get(
        f"/api/v1/roles/{role_id}/pipeline",
        params={"limit": 100},
    )
    assert pipeline_response.status_code == 200, pipeline_response.text
    pipeline = pipeline_response.json()
    assert int(pipeline["role_id"]) == role_id
    assert int(pipeline["total"]) == len(SEMANTIC_KEYS)
    assert {
        truth.key_by_name[row["candidate_name"]]
        for row in pipeline["items"]
    } == set(SEMANTIC_KEYS)
    expected_advanced = sum(
        stage == "advanced" for stage in truth.expected_local_stages.values()
    )
    expected_review = sum(
        stage == "review" for stage in truth.expected_local_stages.values()
    )
    assert pipeline["stage_counts"]["review"] == expected_review
    # The compact stage-count bar deliberately omits the terminal hand-off
    # bucket; the canonical items above still carry every advanced membership.
    assert "advanced" not in pipeline["stage_counts"]
    assert pipeline["stage_counts"]["all"] == expected_review

    for key in ("moved-on", "pending-only", "resolved-no-effect"):
        detail_response = client.get(
            f"/api/v1/applications/{truth.applications[key]}",
            params={"view_role_id": role_id},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["candidate_name"] == truth.names[key]
        assert detail["pipeline_stage"] == truth.expected_local_stages[key]
        assert int(detail["role_id"]) == role_id

    # REST history is role-local even when several roles use the same evidence
    # application. It also exposes the same first-class destination/effect
    # provenance used by canonical action-history tools.
    returned_events: dict[int, dict[str, Any]] = {}
    for application_id in truth.applications.values():
        response = client.get(
            f"/api/v1/applications/{application_id}/events",
            params={"role_id": role_id},
        )
        assert response.status_code == 200, response.text
        for event in response.json():
            assert int(event["role_id"]) == role_id
            returned_events[int(event["id"])] = event
    assert set(truth.confirmed_technical_event_ids) <= set(returned_events)
    assert truth.failed_technical_event_id in returned_events
    for event_id in truth.confirmed_technical_event_ids:
        event = returned_events[int(event_id)]
        assert event["target_stage"] == "Technical Interview"
        assert event["effect_status"] == "confirmed"
    assert (
        returned_events[int(truth.failed_technical_event_id)]["effect_status"]
        == "failed"
    )

    decisions_response = client.get(
        "/api/v1/agent-decisions",
        params={"role_id": role_id, "status": "all", "limit": 100},
    )
    assert decisions_response.status_code == 200, decisions_response.text
    assert {int(item["id"]) for item in decisions_response.json()} == set(
        truth.decisions.values()
    )

    reporting_response = client.get(
        "/api/v1/analytics/reporting-summary",
        params={
            "role_id": role_id,
            "date_from": (NOW - timedelta(days=30)).isoformat(),
            "date_to": NOW.isoformat(),
        },
    )
    assert reporting_response.status_code == 200, reporting_response.text
    reporting = reporting_response.json()
    assert int(reporting["kpis"]["decisions_made"]["current"]) == len(
        truth.decisions
    )
    assert {int(item["id"]) for item in reporting["decisions_feed"]} == set(
        truth.decisions.values()
    )
    funnel = {item["key"]: int(item["count"]) for item in reporting["funnel"]}
    assert sum(funnel.values()) == len(SEMANTIC_KEYS)
    assert funnel["advanced"] == expected_advanced
    assert funnel["completed"] == expected_review

    breakdown_response = client.get(
        "/api/v1/analytics/decisions-breakdown",
        params={"role_id": role_id},
    )
    assert breakdown_response.status_code == 200, breakdown_response.text
    breakdown = breakdown_response.json()
    [role_breakdown] = breakdown["roles"]
    assert int(role_breakdown["role_id"]) == role_id
    assert int(role_breakdown["decisions"]["total"]) == len(truth.decisions)
    assert int(role_breakdown["score_stats"]["count"]) == len(SEMANTIC_KEYS)
    expected_current_stages = (
        {"advanced": 3, "review": 4}
        if truth.role.role_kind == ROLE_KIND_SISTER
        else {"advanced": 4, "applied": 3}
    )
    assert role_breakdown["workable_stages"] == expected_current_stages


def _assert_role_truth_across_surfaces(
    *,
    db: Any,
    world: CapabilityWorld,
    truth: RoleTruth,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = Principal(
        organization_id=int(world.user.organization_id),
        auth_kind="jwt",
        scopes=frozenset(
            {
                SCOPE_ROLES_READ,
                SCOPE_APPLICATIONS_READ,
                SCOPE_ASSESSMENTS_READ,
            }
        ),
        user=world.user,
    )
    monkeypatch.setattr(mcp_server, "SessionLocal", lambda: _BorrowedSession(db))
    monkeypatch.setattr(
        mcp_server,
        "authenticate_request",
        lambda _request, _db: principal,
    )

    def override_db():
        yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: world.user
    app.dependency_overrides[get_applications_search_principal] = lambda: world.user
    try:
        with TestClient(app) as client:
            _assert_rest_truth(client, truth=truth)
            for invoke in _surface_invokers(
                db=db,
                user=world.user,
                role=truth.role,
                client=client,
            ).values():
                _assert_tool_surface_truth(invoke, truth=truth)

            # A role-bound surface owns role scope. A model-supplied role id
            # fails closed instead of widening or silently changing the query.
            with pytest.raises(ValueError, match="role_id is bound"):
                autonomous_tools.dispatch(
                    "search_role_candidates",
                    {
                        "role_id": int(world.unrelated_role.id),
                        "application_outcome": "open",
                        "limit": 100,
                    },
                    db=db,
                    agent_run=SimpleNamespace(decisions_emitted=0),
                    role=truth.role,
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_standard_role_candidate_capabilities_match_constructed_truth_across_all_surfaces(
    postgres_search_db: Any,
    candidate_capability_world: CapabilityWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_role_truth_across_surfaces(
        db=postgres_search_db,
        world=candidate_capability_world,
        truth=candidate_capability_world.standard,
        monkeypatch=monkeypatch,
    )


def test_related_role_candidate_capabilities_match_constructed_truth_across_all_surfaces(
    postgres_search_db: Any,
    candidate_capability_world: CapabilityWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_role_truth_across_surfaces(
        db=postgres_search_db,
        world=candidate_capability_world,
        truth=candidate_capability_world.related,
        monkeypatch=monkeypatch,
    )


def test_removed_related_membership_leaves_pool_but_keeps_role_history_on_every_surface(
    postgres_search_db: Any,
    candidate_capability_world: CapabilityWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current membership and immutable role history have different lifecycles."""

    db = postgres_search_db
    world = candidate_capability_world
    truth = world.related
    key = "direct-recruiter"
    membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(world.user.organization_id),
            SisterRoleEvaluation.role_id == int(truth.role.id),
            SisterRoleEvaluation.candidate_id == truth.candidates[key],
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .one()
    )
    membership.deleted_at = NOW
    db.flush()

    principal = Principal(
        organization_id=int(world.user.organization_id),
        auth_kind="jwt",
        scopes=frozenset(
            {SCOPE_ROLES_READ, SCOPE_APPLICATIONS_READ, SCOPE_ASSESSMENTS_READ}
        ),
        user=world.user,
    )
    monkeypatch.setattr(mcp_server, "SessionLocal", lambda: _BorrowedSession(db))
    monkeypatch.setattr(
        mcp_server,
        "authenticate_request",
        lambda _request, _db: principal,
    )

    def override_db():
        yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: world.user
    app.dependency_overrides[get_applications_search_principal] = lambda: world.user
    try:
        with TestClient(app) as client:
            for invoke in _surface_invokers(
                db=db,
                user=world.user,
                role=truth.role,
                client=client,
            ).values():
                roster = invoke(
                    "search_role_candidates",
                    {"application_outcome": "open", "limit": 100, "offset": 0},
                )
                assert truth.names[key] not in {
                    item["candidate_name"] for item in roster["items"]
                }
                actions = invoke(
                    "list_candidate_actions",
                    {
                        "action": "advanced",
                        "target_stage": "Technical Interview",
                        "status": "confirmed",
                        "occurred_after": WINDOW_START.isoformat(),
                        "occurred_before": NOW.isoformat(),
                        "limit": 100,
                        "offset": 0,
                    },
                )
                historical = next(
                    item
                    for item in actions["items"]
                    if item["candidate_name"] == truth.names[key]
                )
                assert historical["in_current_role_pool"] is False
                assert historical["role_id"] == int(truth.role.id)

            response = client.get(
                f"/api/v1/applications/{truth.applications[key]}/events",
                params={"role_id": int(truth.role.id)},
            )
            assert response.status_code == 200, response.text
            event_ids = {int(event["id"]) for event in response.json()}
            assert int(truth.confirmed_technical_event_ids[1]) in event_ids
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
