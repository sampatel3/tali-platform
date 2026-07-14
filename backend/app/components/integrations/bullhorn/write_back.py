"""Bullhorn candidate write-back — the four ATS write operations via the client.

Structural analogue of ``services/workable_actions_service`` (its move / disqualify
/ revert / note helpers), but against Bullhorn's data model: the write target is a
``JobSubmission`` (``bullhorn_job_submission_id`` on the application) and its
free-text ``status`` string.

THE CONTRACT (build plan §6, line 89 — "op_runner resolves provider through the
PR-1 seam"):
* Each helper returns the SAME result dict shape the Workable helpers return —
  ``{success, action, code, message, config, response}`` — so the shared
  op_runner reads it identically.
* Under ``strict_workable_writes()`` (the decision-dispatch path turns it on) a
  failure RAISES the shared :class:`WorkableWritebackError` instead of returning a
  failure dict, exactly like the Workable helpers — this is the transport-agnostic
  gating exception the op_runner already keys its retry / requeue / surface logic
  off, so Bullhorn needs NO new op types and NO change to gated/ungated semantics.
* On a successful status write we set ``bullhorn_status``, the normalized
  external-stage fields, and ``bullhorn_status_local_write_at`` on the
  application (local-write-wins source), mirroring how the Workable move stamps
  ``workable_stage_local_write_at``.

REVERSE STAGE MAPPING — Taali intent → remote status, NEVER guessed
------------------------------------------------------------------
:mod:`stage_map` maps remote status → Taali stage (the read direction). Write-back
needs the reverse: a recruiter's Taali intent (advance / reject) → the org's own
free-text Bullhorn status string. We derive it ONLY from existing
:class:`AtsStageMap` rows for the org:
* reject → the row flagged ``is_reject`` (the org's rejected-category status, seeded
  from ``rejectedJobResponseStatus`` at connect).
* advance → the row whose ``taali_stage`` is ``advanced`` (seeded from the
  interview / confirmed categorization settings).
If no such row exists the status is UNMAPPED — we surface a typed
``needs_mapping`` failure (non-retriable) and write nothing, never inventing a
status string. This is the write-side twin of the sync's needs-mapping rule.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ....domains.assessments_runtime.pipeline_service import normalize_pipeline_key
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....services.document_service import sanitize_text_for_storage
from ....services.workable_actions_service import (
    WorkableWritebackError,
    _STRICT_WORKABLE_WRITES,
)
from ....models.ats_stage_map import AtsStageMap
from .errors import redact_exc
from .stage_map import ATS_BULLHORN
from .service import BullhornService

logger = logging.getLogger("taali.bullhorn.write_back")

# Taali advance intent resolves to the org's "advanced"-mapped Bullhorn status.
_ADVANCED_STAGE = "advanced"
# The categorization setting whose value Bullhorn designates as the org's
# placed/hired status — a terminal status the advance write must never target.
_CONFIRMED_PLACED_SETTING = "confirmedJobResponseStatus"
# Default Bullhorn Note ``action`` for a free-form recruiter note. Bullhorn's
# commentActionList is per-org free text; ``org.bullhorn_config['note_action']``
# overrides this when the org configured one (never guessed beyond this default).
_DEFAULT_NOTE_ACTION = "Other"


def _confirmed_placed_status(org: Organization) -> str | None:
    """The org's placed/hired Bullhorn status from the stored categorization, or None.

    Read from ``org.bullhorn_config`` (captured at connect). Returns None when the
    config isn't present — the advance resolver then falls back to id-ordering, so
    this is a purely additive safeguard, never a hard dependency.
    """
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    status = str(config.get(_CONFIRMED_PLACED_SETTING) or "").strip()
    return status or None


def _build_failure_result(
    *,
    action: str,
    code: str,
    message: str,
    config: dict[str, Any],
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a Workable-shaped failure dict, or raise under strict mode.

    Mirrors ``workable_actions_service._build_failure_result`` byte-for-byte in
    contract: ``api_error`` (a live 4xx/5xx from Bullhorn — including the
    server-side workflow-validation rejections some orgs enforce on a status
    write) is retriable; config / linkage / needs-mapping codes won't fix
    themselves and are non-retriable so the op surfaces terminally to the
    Decision Hub instead of looping.
    """
    if _STRICT_WORKABLE_WRITES.get():
        raise WorkableWritebackError(
            action=action,
            code=code,
            message=message,
            retriable=(code == "api_error"),
        )
    return {
        "success": False,
        "action": action,
        "code": code,
        "message": message,
        "config": config,
        "response": response or {},
    }


