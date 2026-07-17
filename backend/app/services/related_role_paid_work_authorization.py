"""Recruiter-confirmed bounds for direct related-role paid work."""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException

from ..models.organization import Organization
from ..models.role import Role
from .agent_policy_settings import apply_workspace_agent_defaults
from .related_role_spec_lifecycle import RELATED_ROLE_SCORE_COST_USD


RELATED_ROLE_PAID_SCOPE_CHANGED = "RELATED_ROLE_PAID_SCOPE_CHANGED"


def _value(authority: Any, key: str) -> Any:
    if isinstance(authority, dict):
        return authority.get(key)
    return getattr(authority, key)


def related_role_budget_preview(
    organization: Organization | None,
    *,
    scoreable_count: int,
) -> dict[str, Any]:
    """Return the exact default cap and conservative full-roster minimum."""

    role_probe = Role()
    apply_workspace_agent_defaults(role_probe, organization)
    scoreable = max(0, int(scoreable_count))
    minimum_budget_cents = int(
        math.ceil(scoreable * RELATED_ROLE_SCORE_COST_USD * 100)
    )
    proposed_budget_cents = int(role_probe.monthly_usd_budget_cents or 0)
    return {
        "estimated_cost_usd": round(scoreable * RELATED_ROLE_SCORE_COST_USD, 2),
        "minimum_initial_budget_cents": minimum_budget_cents,
        "ongoing_score_cost_usd": RELATED_ROLE_SCORE_COST_USD,
        "proposed_monthly_budget_cents": proposed_budget_cents,
        "initial_scope_fits_monthly_budget": (
            minimum_budget_cents <= proposed_budget_cents
        ),
    }


def select_related_role_monthly_budget(
    preview: dict[str, Any],
    requested_budget_cents: int | None,
) -> dict[str, Any]:
    """Attach the proposed explicit cap and whether it covers initial scoring."""

    selected = int(
        requested_budget_cents
        if requested_budget_cents is not None
        else preview.get("proposed_monthly_budget_cents") or 0
    )
    if selected < 1 or selected > 10_000_000:
        raise ValueError("monthly_budget_cents must be between 1 and 10000000")
    result = dict(preview)
    minimum = int(result.get("minimum_initial_budget_cents") or 0)
    result["selected_monthly_budget_cents"] = selected
    result["initial_scope_fits_selected_budget"] = selected >= minimum
    if selected < minimum:
        result["message"] = (
            f"The selected monthly cap (${selected / 100:.2f}) is below the "
            f"${minimum / 100:.2f} minimum for the current full scoreable roster. "
            "Choose an adequate cap and preview again; nothing has been created."
        )
    return result


def related_role_create_authority(preview: dict[str, Any]) -> dict[str, Any]:
    """Build the confirmation fields shared by direct and chat creation paths."""

    return {
        "expected_source_role_id": int(preview.get("source_role_id") or 0),
        "expected_source_role_name": str(preview.get("source_role_name") or ""),
        "expected_source_role_version": int(preview.get("source_role_version") or 0),
        "expected_default_monthly_budget_cents": int(
            preview.get("proposed_monthly_budget_cents") or 0
        ),
        "approved_max_candidates_total": int(preview.get("candidates_total") or 0),
        "approved_max_scoreable_count": int(
            preview.get("candidates_scoreable")
            if preview.get("candidates_scoreable") is not None
            else preview.get("candidates_with_cv") or 0
        ),
        "approved_monthly_budget_cents": int(
            preview.get("selected_monthly_budget_cents") or 0
        ),
    }


