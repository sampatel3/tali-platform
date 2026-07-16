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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...cv_parsing.origins import CV_PARSE_ORIGIN_NATIVE_APPLY
from ...models.candidate_application import CandidateApplication
from ...models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ...models.organization import Organization
from ...models.role import Role
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.middleware import resolve_client_ip
from ...services.rate_limit import check_rate_limit
from ...services.job_page_lifecycle import lock_native_intake_authority, role_accepts_native_applications
from .apply_service import submit_application
from .public_apply_support import (
    APPLY_EMAIL_REQUIRED_MESSAGE as _APPLY_EMAIL_REQUIRED_MESSAGE,
    attach_resume as _attach_resume,
    find_existing_application as _find_existing_application,
    parse_answers as _parse_answers,
    role_requires_email as _role_requires_email,
    usable_email as _usable_email,
)
from .screening_service import list_role_questions
from .careers_board_queries import list_public_careers_pages

public_router = APIRouter(prefix="/api/v1/public", tags=["Job pages"])

# Candidate-facing copy — plain and friendly, no internal jargon. Deliberately
# generic on every apply outcome (knockout details stay server-side).
_APPLY_RECEIVED_MESSAGE = "Thanks for applying — we've received your application."
_APPLY_CLOSED_MESSAGE = "This job isn't accepting applications right now."


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


def _role_accepts_public_applications(
    role: Role | None,
    *,
    db: Session | None = None,
) -> bool:
    """Whether the materialized role is live for native public intake.

    Requisition publish deliberately creates a DRAFT role; Turn on is the
    explicit go-live transition.  The shared lifecycle policy also makes
    Turn off/Pause and a non-live linked Workable job fail closed. It keys on
    ``job_status`` rather than ``source`` because Workable adoption changes the
    latter while retaining the same requisition page.
    """
    return role_accepts_native_applications(role, db=db)


def _role_requires_resume(role: Role | None) -> bool:
    """Resume policy for a public application.

    A managed requisition always requires a readable CV whenever it is accepting
    applications. Turn off/Pause now closes intake entirely, so no unscorable or
    unexpectedly billable applications accumulate between agent runs. Preserve
    the existing gate for any other agent-enabled role.
    """
    if role is None:
        return False
    return bool(
        getattr(role, "source", None) == "requisition"
        or getattr(role, "agentic_mode_enabled", False)
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
    if page is None or page.status != JOB_PAGE_STATUS_OPEN:
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
        and _role_accepts_public_applications(role, db=db)
    )
    resume_required = bool(
        accepts_applications
        and _role_requires_resume(role)
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
        "resume_required": resume_required,
        "screening_questions": screening_questions,
    }


