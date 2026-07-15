"""JobSubmission → Candidate + CandidateApplication upsert for the Bullhorn sync.

Mirrors Workable's ``_sync_candidate_for_role`` but against Bullhorn's data
model: a ``JobSubmission`` is the application object (candidate ↔ jobOrder), and
its free-text ``status`` maps to a Taali stage via :mod:`stage_map`.

Keys / dedup, consistent with the existing candidate dedup:
* Candidate keyed on ``(org_id, bullhorn_candidate_id)``, then email, then the
  normalized-phone fallback (all org-scoped) — the same ladder Workable uses.
* CandidateApplication is unique on ``(candidate_id, role_id)``; we also match a
  pre-existing row by ``bullhorn_job_submission_id`` and backfill it.

needs-mapping: a JobSubmission status with no :class:`AtsStageMap` row is NEVER
guessed. We store the raw status on ``bullhorn_status`` (so it's visible + shows
up in the needs-mapping list) and leave the application at the funnel top
(``applied``). Pre-handoff mappings still update Tali's evaluation stage; the
historically overloaded ``advanced`` mapping now updates the provider-neutral
``recruiter_stage`` instead (and reject mappings update the outcome).

Cost safety (hard rule): a freshly-created application enqueues scoring via the
SAME shared path Workable import uses (``on_application_created``), only while
the role agent is enabled, unpaused, and lifecycle-ready. The sticky star is
adoption/cadence metadata, not an execution grant. Re-syncs of an existing
application NEVER re-enqueue scoring, and nothing here dispatches paid
re-evaluation of a stale score.
"""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ....domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    normalize_pipeline_key,
    transition_outcome,
    transition_stage,
)
from ....domains.assessments_runtime.role_support import (
    is_resolved,
    refresh_application_score_cache,
)
from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....services.application_events import on_application_created
from ....cv_parsing.origins import CV_PARSE_ORIGIN_ATS_INGEST
from ....services.document_service import (
    extract_text,
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from ....services.pre_screening_service import refresh_pre_screening_fields
from ....services.recruiter_stage_service import (
    mark_external_stage_mapping_resolved,
    sync_from_external,
)
from ....services.job_page_lifecycle import role_allows_new_paid_ats_work
from ....services.s3_service import generate_s3_key, upload_bytes_to_s3
from . import stage_map as stage_map_mod
from .service import BullhornService

logger = logging.getLogger(__name__)

# Candidate fields we page for on a JobSubmission's nested candidate. Bullhorn
# returns only requested fields, so this list is the read contract.
CANDIDATE_FIELDS = "id,firstName,lastName,name,email,phone,mobile,occupation,address,dateLastModified"
# fileAttachment metadata fields we need to pick a resume.
FILE_ATTACHMENT_FIELDS = "id,name,type,contentType,dateAdded"

# Extensions the CV text-extractor understands (mirrors _store_candidate_resume).
_TEXT_EXTS = {"pdf", "docx", "txt"}
_PREVIEW_EXTS = {"pdf", "png", "jpg", "jpeg", "webp"}


def _phone_normalized(candidate_payload: dict) -> str | None:
    from ..workable.sync_service import _normalize_phone_for_match

    raw = candidate_payload.get("phone") or candidate_payload.get("mobile")
    return _normalize_phone_for_match(raw if isinstance(raw, str) else None)


def _candidate_email(candidate_payload: dict) -> str | None:
    value = candidate_payload.get("email")
    if isinstance(value, str) and "@" in value and "." in value:
        return value.strip().lower()
    return None


def _candidate_name(candidate_payload: dict, *, fallback: str) -> str:
    name = candidate_payload.get("name")
    if isinstance(name, str) and name.strip():
        return sanitize_text_for_storage(name.strip())
    first = str(candidate_payload.get("firstName") or "").strip()
    last = str(candidate_payload.get("lastName") or "").strip()
    joined = f"{first} {last}".strip()
    return sanitize_text_for_storage(joined or fallback)


def _submission_status(submission: dict) -> str:
    return str(submission.get("status") or "").strip()


def _submission_applied_at(submission: dict) -> datetime | None:
    """JobSubmission.dateAdded (epoch MILLISECONDS) → aware UTC datetime, or None.

    This is the remote-ATS applied date; we store it on
    ``CandidateApplication.workable_created_at`` so the applied-date decision
    surfaces have a real date for Bullhorn apps."""
    raw = submission.get("dateAdded")
    if raw in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _resolve_candidate(
    db: Session, org: Organization, bullhorn_candidate_id: str, candidate_payload: dict
) -> Candidate:
    """Find-or-create the Candidate using the id → email → phone dedup ladder."""
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org.id,
            Candidate.bullhorn_candidate_id == bullhorn_candidate_id,
        )
        .first()
    )
    email = _candidate_email(candidate_payload)
    if not candidate and email:
        candidate = (
            db.query(Candidate)
            .filter(Candidate.organization_id == org.id, Candidate.email == email)
            .first()
        )
    if not candidate:
        phone_key = _phone_normalized(candidate_payload)
        if phone_key:
            candidate = (
                db.query(Candidate)
                .filter(
                    Candidate.organization_id == org.id,
                    Candidate.phone_normalized == phone_key,
                )
                .first()
            )
    if not candidate:
        candidate = Candidate(
            organization_id=org.id,
            email=sanitize_text_for_storage(email) if email else None,
        )
        db.add(candidate)

    candidate.deleted_at = None
    if email:
        candidate.email = sanitize_text_for_storage(email)
    fallback = candidate.full_name or email or f"Bullhorn candidate {bullhorn_candidate_id}"
    candidate.full_name = _candidate_name(candidate_payload, fallback=fallback)
    occupation = candidate_payload.get("occupation")
    if isinstance(occupation, str) and occupation.strip():
        candidate.position = sanitize_text_for_storage(occupation.strip())
    phone = candidate_payload.get("phone") or candidate_payload.get("mobile")
    if isinstance(phone, str) and phone.strip():
        candidate.phone = sanitize_text_for_storage(phone.strip())
    candidate.phone_normalized = _phone_normalized(candidate_payload)
    candidate.bullhorn_candidate_id = sanitize_text_for_storage(bullhorn_candidate_id)
    candidate.bullhorn_data = sanitize_json_for_storage(candidate_payload)
    db.flush()
    return candidate


