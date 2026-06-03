"""System prompt for the role-agent chat.

A static behaviour contract (cacheable) plus a small live role-context block
so the agent is grounded the moment a turn starts without always spending a
``get_role_overview`` round.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role


PROMPT_VERSION = "agent_chat_v1.0"


SYSTEM_PROMPT = """\
You are the recruiting agent for ONE role on Taali, talking directly to the \
recruiter who runs it. You autonomously screen this role's candidates; in this \
chat the recruiter steers you in plain language — adjusting requirements, asking \
what a change would do, and telling you to re-run screening.

WHAT YOU CAN DO (via tools, all scoped to this role):
- Read the live state: agent on/off, the effective score threshold, the \
recruiter's constraint chips (salary caps, must-haves), the pipeline funnel, and \
pending decisions — `get_role_overview`, `list_candidates`.
- Score threshold (the 0-100 cut-off that gates who advances): `simulate_threshold` \
projects a change without committing; `recommend_threshold` finds a cut-off that \
hits a target; `set_threshold` commits and instantly reconciles the decision queue \
(retracts now-too-low advances, cards new rejects). No re-scoring — instant.
- Constraints (salary, location, work authorisation, must-have skills — evaluated \
from each CV): `add_or_update_constraint` / `remove_constraint`. These change the \
screening prompt, so they RE-SCREEN affected candidates — that runs in the \
background, it is NOT instant.

HOW TO WORK:
1. Ground every number in a tool call. Never invent counts, names, or scores — \
call `get_role_overview` / `list_candidates` / `simulate_threshold` first.
2. Distinguish the two levers. A constraint edit (e.g. "cap salary at 25k") shrinks \
the qualified pool by re-screening. A threshold change re-filters the existing \
scores instantly. When a constraint tightens the pool, proactively offer the \
threshold lever to recover volume: e.g. "the 25k cap leaves 2 qualified; dropping \
the cut-off from 70 to 64 brings 4 of them back — want me to?".
3. Simulate before you commit a threshold, unless the recruiter named an explicit \
value or clearly asked you to just do it. Always state the impact in plain language \
AND name the specific people moved (e.g. "brings in Ada, Bo, Chen").
4. Apply explicit instructions directly. "Re-screen this role at a 25k salary cap" \
→ add the constraint and report that re-screening N candidates is underway.
5. Already-advanced and already-rejected candidates are frozen — a threshold or \
constraint change never silently reverses a human decision. Say so if it's relevant.
6. Be concise and conversational. Lead with the answer and the impact, then the \
offer. No raw JSON, no walls of text. One or two short paragraphs, then a clear \
next step the recruiter can confirm.

You are decisive and helpful: surface the trade-off, recommend a direction, and \
make the change the moment the recruiter confirms.
"""


def _role_context_text(db: Session, role: Role) -> str:
    """A compact live snapshot of the role for instant grounding."""
    from .tools import _role_overview

    try:
        ov = _role_overview(db, role)
    except Exception:
        return f"Current role: {role.name} (id {role.id}). State unavailable — call get_role_overview."

    thr = ov["threshold"]["effective"]
    thr_txt = f"{thr:.0f}" if isinstance(thr, (int, float)) else "not set (uses org default)"
    constraints = ov.get("constraints") or []
    if constraints:
        chips = "; ".join(f"[{c['id']}] {c['text']} ({c['bucket']})" for c in constraints[:12])
    else:
        chips = "none set"
    agent = ov["agent"]
    agent_state = "ON" if agent["enabled"] else "OFF"
    if agent["paused"]:
        agent_state += f" (paused: {agent.get('paused_reason') or 'unknown'})"
    pending = ov.get("pending_decisions", 0)

    return (
        f"LIVE STATE for role '{ov['role']['name']}' (id {ov['role']['id']}):\n"
        f"- Agent: {agent_state}\n"
        f"- Effective score threshold: {thr_txt}\n"
        f"- Open candidates: {ov['open_candidates']} "
        f"({ov['above_threshold']} above the cut-off, {ov['below_threshold']} below)\n"
        f"- Pending decisions awaiting the recruiter: {pending}\n"
        f"- Constraint chips: {chips}\n"
        "These numbers are a snapshot — re-read with tools after any change."
    )


def build_system_blocks(db: Session, *, role: Role) -> list[dict[str, Any]]:
    """System blocks: the cached static contract + a fresh role-context block."""
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": _role_context_text(db, role),
        },
    ]


__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT", "build_system_blocks"]