def _build_success_result(
    *,
    action: str,
    message: str,
    config: dict[str, Any],
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "action": action,
        "code": "ok",
        "message": message,
        "config": config,
        "response": response or {},
    }


# --- reverse stage mapping (Taali intent → remote status) --------------------


def _remote_status_for_reject(db: Session, org: Organization) -> str | None:
    """Resolve reject only from Bullhorn's canonical status or one unique row."""
    rows = (
        db.query(AtsStageMap)
        .filter(
            AtsStageMap.org_id == org.id,
            AtsStageMap.ats == ATS_BULLHORN,
            AtsStageMap.is_reject.is_(True),
        )
        .order_by(AtsStageMap.id.asc())
        .all()
    )
    configured = str(
        (
            org.bullhorn_config
            if isinstance(org.bullhorn_config, dict)
            else {}
        ).get("rejectedJobResponseStatus")
        or ""
    ).strip()
    if configured:
        matches = [row for row in rows if str(row.remote_status or "").strip() == configured]
        return configured if len(matches) == 1 else None
    if len(rows) != 1:
        return None
    return str(rows[0].remote_status or "").strip() or None


def _remote_status_for_advance(db: Session, org: Organization) -> str | None:
    """The org's advance-mapped Bullhorn status (``taali_stage='advanced'``), or None.

    Prefer a non-reject advanced row (the interview/confirmed categorization
    seeds) so we never resolve "advance" to a status that also means rejected.

    DISAMBIGUATION — advance means "move to interview", never "mark placed/hired".
    ``seed_stage_map_from_categorization`` maps BOTH the interviewScheduled AND
    the confirmed (placed/hired) categorization statuses to ``advanced`` for the
    READ direction (a placed candidate is past hand-off). But writing the placed
    status back to Bullhorn on a mere advance would fire the org's placement /
    billing / client-notification workflows. So the write target must
    deterministically prefer the interviewScheduled-seeded status and NEVER
    resolve to the confirmed/placed one:
    * Exclude the org's configured confirmed/placed status when it is known
      (``bullhorn_config['confirmedJobResponseStatus']``).
    * Prefer Bullhorn's stored ``interviewScheduledJobResponseStatus`` when it
      names one mapped row. Without that canonical setting, require exactly one
      eligible row; multiple candidates are needs-mapping and never oldest-wins.
    """
    placed_status = _confirmed_placed_status(org)
    query = db.query(AtsStageMap).filter(
        AtsStageMap.org_id == org.id,
        AtsStageMap.ats == ATS_BULLHORN,
        AtsStageMap.taali_stage == _ADVANCED_STAGE,
        AtsStageMap.is_reject.is_(False),
    )
    if placed_status:
        query = query.filter(AtsStageMap.remote_status != placed_status)
    rows = query.order_by(AtsStageMap.id.asc()).all()
    configured = str(
        (
            org.bullhorn_config
            if isinstance(org.bullhorn_config, dict)
            else {}
        ).get("interviewScheduledJobResponseStatus")
        or ""
    ).strip()
    if configured:
        matches = [row for row in rows if str(row.remote_status or "").strip() == configured]
        return configured if len(matches) == 1 else None
    if len(rows) != 1:
        return None
    return str(rows[0].remote_status or "").strip() or None