def _apply_stage_mapping(
    db: Session,
    org: Organization,
    *,
    app: CandidateApplication,
    remote_status: str,
    created: bool,
    now: datetime,
) -> None:
    """Sync downstream hiring stage/outcome; needs-mapping stays fail-closed.

    Always records the raw ``bullhorn_status`` so an unmapped status stays
    visible. A mapped status drives the provider-neutral recruiter-stage and
    outcome services. Pre-handoff mappings remain compatible, but ``advanced``
    is Tali's explicit evaluation handoff, not a synonym for a Bullhorn
    interview, placement, or rejection. Resolved rows still receive downstream
    status and outcome corrections while their Tali decision snapshot is frozen.
    """
    app.bullhorn_status = sanitize_text_for_storage(remote_status) if remote_status else None
    app.external_stage_raw = sanitize_text_for_storage(remote_status) if remote_status else None

    # Clear first so a newly-unmapped Bullhorn status cannot retain a stale
    # normalized stage from the previous sync.  Agent payloads treat
    # raw-without-normalized as needs_mapping and fail closed for automation.
    app.external_stage_normalized = None

    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}

    def _configured_status(key: str) -> str:
        return str(config.get(key) or "").strip()

    def _matches_configured(key: str) -> bool:
        configured = _configured_status(key)
        return bool(configured and configured.casefold() == remote_status.strip().casefold())

    is_configured_interview = _matches_configured("interviewScheduledJobResponseStatus")
    is_confirmed_placement = _matches_configured("confirmedJobResponseStatus")
    is_configured_reject = _matches_configured("rejectedJobResponseStatus")
    mapping = stage_map_mod.resolve_stage(db, org, remote_status)
    explicitly_categorized = (
        is_configured_interview or is_confirmed_placement or is_configured_reject
    )
    if mapping is not None:
        app.external_stage_normalized = normalize_pipeline_key(mapping.taali_stage)
    elif is_configured_interview or is_confirmed_placement:
        # Both categorization settings are post-evaluation milestones. Keep the
        # external normalized value compatible with the seeded stage-map row,
        # but never turn it into Tali's explicit evaluation handoff.
        app.external_stage_normalized = "advanced"
    elif is_configured_reject:
        app.external_stage_normalized = "rejected"

    if mapping is None and not explicitly_categorized:
        # needs-mapping: do NOT guess. A freshly-created row already sits at the
        # funnel top from the create defaults. Clear any previously synchronized
        # recruiter stage so the application cannot silently display stale truth;
        # the durable sync exception drives the mapping support surface.
        sync_from_external(
            db,
            app=app,
            raw_stage=remote_status,
            provider="bullhorn",
        )
        if created:
            logger.info(
                "Bullhorn status needs mapping org_id=%s app_id=%s status=%r — left at funnel top",
                org.id,
                app.id,
                remote_status,
            )
        return

    try:
        # Bullhorn's configured interview/confirmed statuses are authoritative
        # semantic categories even when their tenant labels are arbitrary.
        mapped_pipeline_stage = (
            normalize_pipeline_key(mapping.taali_stage)
            if mapping is not None
            else "advanced"
        )
        is_reject = bool((mapping and mapping.is_reject) or is_configured_reject)

        if (
            mapping is not None
            and mapped_pipeline_stage != "advanced"
            and not is_reject
            and not is_configured_interview
            and not is_confirmed_placement
            and not is_resolved(app)
        ):
            # Preserve configured pre-handoff evaluation mappings. Only the
            # overloaded ``advanced`` target moved to the hiring-stage axis.
            transition_stage(
                db,
                app=app,
                to_stage=mapped_pipeline_stage,
                source="sync",
                actor_type="sync",
                reason=f"Bullhorn status mapped: {remote_status}",
                metadata={"bullhorn_status": remote_status, "is_reject": False},
            )

        if is_confirmed_placement:
            sync_from_external(
                db,
                app=app,
                raw_stage=remote_status,
                provider="bullhorn",
                force_stage="hired",
            )
        elif is_configured_interview or mapped_pipeline_stage == "advanced":
            sync_from_external(
                db,
                app=app,
                raw_stage=remote_status,
                provider="bullhorn",
                force_stage="interviewing",
            )
        elif not is_reject:
            # A non-terminal AtsStageMap row is an explicit provider mapping.
            # Its Tali evaluation transition remains separate; the downstream
            # hiring axis truthfully stays in screening until handoff.
            sync_from_external(
                db,
                app=app,
                raw_stage=remote_status,
                provider="bullhorn",
                force_stage="screening",
            )
        else:
            mark_external_stage_mapping_resolved(
                app,
                provider="bullhorn",
                raw_stage=remote_status,
            )

        if is_reject and (app.application_outcome or "open").lower() != "rejected":
            transition_outcome(
                db,
                app=app,
                to_outcome="rejected",
                actor_type="sync",
                reason=f"Bullhorn status mapped as reject: {remote_status}",
                metadata={"bullhorn_status": remote_status},
            )
    except Exception as exc:  # pragma: no cover — never block the candidate sync
        logger.error(
            "Bullhorn stage mapping transition failed app_id=%s status=%r error_type=%s",
            app.id,
            remote_status,
            type(exc).__name__,
        )


