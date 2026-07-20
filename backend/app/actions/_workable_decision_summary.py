"""Best-effort ATS side effects for resolved movement decisions.

Two responsibilities, both invoked from ``approve_decision`` /
``override_decision`` after the underlying action has already succeeded:

1. ``try_workable_advance`` — move the candidate in Workable to the explicit
   recruiter-picked ``target_stage``. Autonomous/system advances fall back to
   the org's configured ``interview_stage_name``; without either, write-back is
   skipped and the local pipeline remains authoritative.

2. ``post_decision_summary_to_workable`` — post a short activity-feed note
   ("TAALI · Candidate advanced") on every resolved movement decision so the
   ATS has a one-glance audit trail with the role, decision provenance and
   score that actually drove the decision.

Both are best-effort: failures are recorded as application events and
returned as booleans / no-ops, never raised. The caller has already
committed the actual stage / outcome change before this fires.

Movement summaries distinguish recruiter resolutions from automatic policy
actions. Stage write-back supports both actor types.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    is_post_handover_workable_stage,
)
from ..domains.integrations_notifications.adapters import build_workable_adapter
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.user import User
from ..platform.config import settings
from ..services.workable_stage_matching import same_workable_stage
from .types import ACTOR_RECRUITER, Actor

logger = logging.getLogger("taali.actions.workable_decision_summary")

_NOTE_BODY_CAP = 1200  # Workable accepts more, but keep the activity feed legible.


def _workable_writeback_ready(
    *,
    app: CandidateApplication,
    org: Optional[Organization],
) -> bool:
    if settings.MVP_DISABLE_WORKABLE:
        return False
    if not getattr(app, "workable_candidate_id", None):
        return False
    if org is None:
        return False
    from ..services.workable_actions_service import workable_writeback_enabled

    if not workable_writeback_enabled(org):
        return False
    return bool(
        getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    )


def _try_bullhorn_advance(
    db: Session,
    actor: Actor,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    reason: Optional[str],
) -> Optional[bool]:
    """Advance via the Bullhorn provider when the org routes to Bullhorn.

    Returns ``None`` when this is NOT a Bullhorn org (caller falls through to the
    Workable path); ``True``/``False`` when Bullhorn handled it (write ok / failed).
    Records a ``bullhorn_moved`` / ``bullhorn_writeback_failed`` event and stamps
    ``bullhorn_status_local_write_at`` (inside the provider). Honours strict mode:
    the provider raises ``WorkableWritebackError`` on failure so the batch aborts +
    re-queues, identical to the Workable move.
    """
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider
    from ..services.workable_actions_service import WorkableWritebackError

    provider = resolve_application_ats_provider(org, db, app)
    if not isinstance(provider, BullhornProvider):
        return None
    submission_id = (getattr(app, "bullhorn_job_submission_id", "") or "").strip()
    if not submission_id:
        return False  # Bullhorn org but unlinked application — nothing to move.
    try:
        result = provider.move_application(
            candidate_id=submission_id, target_stage="advanced", role=getattr(app, "role", None)
        )
    except WorkableWritebackError:
        raise  # strict (batch) path — propagate so the batch re-queues.
    except Exception as exc:  # pragma: no cover — defensive/provider boundary
        error_type = type(exc).__name__
        logger.error(
            "bullhorn advance raised unexpectedly application_id=%s error_type=%s",
            app.id,
            error_type,
        )
        # An unknown provider outcome is never safe to treat as a completed
        # advance.  The shared decision runner catches this typed error, retries
        # it, and returns the decision to the queue if Bullhorn stays unavailable.
        raise WorkableWritebackError(
            action="move",
            code="unexpected",
            message=f"Unexpected Bullhorn move failure ({error_type})",
            retriable=True,
        ) from None
    if result.get("skipped") or result.get("code") == "already_at_target":
        # Bullhorn already reflects the requested status. No provider movement
        # occurred, so do not emit a movement event or ATS summary.
        return False
    if not result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_writeback_failed",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=result.get("message") or "Bullhorn move failed",
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "bullhorn_job_submission_id": submission_id,
                "source": "decision_summary",
            },
        )
        logger.warning(
            "bullhorn advance failed application_id=%s code=%s message=%s",
            app.id,
            result.get("code"),
            result.get("message"),
        )
        return False
    append_application_event(
        db,
        app=app,
        event_type="bullhorn_moved",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=reason or "Advanced by recruiter (decision resolution)",
        metadata={
            "bullhorn_status": result.get("config", {}).get("remote_status"),
            "bullhorn_job_submission_id": submission_id,
            "source": "decision_summary",
        },
    )
    return True


def try_workable_advance(
    db: Session,
    actor: Actor,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    role: Optional[Role],
    target_stage: Optional[str],
    reason: Optional[str] = None,
) -> bool:
    """Move the candidate in Workable to ``target_stage``.

    ``target_stage`` is normally the recruiter's pick. For an agent/system
    actor, an empty value falls back to ``workable_config.interview_stage_name``.
    An explicit target always wins. Returns True iff the move succeeded.
    """
    # Bullhorn-connected org → advance via the Bullhorn provider (writes the
    # org's advanced-mapped JobSubmission status). Same gating contract as the
    # Workable move (strict mode raises so a failed batch re-queues). A no-op for
    # non-Bullhorn orgs (returns None → fall through to the Workable path).
    bullhorn = _try_bullhorn_advance(db, actor, app=app, org=org, reason=reason)
    if bullhorn is not None:
        return bullhorn

    target = (target_stage or "").strip()
    if not target and actor.type in {"agent", "system"} and org is not None:
        from ..services.workable_actions_service import (
            resolve_workable_interview_stage,
        )

        target, _ = resolve_workable_interview_stage(org, role)
    if not target:
        return False
    if not _workable_writeback_ready(app=app, org=org):
        return False
    assert org is not None  # narrowed by _workable_writeback_ready

    # Workable may return a stage id while the recruiter submits its slug or
    # display name. Treat aliases of the same cached stage as an exact-target
    # no-op before the provider request, including custom stages that are not
    # recognized by Taali's legacy handover classifier.
    if same_workable_stage(role, getattr(app, "workable_stage", None), target):
        return False

    # No-op move guard: if the recruiter has already advanced the candidate
    # past Taali's handover point in Workable (interview/offer flow), the stage
    # move is redundant — Workable 422s a move to a stage they've already
    # passed, which under strict (batch) mode raises and re-queues the decision
    # forever. Skip the move and return False: no provider movement occurred,
    # so the caller must not post a fresh movement summary.
    if is_post_handover_workable_stage(getattr(app, "workable_stage", None)):
        append_application_event(
            db,
            app=app,
            event_type="workable_move_skipped",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=(
                f"Already in Workable stage '{app.workable_stage}' (past handover) — "
                "advance stage-move skipped as a no-op; no ATS message posted."
            ),
            metadata={
                "current_stage": app.workable_stage,
                "target_stage": target,
                "source": "decision_summary",
            },
        )
        return False

    from ..services.workable_actions_service import (
        WorkableWritebackError,
        move_candidate_in_workable,
    )

    try:
        result = move_candidate_in_workable(
            org=org,
            candidate_id=str(app.workable_candidate_id),
            target_stage=target,
            role=role,
        )
    except WorkableWritebackError:
        # strict mode (decision-dispatch path): propagate so the dispatch task
        # aborts + re-queues rather than committing a Taali-only stage change.
        raise
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable advance raised unexpectedly (application_id=%s)", app.id
        )
        return False

    config = result.get("config") or {}
    if result.get("skipped"):
        # Read-only mode: the write-back is a benign no-op, resolved in Taali
        # only. Don't stamp workable_stage or log a failure. (The
        # _workable_writeback_ready gate normally prevents reaching here.)
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_skipped",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason="read-only mode — resolved in Taali only",
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "workable_candidate_id": app.workable_candidate_id,
                "target_stage": target,
                "source": "decision_summary",
            },
        )
        return False
    if not result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=result.get("message") or "Workable move failed",
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "workable_candidate_id": app.workable_candidate_id,
                "target_stage": target,
                "source": "decision_summary",
            },
        )
        logger.warning(
            "workable advance failed application_id=%s code=%s message=%s",
            app.id,
            result.get("code"),
            result.get("message"),
        )
        return False

    app.workable_stage = target
    # Local-write-wins: stamp so the candidate sync won't revert this fresh move
    # with a stale snapshot still propagating in Workable.
    app.workable_stage_local_write_at = datetime.now(timezone.utc)
    append_application_event(
        db,
        app=app,
        event_type="workable_moved",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=reason or "Advanced by recruiter (decision resolution)",
        metadata={
            "target_stage": target,
            "workable_candidate_id": app.workable_candidate_id,
            "workable_actor_member_id": config.get("actor_member_id"),
            "source": "decision_summary",
        },
    )
    return True


_MOVEMENT_BY_VERDICT = {
    "advanced": "advanced",
    # This legacy override changes the ATS application in exactly the same way
    # as an advance. Assessment delivery has its own provider-success notes and
    # must not leak into this movement-only summary.
    "skip_advanced": "advanced",
    "rejected": "rejected",
}


def _format_score(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    display = str(int(number)) if number.is_integer() else f"{number:.1f}"
    return f"{display}/100"


def _truncate(text: str, *, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_score(*values) -> Optional[float]:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    return None


def _is_related_role_decision(
    decision: AgentDecision, app: CandidateApplication
) -> bool:
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    if evidence.get("shared_ats_application") or evidence.get("related_role_id"):
        return True
    try:
        return int(decision.role_id) != int(app.role_id)
    except (TypeError, ValueError):
        return False


def _decision_score_context(
    decision: AgentDecision,
    app: CandidateApplication,
    *,
    related_role: bool,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return ``(decision score, threshold, original-app score)``.

    Related-role decisions share the owner's ATS application but freeze their
    own causal score and threshold in ``AgentDecision.evidence``. Never replace
    that score with the owner's mutable application cache. For ordinary roles,
    prefer the score frozen for the policy stage and fall back to the canonical
    application cache only for legacy decisions without score evidence.
    """
    evidence = decision.evidence if isinstance(decision.evidence, dict) else {}
    if _has_assessment_provenance(evidence):
        # This boundary applies to related-role decisions too. Assessment
        # results, thresholds and any original-application comparison remain
        # inside Taali even when the resulting movement affects a shared ATS
        # application.
        return None, None, None

    threshold = _first_score(
        evidence.get("effective_threshold"), evidence.get("threshold_100")
    )
    if related_role:
        score = _first_score(
            evidence.get("taali_score"),
            evidence.get("assessment_score"),
            evidence.get("role_fit_score"),
        )
        original_score = _first_score(
            getattr(app, "taali_score_cache_100", None)
        )
        return score, threshold, original_score

    source = str(evidence.get("source") or "").strip().lower()
    if source == "pre_screen_threshold":
        score = _first_score(
            evidence.get("pre_screen_score_100"),
            evidence.get("pre_screen_score"),
        )
    else:
        score = _first_score(
            evidence.get("role_fit_score"),
            evidence.get("taali_score"),
            evidence.get("pre_screen_score_100"),
            evidence.get("pre_screen_score"),
        )
    if score is None:
        score = _first_score(getattr(app, "taali_score_cache_100", None))
    return score, threshold, None


