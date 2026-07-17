"""Primitive-only provider plans for exact ATS outcome writes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .ats_stage_move_provider import (
    StageMoveProviderFailure,
    StageMoveProviderPlan,
    perform_stage_move_provider_call,
)
from .workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)


@dataclass(frozen=True)
class WorkableOutcomeProviderPlan:
    provider_target_id: str = field(repr=False)
    target_outcome: str
    organization_id: int
    workable_connected: bool
    workable_subdomain: str | None = field(repr=False)
    workable_config: dict[str, Any] = field(repr=False)
    role_name: str | None = field(repr=False)
    workable_actor_member_id: str | None = field(repr=False)
    workable_job_data: dict[str, Any] | None = field(repr=False)
    candidate_name: str | None = field(repr=False)
    candidate_email: str | None = field(repr=False)
    pre_screen_score_100: float | None = field(repr=False)
    pre_screen_recommendation: str | None = field(repr=False)
    reason: str | None = field(default=None, repr=False)
    note_template: str | None = field(default=None, repr=False)
    threshold_100: float | int | None = field(default=None, repr=False)
    workable_access_token: str | None = field(default=None, repr=False)


OutcomeProviderPlan = WorkableOutcomeProviderPlan | StageMoveProviderPlan


class OutcomeProviderFailure(WorkableWritebackError):
    """Provider failure carrying exact side-effect certainty."""

    def __init__(
        self,
        *,
        action: str,
        code: str,
        message: str,
        provider_called: bool | None,
        retriable: bool = False,
    ) -> None:
        super().__init__(
            action=action,
            code=code,
            message=message,
            retriable=bool(retriable and provider_called is False),
        )
        self.provider_called = provider_called


def workable_outcome_provider_plan(
    *,
    org: Organization,
    app: CandidateApplication,
    role: Role | None,
    candidate: Candidate | None = None,
    target_outcome: str,
    reason: str | None = None,
    note_template: str | None = None,
    threshold_100: float | int | None = None,
) -> WorkableOutcomeProviderPlan:
    candidate = candidate or getattr(app, "candidate", None)
    return WorkableOutcomeProviderPlan(
        provider_target_id=str(app.workable_candidate_id or "").strip(),
        target_outcome=str(target_outcome or "").strip().lower(),
        organization_id=int(org.id),
        workable_connected=bool(org.workable_connected),
        workable_access_token=org.workable_access_token,
        workable_subdomain=org.workable_subdomain,
        workable_config=deepcopy(org.workable_config or {}),
        role_name=getattr(role, "name", None),
        workable_actor_member_id=getattr(role, "workable_actor_member_id", None),
        workable_job_data=deepcopy(getattr(role, "workable_job_data", None)),
        candidate_name=getattr(candidate, "full_name", None),
        candidate_email=getattr(candidate, "email", None),
        pre_screen_score_100=app.pre_screen_score_100,
        pre_screen_recommendation=app.pre_screen_recommendation,
        reason=reason,
        note_template=note_template,
        threshold_100=threshold_100,
    )


def bullhorn_outcome_provider_plan(
    db,
    *,
    org: Organization,
    app: CandidateApplication,
    target_outcome: str,
) -> StageMoveProviderPlan:
    from ..components.integrations.bullhorn.write_back import resolve_remote_status

    target_stage = "advanced" if str(target_outcome).lower() == "open" else "rejected"
    return StageMoveProviderPlan(
        provider="bullhorn",
        provider_target_id=str(app.bullhorn_job_submission_id or "").strip(),
        target_stage=target_stage,
        provider_remote_stage=resolve_remote_status(db, org, taali_intent=target_stage),
        organization_id=int(org.id),
        bullhorn_username=org.bullhorn_username,
        bullhorn_client_id=org.bullhorn_client_id,
        bullhorn_client_secret=org.bullhorn_client_secret,
        bullhorn_refresh_token=org.bullhorn_refresh_token,
        bullhorn_rest_url=org.bullhorn_rest_url,
        bullhorn_credential_generation=int(org.bullhorn_credential_generation or 0),
    )


def prepare_manual_outcome_provider_plan(
    db, *, organization_id: int, payload: dict
) -> tuple[OutcomeProviderPlan, int]:
    """Recheck the exact durable claim and copy its provider inputs."""

    from ..platform.config import settings
    from .ats_writeback_state import OUTCOME_WRITEBACK_KEY
    from .manual_outcome_identity import (
        manual_outcome_provider_snapshot,
        validate_manual_outcome_payload,
    )
    from .manual_outcome_lifecycle import manual_outcome_matches_application

    expected_version, target_outcome, expected_local, operation_id = (
        validate_manual_outcome_payload(payload)
    )
    provider, provider_target_id = manual_outcome_provider_snapshot(payload)
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(payload["application_id"]),
            CandidateApplication.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == int(app.role_id) if app is not None else False,
            Role.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    state = app.integration_sync_state if app is not None else None
    receipt = state.get(OUTCOME_WRITEBACK_KEY) if isinstance(state, dict) else None
    try:
        exact_receipt = bool(
            isinstance(receipt, dict)
            and str(receipt.get("status") or "") == "provider_call_started"
            and str(receipt.get("operation_id") or "") == operation_id
            and str(receipt.get("provider") or "").lower() == provider
            and str(receipt.get("provider_target_id") or "") == provider_target_id
            and int(receipt.get("expected_application_version") or 0)
            == expected_version
            and str(receipt.get("expected_local_outcome") or "").lower()
            == expected_local
            and str(receipt.get("target_outcome") or "").lower() == target_outcome
        )
    except (TypeError, ValueError):
        exact_receipt = False
    if (
        app is None
        or org is None
        or role is None
        or not exact_receipt
        or not manual_outcome_matches_application(app, payload)
    ):
        raise OutcomeProviderFailure(
            action="manual_outcome",
            code="lifecycle_changed",
            message="The exact application or ATS target changed before delivery",
            provider_called=False,
            retriable=False,
        )
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(app.candidate_id),
            Candidate.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    if candidate is None:
        raise OutcomeProviderFailure(
            action="manual_outcome",
            code="lifecycle_changed",
            message="The exact candidate changed before ATS delivery",
            provider_called=False,
            retriable=False,
        )
    if provider == "workable":
        return (
            workable_outcome_provider_plan(
                org=org,
                app=app,
                role=role,
                candidate=candidate,
                target_outcome=target_outcome,
                reason=payload.get("reason"),
            ),
            int(app.id),
        )
    if not (
        settings.BULLHORN_ENABLED
        and org.bullhorn_connected
        and org.bullhorn_username
        and org.bullhorn_client_id
        and org.bullhorn_client_secret
        and org.bullhorn_refresh_token
    ):
        raise OutcomeProviderFailure(
            action="manual_outcome",
            code="not_configured",
            message="Bullhorn is disabled or disconnected for this application",
            provider_called=False,
            retriable=False,
        )
    return (
        bullhorn_outcome_provider_plan(
            db,
            org=org,
            app=app,
            target_outcome=target_outcome,
        ),
        int(app.id),
    )


def _perform_workable(plan: WorkableOutcomeProviderPlan) -> dict[str, Any]:
    from .workable_actions_service import (
        disqualify_candidate_in_workable,
        revert_candidate_disqualification_in_workable,
    )

    org = SimpleNamespace(
        id=plan.organization_id,
        workable_connected=plan.workable_connected,
        workable_access_token=plan.workable_access_token,
        workable_subdomain=plan.workable_subdomain,
        workable_config=deepcopy(plan.workable_config),
    )
    role = SimpleNamespace(
        name=plan.role_name,
        workable_actor_member_id=plan.workable_actor_member_id,
        workable_job_data=deepcopy(plan.workable_job_data),
    )
    app = SimpleNamespace(
        workable_candidate_id=plan.provider_target_id,
        candidate=SimpleNamespace(
            full_name=plan.candidate_name,
            email=plan.candidate_email,
        ),
        pre_screen_score_100=plan.pre_screen_score_100,
        pre_screen_recommendation=plan.pre_screen_recommendation,
    )
    with strict_workable_writes():
        if plan.target_outcome == "open":
            return revert_candidate_disqualification_in_workable(
                org=org, app=app, role=role
            )
        return disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=role,
            reason=plan.reason,
            note_template=plan.note_template,
            threshold_100=plan.threshold_100,
            withdrew=False,
        )


def perform_outcome_provider_call(plan: OutcomeProviderPlan) -> dict[str, Any]:
    """Perform one outcome write without a DB session or ORM object."""

    try:
        if isinstance(plan, WorkableOutcomeProviderPlan):
            return _perform_workable(plan)
        return perform_stage_move_provider_call(plan)
    except WorkableWritebackError as exc:
        provider_called = False if exc.code != "api_error" else None
        raise OutcomeProviderFailure(
            action=exc.action,
            code=exc.code,
            message=exc.message,
            provider_called=provider_called,
            retriable=exc.retriable,
        ) from None
    except StageMoveProviderFailure as exc:
        raise OutcomeProviderFailure(
            action="manual_outcome",
            code=exc.code,
            message=exc.message,
            provider_called=exc.provider_called,
            retriable=exc.retriable,
        ) from None
    except Exception:
        raise OutcomeProviderFailure(
            action="manual_outcome",
            code="api_error",
            message="ATS outcome delivery did not confirm; verify it before retrying",
            provider_called=None,
            retriable=False,
        ) from None


def stamp_bullhorn_outcome_success(
    app: CandidateApplication, plan: OutcomeProviderPlan, result: dict[str, Any]
) -> None:
    if not isinstance(plan, StageMoveProviderPlan):
        return
    remote_status = str(result.get("provider_remote_stage") or "").strip()
    app.bullhorn_status = remote_status or None
    app.external_stage_raw = remote_status or None
    app.external_stage_normalized = plan.target_stage
    app.bullhorn_status_local_write_at = datetime.now(timezone.utc)


__all__ = [
    "OutcomeProviderPlan",
    "OutcomeProviderFailure",
    "WorkableOutcomeProviderPlan",
    "bullhorn_outcome_provider_plan",
    "perform_outcome_provider_call",
    "prepare_manual_outcome_provider_plan",
    "stamp_bullhorn_outcome_success",
    "workable_outcome_provider_plan",
]