@public_router.get("/careers/{slug}")
def view_careers_board(
    slug: str,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
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

    pages, has_more, next_offset = list_public_careers_pages(
        db,
        organization_id=org.id,
        limit=limit,
        offset=offset,
    )

    return {
        "organization_name": org.name,
        "slug": org.slug,
        "has_more": has_more,
        "next_offset": next_offset,
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

    client_ip = resolve_client_ip(request)
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
    if page is None or page.status != JOB_PAGE_STATUS_OPEN:
        raise HTTPException(status_code=404, detail="Job not found")
    role = _resolve_role_for_page(db, page)
    if role is None:
        raise HTTPException(status_code=404, detail=_APPLY_CLOSED_MESSAGE)
    if not _role_accepts_public_applications(role, db=db):
        raise HTTPException(status_code=404, detail=_APPLY_CLOSED_MESSAGE)
    usable_email = _usable_email(email)
    if _role_requires_email(role) and usable_email is None:
        raise HTTPException(status_code=422, detail=_APPLY_EMAIL_REQUIRED_MESSAGE)
    if usable_email is not None:
        email = usable_email
    has_resume = bool(resume is not None and (resume.filename or "").strip())
    if _role_requires_resume(role) and not has_resume:
        raise HTTPException(
            status_code=422,
            detail="Please upload a resume so your application can be evaluated.",
        )

    parsed_answers = _parse_answers(answers)

    try:
        # One SAVEPOINT owns candidate resolution, application creation and CV
        # extraction together.  ``submit_application`` itself uses a nested
        # savepoint for identity races; this outer boundary ensures a later
        # unreadable-resume refusal removes even a newly-resolved candidate.
        with db.begin_nested():
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
            # Idempotent re-apply doubles as a safe repair path for legacy
            # applications accepted before resume enforcement. Never overwrite a
            # usable CV, but do let the candidate fill a missing/unreadable one so
            # the autonomous pipeline can score it without recruiter intervention.
            attached_resume = bool(
                has_resume
                and (
                    result.created
                    or not (getattr(result.application, "cv_text", None) or "").strip()
                )
            )
            if attached_resume:
                _attach_resume(db, result.application, page.organization_id, resume)
        # Resume extraction may overlap Pause/Turn off; re-authorize under the
        # shared org -> role locks immediately before accepting the application.
        if lock_native_intake_authority(db, role=role) is None:
            raise HTTPException(status_code=404, detail=_APPLY_CLOSED_MESSAGE)
        db.commit()
    except HTTPException:
        # In particular, unreadable resume extraction happens after the
        # idempotent application resolver has flushed candidate/application
        # rows. Roll those provisional rows back so a 422 never counts as an
        # accepted application.
        db.rollback()
        raise
    except IntegrityError:
        # Double-submit race: a concurrent request created the (candidate, role)
        # application between our read and insert. Recover the winning row and
        # return the idempotent success response instead of a 500.
        db.rollback()
        existing = _find_existing_application(db, page.organization_id, role, email, phone)
        if existing is None:
            raise HTTPException(status_code=409, detail=_APPLY_CLOSED_MESSAGE)
        attached_resume = bool(
            has_resume and not (getattr(existing, "cv_text", None) or "").strip()
        )
        if attached_resume:
            _attach_resume(db, existing, page.organization_id, resume)
            db.commit()
            knockout = (existing.screening_answers or {}).get("_knockout", {})
            if bool(knockout.get("passed", True)):
                from ...services.application_events import on_application_created

                on_application_created(
                    existing,
                    score=True,
                    score_force=True,
                    parse_origin=CV_PARSE_ORIGIN_NATIVE_APPLY,
                )
        return {
            "status": "received",
            "message": _APPLY_RECEIVED_MESSAGE,
            "application_id": existing.id,
            "eeo_token": existing.eeo_token,
        }

    # Any newly-attached resume (fresh application or idempotent repair) enters
    # the normal parse + scoring flow. A knockout-failed application already
    # carries a pending reject, so it deliberately does not spend on scoring.
    if attached_resume and result.knockout_passed:
        from ...services.application_events import on_application_created

        on_application_created(
            result.application,
            score=True,
            score_force=True,
            parse_origin=CV_PARSE_ORIGIN_NATIVE_APPLY,
        )

    return {
        "status": "received",
        "message": _APPLY_RECEIVED_MESSAGE,
        "application_id": result.application.id,
        # The applicant carries this back to the OPTIONAL voluntary-EEO step. It
        # is the only key that endpoint accepts — it resolves to exactly this
        # application, so nobody can post demographics for someone else's apply.
        "eeo_token": result.eeo_token,
    }


class _EEORequest(BaseModel):
    gender: str | None = None
    race_ethnicity: str | None = None
    veteran_status: str | None = None
    disability_status: str | None = None
    declined_to_answer: bool = False


@public_router.post("/eeo/{token}", status_code=204)
def submit_eeo(
    token: str,
    payload: _EEORequest,
    request: Request,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """Voluntary EEO / OFCCP self-identification for a just-submitted application.

    Public, no auth. Flag-gated (with apply's flag) and rate-limited per client-IP
    + token. The ``token`` is the opaque ``eeo_token`` minted at apply time; it
    resolves to EXACTLY ONE application — NO raw application_id is ever accepted
    from the public, so nobody can overwrite another applicant's demographics.
    Overwrite-own-only: re-posting the same token corrects this application's row.
    The response is empty (204). The value is stored SEGREGATED from scoring.
    """
    if not settings.ATS_PUBLIC_APPLY_ENABLED:
        raise HTTPException(status_code=503, detail=_APPLY_CLOSED_MESSAGE)

    client_ip = resolve_client_ip(request)
    if not check_rate_limit(
        f"eeo:{token}:{client_ip}",
        limit=settings.ATS_APPLY_RATE_LIMIT_PER_HOUR,
        window_seconds=3600,
    ):
        raise HTTPException(
            status_code=429,
            detail="You've submitted this a few times already — please try again later.",
        )

    tok = (token or "").strip()
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.eeo_token == tok)
        .first()
        if tok
        else None
    )
    # A bad/unknown token reads the same as a missing job — nothing to disclose.
    if application is None:
        raise HTTPException(status_code=404, detail="Not found")

    from ..compliance.eeo_service import record_response

    record_response(
        db,
        application.organization_id,
        application.id,
        **payload.model_dump(),
    )
    db.commit()
    return None
