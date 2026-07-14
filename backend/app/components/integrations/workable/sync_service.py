"""Workable pull-sync service for roles/candidates/applications."""

from __future__ import annotations

import ast
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from ....models.role_brief import RoleBrief
from ....models.workable_sync_run import WorkableSyncRun
from ....platform.config import settings
from ....domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    map_legacy_status_to_pipeline,
    normalize_pipeline_key,
    reconcile_post_handover_advanced,
    transition_outcome,
    transition_stage,
)
from ....domains.assessments_runtime.role_support import (
    is_resolved,
    refresh_application_score_cache,
)
from ....services.document_service import (
    extract_text,
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from ....services.s3_service import (
    generate_s3_key,
    upload_bytes_to_s3,
)
from ....services.application_events import on_application_created
from ....cv_parsing.origins import CV_PARSE_ORIGIN_ATS_INGEST
from ....services.agent_policy_settings import apply_workspace_agent_defaults
from ....services.job_page_lifecycle import role_allows_new_paid_ats_work
from ....services.fit_matching_service import (
    CvMatchValidationError,
    calculate_cv_job_match_sync,
    calculate_cv_job_match_v4_sync,
)
from ....services.spec_normalizer import normalize_spec
from ....services.interview_support_service import build_role_interview_pack_templates
from ....services.job_spec_override_service import has_manual_job_spec_override
from ....services.pre_screening_service import refresh_pre_screening_fields
from ....services.taali_scoring import normalize_score_100
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


def _job_spec_block_key(value: str) -> str:
    """Canonical key for duplicate Workable section blocks."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _merge_cached_workable_job_data(cached: dict | None, current: dict) -> dict:
    """Merge a list-job payload over the last good Workable payload.

    ``GET /jobs`` is deliberately lightweight while ``GET /jobs/:shortcode``
    carries the description.  When that detail request transiently returns no
    payload, keep the last good nested detail fields while still accepting fresh
    list metadata such as title/state.  Empty values from the lightweight payload
    must not erase non-empty cached values during that degraded sync.

    Successful detail fetches do not use this helper, so their payload remains the
    source of truth exactly as before.
    """
    merged = dict(cached) if isinstance(cached, dict) else {}
    for key, value in (current or {}).items():
        cached_value = merged.get(key)
        if isinstance(cached_value, dict) and isinstance(value, dict):
            merged[key] = _merge_cached_workable_job_data(cached_value, value)
            continue
        if value not in (None, "", [], {}) or key not in merged:
            merged[key] = value

    # Expanded list rows can carry only one fresh spec field (commonly
    # ``description``) while the cached detail payload still owns the other
    # sections. The formatter flattens nested ``job``/``details`` dictionaries
    # over top-level values, so remove stale nested copies of each fresh
    # top-level field; otherwise the cached description would silently win.
    fresh_top_level_spec_keys = {
        key
        for key in ("description", "full_description", "requirements", "benefits")
        if isinstance((current or {}).get(key), str) and (current or {}).get(key).strip()
    }

    def _without_stale_spec_keys(value: dict) -> dict:
        cleaned = {}
        for key, item in value.items():
            if key in fresh_top_level_spec_keys:
                continue
            if key in ("job", "details") and isinstance(item, dict):
                cleaned[key] = _without_stale_spec_keys(item)
            else:
                cleaned[key] = item
        return cleaned

    if fresh_top_level_spec_keys:
        for wrapper_key in ("job", "details"):
            wrapper = merged.get(wrapper_key)
            if isinstance(wrapper, dict):
                merged[wrapper_key] = _without_stale_spec_keys(wrapper)
    return merged


def _workable_payload_has_spec_content(value: object) -> bool:
    """Whether a cached Workable payload is rich enough for provenance checks."""

    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if key in ("description", "full_description", "requirements", "benefits"):
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict) and any(
                isinstance(item.get(part), str) and item.get(part).strip()
                for part in ("text", "html", "content")
            ):
                return True
        if key in ("job", "details") and _workable_payload_has_spec_content(item):
            return True
    return False


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

    section_content = {
        "Description": [],
        "Requirements": [],
        "Benefits": [],
    }
    seen_blocks: set[str] = set()

    # Description/full_description are the same user-facing section. Workable can
    # return both, and full_description often repeats the short description.
    for key, label in (
        ("description", "Description"),
        ("full_description", "Description"),
        ("requirements", "Requirements"),
        ("benefits", "Benefits"),
    ):
        raw = merged.get(key)
        value = _extract_html_or_text(raw)
        if value:
            cleaned = _strip_html(value)
            unique_blocks = []
            for block in re.split(r"\n{2,}", cleaned):
                block = block.strip()
                key_for_block = _job_spec_block_key(block)
                if not block or key_for_block in seen_blocks:
                    continue
                seen_blocks.add(key_for_block)
                unique_blocks.append(block)
            if unique_blocks:
                section_content[label].append("\n\n".join(unique_blocks))

    for label in ("Description", "Requirements", "Benefits"):
        if section_content[label]:
            lines.append(f"## {label}")
            lines.append("")
            lines.append("\n\n".join(section_content[label]))
            lines.append("")

    result = "\n".join(lines).strip()
    # Final pass: strip any remaining embedded reprs (defense in depth)
    result = _remove_embedded_dict_reprs(result)
    return sanitize_text_for_storage(result.strip())


# Workable stages where the hiring decision is effectively made and Tali has
# nothing left to actively do → park in `advanced`. Covers negatives
# (rejected/disqualified/declined) AND positives (offer/hired). "offer" is
# terminal-but-pending: it parks the candidate in `advanced` with outcome left
# `open` (not hired yet) — it's a POSITIVE training label via workable_stage,
# captured by the cv_match calibrator. Mid-interview stages (phone/technical/
# final interview) are deliberately NOT here — they stay in Tali's funnel.
TERMINAL_STAGES = {"hired", "rejected", "withdrawn", "disqualified", "declined", "archived", "offer"}


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


def _adopt_requisition_role(
    db: Session,
    org: Organization,
    *,
    job_id: str,
    title: str,
    description: str,
) -> Role | None:
    """Requisition -> Workable bridge: link a freshly-imported Workable job back
    to the INACTIVE Taali job a requisition published, instead of minting a
    duplicate role.

    The recruiter pasted the requisition spec — which carries a ``Taali ref:
    TAL-XXXXX`` line — into the Workable job description. We scan the imported
    description (then the title) for that code; if it points to a draft
    requisition role in this org that isn't yet linked to Workable, we adopt that
    role: attach the Workable job id and flip it ``draft`` -> ``open``. The
    brief's recruiter criteria survive because the caller treats an adopted role
    as existing (``created=False``), so it skips the org-criteria snapshot.

    Returns the adopted role, or None when there's no usable match (no code, no
    matching brief, the brief has no role, or the role is already linked / past
    draft). A stable ``job_id`` is required so the next sync re-finds the role by
    ``workable_job_id`` rather than re-adopting.
    """
    if not job_id:
        return None
    from ....services.role_brief_service import find_ref_code

    code = find_ref_code(description) or find_ref_code(title)
    if not code:
        return None
    brief = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == org.id, RoleBrief.ref_code == code)
        .first()
    )
    if brief is None or not brief.role_id:
        return None
    role = (
        db.query(Role)
        .filter(Role.id == brief.role_id, Role.organization_id == org.id)
        .first()
    )
    if role is None:
        return None
    # Adopt only an unlinked requisition job. ``open`` is valid here: the normal
    # one-click flow opens the native page as soon as the recruiter turns the
    # agent on, and optional ATS distribution may be connected afterwards. A
    # provider link or terminal local state is never eligible, so a re-imported
    # spec cannot hijack an existing/filled role.
    if (
        role.workable_job_id
        or getattr(role, "bullhorn_job_order_id", None)
        or role.job_status not in (None, JOB_STATUS_DRAFT, JOB_STATUS_OPEN)
    ):
        return None
    role.workable_job_id = job_id
    role.job_status = JOB_STATUS_OPEN
    role.deleted_at = None
    logger.info(
        "Workable bridge: adopted requisition role_id=%s into job_id=%s via ref %s",
        role.id,
        job_id,
        code,
    )
    return role


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


def _is_disqualified(payload: dict, ref: dict | None = None) -> bool:
    """True when Workable marks the candidate disqualified.

    Disqualification is an overlay flag, not a stage — the candidate keeps
    their stage (e.g. "Technical Interview") in Workable. We handle it
    separately from terminal *stages* (hired/rejected) so the row gets
    updated rather than skipped.
    """
    if payload.get("disqualified") is True:
        return True
    if ref is not None and ref.get("disqualified") is True:
        return True
    return False


def _disqualified_at_from_payload(payload: dict, ref: dict | None = None) -> datetime | None:
    for source in (payload, ref or {}):
        raw = source.get("disqualified_at")
        if isinstance(raw, str) and raw.strip():
            text = raw.strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                continue
    return None


# Maps a terminal Workable stage to Tali's application_outcome so the
# calibration loop (agent_runtime.outcome_learning) can learn from the realized
# result. "archived" is intentionally omitted — too ambiguous to label.
_TERMINAL_STAGE_TO_OUTCOME = {
    "hired": "hired",
    "rejected": "rejected",
    "disqualified": "rejected",
    "declined": "rejected",
    "withdrawn": "withdrawn",
}


def _terminal_outcome(payload: dict, ref: dict | None = None, *, disqualified: bool = False) -> str | None:
    """Resolve the realized application_outcome from a terminal Workable payload.

    Returns one of ``hired`` / ``rejected`` / ``withdrawn``, or ``None`` when the
    payload is terminal but carries no outcome we can confidently label.
    """
    if disqualified:
        return "rejected"
    for source in (payload, ref or {}):
        if source.get("hired_at"):
            return "hired"
    for source in (payload, ref or {}):
        for raw in (source.get("stage_kind"), source.get("stage"), source.get("stage_name"), source.get("status")):
            normalized = _normalize_stage_for_terminal(str(raw or ""))
            if normalized:
                return _TERMINAL_STAGE_TO_OUTCOME.get(normalized)
    return None


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


_PHONE_NON_DIGITS = re.compile(r"\D+")


def _normalize_phone_for_match(raw: str | None) -> str | None:
    """The last 9 digits of a phone number — a stable dedup key across the
    formatting/country-code drift in Workable phones ("+971 50 202 2165",
    "+971 +971 502022165", "0502022165" all collapse to "502022165").

    Returns None for anything under 9 digits: too little signal to risk
    merging two different people onto one profile.
    """
    digits = _PHONE_NON_DIGITS.sub("", raw or "")
    return digits[-9:] if len(digits) >= 9 else None


def _candidate_phone(payload: dict) -> str | None:
    """Extract a raw phone string from a Workable candidate payload."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("phone")
    if isinstance(value, str) and value.strip():
        return value.strip()
    for obj_key in ("contact", "profile", "info", "personal_info", "contact_info", "details"):
        obj = payload.get(obj_key)
        if isinstance(obj, dict):
            v = obj.get("phone")
            if isinstance(v, str) and v.strip():
                return v.strip()
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
    if getattr(app, "pre_screen_score_100", None) is not None:
        return app.pre_screen_score_100
    if app.workable_score is not None:
        return app.workable_score
    return app.cv_match_score