_ASSESSMENT_PROVENANCE_KEYS = frozenset(
    {
        "assessment",
        "assessment_completed",
        "assessment_id",
        "assessment_result",
        "assessment_result_id",
        "assessment_result_url",
        "assessment_score",
        "assessment_score_100",
        "assessment_task",
        "assessment_task_id",
        "task_id",
    }
)
_ASSESSMENT_PROVENANCE_VALUE_RE = re.compile(
    r"\b(?:assessment|test|exercise)[-_ ]?(?:score|id|task|result)?\b|"
    r"\b(?:score|task)[-_ ]?result\b",
    re.IGNORECASE,
)


def _has_assessment_provenance(evidence: dict) -> bool:
    """Fail closed when score evidence came from an assessment workflow."""
    if str(evidence.get("decision_stage") or "").strip().casefold() == "assessment":
        return True

    def populated(value) -> bool:
        if value is None or value is False:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    def contains_provenance(mapping: dict) -> bool:
        for key, value in mapping.items():
            normalized = str(key or "").strip().casefold().replace("-", "_")
            if normalized in _ASSESSMENT_PROVENANCE_KEYS and populated(value):
                return True
            if normalized.startswith("assessment_") and any(
                token in normalized for token in ("score", "id", "task", "result")
            ) and populated(value):
                return True
            if normalized in {
                "assessment_provenance",
                "result_provenance",
                "result_source",
                "score_provenance",
                "score_source",
                "source",
            } and _ASSESSMENT_PROVENANCE_VALUE_RE.search(
                str(value or "").replace("_", " ").replace("-", " ")
            ):
                return True
            if isinstance(value, dict) and contains_provenance(value):
                return True
        return False

    return contains_provenance(evidence)


