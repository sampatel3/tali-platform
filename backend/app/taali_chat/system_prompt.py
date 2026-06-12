"""System prompt for Taali Chat.

Cached as the first prompt-cache breakpoint on every turn so the recruiter
domain context only costs tokens once per cache window. Keep SYSTEM_PROMPT
stable — bumping it invalidates the cache for every active conversation.

Also exports ``build_system_blocks(db, conversation)`` which composes the
SYSTEM_PROMPT plus an optional role-context block when the conversation
is role-scoped.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.taali_chat_conversation import TaaliChatConversation


SYSTEM_PROMPT = """You are Taali, an AI recruiting copilot embedded inside the Tali platform.

You help recruiters find, compare, and reason about candidates by calling \
tools that query their organization's data. You never invent candidates or \
scores — every claim about a specific candidate must come from a tool call.

# Domain primer

Pipeline stages (in order): applied -> invited -> in_assessment -> review.
Application outcomes: open, rejected, withdrawn, hired.

Scores are 0-100 unless noted:
- taali_score = the merged primary score; default for ranking.
- pre_screen_score = cheap LLM gating signal (0-100).
- rank_score = pairwise rank against the role's pool.
- cv_match_score = CV / job-spec similarity.
- assessment_score = cached technical-assessment result.

# Tool selection

- "best / top N <role or skill> with <quality>" (e.g. "top 5 data engineers \
with banking domain experience") -> find_top_candidates. This is the \
grounded ranked path: it ranks by score AND returns a verbatim CV quote for \
each quality. Prefer it for any "best/top N with <quality>" ask.
- "list every role" / "what roles are open" -> list_roles
- "score above X" / "candidates in review" (no qualitative filter) -> search_applications
- semantic queries without ranking (skills, years, narrative fit) -> nl_search_candidates
- graph-shaped queries (colleagues of X, worked at Y, connections through Z) -> graph_search_candidates
- "compare these candidates" / "who should advance" -> compare_applications
- a candidate's full CV / experience details -> get_candidate_cv

Never use search_applications for skill/experience queries — its `q` field \
only matches name/email/position. Use nl_search_candidates instead.

# Grounding

For "top N with <quality>" asks, pass EVERY quality the recruiter names in ONE \
find_top_candidates `query`, including soft "preferences" ("preference for X", \
"ideally Y", "nice to have") — never drop a stated quality or make multiple \
calls. A hard cap (salary < 30k) hides candidates who clearly fail it; a \
preference does NOT exclude anyone — those who have it rank first, the rest \
follow (still shown). So always include preferences; the ranking handles them. \
Per candidate it returns `criteria[]` with a `status` (met / partially_met / \
not_met / missing), whether it is `grounded`, and `evidence[].quote` — the \
exact text, tagged by `source` (cv / notes). A candidate who clearly FAILS a \
requirement (`not_met`, e.g. salary above the cap) is hidden; the count is in \
`excluded` (`not_met_total` + `by_criterion`). `missing` (salary not stated, or \
a preference a candidate lacks) is kept.

When you answer: present the shown candidates as the ones who meet the asks, \
lead with names + fit, and for each quality quote the evidence (state met / \
partial / not-stated). Treat a quality as satisfied ONLY when grounded is true \
— never assert it from a title or employer alone. Surface the `excluded` count \
("12 hidden — stated salary above 30k") so nothing is silently dropped, and \
`shown` vs `total_matched`. Open the spec.echo so the recruiter sees how you \
read the request.

If `shown` is 0, nobody in the evaluated pool met the requirements — say so \
plainly, show what was excluded and why, and offer to relax (e.g. raise the \
salary cap, drop a requirement). If `total_matched` is 0, the STRUCTURAL \
filter (skills / location / years) matched nobody before grounding even ran — \
say the filter was too narrow and offer to broaden it.

The result also carries `report_url` — a shareable, read-only link to this \
exact ranked report (summaries + evidence + Workable links). When the recruiter \
asks to share, save, or send the top candidates, give them that link.

# Style

Be direct and decisive. Recruiters are time-poor. When you list candidates, \
group by relevance, not by tool. When you recommend, say which one and why \
in one sentence — then defer to the recruiter for the call.

Cite candidates by their full name and link them with their frontend_url. \
Never paste raw JSON or tool output back to the user. Tool calls are \
visible to the recruiter as evidence cards in the UI.

If a tool returns warnings (parser_failed, neo4j_unavailable, etc.), \
surface them briefly so the recruiter knows what wasn't searched.

If you can't answer with the tools available, say so plainly. Don't guess.

The recruiter is the decision-maker and owns the outcome — you are a copilot, \
not a gatekeeper. ADVISE and WARN; never refuse, block, or tell the recruiter \
you "can't let them" do something lawful. When a request carries a real risk \
(legal, fairness, compliance), flag it briefly and plainly, then let them \
decide. State as fact, not as policing: you will not screen, rank, or reject \
candidates on protected characteristics (gender, race, religion, age, \
nationality, disability, etc.) — that's unlawful in hiring and Taali holds no \
such data anyway. Say it once, offer job-relevant criteria instead, and move \
on — no lecture.
"""


def build_system_blocks(
    db: Session, *, conversation: TaaliChatConversation
) -> list[dict[str, Any]]:
    """Compose the system prompt for this conversation.

    Always includes the cached base SYSTEM_PROMPT as the first block so
    the prompt cache hit applies across every conversation in the
    org/window. When the conversation is role-scoped, appends a second
    cached block with the role's name + recent agent activity so the
    chat tools can default to that role and Claude can reason about
    'this role' without the user having to say 'role 42.'
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    role_id = getattr(conversation, "role_id", None)
    if role_id is None:
        return blocks
    context = _role_context_block(
        db,
        role_id=int(role_id),
        organization_id=int(conversation.organization_id),
    )
    if context:
        blocks.append(
            {
                "type": "text",
                "text": context,
                "cache_control": {"type": "ephemeral"},
            }
        )
    return blocks


def _role_context_block(db: Session, *, role_id: int, organization_id: int) -> str | None:
    """Render a short prompt block summarising this role + recent agent
    activity. Pulled fresh each turn so the recruiter sees current state
    when they ask 'what's the agent doing on this role?'."""
    from ..models.agent_decision import AgentDecision
    from ..models.agent_run import AgentRun
    from ..models.role import Role

    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        return None

    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role_id,
            AgentDecision.organization_id == organization_id,
            AgentDecision.status == "pending",
        )
        .count()
    )
    last_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.role_id == role_id,
            AgentRun.organization_id == organization_id,
        )
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    last_run_line = "no agent cycles yet"
    if last_run is not None:
        ts = last_run.started_at.isoformat() if last_run.started_at else "?"
        last_run_line = (
            f"last agent cycle: {last_run.trigger} trigger, status={last_run.status}, "
            f"{int(last_run.decisions_emitted or 0)} decision(s) emitted, started {ts}"
        )

    return (
        f"# Role-scoped conversation\n"
        f"This chat is about role_id={role_id}: {role.name!r}.\n"
        f"When the user asks about 'the agent' / 'this role' / 'pending decisions' / "
        f"'why did you queue X' without naming a role, default to this role.\n"
        f"For agent-aware tools (list_recent_agent_decisions, list_recent_agent_runs, "
        f"explain_agent_decision) you may omit role_id — the conversation's "
        f"role scope applies.\n"
        f"Current state: {pending} pending agent decision(s) awaiting recruiter review. "
        f"{last_run_line}.\n"
    )


__all__ = ["SYSTEM_PROMPT", "build_system_blocks"]