def _normalize_cv_match_score_100(score: float | int | None, details: dict | None = None) -> float | None:
    """Coerce a freshly-computed CV-match score into 0-100 for persistence.

    The v3 fit-matching path always emits 0-100. The legacy
    ``numeric <= 10 → ×10`` fallback silently inflated real weak scores
    (e.g. 9.6 → 96), so we route through the shared normalizer instead.
    """
    if score is None:
        return None
    scale = str((details or {}).get("score_scale") or "").strip().lower()
    if "10" in scale and "100" not in scale:
        try:
            numeric = float(score)
        except (TypeError, ValueError):
            return None
        if numeric < 0:
            return None
        return round(max(0.0, min(100.0, numeric * 10.0)), 1)
    return normalize_score_100(score)


def _normalize_cv_match_details(details: dict | None, *, final_score_100: float | None) -> dict | None:
    payload = dict(details or {})
    if final_score_100 is None:
        return payload or None
    payload.setdefault("score_scale", "0-100")
    payload.setdefault("final_score_100", final_score_100)
    return payload


def _store_candidate_resume(
    *,
    app: CandidateApplication,
    candidate: Candidate,
    filename: str,
    content: bytes,
) -> bool:
    """Persist a CV fetched from Workable into the active object store.

    Bytes go straight to S3/Tigris — no local-disk hop, no fallback. If
    object storage is unavailable, we skip the store and return False so
    the sync loop logs the candidate and moves on (rather than silently
    writing to ephemeral Railway disk like it used to).
    """
    if not content:
        return False
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    preview_only_exts = {"pdf", "png", "jpg", "jpeg", "webp"}
    text_exts = {"pdf", "docx", "txt"}
    if ext not in (text_exts | preview_only_exts):
        return False
    extracted = sanitize_text_for_storage(extract_text(content, ext)) if ext in text_exts else ""
    if not extracted and ext not in preview_only_exts:
        return False

    entity_id = app.id or candidate.id
    s3_key = generate_s3_key("cv", entity_id, filename)
    import mimetypes as _mt
    content_type = _mt.guess_type(filename)[0] or "application/octet-stream"
    file_url = upload_bytes_to_s3(content, s3_key, content_type=content_type)
    if not file_url:
        logger.warning(
            "Skipping CV store for candidate=%s app=%s filename=%s — object storage unavailable",
            candidate.id, app.id, filename,
        )
        return False

    now = _now()
    app.cv_file_url = file_url
    app.cv_filename = sanitize_text_for_storage(filename)
    app.cv_text = extracted
    app.cv_uploaded_at = now
    # Flag-only PDF-bytes hygiene scan; promoted at score time into
    # integrity_signals.document_hygiene.pdf. Best-effort, never blocks the sync.
    if ext == "pdf":
        from ....services.document_hygiene import stash_pdf_hygiene_on_application

        stash_pdf_hygiene_on_application(app, content, ext)
    candidate.cv_file_url = file_url
    candidate.cv_filename = sanitize_text_for_storage(filename)
    candidate.cv_text = extracted
    candidate.cv_uploaded_at = now
    return True