def _current_scope(
    *,
    source_role: Role,
    candidates_total: int,
    scoreable_count: int,
    current_default_monthly_budget_cents: int | None = None,
    related_role: Role | None = None,
) -> dict[str, Any]:
    scoreable = max(0, int(scoreable_count))
    scope: dict[str, Any] = {
        "source_role": {
            "id": int(source_role.id),
            "name": str(source_role.name),
            "version": int(source_role.version or 1),
        },
        "candidates_total": max(0, int(candidates_total)),
        "scoreable_count": scoreable,
        "estimated_cost_usd": round(scoreable * RELATED_ROLE_SCORE_COST_USD, 2),
        "minimum_initial_budget_cents": int(
            math.ceil(scoreable * RELATED_ROLE_SCORE_COST_USD * 100)
        ),
        "ongoing_score_cost_usd": RELATED_ROLE_SCORE_COST_USD,
    }
    if current_default_monthly_budget_cents is not None:
        scope["current_default_monthly_budget_cents"] = int(
            current_default_monthly_budget_cents
        )
    if related_role is not None:
        scope["related_role"] = {
            "id": int(related_role.id),
            "name": str(related_role.name),
            "version": int(related_role.version or 1),
        }
    return scope


def _scope_changed(
    *,
    operation: str,
    reason: str,
    source_role: Role,
    candidates_total: int,
    scoreable_count: int,
    current_default_monthly_budget_cents: int | None = None,
    related_role: Role | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": RELATED_ROLE_PAID_SCOPE_CHANGED,
            "message": (
                "The related-role source or candidate scope changed after the paid-work "
                "preview. Review the refreshed preview and confirm again."
            ),
            "operation": operation,
            "reason": reason,
            "current_scope": _current_scope(
                source_role=source_role,
                related_role=related_role,
                candidates_total=candidates_total,
                scoreable_count=scoreable_count,
                current_default_monthly_budget_cents=(
                    current_default_monthly_budget_cents
                ),
            ),
        },
    )


def require_related_role_publish_authority(
    *,
    authority: Any | None,
    source_role: Role,
    candidates_total: int,
    scoreable_count: int,
    current_default_monthly_budget_cents: int,
    related_role: Role | None = None,
) -> None:
    """Bind direct create-and-score to the source and displayed roster maxima."""

    reason = None
    if authority is None:
        reason = "confirmation_required"
    elif int(_value(authority, "expected_source_role_id")) != int(source_role.id):
        reason = "source_role_changed"
    elif str(_value(authority, "expected_source_role_name")) != str(source_role.name):
        reason = "source_role_changed"
    elif int(_value(authority, "expected_source_role_version")) != int(source_role.version or 1):
        reason = "source_role_version_changed"
    elif int(_value(authority, "expected_default_monthly_budget_cents")) != int(
        current_default_monthly_budget_cents
    ):
        reason = "default_monthly_cap_changed"
    elif int(candidates_total) > int(_value(authority, "approved_max_candidates_total")):
        reason = "candidate_roster_grew"
    elif int(scoreable_count) > int(_value(authority, "approved_max_scoreable_count")):
        reason = "scoreable_roster_grew"
    elif int(_value(authority, "approved_monthly_budget_cents")) < int(
        math.ceil(int(scoreable_count) * RELATED_ROLE_SCORE_COST_USD * 100)
    ):
        reason = "initial_scope_over_monthly_cap"
    elif related_role is not None and int(
        related_role.monthly_usd_budget_cents or 0
    ) != int(_value(authority, "approved_monthly_budget_cents")):
        reason = "monthly_cap_changed"
    if reason is not None:
        raise _scope_changed(
            operation="publish_related_role",
            reason=reason,
            source_role=source_role,
            related_role=related_role,
            candidates_total=candidates_total,
            scoreable_count=scoreable_count,
            current_default_monthly_budget_cents=(
                current_default_monthly_budget_cents
            ),
        )


def require_related_role_rescore_scope(
    *,
    approved_max_scoreable_count: int,
    source_role: Role,
    related_role: Role,
    candidates_total: int,
    scoreable_count: int,
) -> None:
    """Reject only growth; an equal or smaller paid roster remains authorized."""

    if int(scoreable_count) <= int(approved_max_scoreable_count):
        return
    raise _scope_changed(
        operation="rescore_related_role",
        reason="scoreable_roster_grew",
        source_role=source_role,
        related_role=related_role,
        candidates_total=candidates_total,
        scoreable_count=scoreable_count,
    )


__all__ = [
    "RELATED_ROLE_PAID_SCOPE_CHANGED",
    "related_role_create_authority",
    "related_role_budget_preview",
    "require_related_role_publish_authority",
    "require_related_role_rescore_scope",
    "select_related_role_monthly_budget",
]
