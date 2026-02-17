"""Workable pull-sync service for roles/candidates/applications."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....platform.config import settings
from ....services.document_service import extract_text, save_file_locally
from ....services.fit_matching_service import calculate_cv_job_match_sync
from .service import WorkableRateLimitError, WorkableService

logger = logging.getLogger(__name__)


def _format_job_spec_from_api(job_data: dict) -> str:
    """Build a well-formatted job spec string from full Workable job API data."""
    if not job_data:
        return ""
    # Unwrap if API returned {"job": {...}} or use as-is
    merged = job_data.get("job") if isinstance(job_data.get("job"), dict) else dict(job_data)
    details = merged.get("details") if isinstance(merged.get("details"), dict) else {}
    merged = {**merged, **details}
    merged.pop("details", None)
    lines: list[str] = []

    title = merged.get("title") or merged.get("name") or merged.get("headline") or "Job"
    lines.append(f"# {title}")
    lines.append("")

    for key, label in (
        ("department", "Department"),
        ("location", "Location"),
        ("employment_type", "Employment type"),
        ("application_url", "Apply"),
        ("state", "State"),
        ("full_title", "Full title"),
    ):
        value = merged.get(key)
        if value is not None and str(value).strip():
            lines.append(f"**{label}:** {value}")
    lines.append("")

    for key in ("description", "full_description", "requirements", "benefits"):
        value = merged.get(key)
        if isinstance(value, str) and value.strip():
            label = key.replace("_", " ").title()
            lines.append(f"## {label}")
            lines.append("")
            lines.append(value.strip())
            lines.append("")

    return "\n".join(lines).strip()


TERMINAL_STAGES = {"hired", "rejected", "withdrawn", "disqualified", "declined", "archived"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_terminal_stage(stage_value: str | None) -> bool:
    stage = (stage_value or "").strip().lower()
    return stage in TERMINAL_STAGES


def _is_terminal_candidate(payload: dict) -> bool:
    stage_kind = str(payload.get("stage_kind") or "").strip().lower()
    if stage_kind and stage_kind in TERMINAL_STAGES:
        return True
    stage = (
        payload.get("stage")
        or payload.get("stage_name")
        or payload.get("status")
        or ""
    )
    if _is_terminal_stage(str(stage)):
        return True
    if payload.get("disqualified") is True:
        return True
    if payload.get("hired_at"):
        return True
    return False


def _candidate_email(payload: dict) -> str | None:
    for key in ("email", "work_email", "candidate_email"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    # Workable sometimes provides a list of emails.
    emails = payload.get("emails")
    if isinstance(emails, list):
        for item in emails:
            if not isinstance(item, dict):
                continue
            value = item.get("value") or item.get("email") or item.get("address")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    contact = payload.get("contact")
    if isinstance(contact, dict):
        value = contact.get("email")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _candidate_name(payload: dict, fallback: str | None = None) -> str | None:
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    first = (payload.get("firstname") or "").strip()
    last = (payload.get("lastname") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    return fallback


def _candidate_position(payload: dict, job_title: str | None = None) -> str | None:
    for key in ("headline", "title", "position"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return job_title


def _rank_score_for_application(app: CandidateApplication) -> float | None:
    if app.workable_score is not None:
        return app.workable_score
    return app.cv_match_score


class WorkableSyncService:
    def __init__(self, client: WorkableService):
        self.client = client
        self._job_details_cache: dict[str, dict] = {}

    def sync_org(self, db: Session, org: Organization, *, full_resync: bool = False) -> dict:
        summary = {
            "jobs_seen": 0,
            "jobs_upserted": 0,
            "candidates_seen": 0,
            "candidates_upserted": 0,
            "applications_upserted": 0,
            "cv_downloaded": 0,
            "cv_matched": 0,
            "errors": [],
            "full_resync": bool(full_resync),
        }
        now = _now()
        try:
            jobs = self.client.list_open_jobs()
            summary["jobs_seen"] = len(jobs)
            if not jobs:
                logger.warning("Workable list_open_jobs returned 0 jobs for org_id=%s", org.id)
            rate_limited = False
            for job in jobs:
                if rate_limited:
                    break
                try:
                    role, created_role = self._upsert_role(db, org, job)
                    if created_role:
                        summary["jobs_upserted"] += 1

                    candidates = self._list_job_candidates_for_job(job=job, role=role)
                    job_title = (job.get("title") or job.get("shortcode") or "job")[:60]
                    logger.info(
                        "Workable job shortcode=%s title=%s candidates=%s",
                        job.get("shortcode"),
                        job_title,
                        len(candidates),
                    )
                    for candidate_ref in candidates:
                        summary["candidates_seen"] += 1
                        synced = self._sync_candidate_for_role(
                            db=db,
                            org=org,
                            role=role,
                            job=job,
                            candidate_ref=candidate_ref,
                            now=now,
                            enrich=True,  # always fetch full candidate + CV
                        )
                        summary["candidates_upserted"] += synced.get("candidate_upserted", 0)
                        summary["applications_upserted"] += synced.get("application_upserted", 0)
                        summary["cv_downloaded"] += synced.get("cv_downloaded", 0)
                        summary["cv_matched"] += synced.get("cv_matched", 0)
                except WorkableRateLimitError as exc:
                    logger.warning("Workable sync rate-limited; stopping early for org_id=%s", org.id)
                    summary["errors"].append(str(exc))
                    rate_limited = True
                except Exception as exc:
                    logger.exception("Failed syncing job")
                    summary["errors"].append(str(exc))

            org.workable_last_sync_at = now
            org.workable_last_sync_status = "success" if not summary["errors"] else "partial"
            org.workable_last_sync_summary = summary
            db.commit()
            return summary
        except Exception as exc:
            logger.exception("Workable org sync failed")
            org.workable_last_sync_at = now
            org.workable_last_sync_status = "failed"
            org.workable_last_sync_summary = {
                **summary,
                "errors": [*summary["errors"], str(exc)],
            }
            db.commit()
            raise

    def _job_identifiers(self, job: dict, role: Role | None = None) -> list[str]:
        identifiers: list[str] = []
        # SPI v3 in this account resolves job details/candidates by shortcode.
        for value in (
            job.get("shortcode"),
            role.workable_job_id if role else None,
        ):
            identifier = str(value or "").strip()
            if identifier and identifier not in identifiers:
                identifiers.append(identifier)
        # Some payloads expose a numeric code in application_url (/jobs/<code>).
        application_url = str(job.get("application_url") or "")
        match = re.search(r"/jobs/([0-9]+)", application_url)
        if match:
            code = match.group(1)
            if code not in identifiers:
                identifiers.append(code)
        # Last fallback for accounts that resolve endpoints by id.
        raw_id = str(job.get("id") or "").strip()
        if raw_id and raw_id not in identifiers:
            identifiers.append(raw_id)
        return identifiers

    def _list_job_candidates_for_job(self, *, job: dict, role: Role) -> list[dict]:
        """Fetch all candidates for the job, paginating through every page."""
        for identifier in self._job_identifiers(job, role):
            candidates = self.client.list_job_candidates(
                identifier,
                paginate=True,
                max_pages=None,
            )
            if candidates:
                return candidates
        return []

    def _job_details_for_role(self, *, job: dict, role: Role | None = None) -> dict:
        for identifier in self._job_identifiers(job, role):
            if identifier in self._job_details_cache:
                cached = self._job_details_cache.get(identifier) or {}
                if cached:
                    return cached
                continue
            details = self.client.get_job_details(identifier)
            self._job_details_cache[identifier] = details or {}
            if details:
                return details
        return {}

    def _upsert_role(self, db: Session, org: Organization, job: dict) -> tuple[Role, bool]:
        job_id = str(job.get("id") or job.get("shortcode") or "").strip()
        title = str(job.get("title") or job.get("name") or f"Workable role {job_id or 'unknown'}").strip()
        details = self._job_details_for_role(job=job)
        description = (
            details.get("description")
            or details.get("full_description")
            or details.get("requirements")
            or job.get("description")
            or job.get("full_description")
            or job.get("requirements")
            or ""
        )
        role = None
        if job_id:
            role = (
                db.query(Role)
                .filter(Role.organization_id == org.id, Role.workable_job_id == job_id)
                .first()
            )
        created = False
        if not role:
            role = Role(
                organization_id=org.id,
                source="workable",
                workable_job_id=job_id or None,
                name=title,
            )
            db.add(role)
            created = True
        role.deleted_at = None  # restore if was soft-deleted
        role.source = "workable"
        role.workable_job_id = job_id or role.workable_job_id
        role.workable_job_data = {**job, "details": details} if details else job
        role.name = title
        # Build one formatted spec from full API data for display and attachment
        formatted_spec = _format_job_spec_from_api(role.workable_job_data or {})
        if formatted_spec:
            role.job_spec_text = formatted_spec
            role.description = formatted_spec
        else:
            role.description = description or role.description
            if isinstance(description, str) and description.strip():
                role.job_spec_text = description.strip()
        db.flush()
        # Save job spec as an attachment (file) for download and consistent display
        if (role.job_spec_text or "").strip():
            try:
                spec_content = (role.job_spec_text or "").strip().encode("utf-8")
                path = save_file_locally(
                    content=spec_content,
                    directory="job_spec",
                    prefix=f"job-spec-{role.id}",
                    ext="txt",
                )
                role.job_spec_file_url = path
                role.job_spec_filename = f"job-spec-{role.name or role.id}.txt".replace("/", "-")
                role.job_spec_uploaded_at = _now()
            except Exception:
                logger.exception("Failed saving Workable job spec file for role_id=%s", role.id)
        return role, created

    def _sync_candidate_for_role(
        self,
        *,
        db: Session,
        org: Organization,
        role: Role,
        job: dict,
        candidate_ref: dict,
        now: datetime,
        enrich: bool,
    ) -> dict:
        counters = {
            "candidate_upserted": 0,
            "application_upserted": 0,
            "cv_downloaded": 0,
            "cv_matched": 0,
        }
        candidate_id = str(candidate_ref.get("id") or "").strip()
        if not candidate_id:
            return counters

        candidate_payload = candidate_ref
        if enrich:
            candidate_payload = self.client.get_candidate(candidate_id) or candidate_ref
        stage = (
            candidate_payload.get("stage")
            or candidate_ref.get("stage")
            or candidate_ref.get("stage_name")
            or ""
        )
        if _is_terminal_candidate(candidate_payload) or _is_terminal_candidate(candidate_ref):
            return counters

        email = _candidate_email(candidate_payload) or _candidate_email(candidate_ref)
        if not email:
            return counters

        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == org.id,
                Candidate.workable_candidate_id == candidate_id,
            )
            .first()
        )
        if not candidate:
            candidate = (
                db.query(Candidate)
                .filter(
                    Candidate.organization_id == org.id,
                    Candidate.email == email,
                )
                .first()
            )
        created_candidate = False
        if not candidate:
            candidate = Candidate(
                organization_id=org.id,
                email=email,
            )
            db.add(candidate)
            created_candidate = True

        candidate.deleted_at = None  # restore if was soft-deleted
        candidate.email = email
        candidate.full_name = _candidate_name(candidate_payload, fallback=candidate.full_name or email)
        candidate.position = _candidate_position(candidate_payload, role.name)
        candidate.workable_candidate_id = candidate_id
        candidate.workable_data = candidate_payload
        db.flush()
        if created_candidate:
            counters["candidate_upserted"] += 1

        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.candidate_id == candidate.id,
                CandidateApplication.role_id == role.id,
            )
            .first()
        )
        created_app = False
        if not app:
            app = CandidateApplication(
                organization_id=org.id,
                candidate_id=candidate.id,
                role_id=role.id,
                status="applied",
            )
            db.add(app)
            created_app = True

        app.deleted_at = None  # restore if was soft-deleted
        app.source = "workable"
        app.status = str(stage or app.status or "applied")
        app.workable_candidate_id = candidate_id
        app.workable_stage = str(stage or "")
        app.last_synced_at = now

        # Skip ratings API during sync to stay under rate limit (10 req/10 sec); use candidate payload score only
        ratings_payload = None
        raw_score, normalized_score, score_source = self.client.extract_workable_score(
            candidate_payload=candidate_payload,
            ratings_payload=ratings_payload,
        )
        # Only overwrite when we successfully extracted a score.
        if raw_score is not None or normalized_score is not None:
            app.workable_score_raw = raw_score
            app.workable_score = normalized_score
            app.workable_score_source = score_source
        if enrich:
            downloaded = self.client.download_candidate_resume(candidate_payload)
            if not downloaded:
                logger.debug(
                    "No CV downloaded for candidate_id=%s (workable)",
                    candidate_id,
                )
            if downloaded:
                filename, content = downloaded
                ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
                if ext in {"pdf", "docx", "txt"}:
                    try:
                        path = save_file_locally(content=content, directory="cv", prefix=f"cv-{app.id or candidate.id}", ext=ext)
                        extracted = extract_text(content, ext)
                        if extracted:
                            app.cv_file_url = path
                            app.cv_filename = filename
                            app.cv_text = extracted
                            app.cv_uploaded_at = now
                            candidate.cv_file_url = path
                            candidate.cv_filename = filename
                            candidate.cv_text = extracted
                            candidate.cv_uploaded_at = now
                            counters["cv_downloaded"] += 1
                    except Exception:
                        logger.exception("Failed processing downloaded CV")

        cv_matched = self._compute_cv_match(app=app, role=role, now=now)
        if cv_matched:
            counters["cv_matched"] += 1
        app.rank_score = _rank_score_for_application(app)
        db.flush()
        if created_app:
            counters["application_upserted"] += 1
        return counters

    def _compute_cv_match(self, *, app: CandidateApplication, role: Role, now: datetime) -> bool:
        cv_text = (app.cv_text or "").strip()
        job_spec = (role.job_spec_text or "").strip()
        if not cv_text or not job_spec or not settings.ANTHROPIC_API_KEY:
            app.cv_match_score = app.cv_match_score
            return False
        try:
            result = calculate_cv_job_match_sync(
                cv_text=cv_text,
                job_spec_text=job_spec,
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.resolved_claude_model,
            )
            app.cv_match_score = result.get("cv_job_match_score")
            app.cv_match_details = result.get("match_details", {})
            app.cv_match_scored_at = now
            return app.cv_match_score is not None
        except Exception:
            logger.exception("Failed computing CV match during Workable sync")
            return False