def _movement_headline(
    *,
    movement: str,
    actor: Actor,
    related_role: bool,
    overridden: bool,
) -> str:
    if overridden:
        suffix = f"Candidate {movement} — recommendation overridden"
    elif related_role:
        suffix = f"Candidate {movement} for a related role"
    elif actor.type != ACTOR_RECRUITER:
        suffix = f"Candidate {movement} automatically"
    else:
        suffix = f"Candidate {movement}"
    return f"TAALI · {suffix}"


_RECOMMENDATION_LABELS = {
    "advance_to_interview": "Advance",
    "reject": "Reject",
    "skip_assessment_reject": "Reject",
    # Assessment workflow remains internal to Taali. An ATS override summary
    # describes the prior intent without naming the lifecycle action.
    "send_assessment": "Continue in Taali",
    "resend_assessment_invite": "Continue in Taali",
}


def _recommendation_label(decision: AgentDecision) -> Optional[str]:
    return _RECOMMENDATION_LABELS.get(str(decision.decision_type or "").strip())


def _role_name_for(
    decision: AgentDecision,
    app: CandidateApplication,
    *,
    explicit_name: Optional[str],
) -> Optional[str]:
    name = (explicit_name or "").strip()
    if name:
        return name
    try:
        decision_role = getattr(decision, "role", None)
        name = str(getattr(decision_role, "name", None) or "").strip()
    except Exception:  # pragma: no cover - detached ORM defensive fallback
        name = ""
    if name:
        return name
    try:
        app_role = getattr(app, "role", None)
        name = str(getattr(app_role, "name", None) or "").strip()
    except Exception:  # pragma: no cover - detached ORM defensive fallback
        name = ""
    return name or None


