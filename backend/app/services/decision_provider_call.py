"""Primitive-only provider phase for gated decision actions."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

from .ats_stage_move_provider import (
    StageMoveProviderFailure,
    StageMoveProviderPlan,
    perform_stage_move_provider_call,
)


class DecisionProviderFailure(RuntimeError):
    """A provider failure with explicit certainty about remote mutation."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        provider_called: bool | None,
        retriable: bool,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.provider_called = provider_called
        self.retriable = bool(retriable)


@dataclass(frozen=True)
class DecisionProviderPlan:
    operation_action: str
    provider: str
    provider_target_id: str
    provider_remote_stage: str | None
    target_stage: str | None
    organization_id: int
    stage_plan: StageMoveProviderPlan | None = field(default=None, repr=False)
    workable_access_token: str | None = field(default=None, repr=False)
    workable_subdomain: str | None = None
    workable_actor_member_id: str | None = None
    workable_disqualify_reason_id: str | None = None
    workable_disqualify_note: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DecisionProviderAuthority:
    provider: str
    provider_target_id: str
    provider_remote_stage: str | None
    target_stage: str | None
    provider_connection_key: str
    owner_external_job_id: str | None
    candidate_provider_id: str | None
    plan: DecisionProviderPlan | None
    failure: tuple[str, str] | None
    local_only_reason: str | None