def _store_resume(
    *,
    app: CandidateApplication,
    candidate: Candidate,
    filename: str,
    content: bytes,
    now: datetime,
) -> bool:
    """Persist CV bytes to object storage + extract text (mirrors Workable)."""
    if not content:
        return False
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in (_TEXT_EXTS | _PREVIEW_EXTS):
        return False
    extracted = sanitize_text_for_storage(extract_text(content, ext)) if ext in _TEXT_EXTS else ""
    if not extracted and ext not in _PREVIEW_EXTS:
        return False
    entity_id = app.id or candidate.id
    s3_key = generate_s3_key("cv", entity_id, filename)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_url = upload_bytes_to_s3(content, s3_key, content_type=content_type)
    if not file_url:
        logger.warning(
            "Skipping Bullhorn CV store for candidate=%s app=%s — object storage unavailable",
            candidate.id,
            app.id,
        )
        return False
    app.cv_file_url = file_url
    app.cv_filename = sanitize_text_for_storage(filename)
    app.cv_text = extracted
    app.cv_uploaded_at = now
    candidate.cv_file_url = file_url
    candidate.cv_filename = sanitize_text_for_storage(filename)
    candidate.cv_text = extracted
    candidate.cv_uploaded_at = now
    return True


def _fetch_and_store_cv(
    *,
    client: BullhornService,
    app: CandidateApplication,
    candidate: Candidate,
    bullhorn_candidate_id: str,
    now: datetime,
) -> None:
    """Loose-match a Resume-typed fileAttachment → bytes → the existing CV path.

    Fallback: if no attachment yields usable text, POST the first attachment's
    bytes to ``resume/convertToText``. Only runs when we don't already have a CV
    for this application.
    """
    if (app.cv_text or "").strip() or (app.cv_file_url or "").strip():
        return
    try:
        attachments = client.list_file_attachments(
            candidate_id=bullhorn_candidate_id, fields=FILE_ATTACHMENT_FIELDS
        )
    except Exception as exc:  # pragma: no cover — never block on a CV listing failure
        logger.error(
            "Bullhorn fileAttachments listing failed candidate=%s error_type=%s",
            bullhorn_candidate_id,
            type(exc).__name__,
        )
        return

    # Loose match: a "Resume"-typed attachment first, else any attachment with a
    # text-extractable extension.
    def _is_resume(meta: dict) -> bool:
        return "resume" in str(meta.get("type") or "").lower()

    def _ext_ok(meta: dict) -> bool:
        name = str(meta.get("name") or "")
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
        return ext in (_TEXT_EXTS | _PREVIEW_EXTS)

    resume_meta = next((a for a in attachments if _is_resume(a) and _ext_ok(a)), None)
    if resume_meta is None:
        resume_meta = next((a for a in attachments if _ext_ok(a)), None)
    if resume_meta is None:
        return

    file_id = resume_meta.get("id")
    filename = str(resume_meta.get("name") or f"resume-{file_id}")
    try:
        content = client.get_file_raw(candidate_id=bullhorn_candidate_id, file_id=file_id)
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Bullhorn CV download failed candidate=%s file=%s error_type=%s",
            bullhorn_candidate_id,
            file_id,
            type(exc).__name__,
        )
        return

    stored = _store_resume(
        app=app, candidate=candidate, filename=filename, content=content, now=now
    )
    if stored:
        return

    # Fallback: convertToText on the raw bytes (e.g. a doc extension we don't
    # text-extract locally). Stores just the text; no preview file.
    try:
        content_type = str(resume_meta.get("contentType") or "application/octet-stream")
        text = client.convert_resume_to_text(
            filename=filename, content=content, content_type=content_type
        )
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Bullhorn convertToText failed candidate=%s error_type=%s",
            bullhorn_candidate_id,
            type(exc).__name__,
        )
        return
    text = sanitize_text_for_storage(text or "")
    if text.strip():
        app.cv_text = text
        app.cv_filename = sanitize_text_for_storage(filename)
        app.cv_uploaded_at = now
        candidate.cv_text = text
        candidate.cv_filename = sanitize_text_for_storage(filename)
        candidate.cv_uploaded_at = now


