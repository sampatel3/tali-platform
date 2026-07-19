"""Role-level cache of AI-generated tech screening questions.

Replaces the per-candidate ``interview_tech_questions.maybe_generate_tech_questions``
path that fired one Anthropic call per CV scoring event (~302/day on
2026-05-21). Now: one set of questions per role, cached, regenerated
only when the inputs that produced it change (job_spec_text or any
recruiter criterion). Every candidate on the same role sees the same
screening questions — which matches how recruiters actually run
screening interviews.

Public API:
- ``compute_signature(role)`` — deterministic hash of the inputs
- ``get_or_regenerate(db, role)`` — read cache; regenerate if signature
  drifted or never been generated. Returns the question payload or None
  if generation failed (caller can fall back to the deterministic
  template).
- ``invalidate(role)`` — null the signature so the next read regenerates.
  Hooked from the existing job-spec-change / criteria-change paths.

Cost discipline: same prompt module the per-candidate path used
(``interview_tech_prompt``), but called with candidate-specific inputs
set to None. The prompt was already structured to handle missing
candidate context — we just lean on that branch.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED
from ..platform.config import settings
from .interview_tech_prompt import generate_tech_questions
from .provider_error_evidence import safe_provider_error_code
from .role_criteria_service import render_role_intent_block

logger = logging.getLogger("taali.role_tech_questions")


def compute_signature(role: Role) -> str:
    """Hash the role inputs that determine the generated questions.

    Includes: job_spec_text, every non-derived criterion's (id, text,
    bucket, priority). Derived criteria are excluded — they come from
    the model itself, not the recruiter, so changing them shouldn't
    invalidate the cache (would cause an infinite regen loop).

    The signature is stored alongside the cache; when it differs from
    the live computed value, the cache is stale.
    """
    parts: list[str] = []
    parts.append((role.job_spec_text or "").strip())
    # Order by content (text), not db id, so the signature is stable
    # across roles with the same recruiter-authored criteria (and so
    # delete-then-recreate of the same chip text doesn't churn the cache).
    chips = [
        c for c in (role.criteria or [])
        if c.deleted_at is None and c.source != CRITERION_SOURCE_DERIVED
    ]
    chip_lines = sorted(
        f"{(c.text or '').strip()}|{c.bucket or ''}|{'M' if c.must_have else 'P'}"
        for c in chips
    )
    parts.extend(chip_lines)
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def invalidate(role: Role) -> None:
    """Null the cache signature so the next ``get_or_regenerate`` call
    runs the LLM. Cache payload stays around as a fallback so the UI
    doesn't blank out while regeneration is in flight.

    Hooked from the existing role-intent-change paths
    (``mark_role_scores_stale`` and the criteria CRUD helper) so any
    invalidation that already happens for CV match scoring also
    invalidates the tech questions in lockstep.
    """
    role.tech_questions_signature = None


def get_or_regenerate(
    db: Session,
    role: Role,
    *,
    force: bool = False,
) -> Optional[list[dict[str, Any]]]:
    """Return the cached question payload, regenerating it via the LLM
    when the cache is missing or stale (or ``force=True``).

    Returns ``None`` when the role lacks a job spec, the Anthropic key
    isn't configured, or the generation itself failed — caller should
    fall back to the deterministic template.

    Side effect on success: updates ``role.tech_questions_cached``,
    ``role.tech_questions_cached_at``, ``role.tech_questions_signature``.
    Commits via the caller's session.
    """
    if role is None or not (role.job_spec_text or "").strip():
        return None
    if not settings.ANTHROPIC_API_KEY:
        return None

    live_sig = compute_signature(role)
    cached = role.tech_questions_cached
    cached_sig = role.tech_questions_signature
    if not force and isinstance(cached, list) and cached and cached_sig == live_sig:
        return cached

    try:
        questions = generate_tech_questions(
            job_spec_text=str(role.job_spec_text or "").strip(),
            recruiter_requirements=render_role_intent_block(role) or None,
            # Role-level cache: drop every per-candidate input. The
            # prompt was built to handle these as optional, and the
            # questions become role-wide as a result.
            requirements_assessment=None,
            transcript_text=None,
            recruiter_notes=None,
            pre_screen_evidence=None,
            metering={
                "feature": "interview_tech",
                "organization_id": getattr(role, "organization_id", None),
                "role_id": int(role.id),
                "entity_id": f"role:{role.id}",
                "trace_id": f"interview-tech:role:{role.id}:{live_sig}",
            },
        )
    except Exception as exc:
        logger.warning(
            "role_tech_questions: LLM call failed role_id=%s error_code=%s",
            role.id,
            safe_provider_error_code(exc, operation="role_tech_questions"),
        )
        return cached if isinstance(cached, list) else None

    if not isinstance(questions, list) or not questions:
        # Generator returns None / empty on its own validation failures —
        # keep the previous cache if we have one rather than nulling it.
        return cached if isinstance(cached, list) else None

    role.tech_questions_cached = questions
    role.tech_questions_cached_at = datetime.now(timezone.utc)
    role.tech_questions_signature = live_sig
    db.add(role)
    return questions
