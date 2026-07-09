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
(``applied``). A mapped status sets the Taali ``pipeline_stage`` (and, when the
mapping is a reject, the ``rejected`` outcome) via the shared pipeline
transitions.

Cost safety (hard rule): a freshly-created application enqueues scoring via the
SAME shared path Workable import uses (``on_application_created``), gated exactly
like Workable on ``role.starred_for_auto_sync`` — fresh candidates only, on the
create branch only. Re-syncs of an existing application NEVER re-enqueue
scoring, and nothing here dispatches paid re-evaluation of a stale score.
"""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime

from sqlalchemy.orm import Session

from ....domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
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
from ....services.document_service import (
    extract_text,
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from ....services.pre_screening_service import refresh_pre_screening_fields
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
    """Set Taali pipeline stage/outcome from the mapped status; needs-mapping → funnel top.

    Always records the raw ``bullhorn_status`` so an unmapped status stays
    visible. A mapped status drives a shared pipeline transition (idempotent —
    no-ops when already at the target). We never *demote* a resolved application
    or overwrite a locally-advanced stage from a remote wobble: the transition
    helpers guard their own no-op cases, and we skip mapping entirely for an
    already-resolved row (its decision snapshot is frozen).
    """
    app.bullhorn_status = sanitize_text_for_storage(remote_status) if remote_status else None
    app.external_stage_raw = sanitize_text_for_storage(remote_status) if remote_status else None

    if is_resolved(app):
        # Frozen: keep the remote status current for the trail, but do not move
        # the Taali stage or re-open a decision.
        return

    mapping = stage_map_mod.resolve_stage(db, org, remote_status)
    if mapping is None:
        # needs-mapping: do NOT guess. A freshly-created row already sits at the
        # funnel top from the create defaults; an existing row is left where the
        # recruiter/agent put it. The raw status above surfaces it for mapping.
        if created:
            logger.info(
                "Bullhorn status needs mapping org_id=%s app_id=%s status=%r — left at funnel top",
                org.id,
                app.id,
                remote_status,
            )
        return

    try:
        transition_stage(
            db,
            app=app,
            to_stage=mapping.taali_stage,
            source="sync",
            actor_type="sync",
            reason=f"Bullhorn status mapped: {remote_status}",
            metadata={"bullhorn_status": remote_status, "is_reject": mapping.is_reject},
        )
        if mapping.is_reject and (app.application_outcome or "open").lower() != "rejected":
            transition_outcome(
                db,
                app=app,
                to_outcome="rejected",
                actor_type="sync",
                reason=f"Bullhorn status mapped as reject: {remote_status}",
                metadata={"bullhorn_status": remote_status},
            )
    except Exception:  # pragma: no cover — never block the candidate sync
        logger.exception(
            "Bullhorn stage mapping transition failed app_id=%s status=%r",
            app.id,
            remote_status,
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
    except Exception:  # pragma: no cover — never block on a CV listing failure
        logger.exception("Bullhorn fileAttachments listing failed candidate=%s", bullhorn_candidate_id)
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
    except Exception:  # pragma: no cover
        logger.exception("Bullhorn CV download failed candidate=%s file=%s", bullhorn_candidate_id, file_id)
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
    except Exception:  # pragma: no cover
        logger.exception("Bullhorn convertToText failed candidate=%s", bullhorn_candidate_id)
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

    app.deleted_at = None
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = sanitize_text_for_storage(submission_id)
    ensure_pipeline_fields(app, source="sync" if created_application else "system")
    db.flush()
    if created_application:
        initialize_pipeline_event_if_missing(
            db, app=app, actor_type="sync", reason="Imported from Bullhorn"
        )

    app.external_refs = sanitize_json_for_storage(
        {
            "bullhorn_candidate_id": bullhorn_candidate_id,
            "bullhorn_job_order_id": role.bullhorn_job_order_id,
            "bullhorn_job_submission_id": submission_id,
        }
    )
    app.integration_sync_state = sanitize_json_for_storage(
        {"last_sync_at": now.isoformat(), "sync_status": "success", "source": "bullhorn"}
    )
    app.last_synced_at = now

    # Map the remote status → Taali stage (needs-mapping stays at funnel top).
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

    # COST SAFETY: mirror Workable import exactly. A freshly-created application
    # enqueues scoring through the shared path ONLY when the role is opted into
    # real-time auto-sync scoring (``starred_for_auto_sync``); the scoring job
    # emits the deterministic decision downstream. Re-syncs never re-enqueue, and
    # no bullhorn-specific rescore trigger exists. Context on an existing
    # application is stored for the NEXT recruiter-approved evaluation only.
    auto_score = bool(created_application and getattr(role, "starred_for_auto_sync", False))
    on_application_created(app, score=auto_score)

    db.flush()
    counters["application_upserted"] += 1
    return counters
