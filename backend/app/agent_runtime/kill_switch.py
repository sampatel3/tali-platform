"""Org-level + global agent kill switch (incident response).

Per-role pausing (``role.agent_paused_at``, set by the budget guard or the
recruiter toggle) is the routine lever for halting a single role. This module
adds two coarser switches for incident response — when something goes wrong
broadly (a bad prompt version, runaway tool loops, an Anthropic/Graphiti
outage) and pausing roles one by one is too slow:

- GLOBAL: ``settings.AGENT_GLOBAL_KILL_SWITCH`` (env flag). When True, every
  agent cycle is halted across all orgs and roles.
- ORG: ``org.workspace_settings["agent_kill_switch"]`` (stored the same way as
  ``decision_policy_auto_apply``). When True, every cycle for that org's roles
  is halted.

Both default to "off" (agents run), so existing behavior is unchanged.

Every cycle entry point (the ``agent_*`` Celery tasks) calls
``halt_reason_for_role`` and short-circuits with the returned reason, mirroring
the existing ``agentic_mode_disabled`` / ``agent_paused`` skip returns — no
AgentRun row is created. ``orchestrator.run_cycle`` also checks
``halt_reason_for_org`` as defense-in-depth for any direct caller (e.g. the
manual-run CLI script), aborting the cycle before the first Anthropic call.
"""

from __future__ import annotations

from typing import Optional

from ..models.organization import Organization
from ..platform.config import settings

# Skip/abort reason strings. Stable identifiers — surfaced in task skip
# returns and stamped on aborted AgentRun.error rows, so keep them greppable.
GLOBAL_KILL_SWITCH_REASON = "agent_kill_switch_global"
ORG_KILL_SWITCH_REASON = "agent_kill_switch_org"

# Key under ``Organization.workspace_settings`` that holds the org switch.
ORG_SETTINGS_KEY = "agent_kill_switch"


def global_kill_switch_active() -> bool:
    """True when the platform-wide kill switch is engaged."""
    return bool(getattr(settings, "AGENT_GLOBAL_KILL_SWITCH", False))


def org_kill_switch_active(org: Optional[Organization]) -> bool:
    """True when ``org`` has its kill switch engaged.

    Reads the raw ``workspace_settings`` dict (like the decision-policy
    settings do) so a value set directly in the DB during an incident is
    honoured even if it bypassed the typed settings API.
    """
    if org is None:
        return False
    ws = org.workspace_settings if isinstance(org.workspace_settings, dict) else None
    return bool((ws or {}).get(ORG_SETTINGS_KEY, False))


def halt_reason_for_org(org: Optional[Organization]) -> Optional[str]:
    """Return a skip reason if any kill switch halts cycles for ``org``,
    else None. Checks the global switch first (cheapest)."""
    if global_kill_switch_active():
        return GLOBAL_KILL_SWITCH_REASON
    if org_kill_switch_active(org):
        return ORG_KILL_SWITCH_REASON
    return None


def halt_reason_for_role(db, *, role) -> Optional[str]:
    """Return a skip reason if any kill switch halts cycles for ``role``,
    else None. The global switch short-circuits without a DB hit; the org
    switch loads the role's organization only when the global one is off.
    """
    if global_kill_switch_active():
        return GLOBAL_KILL_SWITCH_REASON
    org = (
        db.query(Organization)
        .filter(Organization.id == role.organization_id)
        .first()
    )
    return ORG_KILL_SWITCH_REASON if org_kill_switch_active(org) else None
