"""The requisition-intake chat system prompt builder.

Split out of ``requisition_chat_service`` to keep that module under the
file-size gate. Assembles the system prompt from the org's template, the
captured-so-far values, and the focus gaps — with a CLIENT-framed variant
(no pay questions) when a consultancy's client is the speaker.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..models.role_brief import RoleBrief
from .requisition_chat_capture import _captured_brief_values, _question_for_gap


def build_chat_system_prompt(
    brief: RoleBrief,
    template: dict[str, Any],
    focus_gaps: list[dict[str, str]],
    recent_titles: Optional[list[str]] = None,
    *,
    client_org_name: Optional[str] = None,
) -> str:
    """The system prompt: template + captured-so-far + focus gaps (+ a compact
    recent-roles line for warm-start context when ``recent_titles`` is given).

    When ``client_org_name`` is set the prompt is CLIENT-FRAMED: the speaker is
    the consultancy's client describing a role they want ``{org}`` to hire for,
    the agent captures the role + its requirements, and it must NEVER ask about
    salary / compensation / budget — the consultancy owns economics. (The
    client-scoped template already has the compensation section removed; the
    instruction makes the boundary explicit so the agent never volunteers a
    pay question either.)"""
    captured = _captured_brief_values(brief, template)
    # Compact template: just the structure the model needs to fill.
    compact_template = {
        "sections": [
            {
                "key": s.get("key"),
                "fields": [
                    {
                        "key": f.get("key"),
                        "label": f.get("label"),
                        "type": f.get("type"),
                        "required": bool(f.get("required")),
                        **({"options": f["options"]} if f.get("options") else {}),
                    }
                    for f in (s.get("fields") or [])
                ],
            }
            for s in (template.get("sections") or [])
        ]
    }
    focus_lines = "\n".join(
        f"- {g['label']}: {_question_for_gap(template, g['key'])}" for g in focus_gaps
    ) or "- (the spec looks complete)"
    recent_clean = [str(t).strip() for t in (recent_titles or []) if str(t).strip()]
    recent_line = (
        f"\n\nFor context, recent roles at this org: {', '.join(recent_clean)}."
        if recent_clean
        else ""
    )
    org = (client_org_name or "").strip()
    if org:
        # CLIENT-framed intro + a hard no-pay-questions instruction. The
        # speaker is the consultancy's client, not an internal recruiter.
        intro = (
            f"You are {org}'s requisition intake agent, helping {org}'s CLIENT "
            f"describe a role they want {org} to hire for. Capture the role and "
            "its requirements. Here is the spec template you must fill: "
        )
        comp_instruction = (
            "Do NOT ask about salary, compensation, or budget — "
            f"{org}'s team handles that; never raise pay even if prompted. "
        )
        closing = (
            "ALWAYS keep momentum: every reply asks the next most useful "
            "question, or — once the role is captured — thanks them and says "
            f"{org}'s team will take it from here. "
        )
    else:
        intro = (
            "You are Taali's requisition intake agent, helping a recruiter or "
            "hiring manager capture a complete hiring spec by talking. Here is "
            "the org's spec template you must fill: "
        )
        comp_instruction = (
            "Salary is in AED by default — don't ask about currency unless the "
            "recruiter raises it. "
        )
        closing = (
            "ALWAYS keep momentum: every reply asks the next most useful "
            "question, or — once the required spec is captured — says so and "
            "offers to publish. "
        )
    return (
        intro
        + json.dumps(compact_template, separators=(",", ":"))
        + "\n\nCaptured so far: "
        + json.dumps(captured, separators=(",", ":"), default=str)
        + "\n\nMost important gaps to close next:\n"
        + focus_lines
        + recent_line
        + "\n\nFrom the user's message and any attached transcript/screenshot, "
        "capture every field you can — use the typed fields for standard columns "
        "and the 'custom' object for any other template key (e.g. 'urgency'); "
        "never skip a field just because it isn't a typed column. "
        + comp_instruction
        + "Then reply "
        "conversationally — warm, concise, fast — acknowledging what you got and "
        "asking about the focus gaps next (one or two at a time, never "
        "interrogate). "
        + closing
        + "ALWAYS set suggested_replies to up to 6 short, "
        "tappable options for the question you ask: for select fields use the "
        "template's options verbatim; for numbers, dates or free text offer the "
        "most likely answers or sensible ranges (they can still type anything)."
    )
