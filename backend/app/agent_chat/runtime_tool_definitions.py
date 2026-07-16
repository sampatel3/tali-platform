"""Anthropic tool definitions split from the dispatcher facade."""

from __future__ import annotations

from typing import Any

RUNTIME_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
            "name": "set_agent_state",
            "description": (
                "Activate (turn on / resume) or pause this role's agent. Use when the "
                "recruiter asks to start, restart, resume, re-enable, or pause the agent. "
                "First activation preserves the role's action-level automation policy "
                "and persists one durable "
                "command that generates, battle-tests and approves the assessment before "
                "turning the role on; it never needs a second draft-approval click. The "
                "role stays honestly off while that work or production readiness retries. "
                "Activating needs a monthly budget. A manual pause never clears until an "
                "explicit resume."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["activate", "pause"],
                        "description": "'activate' to turn on / resume, 'pause' to pause.",
                    },
                },
                "required": ["action"],
            },
        },
    {
            "name": "adjust_agent_settings",
            "description": (
                "Adjust this role agent's settings. Only set the fields the recruiter "
                "asks to change. monthly_budget_cents = monthly spend cap in cents "
                "(e.g. 5000 = $50/mo). auto_reject and the narrower "
                "auto_reject_pre_screen can execute ONLY deterministic pre-screen "
                "failures; LLM/full-score/assessment reject recommendations always "
                "require human confirmation. auto_send_assessment, "
                "auto_resend_assessment, and auto_advance independently control "
                "the reversible positive actions. auto_promote remains a legacy "
                "aggregate alias for clients that have not set granular choices. "
                "auto_skip_assessment = bypass the assessment stage entirely; strong "
                "candidates queue as advance-to-interview decisions instead of "
                "receiving an assessment invite. Raising the "
                "budget can resume an automatic budget/credit hold after readiness "
                "passes, but never clears a recruiter-authored manual pause."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "monthly_budget_cents": {"type": ["integer", "null"]},
                    "auto_reject": {"type": ["boolean", "null"]},
                    "auto_reject_pre_screen": {"type": ["boolean", "null"]},
                    "auto_promote": {"type": ["boolean", "null"]},
                    "auto_send_assessment": {"type": ["boolean", "null"]},
                    "auto_resend_assessment": {"type": ["boolean", "null"]},
                    "auto_advance": {"type": ["boolean", "null"]},
                    "auto_skip_assessment": {"type": ["boolean", "null"]},
                },
            },
        },
    {
            "name": "rescreen_role",
            "description": (
                "Run the re-screen the recruiter opted into AFTER a constraint change. "
                "A constraint/must-have edit applies immediately but does NOT re-screen "
                "automatically — re-screening re-scores the pool and costs money. First "
                "report the `would_rescreen` count + `est_cost_usd` from the "
                "constraint_change result and ask the recruiter to confirm; call this "
                "ONLY once they explicitly say yes."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
    {
            "name": "rescore_candidates",
            "description": (
                "Re-score this role's OLD-engine (v1.x) candidates with the current "
                "holistic v2.1.0 engine. Use when the recruiter wants stale scores "
                "refreshed — and let THEM steer the scope; never assume 'all'. ALWAYS "
                "call once WITHOUT confirm first (confirm=false) to preview the matched "
                "count + $ cost, show the recruiter, and call again with confirm=true "
                "ONLY after they explicitly say yes. Scope the subset: 'all', 'top_n' "
                "(highest current scores, set `limit`), 'above_threshold' / "
                "'below_threshold' (set `threshold` 0-100), or 'none'. Re-scoring spends "
                "~$0.083/candidate, so the preview-then-confirm step is mandatory."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "top_n", "above_threshold", "below_threshold", "none"],
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "For scope=top_n: how many of the highest-scoring stale candidates.",
                    },
                    "threshold": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "For scope=above_threshold/below_threshold: the current-score cutoff.",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "false = preview count+cost only (default); true = actually re-score (recruiter must have confirmed).",
                    },
                },
                "required": ["scope"],
            },
        },
    {
            "name": "get_criterion_breakdown",
            "description": (
                "For ONE criterion (criterion_id from get_role_overview), how the scored "
                "candidates currently split — met / missing / unknown / not_assessed — "
                "WITH each one's stored reasoning. Read-only and free (reuses scores we "
                "already have). Use this to reason about a criteria change before "
                "spending: a widening only re-checks the previously-missing, a narrowing "
                "only the previously-met; a typo/cosmetic reword is a no-op."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"criterion_id": {"type": "integer"}},
                "required": ["criterion_id"],
            },
        },
    {
            "name": "rescreen_scoped",
            "description": (
                "Re-screen ONLY the candidates affected by a criteria change — far cheaper "
                "than the whole pool. Pass criterion_id + which stored status-group to "
                "re-check: a WIDENING re-checks ['missing'], a NARROWING ['met']. The "
                "re-screen re-judges each correctly (Saudi flips to met, India stays "
                "missing). Confirm the scope + cost with the recruiter before calling."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "criterion_id": {"type": "integer"},
                    "statuses": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["met", "missing", "unknown"]},
                    },
                },
                "required": ["criterion_id", "statuses"],
            },
        },
    {
            "name": "search_candidates",
            "description": (
                "Exhaustive/deterministic natural-language retrieval over this role's "
                "candidate pool. Reserve it for explicit all/every requests and pool "
                "scoping, e.g. 'all candidates based in MENA' or 'every candidate with a "
                "stated salary'. Report database/returned/verification coverage honestly; "
                "never imply unchecked qualitative matches passed or failed. For bounded "
                "qualitative discovery, use find_top_candidates instead."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    {
            "name": "find_top_candidates",
            "description": (
                "Default for BOUNDED qualitative candidate discovery on THIS role, even "
                "without 'top'/'best' wording (e.g. 'who has banking experience?', 'show "
                "people who've led a team'). Uses the requested limit or defaults to 10, "
                "ranks by score, and returns available per-criterion verdicts/cited CV "
                "evidence plus grounding coverage and degradation warnings. Renders an "
                "evidence card without publishing it externally. Cite only available "
                "evidence; never brand unchecked results grounded. For a bare "
                "'top 10 report', pass query='candidates' and limit=10; the role's stored "
                "scorecard evidence is reused when available. Surface criteria_unchecked."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                    "rank_by": {
                        "type": "string",
                        "enum": [
                            "taali",
                            "pre_screen",
                            "rank",
                            "cv_match",
                            "workable",
                            "assessment",
                            "role_fit",
                        ],
                        "default": "taali",
                    },
                },
                "required": ["query"],
            },
        },
    {
            "name": "create_top_candidates_report",
            "description": (
                "Create a public, read-only 30-day report for a grounded shortlist on "
                "THIS role. Use only when the recruiter explicitly asks to share, send, "
                "or publish a shortlist. The first call recomputes and shows the exact "
                "server-scoped evidence preview without creating a link. Publish only "
                "after the recruiter confirms that preview in a NEW message; the server "
                "recomputes it and asks again if anything changed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                    "rank_by": {
                        "type": "string",
                        "enum": [
                            "taali", "pre_screen", "rank", "cv_match", "workable",
                            "assessment", "role_fit",
                        ],
                        "default": "taali",
                    },
                    "confirmation_token": {
                        "type": ["string", "null"],
                        "description": "Opaque token from the grounded share preview, when available.",
                    },
                },
                "required": ["query"],
            },
        },
    {
            "name": "list_draft_tasks",
            "description": (
                "List auto-generated assessment-task drafts on THIS role. If the result "
                "has automatic_activation=true, the saved Turn-on command is validating "
                "and approving the draft: report progress and NEVER ask for a second "
                "approval or revision click. Otherwise this is an optional manual review "
                "card the recruiter can approve or reject-with-feedback when they ask "
                "about tasks/assessments."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    {
            "name": "role_health_check",
            "description": (
                "FREE, read-only scan of what's most likely HURTING this role's "
                "decisions, ranked, so you can proactively steer the recruiter. "
                "Surfaces: a must-have almost nobody meets (killing the pool), a "
                "requirement you often can't verify from the CV, a requirement "
                "everyone meets (no signal), a score cut-off set too strict / too "
                "loose, a PATTERN of the recruiter overriding your decisions in one "
                "direction (you're mis-calibrated — the strongest signal), stale "
                "old-engine scores, and a decision backlog. Returns `findings` "
                "(ranked) + `top_finding` + `all_clear`. RUN IT when a conversation "
                "opens fresh, when the recruiter asks an open-ended 'how's this role "
                "/ what should I change / review this', or after they resolve a "
                "batch. Then LEAD with `top_finding` phrased as a question + the "
                "concrete fix you can make; one finding at a time, never a wall. "
                "Each finding carries the handles (criterion_id, threshold) to act. "
                "If `all_clear`, say the role looks healthy in a line — don't invent "
                "problems."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
]
