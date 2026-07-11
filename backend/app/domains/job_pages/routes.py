"""Public, no-auth resolver for shareable job pages + the per-org careers board.

Mirrors the top-reports public route: token in the path, optional-auth (so a
stray Authorization header never bounces an anonymous viewer), and the
public-safe snapshot returned in one round-trip. Mounted at app root under
``/api/v1/public`` (the URL the recruiter shares resolves in any browser).

Two surfaces:
- ``GET /job/{token}`` — a single published page.
- ``GET /careers/{slug}`` — the org's whole careers board (all its OPEN pages),
  resolved by ``Organization.slug``.

Both deliberately return NO client / rate / margin — only what a candidate
should see. ``organization_name`` is the poster (the consultancy / employer).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.job_page import JOB_PAGE_STATUS_CLOSED, JOB_PAGE_STATUS_OPEN, JobPage
from ...models.organization import Organization
from ...models.role import Role
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...services.rate_limit import check_rate_limit
from .apply_service import submit_application
from .screening_service import list_role_questions

logger = logging.getLogger("taali.job_pages")

public_router = APIRouter(prefix="/api/v1/public", tags=["Job pages"])

# Candidate-facing copy — plain and friendly, no internal jargon. Deliberately
# generic on every apply outcome (knockout details stay server-side).
_APPLY_RECEIVED_MESSAGE = "Thanks for applying — we've received your application."
_APPLY_CLOSED_MESSAGE = "This job isn't accepting applications right now."
_APPLY_RESUME_TYPE_MESSAGE = "Please upload your resume as a PDF or Word document."


def _public_screening_questions(db: Session, org_id: int, role_id: int) -> list[dict]:
    """Public-safe screening-question subset for the apply form. NEVER exposes
    ``knockout`` / ``knockout_expected`` — the passing answer must not leak to
    the applicant."""
    return [
        {
            "id": q.id,
            "prompt": q.prompt,
            "kind": q.kind,
            "options": q.options,
            "required": q.required,
        }
        for q in list_role_questions(db, org_id, role_id)
    ]


def _resolve_role_for_page(db: Session, page: JobPage) -> Role | None:
    """The materialized Role behind a published page: page → brief → role. A
    published page always has a materialized role (publish runs after
    ``materialize_brief_to_role``); returns None defensively if not."""
    brief = (
        db.query(RoleBrief).filter(RoleBrief.id == page.brief_id).first()
        if page.brief_id is not None
        else None
    )
    if brief is None or brief.role_id is None:
        return None
    return (
        db.query(Role)
        .filter(
            Role.id == brief.role_id,
            Role.organization_id == page.organization_id,
        )
        .first()
    )


def _job_page_url(token: str) -> str:
    """Public job-page URL. ``/job/{token}`` relative when FRONTEND_URL is empty."""
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/job/{token}" if base else f"/job/{token}"


def format_salary_band(
    salary_min: int | None,
    salary_max: int | None,
    currency: str | None,
) -> str:
    """Format a public-facing comp band, e.g. ``"AED 20,000–28,000 / year"``.

    Currency defaults to AED (UAE-based org). Returns ``""`` when there is no
    band at all (neither min nor max). A one-sided band renders the value it
    has ("AED 20,000+ / year" for a floor only, "up to AED 28,000 / year" for
    a ceiling only). Always per year (the only period the public page shows).
    """
    cur = (currency or "AED").strip() or "AED"
    if salary_min and salary_max:
        return f"{cur} {salary_min:,}–{salary_max:,} / year"
    if salary_min:
        return f"{cur} {salary_min:,}+ / year"
    if salary_max:
        return f"up to {cur} {salary_max:,} / year"
    return ""


@public_router.get("/job/{token}")
def view_job_page(
    token: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    page = db.query(JobPage).filter(JobPage.token == token).first()
    # 404 for both "no such page" and a closed page — a closed listing should
    # read as gone, not as "exists but unavailable".
    if page is None or page.status == JOB_PAGE_STATUS_CLOSED:
        raise HTTPException(status_code=404, detail="Job not found")

    org = page.organization
    # Public-safe screening questions (apply-form fields) + whether the page is
    # actually taking applications. Both additive — a page renders unchanged when
    # apply is off (empty questions, ``accepts_applications`` false).
    role = _resolve_role_for_page(db, page)
    screening_questions = (
        _public_screening_questions(db, page.organization_id, role.id)
        if role is not None
        else []
    )
    accepts_applications = bool(
        settings.ATS_PUBLIC_APPLY_ENABLED
        and page.status == JOB_PAGE_STATUS_OPEN
        and role is not None
    )
    return {
        "title": page.title,
        "jd_markdown": page.jd_markdown,
        "location": page.location,
        "workplace_type": page.workplace_type,
        "employment_type": page.employment_type,
        "seniority": page.seniority,
        "salary_min": page.salary_min,
        "salary_max": page.salary_max,
        "salary_currency": page.salary_currency,
        "status": page.status,
        "organization_name": org.name if org else None,
        "accepts_applications": accepts_applications,
        "screening_questions": screening_questions,
    }


@public_router.get("/careers/{slug}")
def view_careers_board(
    slug: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """The org's PUBLIC careers board: every OPEN job page it has published.

    Resolved by ``Organization.slug``. 404 when there is no org with that slug
    (an org without a slug is unreachable here by construction). An org with no
    open pages returns an empty ``jobs`` list — a valid, live-but-empty board.

    Each job carries only the public-safe snapshot (title / location / comp
    band / type) — NEVER any client / rate / margin. Newest first.
    """
    slug = (slug or "").strip()
    org = (
        db.query(Organization).filter(Organization.slug == slug).first()
        if slug
        else None
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Careers page not found")

    pages = (
        db.query(JobPage)
        .filter(
            JobPage.organization_id == org.id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
        )
        .order_by(JobPage.published_at.desc(), JobPage.id.desc())
        .all()
    )

    return {
        "organization_name": org.name,
        "slug": org.slug,
        "jobs": [
            {
                "token": page.token,
                "url": _job_page_url(page.token),
                "title": page.title,
                "location": page.location,
                "workplace_type": page.workplace_type,
                "employment_type": page.employment_type,
                "seniority": page.seniority,
                "salary": format_salary_band(
                    page.salary_min, page.salary_max, page.salary_currency
                ),
                "published_at": page.published_at.isoformat()
                if page.published_at
                else None,
            }
            for page in pages
        ],
    }


# --------------------------------------------------------------------------- #
# Public apply (write) — flag-gated, rate-limited, no auth.
# --------------------------------------------------------------------------- #

# Resume upload limits — mirror the recruiter CV-upload path
# (document_service.MAX_FILE_SIZE / the applications upload-cv route).
_RESUME_ALLOWED_EXTENSIONS = {"pdf", "docx"}


def _parse_answers(raw: str | None) -> dict:
    """Parse the multipart ``answers`` field (a JSON object string) into a dict.
    Empty/absent → ``{}``. A non-object or invalid JSON → 422 (friendly)."""
    if raw is None or raw.strip() == "":
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Answers must be valid JSON.")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="Answers must be a JSON object.")
    return parsed


def _attach_resume(db: Session, application, org_id: int, upload: UploadFile) -> None:
    """Store the resume via the shared CV path and stamp it on the application +
    candidate. Reuses ``process_document_upload`` (validation, object storage,
    text extraction) and the #895 PDF hygiene stash. Raises HTTP 422 (friendly)
    for a wrong file type."""
    from datetime import datetime, timezone

    from ...services.document_hygiene import stash_pdf_hygiene_on_application
    from ...services.document_service import (
        load_stored_document_bytes,
        process_document_upload,
        sanitize_text_for_storage,
    )

    filename = (upload.filename or "").strip()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _RESUME_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=_APPLY_RESUME_TYPE_MESSAGE)

    result = process_document_upload(
        upload=upload,
        entity_id=int(application.id),
        doc_type="cv",
        allowed_extensions=_RESUME_ALLOWED_EXTENSIONS,
    )
    now = datetime.now(timezone.utc)
    text = sanitize_text_for_storage(result["extracted_text"])
    application.cv_file_url = result["file_url"]
    application.cv_filename = result["filename"]
    application.cv_text = text
    application.cv_uploaded_at = now
    if application.candidate:
        application.candidate.cv_file_url = result["file_url"]
        application.candidate.cv_filename = result["filename"]
        application.candidate.cv_text = text
        application.candidate.cv_uploaded_at = now
    # #895 PDF-ingest hardening (best-effort; never blocks the apply).
    try:
        content = load_stored_document_bytes(result["file_url"])
        if content:
            stash_pdf_hygiene_on_application(application, content, ext)
    except Exception:  # pragma: no cover - defensive; hygiene must never block
        logger.warning("resume hygiene scan skipped for application_id=%s", application.id)


@public_router.post("/job-pages/{token}/apply")
def apply_to_job_page(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    full_name: str = Form(...),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    answers: str | None = Form(None),
    source_name: str | None = Form(None),
    resume: UploadFile | None = File(None),
    _user: User | None = Depends(get_optional_current_user),
):
    """Submit an application to a published job page (no auth).

    Flag-gated (503 when ``ATS_PUBLIC_APPLY_ENABLED`` is off) and rate-limited
    per client-IP + job. Resolves the published page → materialized role, then
    resolves-or-creates the candidate, records source attribution, runs the
    knockout gate, and (with a resume) fans out the normal parse + scoring flow.
    The response is deliberately generic on every outcome — a knockout failure
    is queued as a recruiter decision, never disclosed to the applicant.
    """
    if not settings.ATS_PUBLIC_APPLY_ENABLED:
        raise HTTPException(status_code=503, detail=_APPLY_CLOSED_MESSAGE)

    if not ((email or "").strip() or (phone or "").strip()):
        raise HTTPException(
            status_code=422, detail="Please provide an email address or phone number."
        )

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(
        f"apply:{token}:{client_ip}",
        limit=settings.ATS_APPLY_RATE_LIMIT_PER_HOUR,
        window_seconds=3600,
    ):
        raise HTTPException(
            status_code=429,
            detail="You've submitted a few applications already — please try again later.",
        )

    page = db.query(JobPage).filter(JobPage.token == token).first()
    if page is None or page.status == JOB_PAGE_STATUS_CLOSED:
        raise HTTPException(status_code=404, detail="Job not found")
    role = _resolve_role_for_page(db, page)
    if role is None:
        raise HTTPException(status_code=404, detail=_APPLY_CLOSED_MESSAGE)

    parsed_answers = _parse_answers(answers)

    try:
        result = submit_application(
            db,
            page.organization_id,
            role,
            full_name=full_name,
            email=email,
            phone=phone,
            answers=parsed_answers,
            source_name=source_name,
        )
        # Attach the resume only for a genuinely new application — never
        # overwrite the CV of an existing (idempotent re-submit) application.
        if result.created and resume is not None and (resume.filename or "").strip():
            _attach_resume(db, result.application, page.organization_id, resume)
        db.commit()
    except IntegrityError:
        # Double-submit race: a concurrent request created the (candidate, role)
        # application between our read and insert. Recover the winning row and
        # return the idempotent success response instead of a 500.
        db.rollback()
        existing = _find_existing_application(db, page.organization_id, role, email, phone)
        if existing is None:
            raise HTTPException(status_code=409, detail=_APPLY_CLOSED_MESSAGE)
        return {"status": "received", "message": _APPLY_RECEIVED_MESSAGE, "application_id": existing.id}

    # With a resume on a NEW, knockout-passing application, trigger the platform's
    # normal parse + scoring flow (same entry point the recruiter CV upload uses).
    # A knockout-failed application already carries a pending reject — no scoring.
    if (
        result.created
        and result.knockout_passed
        and resume is not None
        and (resume.filename or "").strip()
    ):
        from ...services.application_events import on_application_created

        on_application_created(result.application, score=True, score_force=True)

    return {
        "status": "received",
        "message": _APPLY_RECEIVED_MESSAGE,
        "application_id": result.application.id,
    }


def _find_existing_application(db, org_id, role, email, phone):
    """Re-read the (candidate, role) application after an insert race, resolving
    the candidate by the same identity keys apply used."""
    from ...services.candidate_identity_service import resolve_candidate
    from ...models.candidate_application import CandidateApplication

    candidate = resolve_candidate(db, org_id, email=email, phone=phone)
    if candidate is None:
        return None
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
