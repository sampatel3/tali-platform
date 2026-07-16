"""Canonical role inputs shared by every CV scoring entry point.

The pre-screen service, the agent sub-agents, and the full scorer used to
resolve different job-description fields and translate the same criterion
bucket differently.  Keeping the pure conversions here makes the prompt/cache
inputs deterministic regardless of which entry point initiated scoring.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..cv_matching.schemas import Priority, RequirementInput
from ..models.role import Role


def build_scoring_requirements(role: Role | None) -> list[RequirementInput]:
    """Return ordered, active criteria in the scoring runner's contract.

    ``constraint`` is intentionally distinct from ``must_have``.  The full
    scorer excludes constraints from its weighted fit average and evaluates
    them as explicit feasibility gates, so collapsing the bucket to
    ``must_have`` silently changes its semantics.
    """
    if role is None:
        return []
    requirements: list[RequirementInput] = []
    for criterion in sorted(
        (role.criteria or []), key=lambda item: getattr(item, "ordering", 0)
    ):
        if getattr(criterion, "deleted_at", None) is not None:
            continue
        text = str(getattr(criterion, "text", None) or "").strip()
        if not text:
            continue
        bucket = str(
            getattr(criterion, "bucket", None)
            or ("must" if bool(getattr(criterion, "must_have", False)) else "preferred")
        ).strip().lower()
        priority = {
            "must": Priority.MUST_HAVE,
            "constraint": Priority.CONSTRAINT,
            "preferred": Priority.STRONG_PREFERENCE,
        }.get(bucket, Priority.STRONG_PREFERENCE)
        requirements.append(
            RequirementInput(
                id=f"crit_{int(criterion.id)}",
                requirement=text,
                priority=priority,
            )
        )
    return requirements


def build_pre_screen_requirements(role: Role | None) -> list[RequirementInput]:
    """Backward-compatible name for the canonical criteria conversion."""
    return build_scoring_requirements(role)


def _intent_payload(db: Session | None, role: Role | None) -> dict[str, Any] | None:
    """Fetch the active authored role intent without making it mandatory.

    Older roles legitimately have no ``RoleIntent`` row.  A malformed or
    unavailable optional overlay must not prevent the canonical job spec from
    being scored.
    """
    if db is None or role is None or getattr(role, "id", None) is None:
        return None
    try:
        from ..agent_runtime.role_intent import fetch_active_intent

        record = fetch_active_intent(db, role_id=int(role.id))
    except Exception:
        return None
    if record is None:
        return None
    return {
        "version": int(record.version),
        "structured": record.structured.model_dump(mode="json"),
        "free_text": record.free_text,
    }


def _exemplar_text(
    db: Session | None,
    role: Role | None,
    *,
    agent_name: str | None,
) -> str:
    if (
        db is None
        or role is None
        or not agent_name
        or getattr(role, "id", None) is None
        or getattr(role, "organization_id", None) is None
    ):
        return ""
    try:
        from ..agent_runtime.exemplar_store import render_exemplars_for_prompt

        return render_exemplars_for_prompt(
            db,
            agent_name=agent_name,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            query_features={},
            k=2,
        )
    except Exception:
        return ""


def resolve_role_job_spec(
    role: Role | None,
    *,
    db: Session | None = None,
    agent_name: str | None = None,
    role_intent: Any = None,
    exemplars_text: str | None = None,
) -> str:
    """Resolve one stable JD prompt for service, sub-agent, and full scorer.

    ``Role.job_spec_text`` is the deliberate source of truth.  Falling back to
    the marketing description (or the removed ``additional_requirements``
    column) made the agent path score roles that the normal service correctly
    considered incomplete.  Authored recruiter intent and teach exemplars are
    retained as explicit overlays; callers may provide the values already
    fetched by the agent loop, otherwise they are resolved from ``db``.
    """
    if role is None:
        return ""
    base = str(getattr(role, "job_spec_text", None) or "").strip()
    if not base:
        return ""

    intent = role_intent if role_intent is not None else _intent_payload(db, role)
    exemplars = (
        str(exemplars_text or "").strip()
        if exemplars_text is not None
        else _exemplar_text(db, role, agent_name=agent_name).strip()
    )
    parts = [base]
    if intent:
        if hasattr(intent, "model_dump"):
            intent = intent.model_dump(mode="json")
        if isinstance(intent, (dict, list)):
            rendered_intent = json.dumps(
                intent, sort_keys=True, separators=(",", ":"), default=str
            )
        else:
            rendered_intent = str(intent).strip()
        if rendered_intent:
            parts.append(
                "<RECRUITER_ROLE_INTENT>\n"
                f"{rendered_intent}\n"
                "</RECRUITER_ROLE_INTENT>"
            )
    if exemplars:
        parts.append(f"<RECRUITER_TEACH_EXAMPLES>\n{exemplars}\n</RECRUITER_TEACH_EXAMPLES>")
    return "\n\n".join(parts)


__all__ = [
    "build_pre_screen_requirements",
    "build_scoring_requirements",
    "resolve_role_job_spec",
]
