"""Provider-free compatibility facade for the retired intent sub-agent.

The production policy evaluator has exactly four registered sub-agents.
Recruiter intent is captured as ``RoleIntent`` and consumed at score time, so
this module never registers itself, opens a database session, reads a cache, or
calls a model. Historical schema imports and deterministic JSON validation
remain available.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .base import SubAgent, SubAgentRequest, SubAgentResult


INTENT_PROMPT_VERSION = "intent.retired"
INTENT_PARSER_UNAVAILABLE = "intent_parser_retired"


class Constraint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    value: str = ""
    detail: str | None = None


class IntentDirectives(BaseModel):
    """Historical validated shape consumed by legacy policy payloads."""

    model_config = ConfigDict(extra="ignore")

    strictness_modifier: float = Field(default=0.0, ge=-1.0, le=1.0)
    must_skills: list[str] = Field(default_factory=list)
    disqualifying_signals: list[str] = Field(default_factory=list)
    soft_signals: list[str] = Field(default_factory=list)
    constraints_parsed: list[Constraint] = Field(default_factory=list)


def _parse_or_empty(raw: str) -> IntentDirectives:
    """Validate an already-produced payload without making a provider call."""

    text = (raw or "").strip()
    if text.startswith("```"):
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1:
            text = text[first : last + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return IntentDirectives()
    if not isinstance(parsed, dict):
        return IntentDirectives()
    try:
        return IntentDirectives.model_validate(parsed)
    except ValidationError:
        return IntentDirectives()


class IntentParserSubAgent:
    """Historical object shape whose execution is explicitly unavailable."""

    name = "intent_parser"

    def run(
        self, req: SubAgentRequest, *, db: object | None = None
    ) -> SubAgentResult:
        del req, db
        return SubAgentResult(
            sub_agent=self.name,
            ok=False,
            error=INTENT_PARSER_UNAVAILABLE,
        )


# Compatibility object only. Deliberately NOT passed to register_sub_agent.
INTENT_PARSER_SUB_AGENT: SubAgent = IntentParserSubAgent()


__all__ = [
    "Constraint",
    "INTENT_PARSER_SUB_AGENT",
    "INTENT_PARSER_UNAVAILABLE",
    "INTENT_PROMPT_VERSION",
    "IntentDirectives",
    "IntentParserSubAgent",
]