def _movement_reason(
    *,
    movement: str,
    related_role: bool,
    overridden: bool,
    score: Optional[float],
    threshold: Optional[float],
) -> str:
    """Return concise public rationale without leaking internal reasoning.

    Persisted ``decision.reasoning`` may contain assessment lifecycle details,
    policy identifiers or model prose. The ATS needs only the reason for the
    movement; detailed evidence remains in Taali.
    """
    if overridden:
        if movement == "advanced":
            return (
                "The recruiter overrode the recommendation and approved the "
                "candidate for progression."
            )
        if movement == "rejected":
            return "The recruiter overrode the recommendation and rejected the candidate."

    threshold_label = "related-role threshold" if related_role else "role threshold"
    if movement == "advanced":
        if score is not None and threshold is not None and score >= threshold:
            return (
                f"The candidate met the {threshold_label} and was approved for "
                "progression."
            )
        return "The candidate was approved for progression."
    if movement == "rejected":
        if score is not None and threshold is not None and score < threshold:
            return f"The candidate did not meet the {threshold_label}."
        return "The candidate did not satisfy the role's progression policy."
    return "The candidate's ATS status was updated."


def compose_decision_summary_note(
    decision: AgentDecision,
    app: CandidateApplication,
    *,
    actor: Actor,
    verdict: str,
    override_action: Optional[str] = None,
    reason: Optional[str] = None,
    actor_name: Optional[str] = None,
    role_name: Optional[str] = None,
    moved_to: Optional[str] = None,
) -> str:
    """Build a provider-neutral ATS movement audit note.

    Layout stays short and deliberately excludes confidence, assessment
    details and report links; those belong in Taali's decision/report UI:

        TAALI · Candidate advanced
        Role: Backend Engineer
        Moved to: Final interview
        TAALI score used: 85/100
        Role threshold: 65/100
        Decision: Advanced by Sam Patel
        Reason: The candidate met the role threshold and was approved for progression.
    """
    movement = _MOVEMENT_BY_VERDICT.get(verdict)
    if movement is None:
        movement = verdict.replace("_", " ").strip().lower() or "updated"
    related_role = _is_related_role_decision(decision, app)
    lines = [
        _movement_headline(
            movement=movement,
            actor=actor,
            related_role=related_role,
            overridden=bool(override_action),
        )
    ]

    resolved_role_name = _role_name_for(
        decision, app, explicit_name=role_name
    )
    if resolved_role_name:
        lines.append(f"Role: {_truncate(resolved_role_name, limit=200)}")

    target = _truncate(moved_to or "", limit=160)
    if movement == "advanced" and target:
        lines.append(f"Moved to: {target}")

    score, threshold, original_score = _decision_score_context(
        decision, app, related_role=related_role
    )
    formatted_score = _format_score(score)
    formatted_threshold = _format_score(threshold)
    if formatted_score:
        score_label = "Related-role score used" if related_role else "TAALI score used"
        lines.append(f"{score_label}: {formatted_score}")
    if formatted_threshold:
        lines.append(f"Role threshold: {formatted_threshold}")
    formatted_original_score = _format_score(original_score)
    if related_role and formatted_original_score:
        lines.append(f"Original application score: {formatted_original_score}")

    movement_title = movement.title()
    resolved_actor_name = _truncate(actor_name or "", limit=160)
    if override_action:
        recommendation = _recommendation_label(decision)
        if recommendation:
            lines.append(f"TAALI recommendation: {recommendation}")
        lines.append(f"Final decision: {movement_title}")
        if actor.type == ACTOR_RECRUITER and resolved_actor_name:
            lines.append(f"Decision made by: {resolved_actor_name}")
        elif actor.type == ACTOR_RECRUITER:
            lines.append("Decision source: Recruiter")
        else:
            lines.append("Decision source: Taali automatic policy")
    elif actor.type == ACTOR_RECRUITER and resolved_actor_name:
        lines.append(f"Decision: {movement_title} by {resolved_actor_name}")
    elif actor.type == ACTOR_RECRUITER:
        lines.append("Decision source: Recruiter")
    else:
        lines.append("Decision source: Taali automatic policy")

    decision_reason = _movement_reason(
        movement=movement,
        related_role=related_role,
        overridden=bool(override_action),
        score=score,
        threshold=threshold,
    )
    lines.append(f"Reason: {decision_reason}")

    body = "\n".join(lines).strip()
    if len(body) > _NOTE_BODY_CAP:
        body = body[: _NOTE_BODY_CAP - 1].rstrip() + "…"
    return body


