"""ATS-specific production preflight checks for role-agent activation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, Role


def resolve_activation_ats_role(
    session: Session | None,
    role: Role,
    reasons: list[dict[str, str]],
) -> tuple[Role, bool]:
    """Resolve the one provider-owned role behind an activation target."""

    related_role = str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER
    if not related_role:
        return role, False
    ats_role = (
        session.query(Role)
        .filter(
            Role.id == int(getattr(role, "ats_owner_role_id", 0) or 0),
            Role.organization_id == int(role.organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
        if session is not None and getattr(role, "ats_owner_role_id", None)
        else None
    )
    if ats_role is None:
        reasons.append(
            {
                "code": "related_ats_owner_missing",
                "detail": (
                    "Reconnect this related role to its original ATS role "
                    "before turning on the agent."
                ),
            }
        )
        return role, True
    return ats_role, True


def append_ats_activation_reasons(
    session: Session | None,
    *,
    reasons: list[dict[str, str]],
    org: Organization,
    ats_role: Role,
    related_role: bool,
    uses_assessment: bool,
    effective_auto_send: bool,
    effective_auto_resend: bool,
    effective_auto_advance: bool,
    effective_auto_reject: bool,
    effective_auto_reject_pre_screen: bool,
    settings_obj: Any,
    worker: dict[str, Any],
    worker_capabilities: dict[str, dict[str, Any]],
) -> None:
    """Append connectivity and write-target failures for the effective ATS."""

    from .workable_actions_service import (
        resolve_workable_invite_stage,
        resolve_workable_interview_stage,
        workable_writeback_enabled,
    )

    if getattr(ats_role, "workable_job_id", None):
        workable_reject_enabled = bool(
            not related_role
            and (effective_auto_reject or effective_auto_reject_pre_screen)
        )
        workable_invite_enabled = bool(
            not related_role
            and uses_assessment
            and (effective_auto_send or effective_auto_resend)
        )
        workable_write_needed = bool(
            workable_invite_enabled
            or effective_auto_advance
            or workable_reject_enabled
        )
        workable_connected = bool(
            not getattr(settings_obj, "MVP_DISABLE_WORKABLE", False)
            and getattr(org, "workable_connected", False)
            and getattr(org, "workable_access_token", None)
            and getattr(org, "workable_subdomain", None)
        )
        workable_writable = bool(
            workable_connected and workable_writeback_enabled(org)
        )
        # This connection is also the inbound candidate feed. Require it even
        # when every autonomous write action is deliberately disabled.
        if not workable_connected:
            reasons.append(
                {
                    "code": "workable_connection_required",
                    "detail": (
                        "Connect Workable for this workspace before turning on "
                        "the agent for this Workable role."
                    ),
                }
            )
        elif workable_write_needed and not workable_writable:
            reasons.append(
                {
                    "code": "workable_writeback_required",
                    "detail": (
                        "Enable Workable candidate write-back in Settings → "
                        "Integrations → Workable before turning on the agent."
                    ),
                }
            )

        if workable_writable and workable_invite_enabled:
            invite_stage, invite_error = resolve_workable_invite_stage(org, ats_role)
            if not invite_stage:
                reasons.append(
                    {
                        "code": "workable_invite_stage_missing",
                        "detail": invite_error
                        or (
                            "Choose the Workable assessment/invited stage in "
                            "Agent settings before autonomous assessment sends."
                        ),
                    }
                )

        if effective_auto_advance and workable_writable:
            target_stage, stage_error = resolve_workable_interview_stage(org, ats_role)
            if not target_stage:
                reasons.append(
                    {
                        "code": "workable_interview_stage_missing",
                        "detail": stage_error
                        or (
                            "Choose the Workable interview hand-off stage before "
                            "autonomous advances can write back."
                        ),
                    }
                )

    bullhorn_role = bool(
        not getattr(ats_role, "workable_job_id", None)
        and (
            str(getattr(ats_role, "source", None) or "").strip().lower()
            == "bullhorn"
            or getattr(ats_role, "bullhorn_job_order_id", None)
        )
    )
    if not bullhorn_role:
        return

    bullhorn_enabled = bool(getattr(settings_obj, "BULLHORN_ENABLED", False))
    if not bullhorn_enabled:
        reasons.append(
            {
                "code": "bullhorn_feature_disabled",
                "detail": (
                    "Enable BULLHORN_ENABLED before turning on the agent for "
                    "this Bullhorn role."
                ),
            }
        )
    if (
        worker.get("capability_reporting") is True
        and worker_capabilities.get("celery", {}).get("bullhorn_enabled") is not True
    ):
        reasons.append(
            {
                "code": "bullhorn_worker_feature_disabled",
                "detail": (
                    "The default worker has BULLHORN_ENABLED off or has not "
                    "reported the Bullhorn capability. Deploy the matching "
                    "worker configuration before turning on the agent."
                ),
            }
        )
    if not getattr(ats_role, "bullhorn_job_order_id", None):
        reasons.append(
            {
                "code": "bullhorn_role_not_linked",
                "detail": (
                    "Link this role to its Bullhorn JobOrder, then press Turn on again."
                ),
            }
        )
    required_credentials = {
        "username": getattr(org, "bullhorn_username", None),
        "client id": getattr(org, "bullhorn_client_id", None),
        "client secret": getattr(org, "bullhorn_client_secret", None),
        "refresh token": getattr(org, "bullhorn_refresh_token", None),
    }
    missing_credentials = [
        label
        for label, value in required_credentials.items()
        if not str(value or "").strip()
    ]
    connection_ready = bool(
        getattr(org, "bullhorn_connected", False) and not missing_credentials
    )
    if not connection_ready:
        missing_detail = (
            f" Missing: {', '.join(missing_credentials)}."
            if missing_credentials
            else ""
        )
        reasons.append(
            {
                "code": "bullhorn_connection_required",
                "detail": (
                    "Connect Bullhorn for this workspace before turning on "
                    f"the agent.{missing_detail}"
                ),
            }
        )

    if not (
        bullhorn_enabled
        and connection_ready
        and getattr(ats_role, "bullhorn_job_order_id", None)
    ):
        return

    from ..components.integrations.bullhorn.write_back import resolved_write_targets

    write_targets = resolved_write_targets(session, org)
    required_intents: list[tuple[str, str, str]] = []
    if (
        not related_role
        and uses_assessment
        and (effective_auto_send or effective_auto_resend)
    ):
        required_intents.append(
            (
                "invited",
                "bullhorn_assessment_stage_mapping_required",
                "assessment/invited",
            )
        )
    if effective_auto_advance:
        required_intents.append(
            (
                "advanced",
                "bullhorn_advance_stage_mapping_required",
                "advanced/interview",
            )
        )
    if not related_role and (
        effective_auto_reject or effective_auto_reject_pre_screen
    ):
        required_intents.append(
            (
                "rejected",
                "bullhorn_reject_stage_mapping_required",
                "rejected",
            )
        )
    for intent, code, label in required_intents:
        if write_targets.get(intent):
            continue
        mapping_quantity = "exactly one" if intent == "invited" else "a"
        reasons.append(
            {
                "code": code,
                "detail": (
                    f"Map {mapping_quantity} Bullhorn status to Taali's {label} "
                    "stage in Settings → Integrations → Bullhorn, then "
                    "press Turn on again. Taali will never guess an ATS status."
                ),
            }
        )


__all__ = ["append_ats_activation_reasons", "resolve_activation_ats_role"]
