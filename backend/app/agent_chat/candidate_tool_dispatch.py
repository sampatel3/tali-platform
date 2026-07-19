"""Focused Agent Chat candidate tools."""

from __future__ import annotations
from . import top_report_commands as _top_report_commands
from .tool_dispatch_common import ToolContext, UNHANDLED

def dispatch_candidate_tool(name: str, ctx: ToolContext):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    conversation = ctx.conversation
    confirmation_binding = ctx.confirmation_binding
    if name == "search_candidates":
        # Reuse the Search page's candidate search (Graphiti/GraphRAG via the MCP
        # handlers). Lazy import keeps the graph deps out of the module load path;
        # graceful fallback when the vector layer isn't configured.
        try:
            from ..mcp import handlers as _mcp_handlers

            return _mcp_handlers.nl_search_candidates(
                db, user, query=str(args.get("query") or ""), role_id=int(role.id)
            )
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the turn
            return {"available": False, "error": f"search unavailable: {type(exc).__name__}"}
    if name == "find_top_candidates":
        # Evidence-aware bounded ranking for this role. Tagged as a card so
        # the engine lifts it into message.actions for the evidence-card UI;
        # the model narrates only evidence and coverage actually returned.
        from ..mcp import handlers as _mcp_handlers

        payload = _mcp_handlers.find_top_candidates(
            db,
            user,
            query=str(args.get("query") or ""),
            limit=int(args.get("limit") or 10),
            rank_by=str(args.get("rank_by") or "taali"),
            role_id=int(role.id),
        )
        # A shortlist is read-only, but exposing a bearer URL is an external
        # sharing action. The dedicated report tool binds a later-turn
        # confirmation to the exact recomputed evidence snapshot.
        payload.pop("report_token", None)
        payload.pop("report_url", None)
        return {"type": "candidate_evidence", **payload}
    if name == "create_top_candidates_report":
        return _top_report_commands.create_top_candidates_report(
            db,
            role=role,
            user=user,
            conversation=conversation,
            binding=confirmation_binding,
            arguments=args,
        )
    return UNHANDLED
