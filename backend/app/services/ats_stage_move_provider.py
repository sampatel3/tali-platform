"""Primitive-only ATS stage-move provider phase.

No SQLAlchemy session or ORM object crosses this boundary.  The claim phase
copies the exact credentials/configuration needed for one call into this
in-memory plan after persisting its durable receipt and releasing row locks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class StageMoveProviderFailure(RuntimeError):
    """A classified provider failure with honest side-effect certainty."""

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


class StageMoveObservationFailure(RuntimeError):
    """A read-only exact-target observation could not be trusted."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class StageMoveProviderPlan:
    provider: str
    provider_target_id: str = field(repr=False)
    target_stage: str
    provider_remote_stage: str | None = field(repr=False)
    organization_id: int
    workable_subdomain: str | None = field(default=None, repr=False)
    workable_actor_member_id: str | None = field(default=None, repr=False)
    workable_access_token: str | None = field(default=None, repr=False)
    bullhorn_username: str | None = field(default=None, repr=False)
    bullhorn_client_id: str | None = field(default=None, repr=False)
    bullhorn_client_secret: str | None = field(default=None, repr=False)
    bullhorn_refresh_token: str | None = field(default=None, repr=False)
    bullhorn_rest_url: str | None = field(default=None, repr=False)
    bullhorn_credential_generation: int = field(default=0, repr=False)


@dataclass(frozen=True)
class StageMoveObservationPlan:
    """Detached primitive credentials for one exact read-only stage check."""

    provider: str
    provider_target_id: str = field(repr=False)
    organization_id: int
    workable_subdomain: str | None = field(default=None, repr=False)
    workable_access_token: str | None = field(default=None, repr=False)
    bullhorn_username: str | None = field(default=None, repr=False)
    bullhorn_client_id: str | None = field(default=None, repr=False)
    bullhorn_client_secret: str | None = field(default=None, repr=False)
    bullhorn_refresh_token: str | None = field(default=None, repr=False)
    bullhorn_rest_url: str | None = field(default=None, repr=False)
    bullhorn_credential_generation: int = field(default=0, repr=False)


def stage_move_observation_plan(
    plan: StageMoveProviderPlan,
) -> StageMoveObservationPlan:
    """Copy only fields needed for an exact, mutation-free provider read."""

    return StageMoveObservationPlan(
        provider=plan.provider,
        provider_target_id=plan.provider_target_id,
        organization_id=plan.organization_id,
        workable_subdomain=plan.workable_subdomain,
        workable_access_token=plan.workable_access_token,
        bullhorn_username=plan.bullhorn_username,
        bullhorn_client_id=plan.bullhorn_client_id,
        bullhorn_client_secret=plan.bullhorn_client_secret,
        bullhorn_refresh_token=plan.bullhorn_refresh_token,
        bullhorn_rest_url=plan.bullhorn_rest_url,
        bullhorn_credential_generation=plan.bullhorn_credential_generation,
    )


def _workable_move(plan: StageMoveProviderPlan) -> dict[str, Any]:
    from ..components.integrations.workable.service import WorkableService

    if not all(
        str(value or "").strip()
        for value in (
            plan.workable_access_token,
            plan.workable_subdomain,
            plan.workable_actor_member_id,
            plan.provider_target_id,
            plan.target_stage,
        )
    ):
        raise StageMoveProviderFailure(
            code="not_configured",
            message="Workable connection, actor, target, or stage is unavailable",
            provider_called=False,
            retriable=False,
        )
    try:
        client = WorkableService(
            access_token=str(plan.workable_access_token or ""),
            subdomain=str(plan.workable_subdomain or ""),
        )
    except (TypeError, ValueError):
        raise StageMoveProviderFailure(
            code="not_configured",
            message="Stored Workable connection details are unavailable",
            provider_called=False,
            retriable=False,
        ) from None

    try:
        result = client.move_candidate(
            candidate_id=plan.provider_target_id,
            member_id=str(plan.workable_actor_member_id or ""),
            target_stage=plan.target_stage,
        )
    except Exception:
        raise StageMoveProviderFailure(
            code="api_error",
            message="Workable stage move did not confirm; verify the remote stage",
            provider_called=None,
            retriable=True,
        ) from None
    if not isinstance(result, dict):
        raise StageMoveProviderFailure(
            code="api_error",
            message="Workable returned an invalid stage-move receipt",
            provider_called=None,
            retriable=True,
        )
    if not result.get("success"):
        raise StageMoveProviderFailure(
            code="api_error",
            message=str(
                result.get("error")
                or (result.get("response") or {}).get("error")
                or "Workable stage move did not confirm"
            ),
            # Once the target endpoint begins, a timeout/5xx cannot prove
            # whether the remote system accepted the idempotent stage set.
            provider_called=None,
            retriable=True,
        )
    return {
        "success": True,
        "code": "ok",
        "provider": "workable",
        "provider_remote_stage": plan.target_stage,
    }


