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
from ....services.document_service import save_file_locally
from .service import WorkableRateLimitError, WorkableService

logger = logging.getLogger(__name__)

# Progress is persisted every N candidates to limit DB writes; UI still shows live counts.
PROGRESS_BATCH_SIZE = 50


class WorkableSyncCancelled(Exception):
    """Raised when the user requested sync cancellation; sync should stop immediately."""



def _strip_html(html: str) -> str:
    """Convert HTML to plain text, preserving basic structure."""
    if not html or not isinstance(html, str):
        return html or ""
    import re
    text = html
    # Block elements to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    # Headings to markdown
    for level in range(1, 7):
        text = re.sub(rf"<h{level}[^>]*>(.*?)</h{level}>", rf"\n{'#' * level} \1\n", text, flags=re.IGNORECASE | re.DOTALL)
    # Bold/strong to markdown
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").replace("&quot;", '"')
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_location(value) -> str | None:
    """Format a Workable location value (may be dict or string) into readable text."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        parts = []
        city = value.get("city")
        region = value.get("region")
        country = value.get("country")
        if isinstance(city, str) and city.strip():
            parts.append(city.strip())
        if isinstance(region, str) and region.strip() and region.strip() != (city or "").strip():
            parts.append(region.strip())
        if isinstance(country, str) and country.strip():
            parts.append(country.strip())
        location_str = ", ".join(parts)
        workplace = value.get("workplace_type")
        if isinstance(workplace, str) and workplace.strip():
            location_str = f"{location_str} ({workplace.strip()})" if location_str else workplace.strip()
        return location_str or None
    return None


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

    # Location needs special handling (may be a dict)
    location_str = _format_location(merged.get("location"))
    if location_str:
        lines.append(f"**Location:** {location_str}")

    for key, label in (
        ("department", "Department"),
        ("employment_type", "Employment type"),
        ("application_url", "Apply"),
        ("state", "State"),
        ("full_title", "Full title"),
    ):
        value = merged.get(key)
        if value is not None and isinstance(value, str) and value.strip():
            lines.append(f"**{label}:** {value}")
    lines.append("")

    for key in ("description", "full_description", "requirements", "benefits"):
        value = merged.get(key)
        if isinstance(value, str) and value.strip():
            label = key.replace("_", " ").title()
            lines.append(f"## {label}")
            lines.append("")
            lines.append(_strip_html(value.strip()))
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


def _extract_candidate_fields(payload: dict) -> dict:
    """Extract known profile fields from a Workable candidate payload."""
    fields: dict[str, Any] = {}

    # Headline
    headline = payload.get("headline") or payload.get("title")
    if isinstance(headline, str) and headline.strip():
        fields["headline"] = headline.strip()

    # Image
    image_url = payload.get("image_url") or payload.get("avatar_url")
    if isinstance(image_url, str) and image_url.strip():
        fields["image_url"] = image_url.strip()

    # Location
    location = payload.get("location") or {}
    if isinstance(location, dict):
        city = location.get("city")
        country = location.get("country")
        if isinstance(city, str) and city.strip():
            fields["location_city"] = city.strip()
        if isinstance(country, str) and country.strip():
            fields["location_country"] = country.strip()
    elif isinstance(location, str) and location.strip():
        fields["location_city"] = location.strip()

    # Phone
    phone = payload.get("phone")
    if isinstance(phone, str) and phone.strip():
        fields["phone"] = phone.strip()

    # Profile URL
    profile_url = payload.get("profile_url") or payload.get("url")
    if isinstance(profile_url, str) and profile_url.strip():
        fields["profile_url"] = profile_url.strip()

    # Social profiles
    socials = payload.get("social_profiles")
    if isinstance(socials, list) and socials:
        fields["social_profiles"] = [
            {k: v for k, v in s.items() if k in ("type", "url", "name", "username")}
            for s in socials
            if isinstance(s, dict)
        ]

    # Tags
    tags = payload.get("tags")
    if isinstance(tags, list) and tags:
        fields["tags"] = [str(t) for t in tags if t]

    # Skills
    skills = payload.get("skills")
    if isinstance(skills, list) and skills:
        fields["skills"] = [str(s) for s in skills if s]

    # Education
    education = payload.get("education_entries") or payload.get("education")
    if isinstance(education, list) and education:
        fields["education_entries"] = [
            {k: v for k, v in e.items() if k in ("school", "degree", "field_of_study", "start_date", "end_date")}
            for e in education
            if isinstance(e, dict)
        ]

    # Experience
    experience = payload.get("experience_entries") or payload.get("experience")
    if isinstance(experience, list) and experience:
        fields["experience_entries"] = [
            {k: v for k, v in e.items() if k in ("company", "title", "start_date", "end_date", "current", "summary", "industry")}
            for e in experience
            if isinstance(e, dict)
        ]

    # Summary
    summary = payload.get("summary") or payload.get("cover_letter")
    if isinstance(summary, str) and summary.strip():
        fields["summary"] = summary.strip()

    # Created at
    created_at = payload.get("created_at")
    if isinstance(created_at, str) and created_at.strip():
        try:
            fields["workable_created_at"] = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return fields


class WorkableSyncService:
    def __init__(self, client: WorkableService):
        self.client = client
        self._job_details_cache: dict[str, dict] = {}

    def _is_cancel_requested(self, db: Session, org: Organization) -> bool:
        db.refresh(org)
        return org.workable_sync_cancel_requested_at is not None

    def sync_org(self, db: Session, org: Organization, *, full_resync: bool = False) -> dict:
        summary = {
            "jobs_seen": 0,
            "jobs_upserted": 0,
            "candidates_seen": 0,
            "candidates_upserted": 0,
            "applications_upserted": 0,
            "errors": [],
            "full_resync": bool(full_resync),
            "current_step": "listing_jobs",
            "last_request": "GET /jobs?state=published",
            "current_job_shortcode": None,
            "current_candidate_index": None,
        }
        now = _now()
        try:
            org.workable_sync_cancel_requested_at = None
            db.commit()
            org.workable_sync_progress = dict(summary)
            db.commit()
            jobs = self.client.list_open_jobs()
            summary["jobs_seen"] = len(jobs)
            summary["current_step"] = "listing_candidates" if jobs else None
            summary["last_request"] = "GET /jobs (done)" if jobs else "GET /jobs (0 jobs)"
            if not jobs:
                logger.warning("Workable list_open_jobs returned 0 jobs for org_id=%s", org.id)
                summary["errors"].append(
                    "Workable returned 0 jobs. Ensure you have jobs in Published or Open state in Workable, and that your token has the 'Jobs' (r_jobs) scope."
                )
            org.workable_sync_progress = dict(summary)
            db.commit()
            if self._is_cancel_requested(db, org):
                summary["errors"].append("Sync cancelled by user")
                org.workable_last_sync_at = now
                org.workable_last_sync_status = "cancelled"
                org.workable_last_sync_summary = {**summary, "cancelled": True}
                org.workable_sync_progress = None
                org.workable_sync_cancel_requested_at = None
                db.commit()
                return summary
            rate_limited = False
            for job in jobs:
                db.refresh(org)
                if org.workable_sync_cancel_requested_at is not None:
                    summary["errors"].append("Sync cancelled by user")
                    logger.info("Workable sync cancelled by user for org_id=%s", org.id)
                    break
                if rate_limited:
                    break
                try:
                    role, created_role = self._upsert_role(db, org, job)
                    if created_role:
                        summary["jobs_upserted"] += 1

                    shortcode = (job.get("shortcode") or job.get("id") or "?")[:20]
                    summary["current_step"] = "listing_candidates"
                    summary["current_job_shortcode"] = shortcode
                    summary["last_request"] = f"GET /jobs/{shortcode}/candidates"
                    org.workable_sync_progress = dict(summary)
                    db.commit()
                    candidates = self._list_job_candidates_for_job(job=job, role=role)
                    job_title = (job.get("title") or job.get("shortcode") or "job")[:60]
                    logger.info(
                        "Workable job shortcode=%s title=%s candidates=%s",
                        job.get("shortcode"),
                        job_title,
                        len(candidates),
                    )
                    org.workable_sync_progress = dict(summary)
                    db.commit()
                    total_candidates = len(candidates)
                    for idx, candidate_ref in enumerate(candidates):
                        db.refresh(org)
                        if org.workable_sync_cancel_requested_at is not None:
                            summary["errors"].append("Sync cancelled by user")
                            break
                        summary["candidates_seen"] += 1
                        cid = str(candidate_ref.get("id") or "?")[:12]
                        summary["current_step"] = "syncing_candidate"
                        summary["current_candidate_index"] = f"{idx + 1}/{total_candidates}" if total_candidates else str(idx + 1)
                        summary["last_request"] = f"syncing candidate {cid}"
                        if (idx + 1) % 5 == 1 or summary["candidates_seen"] % PROGRESS_BATCH_SIZE == 0:
                            org.workable_sync_progress = dict(summary)
                            db.commit()
                        try:
                            synced = self._sync_candidate_for_role(
                                db=db,
                                org=org,
                                role=role,
                                job=job,
                                candidate_ref=candidate_ref,
                                now=now,
                            )
                        except WorkableSyncCancelled:
                            summary["errors"].append("Sync cancelled by user")
                            logger.info("Workable sync cancelled by user for org_id=%s", org.id)
                            break
                        summary["candidates_upserted"] += synced.get("candidate_upserted", 0)
                        summary["applications_upserted"] += synced.get("application_upserted", 0)
                        if summary["candidates_seen"] % PROGRESS_BATCH_SIZE == 0:
                            org.workable_sync_progress = dict(summary)
                            db.commit()
                    if any("cancelled" in (e or "").lower() for e in summary["errors"]):
                        break
                except WorkableRateLimitError as exc:
                    logger.warning("Workable sync rate-limited; stopping early for org_id=%s", org.id)
                    summary["errors"].append(str(exc))
                    rate_limited = True
                except Exception as exc:
                    logger.exception("Failed syncing job")
                    summary["errors"].append(str(exc))
                org.workable_sync_progress = dict(summary)
                db.commit()

            org.workable_last_sync_at = now
            cancelled = any("cancelled" in (e or "").lower() for e in summary["errors"])
            org.workable_last_sync_status = "cancelled" if cancelled else ("success" if not summary["errors"] else "partial")
            org.workable_last_sync_summary = {**summary, "cancelled": cancelled}
            org.workable_sync_progress = None
            org.workable_sync_cancel_requested_at = None
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
            org.workable_sync_progress = None
            org.workable_sync_cancel_requested_at = None
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
            stripped = _strip_html(description) if isinstance(description, str) and description.strip() else ""
            role.description = stripped or role.description
            if stripped:
                role.job_spec_text = stripped
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
    ) -> dict:
        if self._is_cancel_requested(db, org):
            raise WorkableSyncCancelled()
        counters = {
            "candidate_upserted": 0,
            "application_upserted": 0,
        }
        candidate_id = str(candidate_ref.get("id") or "").strip()
        if not candidate_id:
            return counters

        # Use bulk payload directly -- no individual candidate fetch
        candidate_payload = candidate_ref

        if self._is_cancel_requested(db, org):
            raise WorkableSyncCancelled()
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
        candidate.workable_enriched = False

        # Extract rich profile fields from bulk payload
        extracted = _extract_candidate_fields(candidate_payload)
        for field, value in extracted.items():
            setattr(candidate, field, value)

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

        # Extract application-level Workable fields
        app.workable_sourced = candidate_payload.get("sourced", None)
        profile_url = candidate_payload.get("profile_url") or candidate_payload.get("url")
        if isinstance(profile_url, str) and profile_url.strip():
            app.workable_profile_url = profile_url.strip()

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

        if self._is_cancel_requested(db, org):
            raise WorkableSyncCancelled()

        app.rank_score = _rank_score_for_application(app)
        db.flush()
        if created_app:
            counters["application_upserted"] += 1
        return counters
