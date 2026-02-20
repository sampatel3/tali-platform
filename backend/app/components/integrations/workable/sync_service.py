"""Workable pull-sync service for roles/candidates/applications."""

from __future__ import annotations

import ast
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....models.workable_sync_run import WorkableSyncRun
from ....services.document_service import (
    sanitize_json_for_storage,
    sanitize_text_for_storage,
    save_file_locally,
)
from .service import WorkableRateLimitError, WorkableService

logger = logging.getLogger(__name__)

class WorkableSyncCancelled(Exception):
    """Raised when the user requested sync cancellation; sync should stop immediately."""



def _strip_html(html: str) -> str:
    """Convert HTML to plain text, preserving basic structure for readable job specs."""
    if not html or not isinstance(html, str):
        return html or ""
    text = html
    # Block elements to newlines (order matters)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<th[^>]*>", " **", text, flags=re.IGNORECASE)
    text = re.sub(r"</th>", "** | ", text, flags=re.IGNORECASE)
    text = re.sub(r"<td[^>]*>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"</td>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<ol[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</ol>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<ul[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</ul>", "\n", text, flags=re.IGNORECASE)
    # Headings to markdown
    for level in range(1, 7):
        text = re.sub(rf"<h{level}[^>]*>(.*?)</h{level}>", rf"\n{'#' * level} \1\n", text, flags=re.IGNORECASE | re.DOTALL)
    # Bold/strong/emphasis to markdown
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    # Strip remaining tags (span, div, etc.)
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    # Fix literal backslash-n shown as text
    text = text.replace("\\n", "\n").replace("\\t", " ")
    # Remove embedded Python dict/list reprs (e.g. location object serialized into text)
    text = _remove_embedded_dict_reprs(text)
    # Normalize whitespace: collapse multiple spaces, clean up newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return sanitize_text_for_storage(text.strip())


def _remove_embedded_dict_reprs(text: str) -> str:
    """Remove Python dict/list reprs embedded in text (e.g. {'country': 'UAE'}, {'key': 'val'})."""
    if not text or not isinstance(text, str):
        return text
    result = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            depth = 0
            start = i
            found = False
            for j in range(i, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        chunk = text[start : j + 1]
                        # Location-like dict: try to format as readable text
                        if "'country'" in chunk or '"country"' in chunk or "'city'" in chunk or '"city"' in chunk:
                            parsed = _parse_location_like(chunk)
                            if parsed:
                                loc = _format_location(parsed)
                                if loc:
                                    result.append(loc)
                        # Any dict repr (key: val) - remove entirely to avoid raw Python repr in job specs
                        i = j + 1
                        found = True
                        break
            if not found:
                result.append(text[i])
                i += 1
        elif text[i] == "[":
            # Remove list reprs like ['a','b'] or [1,2,3]
            depth = 0
            start = i
            found = False
            for j in range(i, len(text)):
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth == 0:
                        i = j + 1
                        found = True
                        break
            if not found:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


def _parse_location_like(value: str) -> dict | None:
    """Try to parse a string that looks like a dict (JSON or Python repr) for location."""
    s = (value or "").strip()
    if not s or not s.startswith("{"):
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:
        parsed = ast.literal_eval(s)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, SyntaxError):
        pass
    return None


def _format_location(value) -> str | None:
    """Format a Workable location value (may be dict, JSON string, or Python repr) into readable text.
    Never returns raw dict repr - always returns human-readable string or None."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        parsed = _parse_location_like(value)
        if parsed:
            value = parsed
        else:
            return value.strip()
    if isinstance(value, dict):
        parts = []
        city = value.get("city") or value.get("city_name")
        region = value.get("region") or value.get("subregion") or value.get("state_code") or value.get("state")
        country = value.get("country") or value.get("country_name")
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
    # Reject lists, objects - never return repr
    return None


def _format_job_spec_from_api(job_data: dict) -> str:
    """Build a well-formatted job spec string from Workable job API data.
    Handles: list response (minimal), job details response ({job: {...}}), or flat dict.
    Never outputs raw dict/list repr or HTML - always sanitized.
    """
    if not job_data or not isinstance(job_data, dict):
        return ""
    # Unwrap nested structures: {"job": {...}}, {"job": {"details": {...}}}, or details from get_job_details
    merged = dict(job_data)
    for _ in range(3):  # Flatten nested job/details
        job_inner = merged.get("job")
        if isinstance(job_inner, dict):
            merged = {**merged, **job_inner}
        details = merged.get("details")  # Re-get after merge so we capture nested details
        if isinstance(details, dict):
            merged = {**merged, **details}
        merged.pop("job", None)
        merged.pop("details", None)
        if not isinstance(merged.get("job"), dict) and not isinstance(merged.get("details"), dict):
            break

    def _extract_html_or_text(val: Any) -> str | None:
        if val is None:
            return None
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for k in ("text", "html", "content"):
                v = val.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    lines: list[str] = []

    title = merged.get("title") or merged.get("name") or merged.get("headline") or "Job"
    lines.append(f"# {title}")
    lines.append("")

    # Location: always use _format_location - never output dict repr
    loc_val = merged.get("location")
    if isinstance(loc_val, list) and loc_val:
        loc_val = loc_val[0] if isinstance(loc_val[0], dict) else None
    location_str = _format_location(loc_val)
    if location_str:
        lines.append(f"**Location:** {location_str}")

    def _scalar_str(val: Any) -> str | None:
        """Convert value to display string; reject dicts/lists to avoid raw repr."""
        if val is None:
            return None
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float, bool)):
            return str(val)
        return None

    for key, label in (
        ("department", "Department"),
        ("employment_type", "Employment type"),
        ("application_url", "Apply"),
        ("state", "State"),
        ("full_title", "Full title"),
    ):
        value = _scalar_str(merged.get(key))
        if value:
            lines.append(f"**{label}:** {value}")
    lines.append("")

    # Description/requirements: extract from dict or string, always _strip_html
    for key in ("description", "full_description", "requirements", "benefits"):
        raw = merged.get(key)
        value = _extract_html_or_text(raw)
        if value:
            label = key.replace("_", " ").title()
            lines.append(f"## {label}")
            lines.append("")
            lines.append(_strip_html(value))
            lines.append("")

    result = "\n".join(lines).strip()
    # Final pass: strip any remaining embedded reprs (defense in depth)
    result = _remove_embedded_dict_reprs(result)
    return sanitize_text_for_storage(result.strip())


TERMINAL_STAGES = {"hired", "rejected", "withdrawn", "disqualified", "declined", "archived"}


def _normalize_stage_for_terminal(value: str | None) -> str | None:
    """Normalize stage string for terminal check; Workable may use various formats."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v in TERMINAL_STAGES:
        return v
    # Match "Rejected", "Hired - 2024", "Interview: Withdrawn", etc.
    for t in TERMINAL_STAGES:
        if v == t or v.startswith(t + ":") or v.startswith(t + " ") or v.endswith(":" + t) or v.endswith(" " + t):
            return t
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_terminal_stage(stage_value: str | None) -> bool:
    stage = (stage_value or "").strip().lower()
    return stage in TERMINAL_STAGES


def _is_terminal_candidate(payload: dict) -> bool:
    """Return True only when we are confident the candidate is in a terminal state."""
    stage_kind = _normalize_stage_for_terminal(str(payload.get("stage_kind") or ""))
    if stage_kind:
        return True
    stage = (
        payload.get("stage")
        or payload.get("stage_name")
        or payload.get("status")
        or ""
    )
    if _normalize_stage_for_terminal(str(stage)):
        return True
    if payload.get("disqualified") is True:
        return True
    if payload.get("hired_at"):
        return True
    return False


def _candidate_email(payload: dict) -> str | None:
    """Extract email from Workable candidate payload. Handles many response shapes."""
    def _valid_email(v) -> str | None:
        if isinstance(v, str) and "@" in v and "." in v:
            return v.strip().lower()
        return None

    for key in ("email", "work_email", "candidate_email", "email_address", "primary_email"):
        value = payload.get(key)
        if (e := _valid_email(value)):
            return e
    # Workable sometimes provides a list of emails
    for key in ("emails", "email_addresses"):
        items = payload.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    value = item.get("value") or item.get("email") or item.get("address")
                    if (e := _valid_email(value)):
                        return e
                elif isinstance(item, str) and (e := _valid_email(item)):
                    return e
    # Nested objects (contact_info common in Workable API)
    for obj_key in ("contact", "profile", "info", "personal_info", "contact_info", "details"):
        obj = payload.get(obj_key)
        if isinstance(obj, dict):
            for k in ("email", "email_address", "primary_email", "work_email"):
                if (e := _valid_email(obj.get(k))):
                    return e
    return None


def _candidate_name(payload: dict, fallback: str | None = None) -> str | None:
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return sanitize_text_for_storage(name.strip())
    first = (payload.get("firstname") or "").strip()
    last = (payload.get("lastname") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return sanitize_text_for_storage(full)
    return sanitize_text_for_storage(fallback) if fallback else None


def _candidate_position(payload: dict, job_title: str | None = None) -> str | None:
    for key in ("headline", "title", "position"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_storage(value.strip())
    return sanitize_text_for_storage(job_title) if job_title else None


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
        fields["headline"] = sanitize_text_for_storage(headline.strip())

    # Image
    image_url = payload.get("image_url") or payload.get("avatar_url")
    if isinstance(image_url, str) and image_url.strip():
        fields["image_url"] = sanitize_text_for_storage(image_url.strip())

    # Location
    location = payload.get("location") or {}
    if isinstance(location, dict):
        city = location.get("city")
        country = location.get("country")
        if isinstance(city, str) and city.strip():
            fields["location_city"] = sanitize_text_for_storage(city.strip())
        if isinstance(country, str) and country.strip():
            fields["location_country"] = sanitize_text_for_storage(country.strip())
    elif isinstance(location, str) and location.strip():
        fields["location_city"] = sanitize_text_for_storage(location.strip())

    # Phone
    phone = payload.get("phone")
    if isinstance(phone, str) and phone.strip():
        fields["phone"] = sanitize_text_for_storage(phone.strip())

    # Profile URL
    profile_url = payload.get("profile_url") or payload.get("url")
    if isinstance(profile_url, str) and profile_url.strip():
        fields["profile_url"] = sanitize_text_for_storage(profile_url.strip())

    # Social profiles
    socials = payload.get("social_profiles")
    if isinstance(socials, list) and socials:
        fields["social_profiles"] = sanitize_json_for_storage([
            {k: v for k, v in s.items() if k in ("type", "url", "name", "username")}
            for s in socials
            if isinstance(s, dict)
        ])

    # Tags
    tags = payload.get("tags")
    if isinstance(tags, list) and tags:
        fields["tags"] = [sanitize_text_for_storage(str(t)) for t in tags if t]

    # Skills
    skills = payload.get("skills")
    if isinstance(skills, list) and skills:
        fields["skills"] = [sanitize_text_for_storage(str(s)) for s in skills if s]

    # Education
    education = payload.get("education_entries") or payload.get("education")
    if isinstance(education, list) and education:
        fields["education_entries"] = sanitize_json_for_storage([
            {k: v for k, v in e.items() if k in ("school", "degree", "field_of_study", "start_date", "end_date")}
            for e in education
            if isinstance(e, dict)
        ])

    # Experience
    experience = payload.get("experience_entries") or payload.get("experience")
    if isinstance(experience, list) and experience:
        fields["experience_entries"] = sanitize_json_for_storage([
            {k: v for k, v in e.items() if k in ("company", "title", "start_date", "end_date", "current", "summary", "industry")}
            for e in experience
            if isinstance(e, dict)
        ])

    # Summary
    summary = payload.get("summary") or payload.get("cover_letter")
    if isinstance(summary, str) and summary.strip():
        fields["summary"] = sanitize_text_for_storage(summary.strip())

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

    def _get_sync_run(self, db: Session, run_id: int | None) -> WorkableSyncRun | None:
        if not run_id:
            return None
        return db.query(WorkableSyncRun).filter(WorkableSyncRun.id == run_id).first()

    def _build_db_snapshot(self, db: Session, org: Organization) -> dict:
        return {
            "roles_active": (
                db.query(Role)
                .filter(Role.organization_id == org.id, Role.deleted_at.is_(None))
                .count()
            ),
            "applications_active": (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.organization_id == org.id,
                    CandidateApplication.deleted_at.is_(None),
                )
                .count()
            ),
            "candidates_active": (
                db.query(Candidate)
                .filter(Candidate.organization_id == org.id, Candidate.deleted_at.is_(None))
                .count()
            ),
        }

    def _persist_progress(
        self,
        db: Session,
        org: Organization,
        run: WorkableSyncRun | None,
        summary: dict,
        *,
        final_status: str | None = None,
    ) -> None:
        errors = []
        for err in summary.get("errors") or []:
            text = sanitize_text_for_storage(str(err))
            if text:
                errors.append(text)
        summary["errors"] = errors
        selected_job_shortcodes = []
        for value in summary.get("selected_job_shortcodes") or []:
            text = sanitize_text_for_storage(str(value or "").strip())
            if text:
                selected_job_shortcodes.append(text)
        summary["selected_job_shortcodes"] = selected_job_shortcodes
        summary["selected_jobs_count"] = int(summary.get("selected_jobs_count") or len(selected_job_shortcodes))
        summary["selected_jobs_applied"] = int(summary.get("selected_jobs_applied") or 0)
        summary["db_snapshot"] = sanitize_json_for_storage(summary.get("db_snapshot") or {})

        if run:
            run.phase = sanitize_text_for_storage(summary.get("phase") or "") or None
            run.jobs_total = int(summary.get("jobs_total") or 0)
            run.jobs_processed = int(summary.get("jobs_processed") or 0)
            run.candidates_seen = int(summary.get("candidates_seen") or 0)
            run.candidates_upserted = int(summary.get("candidates_upserted") or 0)
            run.applications_upserted = int(summary.get("applications_upserted") or 0)
            run.errors = errors
            run.db_snapshot = summary["db_snapshot"]
            if final_status:
                run.status = final_status
                run.finished_at = _now()

        if final_status:
            org.workable_sync_progress = None
            org.workable_sync_started_at = None
            org.workable_sync_cancel_requested_at = None
        else:
            org.workable_sync_progress = sanitize_json_for_storage(
                {
                    "run_id": summary.get("run_id"),
                    "mode": summary.get("mode"),
                    "phase": summary.get("phase"),
                    "jobs_total": summary.get("jobs_total"),
                    "jobs_processed": summary.get("jobs_processed"),
                    "jobs_upserted": summary.get("jobs_upserted"),
                    "candidates_seen": summary.get("candidates_seen"),
                    "candidates_upserted": summary.get("candidates_upserted"),
                    "applications_upserted": summary.get("applications_upserted"),
                    "errors": errors,
                    "current_step": summary.get("current_step"),
                    "current_job_shortcode": summary.get("current_job_shortcode"),
                    "current_candidate_index": summary.get("current_candidate_index"),
                    "last_request": summary.get("last_request"),
                    "selected_job_shortcodes": summary.get("selected_job_shortcodes"),
                    "selected_jobs_count": summary.get("selected_jobs_count"),
                    "selected_jobs_applied": summary.get("selected_jobs_applied"),
                    "db_snapshot": summary.get("db_snapshot"),
                }
            )
        db.commit()

    def _is_cancel_requested(self, db: Session, org: Organization, run: WorkableSyncRun | None = None) -> bool:
        if run is not None:
            db.refresh(run)
            if run.cancel_requested_at is not None:
                return True
        db.refresh(org)
        return org.workable_sync_cancel_requested_at is not None

    def sync_org(
        self,
        db: Session,
        org: Organization,
        *,
        full_resync: bool = False,
        run_id: int | None = None,
        mode: str = "metadata",
        selected_job_shortcodes: list[str] | None = None,
    ) -> dict:
        run = self._get_sync_run(db, run_id)
        requested_mode = (mode or "metadata").strip().lower()
        if requested_mode not in {"metadata", "full"}:
            requested_mode = "metadata"
        effective_mode = "metadata"  # Full mode is reserved in this cycle.
        selected_identifiers: set[str] = set()
        for value in selected_job_shortcodes or []:
            normalized = sanitize_text_for_storage(str(value or "").strip())
            if normalized:
                selected_identifiers.add(normalized)

        summary = {
            "run_id": run.id if run else None,
            "requested_mode": requested_mode,
            "mode": effective_mode,
            "full_mode_reserved": requested_mode == "full",
            "full_resync": bool(full_resync),
            "phase": "listing_jobs",
            "jobs_total": 0,
            "jobs_processed": 0,
            "jobs_seen": 0,
            "jobs_upserted": 0,
            "candidates_seen": 0,
            "candidates_upserted": 0,
            "applications_upserted": 0,
            "errors": [],
            "current_step": "listing_jobs",
            "last_request": "GET /jobs?state=published",
            "current_job_shortcode": None,
            "current_candidate_index": None,
            "selected_job_shortcodes": sorted(selected_identifiers),
            "selected_jobs_count": len(selected_identifiers),
            "selected_jobs_applied": 0,
            "db_snapshot": {},
        }
        now = _now()
        final_status = "success"

        try:
            org.workable_sync_cancel_requested_at = None
            org.workable_sync_started_at = now
            if run:
                run.mode = requested_mode
                run.status = "running"
                run.phase = "listing_jobs"
                run.cancel_requested_at = None
                if run.started_at is None:
                    run.started_at = now
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            self._persist_progress(db, org, run, summary)

            all_jobs = self.client.list_open_jobs()
            summary["jobs_seen"] = len(all_jobs)
            jobs = all_jobs
            if selected_identifiers:
                filtered_jobs: list[dict] = []
                matched_identifiers: set[str] = set()
                for job in all_jobs:
                    if not isinstance(job, dict):
                        continue
                    job_identifiers: set[str] = set()
                    for raw in (job.get("shortcode"), job.get("id")):
                        value = sanitize_text_for_storage(str(raw or "").strip())
                        if value:
                            job_identifiers.add(value)
                    if job_identifiers.intersection(selected_identifiers):
                        filtered_jobs.append(job)
                        matched_identifiers.update(job_identifiers.intersection(selected_identifiers))
                jobs = filtered_jobs
                missing = sorted(selected_identifiers - matched_identifiers)
                if missing:
                    summary["errors"].append(
                        f"{len(missing)} selected roles were not found in Workable jobs."
                    )
                    final_status = "partial"
            summary["selected_jobs_applied"] = len(jobs)
            summary["jobs_total"] = len(jobs)
            summary["phase"] = "syncing_candidates" if jobs else "completed"
            summary["current_step"] = "listing_candidates" if jobs else None
            summary["last_request"] = "GET /jobs (filtered)" if jobs and selected_identifiers else ("GET /jobs (done)" if jobs else "GET /jobs (0 jobs)")
            if not jobs:
                if selected_identifiers:
                    logger.warning("Workable sync selection matched 0 jobs for org_id=%s", org.id)
                    summary["errors"].append("No Workable jobs matched your selected roles.")
                else:
                    logger.warning("Workable list_open_jobs returned 0 jobs for org_id=%s", org.id)
                    summary["errors"].append(
                        "Workable returned 0 jobs. Ensure your token includes r_jobs and the account has published/open jobs."
                    )
                final_status = "partial"
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            self._persist_progress(db, org, run, summary)

            for job_idx, job in enumerate(jobs):
                if self._is_cancel_requested(db, org, run):
                    raise WorkableSyncCancelled()
                try:
                    role, created_role = self._upsert_role(db, org, job)
                    if created_role:
                        summary["jobs_upserted"] += 1

                    shortcode = sanitize_text_for_storage(str(job.get("shortcode") or job.get("id") or "?"))[:20]
                    summary["phase"] = "syncing_candidates"
                    summary["current_step"] = "listing_candidates"
                    summary["current_job_shortcode"] = shortcode
                    summary["current_candidate_index"] = None
                    summary["last_request"] = f"GET /jobs/{shortcode}/candidates"
                    self._persist_progress(db, org, run, summary)

                    candidates = self._list_job_candidates_for_job(job=job, role=role)
                    total_candidates = len(candidates)
                    if not candidates:
                        logger.info("list_job_candidates returned 0 for job shortcode=%s", job.get("shortcode"))

                    for idx, candidate_ref in enumerate(candidates):
                        if self._is_cancel_requested(db, org, run):
                            raise WorkableSyncCancelled()

                        summary["candidates_seen"] += 1
                        cid = sanitize_text_for_storage(str(candidate_ref.get("id") or "?"))[:12]
                        summary["current_step"] = "syncing_candidate"
                        summary["current_candidate_index"] = (
                            f"{idx + 1}/{total_candidates}" if total_candidates else str(idx + 1)
                        )
                        summary["last_request"] = f"syncing candidate {cid}"
                        try:
                            synced = self._sync_candidate_for_role(
                                db=db,
                                org=org,
                                role=role,
                                job=job,
                                candidate_ref=candidate_ref,
                                now=now,
                                run=run,
                            )
                            summary["candidates_upserted"] += synced.get("candidate_upserted", 0)
                            summary["applications_upserted"] += synced.get("application_upserted", 0)
                        except WorkableSyncCancelled:
                            raise
                        except Exception as exc:
                            logger.exception("Failed syncing candidate for job_shortcode=%s", shortcode)
                            summary["errors"].append(str(exc))
                            final_status = "partial"

                        if (idx + 1) % 5 == 0 or idx == 0:
                            summary["db_snapshot"] = self._build_db_snapshot(db, org)
                            self._persist_progress(db, org, run, summary)

                    summary["jobs_processed"] = job_idx + 1
                    summary["db_snapshot"] = self._build_db_snapshot(db, org)
                    self._persist_progress(db, org, run, summary)
                except WorkableRateLimitError as exc:
                    logger.warning("Workable sync rate-limited; stopping early for org_id=%s", org.id)
                    summary["errors"].append(str(exc))
                    final_status = "partial"
                    break
                except WorkableSyncCancelled:
                    raise
                except Exception as exc:
                    logger.exception("Failed syncing job for org_id=%s", org.id)
                    summary["errors"].append(str(exc))
                    final_status = "partial"

            if self._is_cancel_requested(db, org, run):
                raise WorkableSyncCancelled()

            summary["phase"] = "completed"
            summary["current_step"] = None
            summary["current_job_shortcode"] = None
            summary["current_candidate_index"] = None
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            org.workable_last_sync_at = now
            org.workable_last_sync_status = "success" if final_status == "success" else "partial"
            org.workable_last_sync_summary = sanitize_json_for_storage(dict(summary))
            self._persist_progress(
                db,
                org,
                run,
                summary,
                final_status=org.workable_last_sync_status,
            )
            return summary
        except WorkableSyncCancelled:
            summary["errors"].append("Sync cancelled by user")
            summary["phase"] = "cancelled"
            summary["current_step"] = None
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            org.workable_last_sync_at = _now()
            org.workable_last_sync_status = "cancelled"
            org.workable_last_sync_summary = sanitize_json_for_storage(dict(summary))
            self._persist_progress(db, org, run, summary, final_status="cancelled")
            return summary
        except Exception as exc:
            logger.exception("Workable org sync failed")
            summary["errors"].append(str(exc))
            summary["phase"] = "failed"
            summary["current_step"] = None
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            org.workable_last_sync_at = _now()
            org.workable_last_sync_status = "failed"
            org.workable_last_sync_summary = sanitize_json_for_storage(dict(summary))
            self._persist_progress(db, org, run, summary, final_status="failed")
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
        # Prefer shortcode (used by Workable API for /jobs/:shortcode/candidates)
        job_id = sanitize_text_for_storage(str(job.get("shortcode") or job.get("id") or "").strip())
        title = sanitize_text_for_storage(
            str(job.get("title") or job.get("name") or f"Workable role {job_id or 'unknown'}").strip()
        )
        # Always fetch job details to get consistent structure (location, description, etc.).
        details = self._job_details_for_role(job=job, role=None)

        def _get_desc(d: dict) -> str:
            for key in ("description", "full_description", "requirements"):
                v = d.get(key) if isinstance(d, dict) else None
                if isinstance(v, str) and v.strip():
                    return sanitize_text_for_storage(v)
            for sub in (d.get("job"), d.get("details")):
                if isinstance(sub, dict):
                    for key in ("description", "full_description", "requirements"):
                        v = sub.get(key)
                        if isinstance(v, str) and v.strip():
                            return sanitize_text_for_storage(v)
            return ""

        description = _get_desc(details) or _get_desc(job) or ""
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
        role.workable_job_data = sanitize_json_for_storage({**job, "details": details} if details else job)
        role.name = title
        # Build one formatted spec from full API data for display and attachment
        formatted_spec = _format_job_spec_from_api(role.workable_job_data or {})
        if formatted_spec:
            safe_spec = sanitize_text_for_storage(formatted_spec)
            role.job_spec_text = safe_spec
            role.description = safe_spec
        else:
            stripped = _strip_html(description) if isinstance(description, str) and description.strip() else ""
            safe_desc = sanitize_text_for_storage(stripped)
            role.description = safe_desc or role.description
            if stripped:
                role.job_spec_text = safe_desc
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
                role.job_spec_filename = sanitize_text_for_storage(f"job-spec-{role.name or role.id}.txt").replace("/", "-")
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
        run: WorkableSyncRun | None = None,
    ) -> dict:
        if self._is_cancel_requested(db, org, run):
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

        if self._is_cancel_requested(db, org, run):
            raise WorkableSyncCancelled()
        stage = (
            candidate_payload.get("stage")
            or candidate_ref.get("stage")
            or candidate_ref.get("stage_name")
            or ""
        )
        if _is_terminal_candidate(candidate_payload) or _is_terminal_candidate(candidate_ref):
            logger.debug(
                "Skipping candidate id=%s (terminal stage: %s)",
                candidate_id,
                candidate_payload.get("stage") or candidate_payload.get("stage_name") or candidate_ref.get("stage"),
            )
            return counters

        email = _candidate_email(candidate_payload) or _candidate_email(candidate_ref)
        if not email:
            logger.debug(
                "Candidate id=%s has no email in list payload; syncing by Workable ID only.",
                candidate_id,
            )

        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == org.id,
                Candidate.workable_candidate_id == candidate_id,
            )
            .first()
        )
        if not candidate and email:
            candidate = (
                db.query(Candidate)
                .filter(
                    Candidate.organization_id == org.id,
                    Candidate.email == email,
                )
                .first()
            )
        if not candidate:
            candidate = Candidate(
                organization_id=org.id,
                email=sanitize_text_for_storage(email) if email else None,
            )
            db.add(candidate)

        candidate.deleted_at = None  # restore if was soft-deleted
        if email:
            candidate.email = sanitize_text_for_storage(email)
        fallback_name = candidate.full_name or email or f"Workable candidate {candidate_id}"
        candidate.full_name = _candidate_name(candidate_payload, fallback=fallback_name)
        candidate.position = _candidate_position(candidate_payload, role.name)
        candidate.workable_candidate_id = sanitize_text_for_storage(candidate_id)
        candidate.workable_data = sanitize_json_for_storage(candidate_payload)
        candidate.workable_enriched = False

        # Extract rich profile fields from bulk payload
        extracted = _extract_candidate_fields(candidate_payload)
        for field, value in extracted.items():
            setattr(candidate, field, value)

        db.flush()
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
        if not app:
            app = CandidateApplication(
                organization_id=org.id,
                candidate_id=candidate.id,
                role_id=role.id,
                status="applied",
            )
            db.add(app)

        app.deleted_at = None  # restore if was soft-deleted
        app.source = "workable"
        app.status = sanitize_text_for_storage(str(stage or app.status or "applied"))
        app.workable_candidate_id = sanitize_text_for_storage(candidate_id)
        app.workable_stage = sanitize_text_for_storage(str(stage or ""))
        app.last_synced_at = now

        # Extract application-level Workable fields
        app.workable_sourced = candidate_payload.get("sourced", None)
        profile_url = candidate_payload.get("profile_url") or candidate_payload.get("url")
        if isinstance(profile_url, str) and profile_url.strip():
            app.workable_profile_url = sanitize_text_for_storage(profile_url.strip())

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

        if self._is_cancel_requested(db, org, run):
            raise WorkableSyncCancelled()

        app.rank_score = _rank_score_for_application(app)
        db.flush()
        counters["application_upserted"] += 1
        return counters
