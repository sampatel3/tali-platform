"""LLM helper: turn a recruiter's free-text intent answer into chip tuples.

Used by the answer-writeback path in ``actions.ask_recruiter`` when the
recruiter resolves ``intent_slot_missing`` / ``intent_clarification`` with
free prose. Returns a small list of ``(bucket, text)`` pairs that the
caller writes to ``role_criteria`` so the answer shows up on the Agent
settings tab as structured chips (in addition to landing as
``RoleIntent.free_text`` for prompt rendering).

Failure mode: best-effort. If the LLM call fails or returns garbage we
return ``[]`` and the caller still persists the free text — the recruiter
just doesn't get chips. Never raises.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..components.integrations.claude.model_fallback import (
    candidate_models_for,
    is_model_not_found_error,
)
from ..models.org_criterion import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
)
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from .claude_client_resolver import get_client_for_org
from .pricing_service import Feature


logger = logging.getLogger("taali.intent_chip_parser")


_BUCKETS = (BUCKET_MUST, BUCKET_PREFERRED, BUCKET_CONSTRAINT)


@dataclass(frozen=True)
class ParsedChip:
    bucket: str
    text: str


SYSTEM_PROMPT = (
    "You convert a recruiter's free-text answer about a role's must-haves "
    "and constraints into a short list of atomic chips. Each chip belongs "
    "to one of three buckets:\n"
    "  - must: hard requirements (years of experience, specific skills the "
    "candidate cannot succeed without)\n"
    "  - preferred: signals that would help but are not required\n"
    "  - constraint: location, eligibility, time-zone, budget, work-auth, "
    "remote/hybrid policy, etc.\n"
    "Return ONLY valid JSON. No commentary."
)


_OUTPUT_INSTRUCTIONS = (
    "Output JSON shape:\n"
    "{\n"
    '  "chips": [\n'
    '    {"bucket": "must", "text": "5+ years backend Python"},\n'
    '    {"bucket": "constraint", "text": "US time zones"}\n'
    "  ]\n"
    "}\n"
    "Rules:\n"
    "- One concept per chip — split compound sentences.\n"
    "- Keep each chip text short (under 80 chars) and verbatim where possible.\n"
    "- Do NOT invent requirements the recruiter didn't mention.\n"
    "- If the agent's question hints at the bucket (e.g. \"what are the "
    "must-haves\"), bias toward that bucket.\n"
    "- 1-8 chips total. Omit fluff (\"sounds great\", \"see attached\").\n"
    "- Skip chips that duplicate something in EXISTING_CHIPS (case-insensitive)."
)


def parse_intent_text_to_chips(
    db: Session,
    *,
    organization_id: int,
    role: Role,
    answer_text: str,
    agent_question: Optional[str] = None,
    existing_chip_texts: Optional[list[str]] = None,
) -> list[ParsedChip]:
    """Best-effort: parse free text into chips. Never raises."""
    text = (answer_text or "").strip()
    if not text:
        return []

    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .one_or_none()
    )
    if org is None:
        return []

    try:
        client = get_client_for_org(org)
    except Exception as exc:
        logger.warning("intent_chip_parser client init failed: %s", exc)
        return []

    model_version = (
        (getattr(role, "agent_model", None) or "").strip()
        or settings.resolved_claude_scoring_model
    )

    user_message = _build_user_message(
        role_name=str(role.name or ""),
        agent_question=agent_question,
        answer_text=text,
        existing_chip_texts=existing_chip_texts or [],
    )

    # The configured scoring model can resolve to a retired alias (e.g.
    # `claude-3-5-haiku-latest`) on some orgs. Mirror interview_focus's
    # fallback chain so a stale config doesn't silently drop chip parsing.
    last_exc: Exception | None = None
    response = None
    for candidate_model in candidate_models_for(model_version):
        try:
            response = client.messages.create(
                model=candidate_model,
                max_tokens=512,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                # The metered Anthropic wrapper auto-writes a usage_event
                # from response.usage. Use Feature.OTHER (a custom string
                # would raise inside record_event's Feature() conversion)
                # and tag the sub-agent in metadata for attribution.
                metering={
                    "feature": Feature.OTHER,
                    "organization_id": int(organization_id),
                    "role_id": int(role.id),
                    "metadata": {"sub_agent": "intent_chip_parser"},
                    "db": db,
                },
            )
            if candidate_model != model_version:
                logger.warning(
                    "intent_chip_parser fell back to model=%s after primary=%s was unavailable",
                    candidate_model,
                    model_version,
                )
            break
        except Exception as exc:
            if is_model_not_found_error(exc):
                last_exc = exc
                continue
            logger.warning("intent_chip_parser Claude call failed: %s", exc)
            return []
    if response is None:
        logger.warning(
            "intent_chip_parser exhausted model fallbacks (last error: %s)",
            last_exc,
        )
        return []

    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        raw = ""

    return _parse_response(raw, existing_chip_texts=existing_chip_texts or [])


def _build_user_message(
    *,
    role_name: str,
    agent_question: Optional[str],
    answer_text: str,
    existing_chip_texts: list[str],
) -> str:
    parts: list[str] = [
        f"Role: {role_name or '(unnamed)'}",
    ]
    if agent_question:
        parts.append(f"AGENT_QUESTION:\n{agent_question.strip()[:600]}")
    parts.append(f"RECRUITER_ANSWER:\n{answer_text.strip()[:2000]}")
    if existing_chip_texts:
        sample = "\n".join(f"- {t}" for t in existing_chip_texts[:20])
        parts.append(f"EXISTING_CHIPS (do not duplicate):\n{sample}")
    parts.append(_OUTPUT_INSTRUCTIONS)
    return "\n\n".join(parts)


def _parse_response(
    raw: str, *, existing_chip_texts: list[str]
) -> list[ParsedChip]:
    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        return []
    chips = payload.get("chips")
    if not isinstance(chips, list):
        return []

    seen_lower = {(t or "").strip().lower() for t in existing_chip_texts}
    out: list[ParsedChip] = []
    for entry in chips:
        if not isinstance(entry, dict):
            continue
        bucket = str(entry.get("bucket") or "").strip().lower()
        if bucket not in _BUCKETS:
            continue
        text = str(entry.get("text") or "").strip()
        if not text or len(text) > 200:
            continue
        key = text.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append(ParsedChip(bucket=bucket, text=text[:120]))
        if len(out) >= 8:
            break
    return out


def _extract_json(raw: str) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


__all__ = ["ParsedChip", "parse_intent_text_to_chips"]