def post_decision_summary_to_workable(
    db: Session,
    actor: Actor,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    decision: AgentDecision,
    verdict: str,
    override_action: Optional[str] = None,
    reason: Optional[str] = None,
    moved_to: Optional[str] = None,
) -> bool:
    """Post the short decision-resolution note to the role's active ATS.

    The historical name is retained for callers, but Bullhorn-only orgs route
    through the same provider seam and receive the identical Taali audit note.
    Returns True iff the note was posted. Skips silently (False)
    when no ATS is connected or the application isn't linked; logs + records a
    provider-specific failure event when the API call itself errors.
    """
    # Bullhorn-only workspace: post through the shared provider. Workable wins
    # for the deliberate dual-connected migration edge (resolver precedence),
    # preserving the incumbent behavior for DeepLight and every existing org.
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider

    role = db.get(Role, int(decision.role_id)) if decision.role_id else None
    role_name = str(getattr(role, "name", None) or "").strip() or None
    # Use the exact value the composer will place on the ``Role:`` line as the
    # provenance exemption at the provider boundary. Legacy decisions can lack
    # a resolvable role id/name and fall back to the application's role.
    role_name = _role_name_for(decision, app, explicit_name=role_name)
    trusted_composed_role_name = _truncate(role_name or "", limit=200) or None
    actor_name: Optional[str] = None
    if actor.type == ACTOR_RECRUITER and actor.user_id is not None:
        recruiter = db.get(User, int(actor.user_id))
        actor_name = (
            str(getattr(recruiter, "full_name", None) or "").strip() or None
        )

    provider = resolve_application_ats_provider(org, db, app)
    if isinstance(provider, BullhornProvider):
        candidate = getattr(app, "candidate", None)
        candidate_id = str(
            getattr(candidate, "bullhorn_candidate_id", None) or ""
        ).strip()
        if not (
            getattr(app, "bullhorn_job_submission_id", None) and candidate_id
        ):
            return False
        body = compose_decision_summary_note(
            decision,
            app,
            actor=actor,
            verdict=verdict,
            override_action=override_action,
            reason=reason,
            actor_name=actor_name,
            role_name=role_name,
            moved_to=moved_to,
        )
        try:
            result = provider.post_note(
                candidate_id=candidate_id,
                member_id="",
                body=body,
                role=getattr(app, "role", None),
                trusted_role_values=(trusted_composed_role_name,)
                if trusted_composed_role_name
                else None,
            )
        except Exception as exc:  # pragma: no cover - defensive/provider outage
            error_type = type(exc).__name__
            result = {
                "success": False,
                "code": "api_error",
                "message": f"Bullhorn note provider failure ({error_type})",
            }
        if not result.get("success"):
            append_application_event(
                db,
                app=app,
                event_type="bullhorn_writeback_failed",
                actor_type=actor.type,
                actor_id=actor.event_actor_id,
                reason="decision-summary note post failed",
                metadata={
                    "decision_id": int(decision.id),
                    "verdict": verdict,
                    "override_action": override_action,
                    "code": str(result.get("code") or "api_error"),
                    "error": str(
                        result.get("message") or result.get("error") or ""
                    ),
                    "source": "decision_summary",
                    "ats": "bullhorn",
                },
            )
            logger.warning(
                "bullhorn decision-summary post failed application_id=%s "
                "decision_id=%s err=%s",
                app.id,
                decision.id,
                result.get("message") or result.get("error"),
            )
            return False
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_decision_note_posted",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=f"Decision resolution note posted to Bullhorn ({verdict})",
            metadata={
                "decision_id": int(decision.id),
                "verdict": verdict,
                "override_action": override_action,
                "body_preview": body[:240],
                "bullhorn_candidate_id": candidate_id,
            },
        )
        return True

    if not _workable_writeback_ready(app=app, org=org):
        return False
    assert org is not None  # narrowed above

    from ..services.workable_actions_service import resolve_workable_actor_member_id

    member_id = resolve_workable_actor_member_id(org, role=getattr(app, "role", None))
    if not member_id:
        return False

    body = compose_decision_summary_note(
        decision,
        app,
        actor=actor,
        verdict=verdict,
        override_action=override_action,
        reason=reason,
        actor_name=actor_name,
        role_name=role_name,
        moved_to=moved_to,
    )

    adapter = build_workable_adapter(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    try:
        result = adapter.post_candidate_comment(
            candidate_id=str(app.workable_candidate_id),
            member_id=member_id,
            body=body,
            trusted_role_values=(trusted_composed_role_name,)
            if trusted_composed_role_name
            else None,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable decision-summary note raised (application_id=%s)", app.id
        )
        return False

    if not result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason="decision-summary note post failed",
            metadata={
                "decision_id": int(decision.id),
                "verdict": verdict,
                "override_action": override_action,
                "error": str(result.get("error") or ""),
                "source": "decision_summary",
            },
        )
        logger.warning(
            "workable decision-summary post failed application_id=%s decision_id=%s err=%s",
            app.id,
            decision.id,
            result.get("error"),
        )
        return False

    append_application_event(
        db,
        app=app,
        event_type="workable_decision_note_posted",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=f"Decision resolution note posted to Workable ({verdict})",
        metadata={
            "decision_id": int(decision.id),
            "verdict": verdict,
            "override_action": override_action,
            "body_preview": body[:240],
        },
    )
    return True