def _fingerprint(values: dict[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _secret_fingerprint(value: object, *, key: object) -> str:
    """Keyed, one-way connection generation without a verification oracle."""

    return hmac.new(
        str(key or "").encode("utf-8"),
        str(value or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def resolve_decision_provider_authority(
    db,
    *,
    app,
    candidate,
    organization,
    owner_role,
    operation_action: str,
    target_stage: str | None,
    reason: str | None,
) -> DecisionProviderAuthority:
    """Resolve exact provider authority while rows are locked.

    Secrets are copied only into the in-memory plan. The persisted connection
    key contains one-way fingerprints and non-secret routing inputs.
    """

    from ..platform.config import settings
    from .workable_actions_service import (
        build_workable_reject_note,
        resolved_workable_action_config,
        workable_job_syncable,
        workable_writeback_enabled,
    )

    action = str(operation_action or "").strip().lower()
    if action not in {"advance", "reject"}:
        raise ValueError(f"unsupported decision provider action={action!r}")
    target = str(target_stage or "").strip() or None
    workable_target = str(app.workable_candidate_id or "").strip()
    bullhorn_target = str(app.bullhorn_job_submission_id or "").strip()
    owner_workable_job = str(owner_role.workable_job_id or "").strip() or None
    owner_bullhorn_job = str(owner_role.bullhorn_job_order_id or "").strip() or None

    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider
    from ..components.integrations.workable.provider import WorkableProvider

    active_provider = resolve_application_ats_provider(organization, db, app)
    if active_provider is None and workable_target and bullhorn_target:
        connection_key = _fingerprint(
            {
                "provider": "ambiguous",
                "workable_target": workable_target,
                "bullhorn_target": bullhorn_target,
                "operation_action": action,
            }
        )
        return DecisionProviderAuthority(
            provider="ats",
            provider_target_id="",
            provider_remote_stage=None,
            target_stage=target,
            provider_connection_key=connection_key,
            owner_external_job_id=None,
            candidate_provider_id=None,
            plan=None,
            failure=(
                "provider_ambiguous",
                "The application has two links but no active ATS authority",
            ),
            local_only_reason=None,
        )
    workable_selected = isinstance(active_provider, WorkableProvider) or (
        active_provider is None and bool(workable_target) and not bullhorn_target
    )
    bullhorn_selected = isinstance(active_provider, BullhornProvider) or (
        active_provider is None and bool(bullhorn_target) and not workable_target
    )

    if workable_selected and workable_target:
        config = resolved_workable_action_config(organization, role=owner_role)
        reject_note = (
            build_workable_reject_note(
                app=app,
                role=owner_role,
                template=config.get("auto_reject_note_template"),
                reason=reason,
            )
            if action == "reject"
            else None
        )
        write_enabled = bool(workable_writeback_enabled(organization))
        connected = bool(
            organization.workable_connected
            and organization.workable_access_token
            and organization.workable_subdomain
        )
        syncable = bool(workable_job_syncable(owner_role))
        safe = {
            "provider": "workable",
            "target": workable_target,
            "subdomain": str(organization.workable_subdomain or "").lower(),
            "token": _secret_fingerprint(
                organization.workable_access_token, key=settings.SECRET_KEY
            ),
            "actor_member_id": str(config.get("actor_member_id") or ""),
            "disqualify_reason_id": str(
                config.get("workable_disqualify_reason_id") or ""
            ),
            "disqualify_note_sha256": hashlib.sha256(
                str(reject_note or "").encode("utf-8")
            ).hexdigest(),
            "owner_job_id": owner_workable_job,
            "write_enabled": write_enabled,
            "connected": connected,
            "syncable": syncable,
            "operation_action": action,
            "target_stage": target,
        }
        connection_key = _fingerprint(safe)
        if settings.MVP_DISABLE_WORKABLE:
            return DecisionProviderAuthority(
                "local", "", None, target, connection_key, owner_workable_job,
                workable_target, None, None, "workable_globally_disabled",
            )
        if not write_enabled:
            return DecisionProviderAuthority(
                "local", "", None, target, connection_key, owner_workable_job,
                workable_target, None, None, "workable_read_only",
            )
        if not syncable:
            return DecisionProviderAuthority(
                "local", "", None, target, connection_key, owner_workable_job,
                workable_target, None, None, "workable_job_not_live",
            )
        failure = None
        if not connected:
            failure = (
                "missing_connection",
                "Workable is not connected for candidate write-back",
            )
        elif not config.get("has_write_scope"):
            failure = (
                "missing_write_scope",
                "Workable is missing the candidate write scope",
            )
        elif not config.get("actor_member_id"):
            failure = (
                "missing_actor_member_id",
                "Workable actor member is not configured",
            )
        elif action == "advance" and not target:
            failure = ("missing_target_stage", "A Workable target stage is required")
        if failure is not None:
            return DecisionProviderAuthority(
                "workable", workable_target, target, target, connection_key,
                owner_workable_job, workable_target, None, failure, None,
            )

        if action == "advance":
            stage_plan = StageMoveProviderPlan(
                provider="workable",
                provider_target_id=workable_target,
                target_stage=str(target or ""),
                provider_remote_stage=target,
                organization_id=int(organization.id),
                workable_subdomain=str(organization.workable_subdomain),
                workable_actor_member_id=str(config["actor_member_id"]),
                workable_access_token=str(organization.workable_access_token),
            )
            plan = DecisionProviderPlan(
                operation_action=action,
                provider="workable",
                provider_target_id=workable_target,
                provider_remote_stage=target,
                target_stage=target,
                organization_id=int(organization.id),
                stage_plan=stage_plan,
            )
        else:
            plan = DecisionProviderPlan(
                operation_action=action,
                provider="workable",
                provider_target_id=workable_target,
                provider_remote_stage="disqualified",
                target_stage=None,
                organization_id=int(organization.id),
                workable_access_token=str(organization.workable_access_token),
                workable_subdomain=str(organization.workable_subdomain),
                workable_actor_member_id=str(config["actor_member_id"]),
                workable_disqualify_reason_id=(
                    str(config["workable_disqualify_reason_id"])
                    if config.get("workable_disqualify_reason_id")
                    else None
                ),
                workable_disqualify_note=reject_note,
            )
        return DecisionProviderAuthority(
            "workable", workable_target,
            (target if action == "advance" else "disqualified"), target,
            connection_key, owner_workable_job, workable_target, plan, None, None,
        )

    if bullhorn_selected and bullhorn_target:
        from ..components.integrations.bullhorn import write_back

        remote = write_back.resolve_remote_status(
            db,
            organization,
            taali_intent=("advanced" if action == "advance" else "rejected"),
        )
        configured = bool(
            settings.BULLHORN_ENABLED
            and organization.bullhorn_connected
            and organization.bullhorn_username
            and organization.bullhorn_client_id
            and organization.bullhorn_client_secret
            and organization.bullhorn_refresh_token
        )
        safe = {
            "provider": "bullhorn",
            "target": bullhorn_target,
            "credential_generation": int(
                organization.bullhorn_credential_generation or 0
            ),
            "client_id": str(organization.bullhorn_client_id or ""),
            "client_secret": _secret_fingerprint(
                organization.bullhorn_client_secret, key=settings.SECRET_KEY
            ),
            "refresh_token": _secret_fingerprint(
                organization.bullhorn_refresh_token, key=settings.SECRET_KEY
            ),
            "owner_job_id": owner_bullhorn_job,
            "remote_stage": str(remote or ""),
            "operation_action": action,
        }
        connection_key = _fingerprint(safe)
        failure = None
        if not configured:
            failure = (
                "not_configured",
                "Bullhorn is disconnected for this linked application",
            )
        elif not bullhorn_target.isdigit():
            failure = (
                "not_linked",
                "The Bullhorn JobSubmission target is invalid",
            )
        elif not remote:
            failure = (
                "needs_mapping",
                f"No exact Bullhorn status is mapped for decision {action}",
            )
        if failure is not None:
            return DecisionProviderAuthority(
                "bullhorn", bullhorn_target, remote, target, connection_key,
                owner_bullhorn_job,
                str(candidate.bullhorn_candidate_id or "").strip() or None,
                None, failure, None,
            )
        stage_plan = StageMoveProviderPlan(
            provider="bullhorn",
            provider_target_id=bullhorn_target,
            target_stage=("advanced" if action == "advance" else "rejected"),
            provider_remote_stage=str(remote),
            organization_id=int(organization.id),
            bullhorn_username=str(organization.bullhorn_username),
            bullhorn_client_id=str(organization.bullhorn_client_id),
            bullhorn_client_secret=str(organization.bullhorn_client_secret),
            bullhorn_refresh_token=str(organization.bullhorn_refresh_token),
            bullhorn_rest_url=str(organization.bullhorn_rest_url or "") or None,
            bullhorn_credential_generation=int(
                organization.bullhorn_credential_generation or 0
            ),
        )
        return DecisionProviderAuthority(
            provider="bullhorn",
            provider_target_id=bullhorn_target,
            provider_remote_stage=str(remote),
            target_stage=target,
            provider_connection_key=connection_key,
            owner_external_job_id=owner_bullhorn_job,
            candidate_provider_id=(
                str(candidate.bullhorn_candidate_id or "").strip() or None
            ),
            plan=DecisionProviderPlan(
                operation_action=action,
                provider="bullhorn",
                provider_target_id=bullhorn_target,
                provider_remote_stage=str(remote),
                target_stage=target,
                organization_id=int(organization.id),
                stage_plan=stage_plan,
            ),
            failure=None,
            local_only_reason=None,
        )

    return DecisionProviderAuthority(
        provider="local",
        provider_target_id="",
        provider_remote_stage=None,
        target_stage=target,
        provider_connection_key=_fingerprint(
            {"provider": "local", "operation_action": action}
        ),
        owner_external_job_id=None,
        candidate_provider_id=None,
        plan=None,
        failure=None,
        local_only_reason="application_not_linked",
    )


def perform_decision_provider_call(plan: DecisionProviderPlan) -> dict[str, Any]:
    """Perform one provider mutation without a Session or ORM object."""

    if plan.stage_plan is not None:
        try:
            return perform_stage_move_provider_call(plan.stage_plan)
        except StageMoveProviderFailure as exc:
            raise DecisionProviderFailure(
                code=exc.code,
                message=exc.message,
                provider_called=exc.provider_called,
                retriable=exc.retriable,
            ) from None
    if plan.provider != "workable" or plan.operation_action != "reject":
        raise DecisionProviderFailure(
            code="not_configured",
            message="No provider action is configured for this decision",
            provider_called=False,
            retriable=False,
        )
    required = (
        plan.workable_access_token,
        plan.workable_subdomain,
        plan.workable_actor_member_id,
        plan.provider_target_id,
    )
    if not all(str(value or "").strip() for value in required):
        raise DecisionProviderFailure(
            code="not_configured",
            message="Workable connection, actor, or target is unavailable",
            provider_called=False,
            retriable=False,
        )
    from ..components.integrations.workable.service import WorkableService

    try:
        client = WorkableService(
            access_token=str(plan.workable_access_token),
            subdomain=str(plan.workable_subdomain),
        )
    except (TypeError, ValueError):
        raise DecisionProviderFailure(
            code="not_configured",
            message="Stored Workable connection details are unavailable",
            provider_called=False,
            retriable=False,
        ) from None
    try:
        result = client.disqualify_candidate(
            candidate_id=plan.provider_target_id,
            member_id=str(plan.workable_actor_member_id),
            disqualify_reason_id=plan.workable_disqualify_reason_id,
            disqualify_note=plan.workable_disqualify_note,
            withdrew=False,
        )
    except Exception:
        raise DecisionProviderFailure(
            code="api_error",
            message="Workable rejection did not confirm; verify the remote candidate",
            provider_called=None,
            retriable=True,
        ) from None
    if not isinstance(result, dict) or not result.get("success"):
        message = (
            str((result or {}).get("error") or "")
            if isinstance(result, dict)
            else ""
        )
        raise DecisionProviderFailure(
            code="api_error",
            message=message or "Workable rejection did not confirm",
            provider_called=None,
            retriable=True,
        )
    return {
        "success": True,
        "code": "ok",
        "provider": "workable",
        "provider_remote_stage": "disqualified",
    }


__all__ = [
    "DecisionProviderAuthority",
    "DecisionProviderFailure",
    "DecisionProviderPlan",
    "perform_decision_provider_call",
    "resolve_decision_provider_authority",
]
