"""Anthropic tool definitions split from the dispatcher facade."""

from __future__ import annotations

from typing import Any

ROLE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
            "name": "get_role_overview",
            "description": (
                "Snapshot of THIS role for grounding before you act: agent on/off + "
                "pause state + monthly budget, the effective score threshold (role "
                "override / org default / mode), the recruiter constraint chips "
                "(salary caps, must-haves), the pipeline funnel counts, and how many "
                "decisions are pending. ALWAYS call this first so your numbers are real."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    {
            "name": "list_candidates",
            "description": (
                "List candidates on this role by bucket, best score first. bucket: "
                "'above'/'below' (the effective threshold), 'advanced', 'rejected', "
                "'pending' (awaiting a decision), or 'all' (open apps). Each candidate "
                "returns name, pre-screen score, Taali pipeline stage, and a normalized "
                "`ats_context` for native, Workable, or Bullhorn. It also returns the "
                "synced `workable_stage` for Workable roles. Pass "
                "`workable_stage` to filter to a specific Workable stage — that's how you "
                "answer 'who's in final interview?' (Taali's pipeline_stage does NOT track "
                "Workable's interview stages; workable_stage is the source of truth). You can "
                "ALSO see the recruiter's **Workable comments / ratings** on each candidate: "
                "set `include_comments=true` to return them ([{author, created_at, body}], "
                "newest first), and `comment_contains` to filter to candidates a recruiter "
                "commented on (e.g. comment_contains='yes'). So 'top 5 in technical interview "
                "with a Yes comment' = workable_stage='technical interview', "
                "comment_contains='yes', limit=5."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "bucket": {
                        "type": "string",
                        "enum": ["above", "below", "advanced", "rejected", "pending", "all"],
                        "default": "all",
                    },
                    "workable_stage": {
                        "type": ["string", "null"],
                        "description": "Filter to candidates whose synced Workable stage contains this (case-insensitive), e.g. 'final interview'. Applies to the open buckets (not 'rejected').",
                    },
                    "comment_contains": {
                        "type": ["string", "null"],
                        "description": "Filter to candidates who have a synced Workable recruiter comment/rating whose text matches this — whole-word for a single word (comment_contains='yes' matches a 'Yes' comment but not 'yesterday'), substring for a phrase ('strong hire'). Implies include_comments. Open buckets only (not 'rejected').",
                    },
                    "include_comments": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include each candidate's synced Workable recruiter comments [{author, created_at, body}] (newest first, capped) in the result, so you can read/cite them. Set true even without a filter to show what recruiters wrote.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "required": [],
            },
        },
    {
            "name": "sync_workable_comments",
            "description": (
                "Force an immediate Workable sync for THIS role, pulling the latest "
                "recruiter comments / ratings (and stages) for all its candidates. Use "
                "when the recruiter says comments look stale or missing, or asks you to "
                "sync / refresh Workable comments. Comments normally sync automatically "
                "every few minutes; this forces a refresh now. It's ASYNCHRONOUS — tell "
                "the recruiter it's underway and to ask again in a moment so you can "
                "re-read the freshly-synced comments with list_candidates."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    {
            "name": "simulate_threshold",
            "description": (
                "Project the effect of moving the score threshold to a value WITHOUT "
                "committing. Uses stored full role-fit scores and returns candidates "
                "above/below now vs at the new cutoff, pending advances that would be "
                "retracted, new deterministic reject cards, undecided impact counts, "
                "and who is newly cleared. Use to answer 'what happens if I drop the "
                "threshold to 65?' before doing it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "The 0-100 cutoff to simulate.",
                    }
                },
                "required": ["threshold"],
            },
        },
    {
            "name": "recommend_threshold",
            "description": (
                "Recommend a score threshold. Pass target_additional to find a cutoff "
                "that clears ~N more candidates than today, or target_total for ~N "
                "total, or neither to get a sensible 'loosen it a little' suggestion. "
                "Returns the recommended cutoff, projected counts, and who it adds. Use "
                "when the recruiter wants more volume ('how do I let a few more through?')."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_additional": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "Clear this many MORE than the current cutoff.",
                    },
                    "target_total": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "Clear ~this many total.",
                    },
                },
                "required": [],
            },
        },
    {
            "name": "set_threshold",
            "description": (
                "COMMIT a new downstream role-fit threshold for this role and re-flow "
                "deterministic full-score decisions through the policy engine: retract "
                "pending advances now below the cutoff and card new rejects. Stage-1 "
                "pre-screen cards are not changed. Instant, no LLM or re-scoring. Pass "
                "null to return to the role's automatic/default resolution. Prefer "
                "simulate_threshold first and commit only after recruiter confirmation."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 100,
                        "description": "New 0-100 cutoff, or null to clear to org default.",
                    }
                },
                "required": ["threshold"],
            },
        },
    {
            "name": "add_or_update_constraint",
            "description": (
                "Add a recruiter constraint chip to this role, or edit an existing one "
                "by criterion_id. Use for salary caps, location, work authorisation, "
                "must-have skills — anything evaluated from the CV. bucket 'constraint' "
                "is a hard filter, 'must' a must-have, 'preferred' a nice-to-have. This "
                "changes the screening policy immediately but NEVER starts a paid re-screen. "
                "For hard/must changes the result contains an exact whole-pool preview and "
                "server confirmation receipt. Show the count/cost, then wait for a later "
                "recruiter confirmation before calling rescreen_role."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The constraint, e.g. 'Salary expectation <= 25,000'.",
                    },
                    "bucket": {
                        "type": "string",
                        "enum": ["constraint", "must", "preferred"],
                        "default": "constraint",
                    },
                    "criterion_id": {
                        "type": ["integer", "null"],
                        "description": "Edit an existing chip instead of adding a new one.",
                    },
                },
                "required": ["text"],
            },
        },
    {
            "name": "remove_constraint",
            "description": (
                "Remove a recruiter constraint chip by criterion_id (get ids from "
                "get_role_overview). Removal is immediate. A removed must-have/constraint "
                "returns a paid re-screen preview but does not start the re-screen; wait for "
                "the recruiter's confirmation in a later message."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"criterion_id": {"type": "integer"}},
                "required": ["criterion_id"],
            },
        },
    {
            "name": "update_job_spec",
            "description": (
                "Replace THIS role's job description with a new one the recruiter "
                "pasted, and re-derive its must-have / preferred / constraint criteria "
                "from it. Use when the recruiter sends a new or updated JD in chat. A new "
                "JD re-derives EVERY criterion (the biggest change there is), so it does "
                "NOT re-screen automatically: it applies the spec + re-derives the chips "
                "instantly (no LLM) and returns the criteria diff + a re-screen cost "
                "estimate. Recruiter-added chips (salary caps, etc.) are kept. Show what "
                "changed + the cost and ASK before running rescreen_role. For a related "
                "role, this marks its independent shared-roster scores stale; it never "
                "queues that paid scoring until rescreen_role is separately confirmed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_spec_text": {"type": "string", "description": "The full new job description text the recruiter pasted."},
                },
                "required": ["job_spec_text"],
            },
        },
    {
            "name": "start_related_role_draft",
            "description": (
                "Start the existing conversational job-creation flow as a NEW related-role "
                "draft cloned from this ATS role. Its full specification, structured fields, "
                "and labelled responsibilities are copied; intake reads that source and asks "
                "only for genuine gaps before the recruiter describes just the differences. "
                "Use this for open-ended cousin/sister-role requests or whenever the recruiter "
                "wants to refine the new role conversationally. This creates only a draft and "
                "does not spend scoring usage."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": ["string", "null"],
                        "maxLength": 200,
                        "description": "Optional proposed name; defaults to '<original> · Related'.",
                    },
                    "job_spec_text": {
                        "type": ["string", "null"],
                        "maxLength": 100000,
                        "description": "Optional complete revised spec. Omit to clone the original verbatim and refine it in chat.",
                    },
                },
                "required": [],
            },
        },
    {
            "name": "preview_related_role",
            "description": (
                "Preview creating a NEW related Taali role over this ATS role's "
                "existing candidate pool, using a complete cousin/alternate job spec. "
                "Returns the shared roster size, scorable count, and estimated AI "
                "usage without creating anything. Use this instead of update_job_spec "
                "when the recruiter wants a separate role/view while preserving this "
                "original role. Always show the preview and wait for a later explicit "
                "confirmation before calling create_related_role."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the new related role.",
                    },
                    "job_spec_text": {
                        "type": "string",
                        "description": "The complete updated/cousin job specification, not only the differences.",
                    },
                    "monthly_budget_cents": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 10000000,
                        "description": "Exact monthly AI cap for the new related role in USD cents. Omit to preview the current workspace default.",
                    },
                },
                "required": ["name", "job_spec_text"],
            },
        },
    {
            "name": "create_related_role",
            "description": (
                "Create the related role and queue fresh scores for the shared roster. "
                "It is a full Taali role with independent scoring, assessments, Agent "
                "policy, and budget. Rejection and advancement affect every linked role "
                "because they share one ATS application. This paid mutation is accepted "
                "only after preview_related_role has been shown and the recruiter "
                "explicitly confirms in a NEW message."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "job_spec_text": {"type": "string"},
                    "monthly_budget_cents": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 10000000,
                        "description": "The exact cap shown in the confirmed preview; omit to reuse that confirmed cap.",
                    },
                    "confirmation_token": {
                        "type": ["string", "null"],
                        "description": "Opaque token from the preview, when available.",
                    },
                },
                "required": ["name", "job_spec_text"],
            },
        }
]
