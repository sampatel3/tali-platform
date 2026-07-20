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
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED
from ..platform.config import settings
from .interview_tech_prompt import generate_tech_questions
from .role_provider_generation import (
    capture_role_provider_generation,
    lock_and_check_role_provider_generation,
)

logger = logging.getLogger("taali.role_tech_questions")


class RoleTechQuestionGenerationSuperseded(RuntimeError):
    """The output belongs to role inputs or authority that are no longer live."""

    def __init__(self, *, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def compute_signature(role: Role) -> str:
    """Hash the role inputs that determine the generated questions.

    Includes: job_spec_text, every non-derived criterion's (text, bucket,
    priority). Derived criteria are excluded — they come from
    the model itself, not the recruiter, so changing them shouldn't
    invalidate the cache (would cause an infinite regen loop).

    The signature is stored alongside the cache; when it differs from
    the live computed value, the cache is stale.
    """
    chips = (
        (
            (criterion.text or "").strip(),
            criterion.bucket or "",
            bool(criterion.must_have),
        )
        for criterion in (role.criteria or [])
        if criterion.deleted_at is None
        and criterion.source != CRITERION_SOURCE_DERIVED
    )
    return _compute_signature((role.job_spec_text or "").strip(), chips)


def _compute_signature(
    job_spec_text: str,
    criteria: Iterable[tuple[str, str, bool]],
) -> str:
    """Preserve the deployed cache-key format while accepting fresh DB rows."""

    chip_lines = sorted(
        f"{text}|{bucket}|{'M' if must_have else 'P'}"
        for text, bucket, must_have in criteria
    )
    payload = "\n".join([(job_spec_text or "").strip(), *chip_lines]).encode(
        "utf-8"
    )
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
    requires_running_agent: bool = False,
    raise_on_superseded: bool = False,
) -> Optional[list[dict[str, Any]]]:
    """Return the cached question payload, regenerating it via the LLM
    when the cache is missing or stale (or ``force=True``).

    Returns ``None`` when the role lacks a job spec, the Anthropic key
    isn't configured, or the generation itself failed — caller should
    fall back to the deterministic template.

    ``requires_running_agent`` rechecks live role authority at the write
    boundary. When ``raise_on_superseded`` is true, an input or authority
    change raises :class:`RoleTechQuestionGenerationSuperseded` so an
    automation caller cannot mark the newer generation as recovered.

    Side effect on success: updates ``role.tech_questions_cached``,
    ``role.tech_questions_cached_at``, ``role.tech_questions_signature``.
    Commits via the caller's session.
    """
    if role is None or role.id is None or role.organization_id is None:
        return None
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role.id),
            Role.organization_id == int(role.organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if role is None:
        return None
    expected = capture_role_provider_generation(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    if expected is None or not expected.job_spec_text:
        return None
    if not settings.ANTHROPIC_API_KEY:
        return None

    live_sig = _compute_signature(
        expected.job_spec_text,
        expected.recruiter_criteria,
    )
    cached = role.tech_questions_cached
    cached_sig = role.tech_questions_signature
    if not force and isinstance(cached, list) and cached and cached_sig == live_sig:
        fence = lock_and_check_role_provider_generation(
            db,
            expected=expected,
            requires_running_agent=requires_running_agent,
        )
        if not fence.current:
            if raise_on_superseded:
                raise RoleTechQuestionGenerationSuperseded(
                    reason=str(fence.reason or "role_inputs_changed"),
                    detail=fence.detail,
                )
            live_cached = (
                fence.role.tech_questions_cached if fence.role is not None else None
            )
            return live_cached if isinstance(live_cached, list) else None
        live_role = fence.role
        assert live_role is not None
        live_cached = live_role.tech_questions_cached
        if (
            isinstance(live_cached, list)
            and live_cached
            and live_role.tech_questions_signature == live_sig
        ):
            return live_cached
        # Another request invalidated the cache without changing the prompt
        # inputs (for example, an explicit force-regenerate). Do not hold the
        # role lock across a fresh provider call; leave its recovery marker due.
        return live_cached if isinstance(live_cached, list) else None

    try:
        questions = generate_tech_questions(
            job_spec_text=expected.job_spec_text,
            recruiter_requirements=expected.recruiter_requirements or None,
            # Role-level cache: drop every per-candidate input. The
            # prompt was built to handle these as optional, and the
            # questions become role-wide as a result.
            requirements_assessment=None,
            transcript_text=None,
            recruiter_notes=None,
            pre_screen_evidence=None,
            metering={
                "feature": "interview_tech",
                "organization_id": int(expected.organization_id),
                "role_id": int(expected.role_id),
                "entity_id": f"role:{expected.role_id}",
                "trace_id": f"interview-tech:role:{expected.role_id}:{live_sig}",
            },
        )
    except Exception:
        questions = None
        logger.exception("role_tech_questions: LLM call failed for role_id=%s", role.id)

    fence = lock_and_check_role_provider_generation(
        db,
        expected=expected,
        requires_running_agent=requires_running_agent,
    )
    if not fence.current:
        if raise_on_superseded:
            raise RoleTechQuestionGenerationSuperseded(
                reason=str(fence.reason or "role_inputs_changed"),
                detail=fence.detail,
            )
        live_cached = (
            fence.role.tech_questions_cached if fence.role is not None else None
        )
        return live_cached if isinstance(live_cached, list) else None

    role = fence.role
    assert role is not None
    cached = role.tech_questions_cached

    if not isinstance(questions, list) or not questions:
        # Generator returns None / empty on its own validation failures —
        # keep the previous cache if we have one rather than nulling it.
        # A non-null signature from an older generation must not make the
        # activation sweep report that fallback as current.
        if role.tech_questions_signature != live_sig:
            role.tech_questions_signature = None
            db.add(role)
        return cached if isinstance(cached, list) else None

    role.tech_questions_cached = questions
    role.tech_questions_cached_at = datetime.now(timezone.utc)
    role.tech_questions_signature = live_sig
    db.add(role)
    return questions


__all__ = [
    "RoleTechQuestionGenerationSuperseded",
    "compute_signature",
    "get_or_regenerate",
    "invalidate",
]