def _compute_cv_match_for_application(app: CandidateApplication) -> bool:
    role = app.role
    cv_text = (app.cv_text or "").strip()
    job_spec_text = ((role.job_spec_text if role else None) or "").strip()
    if not cv_text or not job_spec_text or not settings.ANTHROPIC_API_KEY:
        return False

    criteria_payload: list[dict] = []
    if role is not None:
        try:
            for c in sorted(role.criteria or [], key=lambda c: getattr(c, "ordering", 0)):
                if getattr(c, "deleted_at", None) is not None:
                    continue
                criteria_payload.append(
                    {
                        "id": int(c.id),
                        "text": str(c.text or "").strip(),
                        "must_have": bool(c.must_have),
                        "source": str(c.source or "recruiter"),
                    }
                )
        except Exception:
            criteria_payload = []

    fit_metering = {
        "feature": "fit_matching",
        "organization_id": getattr(app, "organization_id", None),
        "role_id": getattr(app, "role_id", None),
        "entity_id": f"application:{app.id}",
    }
    if criteria_payload:
        spec = normalize_spec(job_spec_text)
        try:
            result = calculate_cv_job_match_v4_sync(
                cv_text=cv_text,
                role_criteria=criteria_payload,
                spec_description=spec.description,
                spec_requirements=spec.requirements,
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.resolved_claude_scoring_model,
                metering=fit_metering,
            )
        except CvMatchValidationError:
            return False
    else:
        from ....services.role_criteria_service import render_role_intent_lines

        # v3 fallback. Pass each chip as one bullet line — the v3 prompt's
        # "Recruiter-added scoring criteria" section just wants a flat
        # list, not the bucketed structure.
        chip_lines = render_role_intent_lines(role) if role else []
        result = calculate_cv_job_match_sync(
            cv_text=cv_text,
            job_spec_text=job_spec_text,
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.resolved_claude_scoring_model,
            additional_requirements="\n".join(chip_lines) or None,
            metering=fit_metering,
        )
    raw_details = result.get("match_details", {}) if isinstance(result, dict) else {}
    normalized_score = _normalize_cv_match_score_100(
        result.get("cv_job_match_score") if isinstance(result, dict) else None,
        raw_details if isinstance(raw_details, dict) else None,
    )
    app.cv_match_score = normalized_score
    app.cv_match_details = _normalize_cv_match_details(
        raw_details if isinstance(raw_details, dict) else None,
        final_score_100=normalized_score,
    )
    app.cv_match_scored_at = _now()
    refresh_pre_screening_fields(app)
    return True


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

    # Tags. Workable returns either plain strings or
    # ``{"name": "senior"}`` dicts depending on endpoint version. The
    # prior implementation called ``str(t)`` on dicts which stored the
    # Python repr (e.g. ``"{'name': 'senior'}"``) as a string and
    # poisoned downstream consumers — extract the readable label here.
    def _label_value(item: Any) -> str | None:
        if isinstance(item, dict):
            value = (
                item.get("name")
                or item.get("body")
                or item.get("text")
                or item.get("label")
            )
            return value if isinstance(value, str) and value.strip() else None
        if isinstance(item, str):
            return item.strip() or None
        return None

    tags = payload.get("tags")
    if isinstance(tags, list) and tags:
        cleaned_tags = [
            sanitize_text_for_storage(label)
            for label in (_label_value(t) for t in tags)
            if label
        ]
        if cleaned_tags:
            fields["tags"] = cleaned_tags

    # Skills — same shape variability as tags.
    skills = payload.get("skills")
    if isinstance(skills, list) and skills:
        cleaned_skills = [
            sanitize_text_for_storage(label)
            for label in (_label_value(s) for s in skills)
            if label
        ]
        if cleaned_skills:
            fields["skills"] = cleaned_skills

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


# How long a role's cached Workable stage list is trusted before the next sync
# refetches it. Stages change very rarely (recruiters edit a pipeline maybe once
# a year), so a generous TTL keeps us well under Workable's rate limit while
# still picking up the occasional pipeline edit within a few hours.
WORKABLE_STAGES_TTL = timedelta(hours=6)

# Local-write-wins guard. When Taali itself moved a candidate (a recruiter
# advance/move that Taali wrote to Workable), it stamps
# ``workable_stage_local_write_at``. A candidate sync running with a bulk-list
# snapshot fetched BEFORE that move (or just lagging) would otherwise overwrite
# the fresh stage with the old one. Within this window we keep Taali's value;
# after it, Workable has settled and the sync wins again.
_LOCAL_STAGE_WRITE_GUARD = timedelta(minutes=15)