def _bullhorn_client(plan: StageMoveProviderPlan | StageMoveObservationPlan):
    from ..components.integrations.bullhorn.auth import BullhornAuth
    from ..components.integrations.bullhorn.credential_state import (
        persist_rotated_credentials,
    )
    from ..components.integrations.bullhorn.service import BullhornService
    from ..platform.secrets import decrypt_integration_secret

    try:
        client_secret = decrypt_integration_secret(plan.bullhorn_client_secret)
        refresh_token = decrypt_integration_secret(plan.bullhorn_refresh_token)
    except Exception:
        raise StageMoveProviderFailure(
            code="not_configured",
            message="Stored Bullhorn credentials are unavailable",
            provider_called=False,
            retriable=False,
        ) from None

    def _persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        persist_rotated_credentials(
            org_id=int(plan.organization_id),
            expected_generation=int(plan.bullhorn_credential_generation),
            refresh_token=refresh_token,
            rest_url=rest_url,
        )

    auth = BullhornAuth(
        username=str(plan.bullhorn_username or ""),
        client_id=str(plan.bullhorn_client_id or ""),
        client_secret=client_secret,
        refresh_token=refresh_token or None,
        persist_tokens=_persist,
        rest_url=plan.bullhorn_rest_url,
    )
    return BullhornService(auth, client_id=str(plan.bullhorn_client_id or ""))


def _bullhorn_move(plan: StageMoveProviderPlan) -> dict[str, Any]:
    remote_stage = str(plan.provider_remote_stage or "").strip()
    if not remote_stage:
        raise StageMoveProviderFailure(
            code="needs_mapping",
            message="No exact Bullhorn status is mapped for this stage move",
            provider_called=False,
            retriable=False,
        )
    if not str(plan.provider_target_id).isdigit():
        raise StageMoveProviderFailure(
            code="not_linked",
            message="The Bullhorn JobSubmission target is invalid",
            provider_called=False,
            retriable=False,
        )
    client = _bullhorn_client(plan)
    try:
        response = client.update_job_submission_status(
            job_submission_id=plan.provider_target_id,
            status=remote_stage,
        )
    except Exception:
        # This catch begins only after the exact write method is invoked. Auth,
        # transport, timeout, and 5xx failures cannot prove remote non-mutation.
        raise StageMoveProviderFailure(
            code="api_error",
            message="Bullhorn stage move did not confirm; verify the remote status",
            provider_called=None,
            retriable=True,
        ) from None
    if not isinstance(response, dict):
        raise StageMoveProviderFailure(
            code="api_error",
            message="Bullhorn returned an invalid JobSubmission receipt",
            provider_called=None,
            retriable=True,
        )
    changed_id = response.get("changedEntityId")
    if changed_id is not None and str(changed_id) != str(plan.provider_target_id):
        raise StageMoveProviderFailure(
            code="provider_target_mismatch",
            message="Bullhorn returned a different JobSubmission receipt",
            provider_called=None,
            retriable=False,
        )
    return {
        "success": True,
        "code": "ok",
        "provider": "bullhorn",
        "provider_remote_stage": remote_stage,
        "response_id": (
            str(changed_id)
            if changed_id is not None
            else None
        ),
    }


def perform_stage_move_provider_call(
    plan: StageMoveProviderPlan,
) -> dict[str, Any]:
    """Set one exact remote stage with no database session or ORM dependency."""

    provider = str(plan.provider or "").strip().lower()
    if provider == "workable":
        return _workable_move(plan)
    if provider == "bullhorn":
        return _bullhorn_move(plan)
    raise StageMoveProviderFailure(
        code="not_configured",
        message="No writable ATS is connected for this application",
        provider_called=False,
        retriable=False,
    )


