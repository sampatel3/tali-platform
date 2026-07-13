"""Budget + volume guard for the agent's autonomous assessment sends.

When ``role.auto_promote`` is on, the agent sends assessment invites without a
human clicking "Send assessment" per candidate. That autonomy is what the
recruiter opted into — but flipping the toggle on shouldn't be able to blast a
whole batch of just-cleared candidates in one cron cycle, nor run past the
role's spend cap. This guard bounds it so turning the toggle on is *safe*:

- **Budget** — reuse the existing universal monthly USD cap via
  ``role_budget_gate.can_spend_on_role``. Over cap → hold (and the role
  auto-pauses exactly as it already does elsewhere). No new budget system.
- **Volume** — at most ``settings.ASSESSMENT_AUTO_SEND_DAILY_CAP`` assessments
  may be created for a role per UTC day. Above it, further auto-sends are held.

"Held" is NOT "dropped". When the guard trips, the caller routes the send to a
pending ``AgentDecision(decision_type='send_assessment')`` HITL card — the same
card the ``auto_promote=False`` path already produces — so the recruiter can
approve the batch ("send anyway") or hold. Autonomous within the guard,
human-in-the-loop only at the cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.assessment import Assessment
from ..models.role import Role
from ..platform.config import settings


@dataclass(frozen=True)
class AutoSendGuard:
    """Result of the auto-send guard.

    ``ok`` True → the agent may auto-send immediately.
    ``ok`` False → hold the send as a HITL card; ``reason`` is recruiter-facing
    copy and ``hold_kind`` is one of ``"budget"`` / ``"volume"``.
    """

    ok: bool
    reason: Optional[str] = None
    hold_kind: Optional[str] = None


def _utc_day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def daily_cap() -> int:
    """Per-role/day auto-send cap; 0 (or unset) disables the volume guard."""
    return int(getattr(settings, "ASSESSMENT_AUTO_SEND_DAILY_CAP", 0) or 0)


def assessments_created_today(db: Session, *, role: Role) -> int:
    """Count of non-voided assessments created for this role since UTC midnight.

    This is the volume denominator. It intentionally counts *all* assessments
    for the role (agent + any manual sends), because the cap protects the
    candidate experience — total invites going out for the role in a day — not
    just the agent's share.
    """
    return int(
        db.query(func.count(Assessment.id))
        .filter(
            Assessment.organization_id == int(role.organization_id),
            Assessment.role_id == int(role.id),
            Assessment.is_voided.is_(False),
            Assessment.created_at >= _utc_day_start(),
        )
        .scalar()
        or 0
    )


def check_auto_send(db: Session, *, role: Role) -> AutoSendGuard:
    """Decide whether the agent may auto-send an assessment for ``role`` now.

    Budget is checked first (it also auto-pauses the role on breach, via
    ``can_spend_on_role``), then the daily volume cap.
    """
    # Imported lazily: role_budget_gate pulls agent_runtime.budget_guard, which
    # forces the agent_runtime package init — a top-level import here would
    # cycle when tool_registry (part of that package) imports this module.
    from .role_budget_gate import can_spend_on_role

    if not can_spend_on_role(db, role=role):
        return AutoSendGuard(
            ok=False,
            reason="role monthly budget cap reached",
            hold_kind="budget",
        )
    cap = daily_cap()
    if cap > 0:
        sent = assessments_created_today(db, role=role)
        if sent >= cap:
            return AutoSendGuard(
                ok=False,
                reason=f"daily auto-send cap reached ({sent}/{cap} today)",
                hold_kind="volume",
            )
    return AutoSendGuard(ok=True)