def _stage_overwrite_blocked(app, new_stage) -> bool:
    """True when a sync must NOT overwrite ``workable_stage`` because Taali set
    it itself within the guard window and the sync wants a *different* value."""
    written_at = getattr(app, "workable_stage_local_write_at", None)
    if written_at is None:
        return False
    if str(new_stage or "") == str(getattr(app, "workable_stage", None) or ""):
        return False  # same value — nothing to protect
    try:
        return (datetime.now(timezone.utc) - written_at) < _LOCAL_STAGE_WRITE_GUARD
    except Exception:  # pragma: no cover — never let the guard break a sync
        return False


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

    def _discover_new_jobs(
        self,
        db: Session,
        org: Organization,
        all_jobs: list[dict],
        summary: dict,
        should_yield: Callable[[], bool] | None = None,
    ) -> None:
        """Create role rows for newly-listed Workable jobs that have none yet.

        Called from the scoped candidate syncs (starred / agent-mode / nightly),
        which reliably hold the per-org Workable mutex, so newly-published jobs
        are discovered on their 5-min cadence instead of waiting on the 15-min
        ``jobs_only`` sweep that loses the lock race and gets starved on busy
        orgs. Create-only: a job whose role was soft-deleted is left alone (the
        jobs_only sweep / manual full sync still restore those). No candidate
        fetch here — a freshly-created published role auto-stars in
        ``_upsert_role``, so its candidates flow on the next tick. Best-effort:
        never let discovery break the candidate sync it rides on.
        """
        try:
            existing: set[str] = {
                str(code).strip()
                for (code,) in db.query(Role.workable_job_id)
                .filter(
                    Role.organization_id == org.id,
                    Role.workable_job_id.isnot(None),
                )
                .all()
                if code and str(code).strip()
            }
        except Exception:
            logger.exception(
                "discover_new_jobs: failed to load existing role codes org_id=%s",
                org.id,
            )
            return
        for job in all_jobs:
            if not isinstance(job, dict):
                continue
            code = sanitize_text_for_storage(
                str(job.get("shortcode") or job.get("id") or "").strip()
            )
            if not code or code in existing:
                continue
            # Yield the mutex to a waiting user-facing write, exactly as the main
            # job loop does; the remaining new jobs are picked up on the next tick.
            if should_yield is not None and should_yield():
                summary.setdefault("errors", []).append(
                    "Paused job discovery for a pending Workable write; "
                    "remaining new jobs sync on the next sync."
                )
                break
            try:
                _role, created_new = self._upsert_role(db, org, job)
            except WorkableRateLimitError:
                # A 429 during discovery must not abort the candidate sync this
                # rides on — stop discovering and let the primary sync proceed.
                logger.warning(
                    "discover_new_jobs: rate limited, stopping discovery org_id=%s",
                    org.id,
                )
                break
            except Exception:
                logger.exception(
                    "discover_new_jobs: upsert failed org_id=%s code=%s",
                    org.id,
                    code,
                )
                continue
            existing.add(code)
            if created_new:
                summary["jobs_upserted"] = int(summary.get("jobs_upserted") or 0) + 1
                summary.setdefault("discovered_new_jobs", []).append(code)
                logger.info(
                    "discover_new_jobs: created role for new Workable job "
                    "org_id=%s code=%s title=%r",
                    org.id,
                    code,
                    (job.get("title") or job.get("name") or "")[:80],
                )

    def sync_org(
        self,
        db: Session,
        org: Organization,
        *,
        full_resync: bool = False,
        run_id: int | None = None,
        mode: str = "metadata",
        selected_job_shortcodes: list[str] | None = None,
        should_yield: Callable[[], bool] | None = None,
        discover_new_jobs: bool = False,
    ) -> dict:
        run = self._get_sync_run(db, run_id)
        requested_mode = (mode or "metadata").strip().lower()
        # ``jobs_only`` upserts role rows and exits before fetching
        # candidates — used by the 15-min jobs sweep so new postings
        # land fast without paying the per-candidate CV cost.
        if requested_mode not in {"metadata", "full", "jobs_only"}:
            requested_mode = "metadata"
        effective_mode = requested_mode
        selected_identifiers: set[str] = set()
        for value in selected_job_shortcodes or []:
            normalized = sanitize_text_for_storage(str(value or "").strip())
            if normalized:
                selected_identifiers.add(normalized)

        summary = {
            "run_id": run.id if run else None,
            "requested_mode": requested_mode,
            "mode": effective_mode,
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
                # Piggyback discovery: a scoped candidate sync (starred / agent /
                # nightly) holds the per-org Workable mutex far more reliably than
                # the 15-min jobs_only sweep, which loses the lock race and gets
                # starved on busy orgs — so brand-new postings never became roles
                # until a manual full sync ran. Create a role for any just-listed
                # job that has no role row yet (create-only — never resurrect a
                # soft-deleted one) without fetching candidates. Newly-published
                # jobs auto-star in _upsert_role, so the next candidate tick pulls
                # their applicants. Normally there are 0 new jobs, so no added cost.
                if discover_new_jobs:
                    self._discover_new_jobs(db, org, all_jobs, summary, should_yield)
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

            # Set when we stop mid-job to hand the per-org mutex to a waiting
            # user-facing write; breaks the outer job loop after the current
            # job's progress is persisted (see the per-candidate check below).
            yielded_for_op = False
            for job_idx, job in enumerate(jobs):
                if self._is_cancel_requested(db, org, run):
                    raise WorkableSyncCancelled()
                # Cooperative fairness: a periodic sync holds the per-org
                # Workable mutex for its whole run, which can starve a waiting
                # user-facing write (decision approval/override). When one is
                # pending, stop at this job boundary and release the lock; the
                # remaining jobs resync on the next Beat tick (idempotent
                # upserts). Bounds the lock hold to a single job's candidates.
                if should_yield is not None and should_yield():
                    logger.info(
                        "Workable sync yielding the org mutex to a pending op "
                        "after %d/%d jobs for org_id=%s",
                        job_idx,
                        len(jobs),
                        org.id,
                    )
                    summary["errors"].append(
                        "Paused for a pending Workable write; remaining roles "
                        "resync on the next sync."
                    )
                    final_status = "partial"
                    break
                try:
                    role, created_role = self._upsert_role(db, org, job)
                    if created_role:
                        summary["jobs_upserted"] += 1

                    shortcode = sanitize_text_for_storage(str(job.get("shortcode") or job.get("id") or "?"))[:20]

                    # ``jobs_only`` mode: skip every candidate fetch. The
                    # 15-min jobs sweep uses this to keep role rows fresh
                    # without burning the per-candidate API/CV budget.
                    if effective_mode == "jobs_only":
                        summary["jobs_processed"] = job_idx + 1
                        summary["phase"] = "syncing_jobs"
                        summary["current_step"] = "upserted_role"
                        summary["current_job_shortcode"] = shortcode
                        summary["current_candidate_index"] = None
                        summary["last_request"] = f"GET /jobs/{shortcode}"
                        if (job_idx + 1) % 10 == 0:
                            summary["db_snapshot"] = self._build_db_snapshot(db, org)
                            self._persist_progress(db, org, run, summary)
                        continue

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

                    # Fairness before the expensive work: a single starred role
                    # can carry hundreds of applications, whose prefetch wave
                    # (full mode) alone holds the per-org mutex for minutes. If a
                    # user-facing write is already waiting, yield BEFORE paying
                    # for it — this job resyncs on the next tick (idempotent).
                    if should_yield is not None and should_yield():
                        logger.info(
                            "Workable sync yielding the org mutex to a pending op "
                            "before job %d/%d (%d candidates) for org_id=%s",
                            job_idx + 1, len(jobs), total_candidates, org.id,
                        )
                        summary["errors"].append(
                            "Paused for a pending Workable write; this role and "
                            "the remaining roles resync on the next sync."
                        )
                        final_status = "partial"
                        yielded_for_op = True
                        break

                    # Parallel-prefetch full payloads + CVs for this job
                    # before the sequential DB write loop. Turns N serial
                    # Workable GETs into ~N/PREFETCH_WORKERS waves, which
                    # is the dominant cost for "full" syncs of any size.
                    prefetched_payloads: dict[str, dict] = {}
                    prefetched_resumes: dict[str, tuple[str, bytes]] = {}
                    if effective_mode == "full" and candidates:
                        try:
                            prefetched_payloads = self._prefetch_full_candidate_payloads(candidates)
                            # Skip CV downloads for candidate_applications
                            # that already have one. Re-downloading the same
                            # PDF every sync was the dominant cost driver of
                            # the old 30-min sync_workable_orgs sweep and
                            # the proximate cause of Workable rate-limiting.
                            payloads_needing_cv = self._filter_payloads_missing_cv(
                                db, org, role, prefetched_payloads,
                            )
                            prefetched_resumes = self._prefetch_candidate_resumes(payloads_needing_cv)
                        except WorkableRateLimitError:
                            # Re-raise so the per-job try/except below
                            # records the rate-limit and stops the sync
                            # the same way it did before parallelisation.
                            raise
                        except Exception:
                            logger.exception(
                                "Workable prefetch wave failed for job shortcode=%s; falling back to sequential",
                                job.get("shortcode"),
                            )
                            prefetched_payloads = {}
                            prefetched_resumes = {}

                    for idx, candidate_ref in enumerate(candidates):
                        if self._is_cancel_requested(db, org, run):
                            raise WorkableSyncCancelled()

                        # Cooperative fairness WITHIN a job, not just at job
                        # boundaries: a role with hundreds of applications would
                        # otherwise hold the per-org mutex for its whole walk and
                        # starve a waiting user-facing write (decision approval /
                        # override) past its lock-wait window — surfacing as a
                        # "Workable lock timeout" on the approval. Re-check the
                        # op-pending signal between candidates so we release
                        # within ~one candidate. Already-synced candidates are
                        # committed; the rest resync on the next tick (idempotent).
                        if should_yield is not None and should_yield():
                            logger.info(
                                "Workable sync yielding the org mutex to a pending "
                                "op mid-job after %d/%d candidates (job %d/%d) for "
                                "org_id=%s",
                                idx, total_candidates, job_idx + 1, len(jobs), org.id,
                            )
                            summary["errors"].append(
                                "Paused mid-role for a pending Workable write; "
                                "remaining candidates resync on the next sync."
                            )
                            final_status = "partial"
                            yielded_for_op = True
                            break

                        summary["candidates_seen"] += 1
                        cid = sanitize_text_for_storage(str(candidate_ref.get("id") or "?"))[:12]
                        summary["current_step"] = "syncing_candidate"
                        summary["current_candidate_index"] = (
                            f"{idx + 1}/{total_candidates}" if total_candidates else str(idx + 1)
                        )
                        summary["last_request"] = f"syncing candidate {cid}"
                        cid_key = str(candidate_ref.get("id") or "").strip()
                        try:
                            synced = self._sync_candidate_for_role(
                                db=db,
                                org=org,
                                role=role,
                                job=job,
                                candidate_ref=candidate_ref,
                                now=now,
                                run=run,
                                mode=effective_mode,
                                prefetched_full_payload=prefetched_payloads.get(cid_key),
                                prefetched_resume=prefetched_resumes.get(cid_key),
                            )
                            summary["candidates_upserted"] += synced.get("candidate_upserted", 0)
                            summary["applications_upserted"] += synced.get("application_upserted", 0)
                        except WorkableSyncCancelled:
                            raise
                        except Exception as exc:
                            db.rollback()
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
                    db.rollback()
                    logger.warning("Workable sync rate-limited; stopping early for org_id=%s", org.id)
                    summary["errors"].append(str(exc))
                    final_status = "partial"
                    break
                except WorkableSyncCancelled:
                    raise
                except Exception as exc:
                    db.rollback()
                    logger.exception("Failed syncing job for org_id=%s", org.id)
                    summary["errors"].append(str(exc))
                    final_status = "partial"

                # Yielded mid-candidate-loop above: this job's progress is
                # persisted, now release the mutex to the waiting op.
                if yielded_for_op:
                    break

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

    # Workable rate limit is "10 req / 10 sec" per the integration docs.
    # 3 parallel workers keeps the burst under 1 req/sec on average even
    # when responses are fast, while still cutting wall-clock for a
    # 50-candidate "full" sync from ~50s sequential to ~17s.
    _PREFETCH_WORKERS = 3

    def _prefetch_full_candidate_payloads(
        self,
        candidate_refs: list[dict],
    ) -> dict[str, dict]:
        """Fan out ``get_candidate`` calls in parallel.

        Returns a ``{candidate_id: full_payload}`` dict that the
        sequential DB loop can consult instead of making a blocking
        Workable GET per candidate. Failures are swallowed (the
        per-candidate flow falls back to the list payload).
        """
        ids = [
            str(ref.get("id") or "").strip()
            for ref in candidate_refs
            if str(ref.get("id") or "").strip() and not _is_terminal_candidate(ref)
        ]
        if not ids:
            return {}

        payloads: dict[str, dict] = {}

        def _fetch(cid: str) -> tuple[str, dict | None]:
            try:
                return cid, self.client.get_candidate(cid)
            except WorkableRateLimitError:
                # Bubble up so the outer loop's rate-limit handling can
                # pause/abort the whole job. We treat one rate-limit hit
                # as fatal for the prefetch wave.
                raise
            except Exception as exc:
                logger.debug("Prefetch get_candidate(%s) failed: %s", cid, exc)
                return cid, None

        with ThreadPoolExecutor(max_workers=self._PREFETCH_WORKERS) as pool:
            futures = [pool.submit(_fetch, cid) for cid in ids]
            for fut in as_completed(futures):
                cid, payload = fut.result()
                if isinstance(payload, dict) and payload:
                    payloads[cid] = payload
        return payloads

    def _filter_payloads_missing_cv(
        self,
        db: Session,
        org: Organization,
        role: Role,
        payloads_by_id: dict[str, dict],
    ) -> dict[str, dict]:
        """Return only the payloads whose ``candidate_application`` lacks a CV.

        Workable CVs are immutable per upload — once we have one in S3,
        re-downloading wastes a Workable API call and a S3 round-trip.
        Filter the prefetch input to candidates whose existing
        ``CandidateApplication`` row for this role has neither
        ``cv_file_url`` nor ``cv_text`` populated.
        """
        if not payloads_by_id:
            return {}
        candidate_ids = [cid for cid in payloads_by_id.keys() if cid]
        if not candidate_ids:
            return {}
        # Single roundtrip: pull every application for this role+org that
        # already has a CV. Anything not in the result set still needs one.
        already_have_cv = {
            row[0]
            for row in db.query(CandidateApplication.workable_candidate_id)
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.role_id == role.id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.workable_candidate_id.in_(candidate_ids),
                (CandidateApplication.cv_file_url.isnot(None))
                | (CandidateApplication.cv_text.isnot(None)),
            )
            .all()
            if row[0]
        }
        return {
            cid: payload
            for cid, payload in payloads_by_id.items()
            if cid not in already_have_cv
        }

    def _prefetch_candidate_resumes(
        self,
        payloads_by_id: dict[str, dict],
    ) -> dict[str, tuple[str, bytes]]:
        """Fan out resume downloads in parallel for candidates whose
        full payload exposes a resume_url.

        Returns ``{candidate_id: (filename, bytes)}``. Failures are
        swallowed; the per-candidate flow will fall back to a sync
        download or skip the CV entirely.
        """
        if not payloads_by_id:
            return {}

        downloads: dict[str, tuple[str, bytes]] = {}

        def _download(cid: str, payload: dict) -> tuple[str, tuple[str, bytes] | None]:
            try:
                return cid, self.client.download_candidate_resume(payload)
            except WorkableRateLimitError:
                raise
            except Exception as exc:
                logger.debug("Prefetch resume download(%s) failed: %s", cid, exc)
                return cid, None

        with ThreadPoolExecutor(max_workers=self._PREFETCH_WORKERS) as pool:
            futures = [pool.submit(_download, cid, p) for cid, p in payloads_by_id.items()]
            for fut in as_completed(futures):
                cid, result = fut.result()
                if result:
                    downloads[cid] = result
        return downloads

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

    def _refresh_role_stages(self, role: Role, shortcode: str | None) -> None:
        """Refresh a role's cached Workable stage pipeline, TTL-gated.

        Skips the fetch when we already have a stage list younger than
        ``WORKABLE_STAGES_TTL``. A failed or empty fetch (Workable hiccup /
        rate-limit) leaves the last-known list untouched so the picker never
        regresses to "no stages" — and the missing timestamp means the next
        sync retries.
        """
        if not shortcode:
            return
        synced_at = role.workable_stages_synced_at
        if role.workable_stages and synced_at is not None:
            if synced_at.tzinfo is None:
                synced_at = synced_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - synced_at < WORKABLE_STAGES_TTL:
                return
        try:
            stages = self.client.list_job_stages(shortcode)
        except Exception:
            logger.exception("Failed to refresh Workable stages for role_id=%s", role.id)
            return
        # ``list_job_stages`` returns [] both for a genuinely empty pipeline and
        # for a swallowed API error, so only commit a non-empty result. Every
        # real Workable job has stages, so this never strands a valid empty.
        if stages:
            role.workable_stages = sanitize_json_for_storage(stages)
            role.workable_stages_synced_at = datetime.now(timezone.utc)

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

        list_description = _get_desc(job) or ""
        description = _get_desc(details) or list_description
        role = None
        if job_id:
            role = (
                db.query(Role)
                .filter(Role.organization_id == org.id, Role.workable_job_id == job_id)
                .first()
            )
        created = False
        if not role:
            # Bridge: before minting a fresh role, try to ADOPT the inactive
            # requisition job whose ref code is stamped in this Workable job's
            # spec (draft -> open, no duplicate). Adopted roles are treated as
            # existing so their brief-materialized criteria are preserved.
            role = _adopt_requisition_role(
                db, org, job_id=job_id, title=title, description=description
            )
        if not role:
            role = Role(
                organization_id=org.id,
                source="workable",
                workable_job_id=job_id or None,
                name=title,
            )
            apply_workspace_agent_defaults(role, org)
            db.add(role)
            created = True
        previous_job_data = (
            role.workable_job_data
            if isinstance(role.workable_job_data, dict)
            else None
        )
        previous_ats_spec = (
            _format_job_spec_from_api(previous_job_data)
            if _workable_payload_has_spec_content(previous_job_data)
            else None
        )
        manual_spec_override = has_manual_job_spec_override(
            role,
            ats_source="workable",
            cached_ats_spec=previous_ats_spec,
        )
        role.deleted_at = None  # restore if was soft-deleted
        role.source = "workable"
        role.workable_job_id = job_id or role.workable_job_id
        # Cache the role's Workable stage pipeline so the stage pickers serve
        # from our DB. TTL-gated so even the 5-min starred/agent syncs only hit
        # Workable for this every few hours per role.
        self._refresh_role_stages(role, role.workable_job_id)
        # A failed/empty detail fetch must not throw away the last known rich job
        # payload.  Merge the lightweight list row over the cached data so fresh
        # state/title metadata still lands while prior description HTML survives.
        # On a successful detail fetch retain the original replacement behaviour.
        if details:
            next_job_data = {**job, "details": details}
        else:
            next_job_data = _merge_cached_workable_job_data(
                previous_job_data,
                job,
            )
        role.workable_job_data = sanitize_json_for_storage(next_job_data)
        role.name = title
        # Build one formatted spec from full API data for display and attachment.
        # Capture the prior spec FIRST so we only re-do the expensive, churn-
        # inducing side effects (attachment re-upload, derived-criteria
        # re-derive) when the spec actually changed — see ``spec_changed`` below.
        prev_job_spec = (role.job_spec_text or "")
        formatted_spec = _format_job_spec_from_api(role.workable_job_data or {})
        # ``_format_job_spec_from_api`` always emits at least a title for a list
        # row.  During an empty detail response, do not treat that degraded input
        # as authoritative enough to replace an existing spec.  The raw cache was
        # still merged above, so fresh list metadata is retained and a later
        # successful detail fetch can rebuild the text normally.
        preserve_existing_spec_after_empty_detail = bool(
            not details and not list_description and prev_job_spec.strip()
        )
        if manual_spec_override:
            logger.info(
                "Preserving recruiter-edited job spec during Workable sync "
                "role_id=%s workable_job_id=%s",
                role.id,
                role.workable_job_id,
            )
        elif formatted_spec and not preserve_existing_spec_after_empty_detail:
            safe_spec = sanitize_text_for_storage(formatted_spec)
            role.job_spec_text = safe_spec
            role.description = safe_spec
        elif preserve_existing_spec_after_empty_detail:
            logger.warning(
                "Preserving existing Workable job spec after empty detail response "
                "for role_id=%s workable_job_id=%s",
                role.id,
                role.workable_job_id,
            )
        else:
            stripped = _strip_html(description) if isinstance(description, str) and description.strip() else ""
            safe_desc = sanitize_text_for_storage(stripped)
            role.description = safe_desc or role.description
            if stripped:
                role.job_spec_text = safe_desc
        db.flush()
        spec_changed = (role.job_spec_text or "") != prev_job_spec
        # Save the job-spec attachment + re-derive criteria ONLY when the spec
        # actually changed (or the role was just created). ``sync_derived_criteria``
        # HARD-DELETES and re-inserts the derived criteria with fresh row IDs;
        # the decision-staleness fingerprint includes those IDs, so re-deriving
        # an UNCHANGED spec on every sync tick would spuriously invalidate every
        # pending decision for the role (and needlessly re-upload the file +
        # restamp job_spec_uploaded_at). Gating on real change stops that churn.
        if (created or spec_changed) and (role.job_spec_text or "").strip():
            try:
                spec_content = (role.job_spec_text or "").strip().encode("utf-8")
                spec_filename = sanitize_text_for_storage(
                    f"job-spec-{role.name or role.id}.txt"
                ).replace("/", "-")
                s3_key = generate_s3_key("job_spec", role.id, spec_filename)
                spec_url = upload_bytes_to_s3(spec_content, s3_key, content_type="text/plain")
                if spec_url:
                    role.job_spec_file_url = spec_url
                    role.job_spec_filename = spec_filename
                    role.job_spec_uploaded_at = _now()
                else:
                    logger.warning(
                        "Skipping Workable job-spec store for role_id=%s — object storage unavailable",
                        role.id,
                    )
            except Exception:
                logger.exception("Failed saving Workable job spec file for role_id=%s", role.id)
        if not isinstance(role.screening_pack_template, dict) or not isinstance(role.tech_interview_pack_template, dict):
            templates = build_role_interview_pack_templates(role)
            role.screening_pack_template = templates.get("screening")
            role.tech_interview_pack_template = templates.get("tech_stage_2")
        if created:
            from ....services.role_criteria_service import sync_all_criteria

            sync_all_criteria(db, role)
        elif spec_changed:
            # A real external spec change. For an agent-on role, don't blindly
            # re-derive (that would invalidate every pending decision + force a
            # paid re-evaluation). Route through material-change assessment: it
            # applies silently when immaterial and asks the recruiter to confirm
            # when the hiring bar actually moved. Agent-off roles keep the
            # direct re-derive (no decisions in flight to protect).
            if getattr(role, "agentic_mode_enabled", False):
                from ....services.material_change import handle_spec_change

                handle_spec_change(db, role)
            else:
                from ....services.role_criteria_service import sync_derived_criteria

                sync_derived_criteria(db, role)

        # Live (published) jobs are always in continuous sync: auto-star them
        # and mark the star auto-managed so it can be dropped when the job is
        # no longer live. A recruiter's manual star (star_auto_managed False)
        # is never touched here, and agent-on roles are never auto-unstarred.
        job_state = str(
            (job.get("state") or details.get("state") or "")
        ).strip().lower()
        if job_state == "published":
            if not role.starred_for_auto_sync:
                role.starred_for_auto_sync = True
                role.star_auto_managed = True
        elif job_state in {"archived", "closed", "draft"}:
            if (
                role.starred_for_auto_sync
                and getattr(role, "star_auto_managed", False)
                and not getattr(role, "agentic_mode_enabled", False)
            ):
                role.starred_for_auto_sync = False
                role.star_auto_managed = False

        # New Workable role → auto-provision a draft assessment task from its
        # JD (gated by AUTO_GENERATE_ASSESSMENT_TASKS; default on). Persist the
        # request in this sync transaction before the low-latency broker kick;
        # Beat recovers a lost kick after commit. countdown gives the
        # surrounding transaction time to commit before the worker reads it.
        if (created or spec_changed) and (role.job_spec_text or "").strip():
            from ....platform.config import settings

            if getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
                from ....services.task_provisioning_service import (
                    request_assessment_task_provisioning,
                )

                provisioning_requested = request_assessment_task_provisioning(
                    role,
                    reason=("workable_role_create" if created else "workable_spec_update"),
                    supersede_generated_drafts=bool(spec_changed),
                )
                if provisioning_requested:
                    try:
                        from ....tasks.assessment_tasks import generate_assessment_task_for_role
                        generate_assessment_task_for_role.apply_async(
                            args=[int(role.id), int(org.id)], countdown=45,
                        )
                    except Exception:  # pragma: no cover
                        logger.warning(
                            "auto-generate enqueue failed for synced role %s; durable sweep will retry",
                            getattr(role, "id", "?"),
                            exc_info=True,
                        )

        return role, created

    # Resolved (advanced/hired/rejected) candidates are frozen for
    # scoring/enrichment, but we still refresh their read-only Workable
    # activity feed so post-decision recruiter notes (comments + ratings)
    # appear on the profile. Debounced to this interval so re-reading the
    # feed for a growing pile of resolved candidates never reintroduces the
    # per-candidate API pressure the freeze was built to avoid.
    _RESOLVED_ACTIVITIES_REFRESH_INTERVAL = timedelta(hours=6)

    def _refresh_candidate_activities(
        self,
        candidate: Candidate,
        candidate_id: str,
        application: CandidateApplication | None = None,
    ) -> tuple[list, list] | None:
        """Pull the Workable activity feed and store it on the candidate.

        Workable's activities feed is the authoritative source for both
        timeline entries (stage transitions, assessment events, …) AND
        recruiter comments — there is no public ``GET`` on
        ``/candidates/:id/comments``. We split the response: ``action ==
        "comment"`` rows land in ``workable_comments`` (which also feeds the
        pre-screen scoring context); everything else — including recruiter
        ratings, which carry a written ``body`` — lands in
        ``workable_activities``. Ratings are surfaced as notes at
        serialization time (see ``workable_recruiter_comments``) so they show
        in the UI without leaking recruiter opinion into scoring.

        ``candidate_id`` is the PER-APPLICATION Workable id, so the fetched
        feed belongs to one application. When ``application`` is given the
        split is stored on it too — the candidate-level fields are shared
        across a person's applications (last sync wins) and remain only as
        a legacy fallback for readers.

        ``None`` from the client means the fetch failed; we only overwrite
        stored rows on a successful response so a transient error never
        clobbers good data — and return ``None`` so callers can skip their
        own writes too. ``WorkableRateLimitError`` is re-raised for the
        caller's rate-limit handling.
        """
        try:
            activities = self.client.get_candidate_activities(candidate_id)
            if activities is not None:
                comment_entries = [a for a in activities if a.get("action") == "comment"]
                other_entries = [a for a in activities if a.get("action") != "comment"]
                candidate.workable_comments = sanitize_json_for_storage(comment_entries)
                candidate.workable_activities = sanitize_json_for_storage(other_entries)
                if application is not None:
                    application.workable_comments = candidate.workable_comments
                    application.workable_activities = candidate.workable_activities
                return comment_entries, other_entries
        except WorkableRateLimitError:
            raise
        except Exception:
            logger.debug("Workable activities fetch failed for candidate_id=%s", candidate_id)
        return None

    def _activities_refresh_due(self, last_fetch_iso: str | None, now: datetime) -> bool:
        """True when a frozen candidate's activity feed is due for a refresh.

        Due when we have never fetched (no timestamp) or the last fetch is
        older than ``_RESOLVED_ACTIVITIES_REFRESH_INTERVAL``.
        """
        if not last_fetch_iso:
            return True
        try:
            last = datetime.fromisoformat(str(last_fetch_iso))
        except (TypeError, ValueError):
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last) >= self._RESOLVED_ACTIVITIES_REFRESH_INTERVAL

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
        mode: str = "metadata",
        prefetched_full_payload: dict | None = None,
        prefetched_resume: tuple[str, bytes] | None = None,
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

        candidate_payload = candidate_ref
        if mode == "full":
            # Prefer the parallel-prefetched payload; fall back to a
            # blocking GET only if prefetch missed (e.g. failed).
            full_payload = prefetched_full_payload
            if full_payload is None:
                full_payload = self.client.get_candidate(candidate_id)
            if isinstance(full_payload, dict) and full_payload:
                candidate_payload = {**candidate_ref, **full_payload}

        if self._is_cancel_requested(db, org, run):
            raise WorkableSyncCancelled()
        stage = (
            candidate_payload.get("stage")
            or candidate_ref.get("stage")
            or candidate_ref.get("stage_name")
            or ""
        )
        ref_disqualified = _is_disqualified(candidate_payload, candidate_ref)
        ref_terminal = _is_terminal_candidate(candidate_payload) or _is_terminal_candidate(candidate_ref)

        # Any application that already exists for this Workable candidate on this
        # role. Drives the two freeze paths below.
        existing = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.workable_candidate_id == candidate_id,
                CandidateApplication.role_id == role.id,
            )
            .first()
        )
        if existing is None:
            # Older / manually-created rows may be linked by candidate email
            # rather than the Workable id. Match those too so terminal capture
            # and the resolved-freeze still apply, and backfill the Workable id.
            lookup_email = _candidate_email(candidate_payload) or _candidate_email(candidate_ref)
            if lookup_email:
                linked_candidate = (
                    db.query(Candidate)
                    .filter(
                        Candidate.organization_id == org.id,
                        Candidate.email == lookup_email,
                    )
                    .first()
                )
                if linked_candidate is not None:
                    existing = (
                        db.query(CandidateApplication)
                        .filter(
                            CandidateApplication.organization_id == org.id,
                            CandidateApplication.candidate_id == linked_candidate.id,
                            CandidateApplication.role_id == role.id,
                        )
                        .first()
                    )
                    if existing is not None and not existing.workable_candidate_id:
                        existing.workable_candidate_id = sanitize_text_for_storage(candidate_id)

        if ref_terminal or ref_disqualified:
            # The candidate has reached a terminal state in Workable
            # (hired / rejected / disqualified / withdrawn). Candidates who have
            # left Tali are exactly the ones whose realized outcome we want for
            # model refinement. For an existing app we record the outcome (which
            # fires the outcome_learning calibration hooks via transition_outcome),
            # refresh the observed Workable stage, and park them in Tali's
            # terminal `advanced` stage. Brand-new terminal candidates are not
            # imported — Tali never tracked or scored them, so there is no
            # decision to pair the outcome with.
            if existing is None:
                return counters
            existing.deleted_at = None
            if stage and not _stage_overwrite_blocked(existing, stage):
                existing.workable_stage = sanitize_text_for_storage(str(stage))
            existing.last_synced_at = now
            if ref_disqualified:
                existing.workable_disqualified = True
                existing.workable_disqualified_at = (
                    _disqualified_at_from_payload(candidate_payload, candidate_ref) or now
                )
            # Park in `advanced` — they're past Tali's flow. (No-op if already there.)
            if (existing.pipeline_stage or "").lower() != "advanced":
                try:
                    # No idempotency_key: transition_stage already no-ops
                    # when from_stage == target, and the caller guards on
                    # "not already advanced". A permanent
                    # ``sync_terminal_advance:{id}`` key instead blocked a
                    # legitimate re-advance (and its outcome-learning hook)
                    # if a candidate round-tripped back to non-terminal and
                    # was later re-observed terminal.
                    transition_stage(
                        db,
                        app=existing,
                        to_stage="advanced",
                        source="sync",
                        actor_type="sync",
                        reason="Reached terminal stage in Workable",
                        metadata={"workable_stage": str(stage or ""), "disqualified": ref_disqualified},
                    )
                except Exception:  # pragma: no cover — never block a sync
                    import logging
                    logging.getLogger("taali.workable.sync").exception(
                        "Terminal advance failed for app_id=%s", existing.id,
                    )
            # Record the realized outcome so calibration can learn from it.
            outcome = _terminal_outcome(candidate_payload, candidate_ref, disqualified=ref_disqualified)
            if outcome and (existing.application_outcome or "open").lower() != outcome:
                try:
                    # No idempotency_key: transition_outcome already no-ops when
                    # the outcome is unchanged (from_outcome == target). A
                    # permanent per-outcome key would instead block a legitimate
                    # later correction if the outcome flips and returns to a
                    # previously-seen value (rejected -> hired -> rejected).
                    transition_outcome(
                        db,
                        app=existing,
                        to_outcome=outcome,
                        actor_type="sync",
                        reason=f"Workable outcome: {stage or outcome}",
                        metadata={"workable_stage": str(stage or ""), "disqualified": ref_disqualified},
                    )
                except Exception:  # pragma: no cover — never block a sync
                    import logging
                    logging.getLogger("taali.workable.sync").exception(
                        "Outcome capture failed for app_id=%s", existing.id,
                    )
            counters["application_upserted"] += 1
            return counters

        if existing is not None and is_resolved(existing):
            # Already resolved (advanced / hired / rejected): the candidate has
            # left Tali's flow and is FROZEN — no profile enrichment, no CV
            # refresh, no scoring, no agent activity. We only keep their Workable
            # stage current (e.g. a non-terminal interview -> offer move) so the
            # trail stays accurate; the realized outcome is captured by the
            # terminal branch above when it lands. Their data is used solely for
            # model refinement from here on.
            existing.deleted_at = None
            if stage and not _stage_overwrite_blocked(existing, stage):
                existing.workable_stage = sanitize_text_for_storage(str(stage))
                existing.external_stage_raw = sanitize_text_for_storage(str(stage))
                existing.external_stage_normalized = normalize_pipeline_key(str(stage))
            existing.last_synced_at = now

            # Frozen for scoring, but still refresh the read-only activity feed
            # so recruiter comments + ratings added AFTER the decision surface on
            # the profile. Debounced via last_activities_fetch_at so a growing
            # pile of resolved candidates can't re-introduce the per-candidate
            # API pressure the freeze prevents.
            prev_state = (
                existing.integration_sync_state
                if isinstance(existing.integration_sync_state, dict)
                else {}
            )
            activities_fetched_at = prev_state.get("last_activities_fetch_at")
            if mode == "full" and self._activities_refresh_due(activities_fetched_at, now):
                frozen_candidate = (
                    db.query(Candidate)
                    .filter(Candidate.id == existing.candidate_id)
                    .first()
                )
                if frozen_candidate is not None:
                    self._refresh_candidate_activities(
                        frozen_candidate, candidate_id, application=existing
                    )
                    activities_fetched_at = now.isoformat()

            existing.integration_sync_state = sanitize_json_for_storage(
                {
                    "last_sync_at": now.isoformat(),
                    "sync_status": "success",
                    "run_id": run.id if run else None,
                    "source": "workable",
                    "mode": mode,
                    "frozen": True,
                    "last_activities_fetch_at": activities_fetched_at,
                }
            )
            counters["application_upserted"] += 1
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
            # Phone fallback: the same person sometimes applies to a second job
            # under a different email, so both workable_candidate_id and email
            # miss and we'd create a duplicate profile. Match on the normalized
            # phone (org-scoped) to collapse them onto one candidate.
            phone_key = _normalize_phone_for_match(_candidate_phone(candidate_payload))
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

        candidate.deleted_at = None  # restore if was soft-deleted
        if email:
            candidate.email = sanitize_text_for_storage(email)
        fallback_name = candidate.full_name or email or f"Workable candidate {candidate_id}"
        candidate.full_name = _candidate_name(candidate_payload, fallback=fallback_name)
        candidate.position = _candidate_position(candidate_payload, role.name)

        candidate.workable_candidate_id = sanitize_text_for_storage(candidate_id)
        candidate.workable_data = sanitize_json_for_storage(candidate_payload)
        candidate.workable_enriched = mode == "full"

        # Extract rich profile fields from bulk payload
        extracted = _extract_candidate_fields(candidate_payload)
        for field, value in extracted.items():
            setattr(candidate, field, value)
        # Keep the phone dedup key in sync with whatever phone we just stored.
        candidate.phone_normalized = _normalize_phone_for_match(candidate.phone)

        # Refresh the Workable activity feed (timeline + recruiter
        # comments/ratings) on full enrichment. See
        # ``_refresh_candidate_activities`` for the split and error policy.
        # The split is applied to the application row below, once it exists.
        activities_split = None
        if mode == "full":
            activities_split = self._refresh_candidate_activities(candidate, candidate_id)

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
        created_application = False
        if not app:
            mapped_stage, mapped_outcome = map_legacy_status_to_pipeline(str(stage or "applied"))
            # Tali's `advanced` stage must only ever result from a Tali
            # hand-back decision, never from observing the candidate's Workable
            # stage. A fresh import that is already past handover in Workable
            # (e.g. "Technical Interview") still enters Tali at the top of the
            # funnel — the real Workable stage stays visible via workable_stage.
            # `hired` keeps its terminal mapping (genuinely out, nothing to do).
            if mapped_stage == "advanced" and mapped_outcome != "hired":
                mapped_stage = "applied"
            app = CandidateApplication(
                organization_id=org.id,
                candidate_id=candidate.id,
                role_id=role.id,
                status=str(stage or "applied"),
                pipeline_stage=mapped_stage,
                pipeline_stage_source="sync",
                pipeline_stage_updated_at=now,
                application_outcome=mapped_outcome,
                application_outcome_updated_at=now,
                version=1,
            )
            db.add(app)
            created_application = True

        app.deleted_at = None  # restore if was soft-deleted
        app.source = "workable"
        if created_application:
            app.status = sanitize_text_for_storage(str(stage or app.status or "applied"))
        ensure_pipeline_fields(app, source="sync" if created_application else "system")
        db.flush()
        if created_application:
            initialize_pipeline_event_if_missing(
                db,
                app=app,
                actor_type="sync",
                reason="Imported from Workable",
            )
        app.workable_candidate_id = sanitize_text_for_storage(candidate_id)
        if mode == "full":
            # Per-application Workable context. ``candidate_payload`` and the
            # activities fetch above are keyed by THIS application's Workable
            # id, so they belong here — the candidate-level copies are shared
            # across a person's applications and kept only as legacy fallback.
            if isinstance(candidate_payload, dict) and "answers" in candidate_payload:
                app.workable_answers = sanitize_json_for_storage(
                    candidate_payload.get("answers")
                )
            if activities_split is not None:
                comment_entries, other_entries = activities_split
                app.workable_comments = sanitize_json_for_storage(comment_entries)
                app.workable_activities = sanitize_json_for_storage(other_entries)
        if not _stage_overwrite_blocked(app, stage):
            app.workable_stage = sanitize_text_for_storage(str(stage or ""))
            app.external_stage_raw = sanitize_text_for_storage(str(stage or ""))
            app.external_stage_normalized = normalize_pipeline_key(str(stage or ""))

        # A recruiter moving the candidate forward in Workable (Phone Screen /
        # Technical / Final Interview / Offer — a post-handover stage) is a
        # hand-off: reflect it as `advanced` on Taali so they don't strand as
        # `applied`, and so no stale reject/advance card lingers on someone the
        # recruiter is already interviewing. Local only — Workable already has
        # them there, nothing is written back. Disqualification is handled near
        # the top of this function.
        try:
            reconcile_post_handover_advanced(db, app=app, role=role)
        except Exception:  # pragma: no cover — never block the candidate sync
            logger.exception(
                "post-handover advance reconcile failed application_id=%s", app.id
            )

        app.external_refs = sanitize_json_for_storage(
            {
                "workable_candidate_id": candidate_id,
                "workable_job_id": role.workable_job_id,
                "workable_role_shortcode": job.get("shortcode"),
                "workable_role_id": job.get("id"),
            }
        )
        app.integration_sync_state = sanitize_json_for_storage(
            {
                "last_sync_at": now.isoformat(),
                "sync_status": "success",
                "run_id": run.id if run else None,
                "source": "workable",
                "mode": mode,
            }
        )
        app.last_synced_at = now

        # Extract application-level Workable fields
        app.workable_sourced = candidate_payload.get("sourced", None)
        # Applied date: the payload's created_at is per JOB APPLICATION (the
        # Workable candidate id is per-application), so it belongs here — the
        # candidate-level copy is last-sync-wins across a person's applications.
        applied_raw = candidate_payload.get("created_at")
        if isinstance(applied_raw, str) and applied_raw.strip():
            try:
                app.workable_created_at = datetime.fromisoformat(
                    applied_raw.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass
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

        if mode == "full":
            if not (app.cv_text or "").strip() and (candidate.cv_text or "").strip():
                app.cv_file_url = candidate.cv_file_url
                app.cv_filename = candidate.cv_filename
                app.cv_text = candidate.cv_text
                app.cv_uploaded_at = candidate.cv_uploaded_at
            # Only fetch a CV if we don't already have one for this app.
            # The prefetch wave (``_filter_payloads_missing_cv``) makes the
            # same decision in bulk for the parallel path; this guard
            # keeps the sequential fallback consistent so a partially-
            # populated row (URL but no extracted text, for example)
            # doesn't trigger a needless re-download.
            need_cv = not (app.cv_text or "").strip() and not (app.cv_file_url or "").strip()
            if need_cv:
                # Prefer the parallel-prefetched resume; only hit the
                # network here if prefetch had nothing for this candidate.
                downloaded = prefetched_resume or self.client.download_candidate_resume(candidate_payload)
                if downloaded:
                    filename, content = downloaded
                    _store_candidate_resume(
                        app=app,
                        candidate=candidate,
                        filename=filename,
                        content=content,
                    )
            # Refresh the read-only score cache from existing fields. Paid
            # scoring is never run synchronously inside the sync loop. Newly
            # created applications on a running role agent are admitted to the
            # bounded async scoring path below; manual Score / Rescore
            # remains an optional recovery/override for other roles.
            if app.score_cached_at is None:
                refresh_application_score_cache(app, db=db)
            else:
                refresh_pre_screening_fields(app)
            # The star is sticky adoption/sync-cadence metadata, not permission
            # to spend.  Only a lifecycle-ready, enabled, unpaused role may
            # launch NEW paid parse/score work. Metadata continues to sync while
            # paused/off, and work queued before the hold is left untouched.
            paid_work_allowed = role_allows_new_paid_ats_work(role)
            auto_score = bool(created_application and paid_work_allowed)
            on_application_created(
                app,
                score=auto_score,
                allow_paid_work=paid_work_allowed,
                parse_origin=CV_PARSE_ORIGIN_ATS_INGEST,
            )
            # NOTE: syncs never dispatch paid re-scoring. A changed
            # Workable context (new answers/comments/activities) is
            # stored for display and for the NEXT recruiter-approved
            # evaluation; re-scoring an already-scored application is
            # recruiter-triggered only (agent chat quotes the cost
            # first). The old auto-rescore-on-context-change trigger
            # looped on multi-role candidates and burned credits.
        else:
            refresh_pre_screening_fields(app)
        app.rank_score = _rank_score_for_application(app)
        if not created_application:
            # Preserve local source-of-truth stage for existing applications.
            app.status = sanitize_text_for_storage(app.status)
        db.flush()
        # Related-role fan-out is part of the transactional application-created
        # outbox above. It runs only after this outer sync transaction commits,
        # and pending evaluation rows make a lost broker kick recoverable.
        counters["application_upserted"] += 1
        return counters
