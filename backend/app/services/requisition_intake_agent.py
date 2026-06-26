"""Requisition intake agent — extract a hiring brief from natural input.

Given hiring-manager conversation turns, a kickoff-call transcript, or an uploaded
JD, the agent extracts/updates the full RoleBrief (job profile + criteria + the
agent-context layers) using forced tool-use structured output through the METERED
Anthropic client (so every call is billed + logged). The pure pieces
(build_intake_messages, apply_extraction) are unit-tested without an LLM; the
single LLM call goes through app.llm.structured.generate_structured.
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..llm.core import MeteringContext
from ..llm.structured import generate_structured
from ..models.role_brief import RoleBrief
from ..platform.config import settings
from .claude_client_resolver import get_metered_client
from .role_brief_service import update_brief_fields

_INTAKE_FEATURE = "requisition_intake"
_MAX_TOKENS = 4000


class WeightedPriority(BaseModel):
    factor: str
    weight: Optional[str] = None  # high | medium | low


class CalibrationExemplar(BaseModel):
    kind: str  # good | bad
    description: str


class SourcingSignals(BaseModel):
    companies: Optional[list[str]] = None
    industries: Optional[list[str]] = None
    titles: Optional[list[str]] = None


class HiringProcess(BaseModel):
    rounds: Optional[int] = None
    autonomy_level: Optional[str] = None  # how much the agent may automate
    urgency: Optional[str] = None


class BriefExtraction(BaseModel):
    """The structured hiring brief the agent extracts. All optional so a partial
    conversation yields a partial fill; later turns refine it."""

    # Job profile
    title: Optional[str] = None
    summary: Optional[str] = None
    department: Optional[str] = None
    location_city: Optional[str] = None
    location_country: Optional[str] = None
    workplace_type: Optional[str] = None
    employment_type: Optional[str] = None
    seniority: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    openings: Optional[int] = None
    target_start: Optional[str] = None
    # Criteria
    must_haves: Optional[list[str]] = None
    preferred: Optional[list[str]] = None
    dealbreakers: Optional[list[str]] = None
    # Agent-context layers
    success_profile: Optional[str] = None
    priorities: Optional[list[WeightedPriority]] = None
    tradeoffs: Optional[list[str]] = None
    calibration_exemplars: Optional[list[CalibrationExemplar]] = None
    sourcing_signals: Optional[SourcingSignals] = None
    assessment_focus: Optional[list[str]] = None
    process: Optional[HiringProcess] = None
    evp: Optional[list[str]] = None
    # Intake meta
    open_questions: Optional[list[str]] = None  # high-value gaps to ask the HM next
    completeness: Optional[int] = None  # 0..100 coverage estimate


_SYSTEM_PROMPT = (
    "You are Taali's hiring-intake agent. From the hiring manager's words (a "
    "transcript, chat, or pasted JD), extract a structured hiring brief.\n\n"
    "Capture not just the job spec (title, location, comp, must-haves) but the "
    "REASONING a recruiter would otherwise lose: what 'great' looks like, the "
    "weighted priorities and trade-offs ('hunger > years'), hard dealbreakers, "
    "calibration examples ('someone like X; we passed on Y because Z'), where good "
    "candidates come from, what to actually assess, and how the process should run. "
    "This brief is read by downstream scoring/screening/decision agents, so be "
    "specific and faithful — never invent facts the input doesn't support; leave a "
    "field null if unknown and add it to open_questions if it's important. List only "
    "the few HIGH-VALUE questions still worth asking. Estimate completeness 0-100."
)


def build_intake_messages(brief: RoleBrief, new_input: str) -> tuple[str, list[dict]]:
    """Pure: build (system, messages) for one extraction pass. Includes the
    current brief so the model refines rather than overwrites."""
    current = {
        k: getattr(brief, k)
        for k in (
            "title", "summary", "department", "location_city", "location_country",
            "workplace_type", "employment_type", "seniority", "salary_min",
            "salary_max", "salary_currency", "salary_period", "openings",
            "target_start", "must_haves", "preferred", "dealbreakers",
            "success_profile", "priorities", "tradeoffs", "calibration_exemplars",
            "sourcing_signals", "assessment_focus", "process", "evp",
        )
        if getattr(brief, k) is not None
    }
    user = (
        "CURRENT BRIEF (may be empty or partial — refine, don't discard):\n"
        + json.dumps(current, indent=2, default=str)
        + "\n\nNEW INPUT FROM THE HIRING MANAGER:\n"
        + (new_input or "").strip()
        + "\n\nExtract/update the structured hiring brief from all available signal."
    )
    return _SYSTEM_PROMPT, [{"role": "user", "content": user}]


def apply_extraction(db: Session, brief: RoleBrief, extraction: BriefExtraction) -> RoleBrief:
    """Pure-ish: fold an extraction into the brief (open_questions -> agent_state).
    Only sets fields the extraction provided (exclude_none)."""
    data = extraction.model_dump(exclude_none=True)
    open_questions = data.pop("open_questions", None)
    if open_questions is not None:
        state = dict(brief.agent_state or {})
        state["open_questions"] = open_questions
        data["agent_state"] = state
    if data:
        update_brief_fields(db, brief, **data)
    return brief


def run_intake_extraction(
    db: Session,
    brief: RoleBrief,
    new_input: str,
    *,
    source_kind: Optional[str] = None,
    client=None,
    model: Optional[str] = None,
):
    """Run one extraction pass over ``new_input`` and fold it into the brief.
    Returns the StructuredResult (``.ok`` / ``.value`` / ``.error_reason``)."""
    if source_kind and not brief.source_kind:
        update_brief_fields(db, brief, source_kind=source_kind)
    if not brief.raw_input and new_input:
        update_brief_fields(db, brief, raw_input=new_input)
    if client is None:
        client = get_metered_client(organization_id=brief.organization_id)
    resolved_model = model or settings.resolved_claude_model
    system, messages = build_intake_messages(brief, new_input)
    result = generate_structured(
        client,
        model=resolved_model,
        system=system,
        messages=messages,
        output_model=BriefExtraction,
        metering=MeteringContext(
            feature=_INTAKE_FEATURE,
            organization_id=brief.organization_id,
            role_id=brief.role_id,
            entity_id=f"role_brief:{brief.id}",
        ),
        max_tokens=_MAX_TOKENS,
        temperature=0.0,
        use_tool_use=True,
    )
    if result.ok and result.value is not None:
        apply_extraction(db, brief, result.value)
    return result
