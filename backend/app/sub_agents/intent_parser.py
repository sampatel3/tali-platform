"""Intent-parser — Workable-note slot extractor (DEPRECATED as a sub-agent).

DEPRECATION (single-version cleanup, May 2026)
----------------------------------------------
Per §2 of ``recruitment_system_architecture.md`` the canonical
sub-agent set is exactly five: pre_screen, cv_scoring, graph_priors,
task_selection, assessment_scoring. ``intent_parser`` is no longer
one of them and is **no longer auto-registered** in
``app.sub_agents.__init__``.

Why this module still exists:
  - The ``IntentDirectives`` Pydantic shape and the slot-extraction
    helper are consumed by ``decision_policy.intent.apply_intent_overrides``,
    which the rule engine still calls.
  - The Workable-note → directives translation is a useful internal
    helper that nothing on the new spec replaces.

Recruiter intent — the canonical surface — is captured manually as
``RoleIntent`` (Amendment A1) and read by every sub-agent at score
time via ``app.agent_runtime.role_intent.fetch_active_intent``.

Sunset target: when no caller depends on ``parsed_intent`` and the
engine's strictness-threshold overlay either moves into ``RoleIntent``
fields or is retired, this module can be deleted.

----------------------------------------------

Original docstring follows.

Recruiter intent is supplied (per spec) as four already-categorised
free-text slots:

  must_have, preferred, nice_to_have, constraints

The prompt's only job is to extract structured directives from within
each slot — *not* to re-categorise. Output is validated against
``IntentDirectives``; on parse failure the sub-agent returns ok=True
with empty directives so the engine degrades to "no overlay" cleanly.

Cache key: ``sha256(intent_json + role_id + prompt_version)``. Backed
by ``cv_score_cache`` rows (different prompt_version keeps it isolated
from CV-match cache rows). Bumping ``INTENT_PROMPT_VERSION``
invalidates cleanly.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from ..models.cv_score_cache import CvScoreCache
from ..models.role import Role
from ..models.role_criterion import RoleCriterion
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..services.claude_client_resolver import get_client_for_org
from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import register_sub_agent


logger = logging.getLogger("taali.sub_agents.intent_parser")


INTENT_PROMPT_VERSION = "intent.v1"


# ---------------------------------------------------------------------------
# Output schema (LLM-facing — extra=ignore so a stray helper field doesn't
# fail the whole parse)
# ---------------------------------------------------------------------------


class Constraint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str  # e.g. 'location', 'eligibility', 'budget', 'timezone', 'other'
    value: str = ""
    detail: str | None = None


class IntentDirectives(BaseModel):
    """Parsed shape returned by the LLM and consumed by the engine."""

    model_config = ConfigDict(extra="ignore")

    strictness_modifier: float = Field(default=0.0, ge=-1.0, le=1.0)
    must_skills: list[str] = Field(default_factory=list)
    disqualifying_signals: list[str] = Field(default_factory=list)
    soft_signals: list[str] = Field(default_factory=list)
    constraints_parsed: list[Constraint] = Field(default_factory=list)


_EMPTY_DIRECTIVES = IntentDirectives().model_dump()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You extract structured hiring directives from already-categorised "
    "recruiter notes. The four slots — must_have, preferred, nice_to_have, "
    "constraints — are supplied by the recruiter; do NOT re-categorise. "
    "For each slot, surface concrete skills/signals that downstream "
    "scoring code can match against. Respond ONLY with valid JSON."
)


def _build_user_message(slots: dict[str, str]) -> str:
    return (
        "Recruiter intent for this role (already categorised by the recruiter):\n\n"
        f"MUST-HAVE:\n{slots.get('must_have') or '(none provided)'}\n\n"
        f"PREFERRED:\n{slots.get('preferred') or '(none provided)'}\n\n"
        f"NICE-TO-HAVE:\n{slots.get('nice_to_have') or '(none provided)'}\n\n"
        f"CONSTRAINTS:\n{slots.get('constraints') or '(none provided)'}\n\n"
        "Return JSON of the form:\n"
        "{\n"
        '  "strictness_modifier": <float in [-1, 1] — overall tightness '
        "implied by the language; 0 if neutral>,\n"
        '  "must_skills": [<concrete skills/qualifications drawn from MUST-HAVE>],\n'
        '  "disqualifying_signals": [<short phrases from MUST-HAVE/CONSTRAINTS '
        "that should auto-reject if matched>],\n"
        '  "soft_signals": [<phrases from PREFERRED/NICE-TO-HAVE>],\n'
        '  "constraints_parsed": [\n'
        '    {"kind": "location|eligibility|budget|timezone|other", '
        '"value": "...", "detail": "..."}\n'
        "  ]\n"
        "}\n"
        "Skip any list that is empty rather than inventing entries. Do not paraphrase."
    )


# ---------------------------------------------------------------------------
# Cache (reuses cv_score_cache)
# ---------------------------------------------------------------------------


def _cache_key(slots: dict[str, str], *, role_id: int, model_version: str) -> str:
    payload = {
        "slots": {k: (slots.get(k) or "") for k in (
            "must_have", "preferred", "nice_to_have", "constraints"
        )},
        "role_id": int(role_id),
        "prompt_version": INTENT_PROMPT_VERSION,
        "model_version": model_version,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "intent:" + hashlib.sha256(blob).hexdigest()


def _cache_get(db: Session, cache_key: str) -> dict[str, Any] | None:
    row = db.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
    if row is None:
        return None
    result = row.result if isinstance(row.result, dict) else None
    if not result:
        return None
    try:
        row.hit_count = (row.hit_count or 0) + 1
        row.last_hit_at = datetime.now(timezone.utc)
        db.flush()
    except Exception:  # pragma: no cover — defensive
        pass
    return result


def _cache_set(
    db: Session,
    cache_key: str,
    *,
    payload: dict[str, Any],
    model_version: str,
) -> None:
    if db.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none() is not None:
        return
    row = CvScoreCache(
        cache_key=cache_key,
        prompt_version=INTENT_PROMPT_VERSION,
        model=model_version,
        score_100=None,
        result=payload,
        hit_count=0,
    )
    db.add(row)
    db.flush()


# ---------------------------------------------------------------------------
# Slot resolution from existing role data
# ---------------------------------------------------------------------------


def _slots_from_role(db: Session, role: Role) -> dict[str, str]:
    """Best-effort: project existing role criteria + extra requirements
    into the four slots. Phase 6 surfaces explicit slot fields in the
    Hub; until then we synthesise.
    """
    must_lines: list[str] = []
    preferred_lines: list[str] = []
    constraint_lines: list[str] = []
    rows = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role.id,
            RoleCriterion.deleted_at.is_(None),
        )
        .order_by(RoleCriterion.ordering.asc())
        .all()
    )
    for row in rows:
        text = (row.text or "").strip()
        if not text:
            continue
        if row.source == "recruiter_constraint":
            constraint_lines.append(text)
        elif row.must_have:
            must_lines.append(text)
        else:
            preferred_lines.append(text)
    additional = (role.additional_requirements or "").strip()
    if additional:
        preferred_lines.append(additional)
    return {
        "must_have": "\n".join(must_lines),
        "preferred": "\n".join(preferred_lines),
        "nice_to_have": "",
        "constraints": "\n".join(constraint_lines),
    }


# ---------------------------------------------------------------------------
# Sub-agent
# ---------------------------------------------------------------------------


class IntentParserSubAgent:
    name = "intent_parser"

    def run(
        self, req: SubAgentRequest, *, db: Session | None = None
    ) -> SubAgentResult:
        session = db or SessionLocal()
        owns = db is None
        try:
            return self._run(req, session)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("intent_parser sub-agent crashed")
            return SubAgentResult(
                sub_agent=self.name, ok=False, error=f"unexpected: {exc}"
            )
        finally:
            if owns:
                session.close()

    def _run(self, req: SubAgentRequest, db: Session) -> SubAgentResult:
        role = (
            db.query(Role)
            .filter(
                Role.id == req.role_id,
                Role.organization_id == req.organization_id,
            )
            .one_or_none()
        )
        if role is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"role {req.role_id} not found",
            )

        slots: dict[str, str] = req.extra.get("slots") or _slots_from_role(db, role)
        model_version = (
            (role.agent_model or "").strip() or settings.resolved_claude_model
        )
        cache_key = _cache_key(slots, role_id=int(role.id), model_version=model_version)

        if not req.skip_cache:
            cached = _cache_get(db, cache_key)
            if cached is not None:
                return SubAgentResult(
                    sub_agent=self.name,
                    ok=True,
                    output=cached,
                    confidence=1.0,
                    cache_hit=True,
                )

        # Empty slots → no need to call Claude.
        if not any((slots.get(k) or "").strip() for k in slots):
            return SubAgentResult(
                sub_agent=self.name,
                ok=True,
                output=dict(_EMPTY_DIRECTIVES),
                confidence=0.0,
                cache_hit=False,
            )

        from ..models.organization import Organization

        org = (
            db.query(Organization)
            .filter(Organization.id == role.organization_id)
            .one_or_none()
        )
        if org is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"organization {role.organization_id} not found",
            )

        try:
            client = get_client_for_org(org)
        except Exception as exc:
            logger.warning("intent_parser client init failed: %s", exc)
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"client_init_failed: {exc}",
            )

        try:
            response = client.messages.create(
                model=model_version,
                max_tokens=512,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": _build_user_message(slots)},
                ],
                metering={
                    **(req.metering_context or {}),
                    "feature": "intent_parser",
                },
            )
        except Exception as exc:
            logger.warning("intent_parser Claude call failed: %s", exc)
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error=f"claude_call_failed: {exc}",
            )

        # Metering is handled by the MeteredAnthropicClient wrapper off the
        # `metering={... "feature": "intent_parser"}` kwarg above — one UsageEvent
        # + the call_log wire-tap per call, now correctly attributed to
        # Feature.INTENT_PARSER. The explicit record_event that used to live here
        # was a SECOND write (under Feature.OTHER) — a double-count that also
        # polluted the OTHER bucket. Removed; the wrapper is the canonical path.
        try:
            raw = response.content[0].text  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            raw = ""

        directives = _parse_or_empty(raw)
        payload = directives.model_dump()
        try:
            _cache_set(
                db,
                cache_key,
                payload=payload,
                model_version=model_version,
            )
        except Exception:  # pragma: no cover — cache writes are best-effort
            logger.exception("intent_parser cache write failed")

        return SubAgentResult(
            sub_agent=self.name,
            ok=True,
            output=payload,
            confidence=0.9,
            cache_hit=False,
            tokens_used=in_tok + out_tok,
        )


def _parse_or_empty(raw: str) -> IntentDirectives:
    text = (raw or "").strip()
    if text.startswith("```"):
        # Strip code fences.
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


INTENT_PARSER_SUB_AGENT: SubAgent = IntentParserSubAgent()
register_sub_agent(INTENT_PARSER_SUB_AGENT)


__all__ = [
    "INTENT_PARSER_SUB_AGENT",
    "INTENT_PROMPT_VERSION",
    "IntentDirectives",
    "IntentParserSubAgent",
]
