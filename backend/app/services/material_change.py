"""Material-change assessment for Workable job-spec edits on agent-on roles.

When Workable pushes a *changed* job spec for a role with agent mode on, we
must not silently re-derive the criteria: re-deriving changes the content
fingerprint, which marks every pending decision stale and forces a fresh
(paid) re-evaluation. Most spec edits are cosmetic (reformatting, a reworded
benefit) and shouldn't cost anything.

So on a real spec change we:

1. Re-derive the *candidate* criteria in memory and compare to what the role
   currently has. Identical content => nothing to do (free, no LLM).
2. Otherwise ask a cheap LLM (Haiku, metered) whether the change is MATERIAL
   to the hiring bar.
   - Material   => raise a ``confirm_material_change`` HITL item and HOLD.
     The recruiter decides whether to apply (re-derive + re-evaluate) or
     ignore (keep the current bar). Pending decisions stay valid until they
     confirm.
   - Not material => apply the new criteria silently AND rebaseline the
     pending decisions' fingerprints so they DON'T go stale (no re-eval spend).

This module is called synchronously from the Workable sync (same transaction)
so there's no commit-timing race: a real material change is rare, so at most a
few cheap LLM calls per sync.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models.agent_needs_input import AgentNeedsInput
from ..models.organization import Organization
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED, RoleCriterion
from ..llm.models import FAST_MODEL
from .claude_client_resolver import get_client_for_org
from .pricing_service import Feature
from .spec_normalizer import DerivedCriterion, derive_criteria, normalize_spec

logger = logging.getLogger("taali.material_change")

# Cheap, current Haiku build — shared ``llm.models.FAST_MODEL`` pin so we
# don't fall into a retired-alias trap on some orgs.
MATERIAL_CHANGE_MODEL = FAST_MODEL


@dataclass(frozen=True)
class MaterialityVerdict:
    material: bool
    summary: str


def _current_derived(db: Session, role: Role) -> list[DerivedCriterion]:
    """Snapshot the role's live derived criteria as DerivedCriterion items.

    Queried directly from the DB (not the ``role.criteria`` relationship) so a
    stale/uncached collection after a delete+insert can't be misread as "no
    criteria" — which would spuriously flag every sync as a material change.
    """
    rows = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role.id,
            RoleCriterion.source == CRITERION_SOURCE_DERIVED,
            RoleCriterion.deleted_at.is_(None),
        )
        .all()
    )
    return [
        DerivedCriterion(text=(c.text or "").strip(), bucket=c.bucket or "preferred")
        for c in rows
    ]


def _fingerprint(items: list[DerivedCriterion]) -> str:
    """Order-independent content fingerprint of a derived-criteria set."""
    import hashlib

    parts = sorted(f"{i.text.strip().lower()}:{i.bucket}" for i in items if i.text.strip())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _already_handled(db: Session, *, role: Role, proposed_fp: str) -> bool:
    """True if we've already surfaced (or the recruiter already closed) a
    confirm_material_change for this exact proposed criteria version.

    Stops us re-calling the LLM + re-raising on every sync tick while the
    question is pending, and stops an immediate re-ask right after the
    recruiter dismisses/ignores the same change.
    """
    recent = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == int(role.id),
            AgentNeedsInput.kind == "confirm_material_change",
        )
        .order_by(AgentNeedsInput.created_at.desc())
        .first()
    )
    if recent is None:
        return False
    schema = recent.response_schema if isinstance(recent.response_schema, dict) else {}
    return schema.get("proposed_criteria_fp") == proposed_fp


def handle_spec_change(db: Session, role: Role) -> str:
    """Entry point for a changed Workable spec on an AGENT-ON role.

    Returns a short status string for logging/tests:
    ``no_change`` | ``already_pending`` | ``material`` | ``immaterial``.
    Never raises — on any failure it falls back to applying the new criteria
    (the legacy behaviour) so a sync is never blocked.
    """
    try:
        new_items = derive_criteria(normalize_spec(role.job_spec_text).requirements)
        current_items = _current_derived(db, role)

        proposed_fp = _fingerprint(new_items)
        if proposed_fp == _fingerprint(current_items):
            return "no_change"  # criteria content identical — nothing to do

        if _already_handled(db, role=role, proposed_fp=proposed_fp):
            return "already_pending"

        verdict = _assess_materiality(db, role=role, current=current_items, proposed=new_items)

        if verdict.material:
            _raise_confirm(db, role=role, proposed=new_items, proposed_fp=proposed_fp, verdict=verdict)
            return "material"

        # Immaterial: apply the new criteria but keep pending decisions valid.
        from .decision_staleness import rebaseline_pending_criteria_fingerprint
        from .role_criteria_service import sync_derived_criteria

        sync_derived_criteria(db, role)
        db.flush()
        rebaseline_pending_criteria_fingerprint(db, role_id=int(role.id))
        return "immaterial"
    except Exception:
        logger.exception("material_change.handle_spec_change failed role_id=%s; applying directly", role.id)
        from .role_criteria_service import sync_derived_criteria

        sync_derived_criteria(db, role)
        return "error_fallback"


_SYSTEM_PROMPT = (
    "You compare two versions of a job's selection criteria and judge whether "
    "the change is MATERIAL to who would be shortlisted. Material means the "
    "hiring bar moved: a new hard requirement, a removed/added must-have skill, "
    "a changed seniority or years-of-experience threshold, a new location or "
    "work-authorization constraint. NOT material means wording, formatting, "
    "reordering, benefits/perks, or restating the same requirement differently. "
    "Return ONLY valid JSON, no commentary."
)

_OUTPUT_INSTRUCTIONS = (
    'Output JSON: {"material": true|false, "summary": "one sentence a recruiter '
    'can read explaining what changed and whether it moves the bar"}. '
    "Keep summary under 200 chars."
)


def _assess_materiality(
    db: Session, *, role: Role, current: list[DerivedCriterion], proposed: list[DerivedCriterion]
) -> MaterialityVerdict:
    """Best-effort LLM judgement. On any failure, defaults to MATERIAL so a
    human reviews the change rather than it silently re-deriving."""
    org = db.query(Organization).filter(Organization.id == role.organization_id).one_or_none()
    if org is None:
        return MaterialityVerdict(material=True, summary="Job spec changed — please review.")

    def _fmt(items: list[DerivedCriterion]) -> str:
        return "\n".join(f"- [{i.bucket}] {i.text}" for i in items) or "(none)"

    user_message = (
        f"Role: {role.name or '(unnamed)'}\n\n"
        f"CURRENT_CRITERIA:\n{_fmt(current)}\n\n"
        f"PROPOSED_CRITERIA (from the new spec):\n{_fmt(proposed)}\n\n"
        f"{_OUTPUT_INSTRUCTIONS}"
    )

    try:
        client = get_client_for_org(org)
        response = client.messages.create(
            model=MATERIAL_CHANGE_MODEL,
            max_tokens=300,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            metering={
                "feature": Feature.OTHER,
                "organization_id": int(role.organization_id),
                "role_id": int(role.id),
                "metadata": {"sub_agent": "material_change_assessor"},
                "db": db,
            },
        )
        raw = response.content[0].text  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("material_change LLM call failed role_id=%s: %s", role.id, exc)
        return MaterialityVerdict(material=True, summary="Job spec changed — please review the new requirements.")

    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        return MaterialityVerdict(material=True, summary="Job spec changed — please review the new requirements.")
    material = bool(payload.get("material", True))
    summary = str(payload.get("summary") or "Job spec changed — please review.").strip()[:240]
    return MaterialityVerdict(material=material, summary=summary)


def _raise_confirm(
    db: Session,
    *,
    role: Role,
    proposed: list[DerivedCriterion],
    proposed_fp: str,
    verdict: MaterialityVerdict,
) -> None:
    from ..actions import ask_recruiter
    from ..actions.types import Actor

    prompt = (
        f"The job spec for '{role.name}' changed and it looks material: "
        f"{verdict.summary} Apply the new requirements (I'll re-check pending "
        f"candidates against them) or keep the current bar?"
    )
    ask_recruiter.open(
        db,
        Actor.system(),
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        kind="confirm_material_change",
        prompt=prompt,
        options=[
            {"value": "apply", "label": "Apply & re-check candidates"},
            {"value": "ignore", "label": "Keep current criteria"},
        ],
        response_schema={
            "proposed_criteria_fp": proposed_fp,
            "proposed_criteria": [{"text": i.text, "bucket": i.bucket} for i in proposed],
            "link_url": f"/jobs/{int(role.id)}",
            "link_label": "Open role",
        },
        rationale=(
            "Re-deriving on every spec edit would invalidate pending decisions "
            "and cost a fresh evaluation, so material changes are confirmed first."
        ),
    )


def _extract_json(raw: str) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


__all__ = ["handle_spec_change", "MaterialityVerdict"]