def _remote_status_for_pipeline_stage(
    db: Session, org: Organization, *, taali_stage: str
) -> str | None:
    """Resolve a non-reject Taali stage only when its mapping is unambiguous.

    Unlike the specially-seeded advance mapping, arbitrary stages such as
    ``invited`` have no Bullhorn categorization setting that establishes a
    safe precedence.  Exactly one row is therefore required; zero or multiple
    matches are both needs-mapping and no remote status is guessed.
    """
    rows = (
        db.query(AtsStageMap)
        .filter(
            AtsStageMap.org_id == org.id,
            AtsStageMap.ats == ATS_BULLHORN,
            AtsStageMap.taali_stage == taali_stage,
            AtsStageMap.is_reject.is_(False),
        )
        .order_by(AtsStageMap.id.asc())
        .limit(2)
        .all()
    )
    if len(rows) != 1:
        return None
    return (rows[0].remote_status or "").strip() or None


def resolve_remote_status(db: Session, org: Organization, *, taali_intent: str) -> str | None:
    """Map a Taali write intent to the org's Bullhorn status string (or None).

    ``taali_intent`` is a Taali stage/verb such as ``"invited"``,
    ``"advanced"`` (advance), or ``"rejected"`` (reject). ``None`` means
    UNMAPPED — the caller must surface needs-mapping and NOT guess. Any other
    intent is treated as unmapped rather than invented.
    """
    intent = (taali_intent or "").strip().lower()
    if intent in ("rejected", "reject"):
        return _remote_status_for_reject(db, org)
    if intent in (_ADVANCED_STAGE, "advance", "skip_advanced"):
        return _remote_status_for_advance(db, org)
    if intent in {"applied", "invited", "in_assessment", "review"}:
        return _remote_status_for_pipeline_stage(
            db, org, taali_stage=intent
        )
    return None


def resolved_write_targets(db: Session, org: Organization) -> dict[str, str | None]:
    """Exact provider write targets for UI/action preflight; null means HITL."""

    return {
        intent: resolve_remote_status(db, org, taali_intent=intent)
        for intent in ("invited", "in_assessment", "review", "advanced", "rejected")
    }


# --- application lookup + local-write stamp ----------------------------------


def _app_by_submission(db: Session, org: Organization, submission_id: str) -> CandidateApplication | None:
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.bullhorn_job_submission_id == str(submission_id),
        )
        .first()
    )


def _stamp_local_write(
    app: CandidateApplication | None, status: str, *, normalized_stage: str
) -> None:
    """Local-write-wins: record our own status + the write timestamp so a stale
    inbound event / sweep won't revert it (see ``local_write.py``)."""
    if app is None:
        return
    app.bullhorn_status = sanitize_text_for_storage(status) if status else None
    app.external_stage_raw = sanitize_text_for_storage(status) if status else None
    app.external_stage_normalized = normalize_pipeline_key(normalized_stage) or None
    app.bullhorn_status_local_write_at = datetime.now(timezone.utc)


def _note_action(org: Organization) -> str:
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    action = str(config.get("note_action") or "").strip()
    return action or _DEFAULT_NOTE_ACTION


# --- the four write operations -----------------------------------------------


def move_submission_status(
    db: Session,
    *,
    org: Organization,
    client: BullhornService,
    submission_id: str,
    taali_intent: str,
) -> dict[str, Any]:
    """Move a JobSubmission to the org's status for ``taali_intent`` (advance/reject).

    Reverse-maps the intent → remote status (never guessed), POSTs the status,
    stamps local-write on success. ``submission_id`` is the Bullhorn
    JobSubmission id (``bullhorn_job_submission_id``).
    """
    config: dict[str, Any] = {"ats": ATS_BULLHORN, "taali_intent": taali_intent}
    clean_submission_id = str(submission_id or "").strip()
    if not clean_submission_id:
        return _build_failure_result(
            action="move",
            code="missing_submission_id",
            message="Application is not linked to a Bullhorn JobSubmission",
            config=config,
        )
    remote_status = resolve_remote_status(db, org, taali_intent=taali_intent)
    if not remote_status:
        return _build_failure_result(
            action="move",
            code="needs_mapping",
            message=(
                f"No Bullhorn status is mapped for Taali intent '{taali_intent}' in this "
                "org — map it before writing back (never guessed)."
            ),
            config=config,
        )
    config["remote_status"] = remote_status
    try:
        response = client.update_job_submission_status(
            job_submission_id=clean_submission_id, status=remote_status
        )
    except Exception as exc:  # noqa: BLE001 — normalize to the shared failure contract
        logger.error(
            "Bullhorn status write failed submission_id=%s error=%s",
            clean_submission_id,
            redact_exc(exc),
        )
        return _build_failure_result(
            action="move",
            code="api_error",
            message="Bullhorn status write failed; automatic retry scheduled",
            config=config,
        )
    normalized_intent = str(taali_intent or "").strip().lower()
    normalized_stage = (
        "rejected"
        if normalized_intent in {"reject", "rejected"}
        else (
            normalized_intent
            if normalized_intent
            in {"applied", "invited", "in_assessment", "review", "advanced"}
            else "advanced"
        )
    )
    _stamp_local_write(
        _app_by_submission(db, org, clean_submission_id),
        remote_status,
        normalized_stage=normalized_stage,
    )
    return _build_success_result(
        action="move",
        message=f"JobSubmission moved to '{remote_status}' in Bullhorn",
        config=config,
        response=response if isinstance(response, dict) else {},
    )