def sync_submission(
    *,
    db: Session,
    org: Organization,
    role: Role,
    submission: dict,
    candidate_payload: dict,
    client: BullhornService,
    now: datetime,
) -> dict:
    """Upsert one JobSubmission → Candidate + CandidateApplication (+ CV, +scoring).

    ``submission`` is a JobSubmission record; ``candidate_payload`` is its
    resolved Candidate (the caller prefetches it). Returns per-entity counters.
    """
    counters = {"candidate_upserted": 0, "application_upserted": 0}
    bullhorn_candidate_id = str(
        (submission.get("candidate") or {}).get("id")
        or candidate_payload.get("id")
        or ""
    ).strip()
    submission_id = str(submission.get("id") or "").strip()
    if not bullhorn_candidate_id or not submission_id:
        return counters

    candidate = _resolve_candidate(db, org, bullhorn_candidate_id, candidate_payload)
    counters["candidate_upserted"] += 1

    # Application unique on (candidate_id, role_id). Also adopt a pre-existing row
    # linked only by the JobSubmission id (backfill), consistent with Workable.
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
        )
        .first()
    )
    if app is None:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.bullhorn_job_submission_id == submission_id,
            )
            .first()
        )

    remote_status = _submission_status(submission)
    created_application = False
    if app is None:
        # A fresh import always enters Taali at the top of the funnel. The remote
        # status is recorded separately (``bullhorn_status``) and mapped below;
        # ``applied`` is never inferred-forward from a mid-pipeline Bullhorn
        # status, matching the Workable arm's policy.
        app = CandidateApplication(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="applied",
            pipeline_stage_source="sync",
            pipeline_stage_updated_at=now,
            application_outcome="open",
            application_outcome_updated_at=now,
            version=1,
        )
        db.add(app)
        created_application = True

    # A sourced lead is deliberately pre-application and therefore unscored.
    # The first JobSubmission id is the durable signal that the person has now
    # applied in Bullhorn. Reuse the sourced row (the candidate/role uniqueness
    # constraint requires it), but move it through the audited engagement edge
    # before the normal intake-event tail below. The external id + stage event
    # are committed by the caller in the same transaction.
    sourced_application_engaged = bool(
        not created_application
        and not (app.bullhorn_job_submission_id or "").strip()
        and (app.pipeline_stage or "").strip().lower() == "sourced"
    )

    app.deleted_at = None
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = sanitize_text_for_storage(submission_id)
    # Remote-ATS applied date → workable_created_at (the shared applied-date
    # column read by the decision surfaces). Backfill legacy rows too, but never
    # overwrite an existing value.
    if app.workable_created_at is None:
        applied_at = _submission_applied_at(submission)
        if applied_at is not None:
            app.workable_created_at = applied_at
    ensure_pipeline_fields(app, source="sync" if created_application else "system")
    db.flush()
    if created_application:
        initialize_pipeline_event_if_missing(
            db, app=app, actor_type="sync", reason="Imported from Bullhorn"
        )
    elif sourced_application_engaged:
        transition_stage(
            db,
            app=app,
            to_stage="applied",
            source="system",
            actor_type="sync",
            reason="Sourced prospect engaged — application linked from Bullhorn",
            metadata={
                "provider": "bullhorn",
                "bullhorn_job_submission_id": submission_id,
            },
            idempotency_key=f"sourced_engaged:bullhorn:{submission_id}",
        )

    app.external_refs = sanitize_json_for_storage(
        {
            "bullhorn_candidate_id": bullhorn_candidate_id,
            "bullhorn_job_order_id": role.bullhorn_job_order_id,
            "bullhorn_job_submission_id": submission_id,
        }
    )
    from ....services.ats_writeback_state import (
        replace_sync_state_preserving_writeback,
    )

    replace_sync_state_preserving_writeback(
        app,
        {
            "last_sync_at": now.isoformat(),
            "sync_status": "success",
            "source": "bullhorn",
        },
    )
    app.last_synced_at = now

    # Map the remote status onto the downstream hiring axis. The Tali evaluation
    # stage is intentionally untouched (needs-mapping stays fail-closed).
    _apply_stage_mapping(
        db, org, app=app, remote_status=remote_status, created=created_application, now=now
    )

    # CV: only when the app is still active (a resolved row is frozen — no CV
    # refresh, matching Workable's freeze).
    if not is_resolved(app):
        _fetch_and_store_cv(
            client=client,
            app=app,
            candidate=candidate,
            bullhorn_candidate_id=bullhorn_candidate_id,
            now=now,
        )

    # Read-only score cache refresh (free); paid scoring only via the gated
    # create-branch enqueue below.
    if getattr(app, "score_cached_at", None) is None:
        refresh_application_score_cache(app, db=db)
    else:
        refresh_pre_screening_fields(app)

    # COST SAFETY: the sticky star records adoption/sync cadence; it is not a
    # runtime spend grant. Metadata continues to sync while paused/off, but only
    # a lifecycle-ready, enabled, unpaused agent may launch NEW paid CV parsing
    # or first-score work. Re-syncs never re-score, and existing queued work is
    # deliberately not cancelled by this ingest-time gate.
    paid_work_allowed = role_allows_new_paid_ats_work(role)
    auto_score = bool(
        (created_application or sourced_application_engaged) and paid_work_allowed
    )
    on_application_created(
        app,
        score=auto_score,
        allow_paid_work=paid_work_allowed,
        parse_origin=CV_PARSE_ORIGIN_ATS_INGEST,
    )

    # Related-role fan-out is part of the provider-neutral application ingest
    # outbox above, so it cannot race this transaction and recovers lost kicks.

    db.flush()
    counters["application_upserted"] += 1
    return counters