def _observed_workable_stage(payload: dict[str, Any]) -> tuple[str, list[str]]:
    raw_stage = payload.get("stage")
    values: list[str] = []
    if isinstance(raw_stage, dict):
        for key in ("slug", "id", "name", "kind"):
            value = str(raw_stage.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
    elif raw_stage is not None:
        value = str(raw_stage).strip()
        if value:
            values.append(value)
    for key in ("stage_slug", "stage_id", "stage_name", "stage_kind", "status"):
        value = str(payload.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    if not values:
        raise StageMoveObservationFailure(
            code="provider_response_malformed",
            message="Workable did not return an exact candidate stage",
        )
    return values[0], values


def perform_stage_move_provider_observation(
    plan: StageMoveObservationPlan,
) -> dict[str, Any]:
    """Read one exact remote stage without a database session or ORM object."""

    provider = str(plan.provider or "").strip().lower()
    target = str(plan.provider_target_id or "").strip()
    if not target:
        raise StageMoveObservationFailure(
            code="provider_target_missing",
            message="The exact ATS stage target is missing",
        )
    try:
        if provider == "workable":
            from ..components.integrations.workable.service import WorkableService

            if not all(
                str(value or "").strip()
                for value in (plan.workable_access_token, plan.workable_subdomain)
            ):
                raise StageMoveObservationFailure(
                    code="not_configured",
                    message="Workable is not configured for an exact stage check",
                )
            payload = WorkableService(
                access_token=str(plan.workable_access_token),
                subdomain=str(plan.workable_subdomain),
            ).get_candidate(target)
            if not isinstance(payload, dict) or str(payload.get("id") or "") != target:
                raise StageMoveObservationFailure(
                    code="provider_target_mismatch",
                    message="Workable returned a different candidate target",
                )
            remote_stage, stage_values = _observed_workable_stage(payload)
            evidence = {
                "candidate_id": target,
                "stage_values": stage_values,
                "disqualified": (
                    payload.get("disqualified")
                    if isinstance(payload.get("disqualified"), bool)
                    else None
                ),
                "updated_at": str(payload.get("updated_at") or "")[:200] or None,
            }
        elif provider == "bullhorn":
            if not target.isdigit():
                raise StageMoveObservationFailure(
                    code="provider_target_invalid",
                    message="The Bullhorn JobSubmission target is invalid",
                )
            try:
                payload = _bullhorn_client(plan).get_job_submission(target)
            except StageMoveProviderFailure as exc:
                raise StageMoveObservationFailure(
                    code=exc.code, message=exc.message
                ) from None
            if not isinstance(payload, dict) or str(payload.get("id") or "") != target:
                raise StageMoveObservationFailure(
                    code="provider_target_mismatch",
                    message="Bullhorn returned a different JobSubmission target",
                )
            remote_stage = str(payload.get("status") or "").strip()
            if not remote_stage:
                raise StageMoveObservationFailure(
                    code="provider_response_malformed",
                    message="Bullhorn did not return an exact JobSubmission status",
                )
            stage_values = [remote_stage]
            evidence = {
                "job_submission_id": target,
                "status": remote_stage,
                "is_deleted": payload.get("isDeleted") is True,
                "date_last_modified": payload.get("dateLastModified"),
            }
        else:
            raise StageMoveObservationFailure(
                code="not_configured",
                message="The exact ATS provider is unsupported",
            )
    except StageMoveObservationFailure:
        raise
    except Exception:
        raise StageMoveObservationFailure(
            code="provider_read_failed",
            message="The exact ATS stage could not be read",
        ) from None
    return {
        "success": True,
        "provider": provider,
        "provider_target_id": target,
        "provider_remote_stage": remote_stage,
        "provider_remote_stage_values": stage_values,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence": evidence,
    }


__all__ = [
    "StageMoveObservationFailure",
    "StageMoveObservationPlan",
    "StageMoveProviderFailure",
    "StageMoveProviderPlan",
    "perform_stage_move_provider_call",
    "perform_stage_move_provider_observation",
    "stage_move_observation_plan",
]
