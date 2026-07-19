"""Focused Agent Chat role_config tools."""

from __future__ import annotations
from ..models.org_criterion import BUCKET_MUST, CRITERION_BUCKETS
from ..models.role_criterion import CRITERION_SOURCE_DERIVED
from ..services.role_change_audit import (
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    ROLE_CHANGE_ACTION_UPDATED,
    capture_role_change_snapshot,
)
from . import constraints as _constraints
from . import impact as _impact
from .confirmations import (
    attach_confirmation,
)
from .tool_dispatch_common import ToolContext, UNHANDLED

def dispatch_role_config_tool(name: str, ctx: ToolContext, *, audit_role_mutation):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    org_id = ctx.organization_id
    confirmation_binding = ctx.confirmation_binding
    _audit_role_mutation = audit_role_mutation
    if name == "set_threshold":
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        raw = args.get("threshold")
        result = _impact.apply_threshold(
            db,
            role,
            float(raw) if raw is not None else None,
            organization_id=org_id,
        )
        _audit_role_mutation(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from,
            actor_user_id=int(user.id),
            action=ROLE_CHANGE_ACTION_UPDATED,
        )
        return result
    if name == "add_or_update_constraint":
        cid = args.get("criterion_id")
        criterion_id = int(cid) if cid is not None else None
        requested_text = str(args.get("text") or "").strip()
        requested_bucket = str(args.get("bucket") or "constraint")
        existing = None
        if criterion_id is not None:
            existing = next(
                (
                    criterion
                    for criterion in (role.criteria or [])
                    if int(criterion.id) == criterion_id
                    and criterion.deleted_at is None
                    and criterion.source != CRITERION_SOURCE_DERIVED
                ),
                None,
            )
        if (
            existing is not None
            and bool(requested_text)
            and requested_bucket in CRITERION_BUCKETS
            and existing.text == requested_text
            and existing.bucket == requested_bucket
            and existing.must_have == (requested_bucket == BUCKET_MUST)
        ):
            # The model can repeat an identical tool call in adjacent rounds.
            # Preserve the response shape while consuming no Role revision and
            # creating no misleading related-table audit boundary.
            db.commit()
            return {
                "type": "constraint_change",
                "action": "updated",
                "criterion": {
                    "id": int(existing.id),
                    "text": existing.text,
                    "bucket": existing.bucket,
                },
                "invalidates_scores": False,
                "rescreening_count": 0,
            }
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        result = _constraints.add_or_update_constraint(
            db,
            role,
            text=requested_text,
            bucket=requested_bucket,
            criterion_id=criterion_id,
            trigger_rescreen=False,  # P0: never auto-spend — the recruiter opts in
        )
        changed_criterion_id = int(result["criterion"]["id"])
        _audit_role_mutation(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from,
            actor_user_id=int(user.id),
            action="role_criteria_updated",
            reason=(
                f"agent chat criterion {result['action']}: "
                f"criterion_id={changed_criterion_id}"
            ),
            allow_empty_changes=True,
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(estimate.get("count") or 0),
                },
            )
        return result
    if name == "remove_constraint":
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        result = _constraints.remove_constraint(
            db, role, int(args["criterion_id"]), trigger_rescreen=False
        )
        removed_criterion_id = int(result["criterion"]["id"])
        _audit_role_mutation(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from,
            actor_user_id=int(user.id),
            action="role_criteria_updated",
            reason=(
                "agent chat criterion removed: "
                f"criterion_id={removed_criterion_id}"
            ),
            allow_empty_changes=True,
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(estimate.get("count") or 0),
                },
            )
        return result
    if name == "update_job_spec":
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        result = _constraints.update_job_spec(
            db, role, job_spec_text=str(args.get("job_spec_text") or "")
        )
        if isinstance(result, dict) and bool(result.get("applied")):
            _audit_role_mutation(
                db,
                role=role,
                before=audit_before,
                from_version=audit_from,
                actor_user_id=int(user.id),
                action=ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
            )
        else:
            # Invalid/no-op input still ends the authorization lock before the
            # model is called again; no Role version is consumed.
            db.commit()
        if isinstance(result, dict) and result.get("would_rescreen"):
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(estimate.get("count") or 0),
                },
            )
        return result
    return UNHANDLED
