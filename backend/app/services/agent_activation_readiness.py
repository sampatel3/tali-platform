"""Fail-closed production preflight for turning a role agent on."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import object_session

from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from .task_approval_service import task_repository_readiness


_PLACEHOLDERS = {"", "skip", "changeme"}


def _configured(value: str | None) -> bool:
    cleaned = (value or "").strip().lower()
    return cleaned not in _PLACEHOLDERS and not cleaned.startswith("your-")


def activation_readiness(
    role: Role,
    *,
    settings_obj: Any = settings,
    auto_skip_assessment: bool | None = None,
) -> dict[str, Any]:
    """Return production runtime readiness for this role's actual path.

    Local/test activation stays independent of external services. Production
    activation verifies the Beat→worker canary, live usage metering, a usable
    model credential, native application ingress when no ATS job is linked,
    and the assessment execution/delivery dependencies only when that stage
    is explicitly enabled.
    """
    from ..platform.startup_validation import is_production_like

    if not is_production_like(settings_obj):
        return {"ready": True, "production": False, "reasons": []}

    reasons: list[dict[str, str]] = []
    from .agent_worker_health import worker_beat_status

    worker = worker_beat_status()
    if not worker.get("ready"):
        failed_queues = ", ".join(
            str(queue_name) for queue_name in (worker.get("failed_queues") or [])
        )
        worker_detail = str(worker.get("reason") or "heartbeat missing")
        if failed_queues:
            worker_detail = f"{worker_detail} (queues: {failed_queues})"
        reasons.append(
            {
                "code": "worker_unready",
                "detail": worker_detail,
            }
        )
    elif worker.get("capability_reporting") is False:
        reasons.append(
            {
                "code": "worker_capabilities_unknown",
                "detail": (
                    "required workers are consuming queues but have not yet "
                    "reported their runtime configuration"
                ),
            }
        )

    worker_capabilities = {
        str(queue_name): status.get("capabilities") or {}
        for queue_name, status in (worker.get("queues") or {}).items()
        if isinstance(status, dict)
    }
    if worker.get("capability_reporting") is True:
        missing_anthropic = [
            queue_name
            for queue_name, capabilities in worker_capabilities.items()
            if not bool(capabilities.get("anthropic_configured"))
        ]
        shadow_meter = [
            queue_name
            for queue_name, capabilities in worker_capabilities.items()
            if not bool(capabilities.get("usage_meter_live"))
        ]
        provider_unready = [
            queue_name
            for queue_name, capabilities in worker_capabilities.items()
            if capabilities.get("anthropic_probe_ok") is not True
        ]
        if missing_anthropic:
            reasons.append(
                {
                    "code": "worker_model_unconfigured",
                    "detail": (
                        "ANTHROPIC_API_KEY is missing in worker queues: "
                        + ", ".join(sorted(missing_anthropic))
                    ),
                }
            )
        if shadow_meter:
            reasons.append(
                {
                    "code": "worker_usage_meter_not_live",
                    "detail": (
                        "USAGE_METER_LIVE is false in worker queues: "
                        + ", ".join(sorted(shadow_meter))
                    ),
                }
            )
        if provider_unready:
            reasons.append(
                {
                    "code": "worker_model_probe_failed",
                    "detail": (
                        "worker could not verify configured Anthropic model access: "
                        + ", ".join(sorted(provider_unready))
                    ),
                }
            )

    # A native requisition without an ATS link gets candidates through Taali's
    # public job page. Never open it while that ingress endpoint is disabled.
    if (
        getattr(role, "source", None) == "requisition"
        and not getattr(role, "workable_job_id", None)
        and not bool(getattr(settings_obj, "ATS_PUBLIC_APPLY_ENABLED", False))
    ):
        reasons.append(
            {
                "code": "native_apply_disabled",
                "detail": "ATS_PUBLIC_APPLY_ENABLED must be true for a native requisition",
            }
        )

    # The emergency shadow-mode override may keep the API online during a
    # metering incident, but it must not start new autonomous spend. Health is
    # deliberately degraded in that mode; Turn on follows the same fail-closed
    # contract so every new run is credit-gated and ledger-debited.
    if not bool(getattr(settings_obj, "USAGE_METER_LIVE", False)):
        reasons.append(
            {
                "code": "usage_meter_not_live",
                "detail": "USAGE_METER_LIVE must be true before starting an agent",
            }
        )

    # The scoring enqueue path currently requires the process-level key before
    # it selects a model client.  Do not let activation claim that an admin or
    # workspace key is sufficient while that downstream hard gate still exists.
    has_model_credential = _configured(
        getattr(settings_obj, "ANTHROPIC_API_KEY", None)
    )
    if not has_model_credential:
        reasons.append(
            {
                "code": "model_unconfigured",
                "detail": "ANTHROPIC_API_KEY is required by the scoring worker",
            }
        )

    session = object_session(role)
    active_tasks = [
        task
        for task in (getattr(role, "tasks", None) or [])
        if bool(getattr(task, "is_active", False))
    ]
    effective_skip_assessment = (
        bool(getattr(role, "auto_skip_assessment", False))
        if auto_skip_assessment is None
        else bool(auto_skip_assessment)
    )
    if not effective_skip_assessment and not active_tasks:
        reasons.append(
            {
                "code": "assessment_task_approval_required",
                "detail": (
                    "Approve an active assessment task or explicitly set "
                    "auto_skip_assessment=true"
                ),
            }
        )
    uses_assessment = bool(active_tasks and not effective_skip_assessment)
    if uses_assessment:
        # Sending without an explicit task id is only autonomous when the
        # assignment engine has exactly one deterministic path: one active task,
        # or one valid in-window experiment whose active arms point at active
        # linked tasks.  Catch ambiguity at Turn on rather than after the first
        # strong candidate reaches an unsendable decision.
        assignable_tasks = []
        if session is None:
            task_configuration_error = (
                "assessment task selection could not be verified without an "
                "attached database session"
            )
        else:
            from .experiment_assignment import role_assignable_tasks

            assignable_tasks, task_configuration_error = role_assignable_tasks(
                session,
                role,
                organization_id=int(role.organization_id),
            )
        if task_configuration_error:
            reasons.append(
                {
                    "code": "assessment_task_ambiguous",
                    "detail": task_configuration_error,
                }
            )
        if not _configured(getattr(settings_obj, "E2B_API_KEY", None)):
            reasons.append(
                {
                    "code": "assessment_execution_unconfigured",
                    "detail": "E2B_API_KEY is required to run candidate assessments",
                }
            )
        if not _configured(getattr(settings_obj, "RESEND_API_KEY", None)):
            reasons.append(
                {
                    "code": "assessment_email_unconfigured",
                    "detail": "RESEND_API_KEY is required to deliver assessment invites",
                }
            )
        repository_configured = not bool(
            getattr(settings_obj, "GITHUB_MOCK_MODE", False)
        ) and _configured(
            getattr(settings_obj, "GITHUB_TOKEN", None)
        )
        if not repository_configured:
            reasons.append(
                {
                    "code": "assessment_repository_unconfigured",
                    "detail": "A real GITHUB_TOKEN with GITHUB_MOCK_MODE=false is required",
                }
            )
        elif not task_configuration_error:
            unavailable_repositories: list[str] = []
            for task in assignable_tasks:
                repo_ready, repo_detail = task_repository_readiness(
                    task,
                    settings_obj=settings_obj,
                )
                if not repo_ready:
                    label = str(getattr(task, "name", None) or f"task {task.id}")
                    unavailable_repositories.append(
                        f"{label} (id={int(task.id)}): {repo_detail or 'repository unavailable'}"
                    )
            if unavailable_repositories:
                reasons.append(
                    {
                        "code": "assessment_task_repository_unready",
                        "detail": "; ".join(unavailable_repositories),
                    }
                )
        if worker.get("capability_reporting") is True:
            default_worker = worker_capabilities.get("celery", {})
            missing_worker_dependencies: list[str] = []
            if not default_worker.get("e2b_configured"):
                missing_worker_dependencies.append("E2B_API_KEY")
            if not default_worker.get("resend_configured"):
                missing_worker_dependencies.append("RESEND_API_KEY")
            if default_worker.get("resend_probe_ok") is not True:
                missing_worker_dependencies.append("verified Resend delivery access")
            if (
                not default_worker.get("github_configured")
                or default_worker.get("github_mock_mode")
            ):
                missing_worker_dependencies.append("real GITHUB_TOKEN")
            if default_worker.get("github_probe_ok") is not True:
                missing_worker_dependencies.append("verified GitHub access")
            if missing_worker_dependencies:
                reasons.append(
                    {
                        "code": "assessment_worker_unconfigured",
                        "detail": (
                            "default worker is missing: "
                            + ", ".join(missing_worker_dependencies)
                        ),
                    }
                )

    # A configured meter with no funded balance is still not runnable: scoring
    # silently declines its reservation and assessment creation returns 402.
    # Require enough for one conservative end-to-end funnel pass. Ongoing
    # depletion remains a legitimate HITL top-up condition, but Turn on must
    # never start already unable to process its first candidate.
    from .pricing_service import Feature, estimate_reservation

    minimum_credits = (
        estimate_reservation(Feature.CV_PARSE)
        + estimate_reservation(Feature.PRESCREEN)
        + estimate_reservation(Feature.SCORE)
        + estimate_reservation(Feature.AGENT_AUTONOMOUS)
    )
    if uses_assessment:
        minimum_credits += estimate_reservation(Feature.ASSESSMENT)
    org = (
        session.query(Organization)
        .filter(Organization.id == int(role.organization_id))
        .one_or_none()
        if session is not None and getattr(role, "organization_id", None) is not None
        else None
    )
    if org is not None and getattr(role, "workable_job_id", None):
        from .agent_policy_settings import role_automation_enabled
        from .workable_actions_service import workable_writeback_enabled

        workable_config = (
            org.workable_config
            if isinstance(getattr(org, "workable_config", None), dict)
            else {}
        )
        if (
            role_automation_enabled(role, "auto_advance")
            and workable_writeback_enabled(org)
            and not str(
                workable_config.get("interview_stage_name") or ""
            ).strip()
        ):
            reasons.append(
                {
                    "code": "workable_interview_stage_missing",
                    "detail": (
                        "Configure workable_config.interview_stage_name before "
                        "autonomous advances can write back to Workable"
                    ),
                }
            )
    available_credits = int(getattr(org, "credits_balance", 0) or 0)
    if available_credits < minimum_credits:
        reasons.append(
            {
                "code": "billing_credits_insufficient",
                "detail": (
                    f"At least {minimum_credits} usage credits are required for "
                    f"one funnel pass; {available_credits} are available"
                ),
            }
        )
    if session is not None:
        # The org balance is only one admission rail. A role can have ample
        # organization credits while its own monthly cap is nearly consumed;
        # pending/running score jobs have not necessarily written UsageEvents
        # yet, so include their conservative reservations as committed spend.
        from ..agent_runtime.budget_guard import (
            remaining_role_admission_microcredits,
        )

        remaining_role_credits = remaining_role_admission_microcredits(
            session,
            role=role,
            per_active_score_job=estimate_reservation(Feature.SCORE),
        )
        if (
            remaining_role_credits is not None
            and remaining_role_credits < minimum_credits
        ):
            reasons.append(
                {
                    "code": "role_monthly_budget_insufficient",
                    "detail": (
                        f"At least {minimum_credits} usage credits are required "
                        "for one funnel pass after active scoring commitments; "
                        f"{remaining_role_credits} remain under this role's "
                        "monthly cap"
                    ),
                }
            )

    return {
        "ready": not reasons,
        "production": True,
        "reasons": reasons,
        "worker": worker,
    }


def readiness_message(result: dict[str, Any]) -> str:
    reasons = result.get("reasons") or []
    if not reasons:
        return "Agent runtime is ready"
    return "; ".join(
        f"{item.get('code', 'unready')}: {item.get('detail', '')}" for item in reasons
    )


__all__ = ["activation_readiness", "readiness_message"]