def reject_submission(
    db: Session,
    *,
    org: Organization,
    client: BullhornService,
    submission_id: str,
) -> dict[str, Any]:
    """Reject a JobSubmission by writing the org's rejected-category status.

    The rejected status comes from the ``is_reject`` stage-map row (seeded from
    ``rejectedJobResponseStatus`` at connect); unmapped → needs-mapping failure.
    """
    return move_submission_status(
        db, org=org, client=client, submission_id=submission_id, taali_intent="rejected"
    )


def revert_submission(
    db: Session,
    *,
    org: Organization,
    client: BullhornService,
    submission_id: str,
    target_intent: str = _ADVANCED_STAGE,
) -> dict[str, Any]:
    """Re-open a previously-rejected JobSubmission.

    Bullhorn has no first-class "un-reject" — reverting means writing a
    non-reject status back. We map ``target_intent`` (default ``advanced``) to
    the org's status; unmapped → needs-mapping (never guessed).
    """
    return move_submission_status(
        db, org=org, client=client, submission_id=submission_id, taali_intent=target_intent
    )


def post_note(
    db: Session,
    *,
    org: Organization,
    client: BullhornService,
    candidate_id: str,
    body: str,
    job_order_id: str | int | None = None,
) -> dict[str, Any]:
    """Create a Bullhorn Note about a candidate (optionally linked to a job).

    ``candidate_id`` is the Bullhorn Candidate id (the note's ``personReference``);
    ``job_order_id`` links it to the role when known. ``action`` defaults to the
    org's configured ``note_action`` (else ``Other``).
    """
    config: dict[str, Any] = {"ats": ATS_BULLHORN}
    clean_candidate_id = str(candidate_id or "").strip()
    clean_body = str(body or "").strip()
    if not clean_candidate_id:
        return _build_failure_result(
            action="note",
            code="missing_candidate_id",
            message="Candidate is not linked to Bullhorn",
            config=config,
        )
    if not clean_body:
        return _build_failure_result(
            action="note", code="empty_body", message="Note body is empty", config=config
        )
    action = _note_action(org)
    config["note_action"] = action
    # Bullhorn's Note.comments is an HTML field: escape the recruiter's raw text
    # so angle brackets / ampersands render literally (never as markup), then
    # turn newlines into <br /> so multi-line notes keep their line breaks.
    html_body = html.escape(clean_body).replace("\n", "<br />")
    try:
        response = client.create_note(
            comments=html_body,
            person_reference_id=clean_candidate_id,
            job_order_id=str(job_order_id).strip() if job_order_id not in (None, "") else None,
            action=action,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Bullhorn note post failed candidate_id=%s error=%s",
            clean_candidate_id,
            redact_exc(exc),
        )
        return _build_failure_result(
            action="note",
            code="api_error",
            message="Bullhorn note post failed; automatic retry scheduled",
            config=config,
        )
    return _build_success_result(
        action="note",
        message="Note posted to Bullhorn",
        config=config,
        response=response if isinstance(response, dict) else {},
    )
