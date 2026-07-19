"""Workable pull-sync service for roles/candidates/applications."""

from __future__ import annotations

import logging
import re
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
from ....services.ats_writeback_state import replace_sync_state_preserving_writeback
from ....services.auto_reject_operation_receipt import fence_auto_reject_lifecycle_restore
from ....services.ats_sync_outcome_fence import fence_inbound_outcome_before_mutation
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
from ....services.role_change_audit import (
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    ROLE_CHANGE_ACTION_RESTORED,
    ROLE_CHANGE_ACTION_UPDATED,
    add_role_change_event,
    build_role_change_diff,
    capture_role_change_snapshot,
)
from ....services.role_concurrency import bump_role_version
from ....services.role_lifecycle import restore_role_from_ats
from ....services.taali_scoring import normalize_score_100
from .error_policy import public_workable_sync_error
from .job_spec_formatting import (
    _format_job_spec_from_api as _format_job_spec_from_api,
    _format_location as _format_location,
    _job_spec_block_key as _job_spec_block_key,
    _merge_cached_workable_job_data as _merge_cached_workable_job_data,
    _parse_location_like as _parse_location_like,
    _remove_embedded_dict_reprs as _remove_embedded_dict_reprs,
    _strip_html as _strip_html,
    _workable_payload_has_spec_content as _workable_payload_has_spec_content,
)
from .service import WorkableRateLimitError, WorkableService
from . import sync_material_change_boundary as material_boundary
from .sync_candidate_claim import (
    build_candidate_claim,
    filter_payloads_missing_cv,
    revalidate_candidate_claim,
)
from .sync_provider_boundaries import (
    RoleProviderClaim,
    WorkableProviderLineageDrift,
    WorkableSyncCancelled,
    apply_resume_upload,
    assert_provider_ready,
    build_role_provider_claim,
    candidate_claim_matches_role,
    claim_role_provider_wave,
    fetch_candidate_activities,
    fetch_role_stages,
    finish_db_phase,
    prepare_resume_upload,
    workable_org_auth_fingerprint,
)
from .sync_lease import WorkableSyncYielded, bind_sync_lease_observer
from .sync_lease import raise_if_sync_should_yield as _raise_if_sync_should_yield
from .sync_provider_reads import (
    job_details_for_role,
    job_identifiers,
    list_job_candidates,
    prefetch_candidate_resumes,
    prefetch_full_candidate_payloads,
)

logger = logging.getLogger(__name__)
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


def _locked_existing_role(db: Session, *criteria: Any) -> Role | None:
    """Lock a role and refresh only when this session cached an older version.

    Selecting the version as a scalar first both acquires the database lock and
    avoids needlessly replacing a same-version identity-mapped instance (which
    matters for dialects such as SQLite that lose timezone metadata on reload).
    """

    locked = (
        db.query(Role.id, Role.version)
        .filter(*criteria)
        .with_for_update(of=Role)
        .first()
    )
    if locked is None:
        return None
    role = db.get(Role, int(locked.id))
    if role is None:
        return None
    locked_version = int(locked.version or 1)
    if int(role.version or 1) != locked_version:
        db.refresh(role)
    return role


def _adopt_requisition_role(
    db: Session,
    org: Organization,
    *,
    job_id: str,
    title: str,
    description: str,
    audit_context: dict[str, Any] | None = None,
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
    role = _locked_existing_role(
        db,
        Role.id == brief.role_id,
        Role.organization_id == org.id,
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
    if audit_context is not None:
        audit_context["before"] = capture_role_change_snapshot(role)
        audit_context["from_version"] = int(role.version or 1)
    role.workable_job_id = job_id
    role.job_status = JOB_STATUS_OPEN
    restore_role_from_ats(role, restored_at=_now(), provider="Workable")
    logger.info(
        "Workable bridge: adopted requisition role_id=%s into job_id=%s via ref %s",
        role.id,
        job_id,
        code,
    )
    return role


def _record_workable_role_change(
    db: Session,
    *,
    role: Role,
    before: dict[str, Any] | None,
    from_version: int | None,
    job_id: str,
) -> None:
    """Version and audit one material Workable update in the sync transaction."""

    if before is None or from_version is None:
        return
    after = capture_role_change_snapshot(role)
    changes = build_role_change_diff(before, after)
    if not changes:
        return
    to_version = bump_role_version(role)
    spec_fields = {
        "description",
        "job_spec_text",
        "job_spec_filename",
        "job_spec_file_url",
        "job_spec_uploaded_at",
        "job_spec_manually_edited_at",
    }
    restored = (
        before.get("deleted_at") is not None
        and after.get("deleted_at") is None
    )
    add_role_change_event(
        db,
        role=role,
        before=before,
        action=(
            ROLE_CHANGE_ACTION_RESTORED
            if restored
            else (
                ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED
                if spec_fields.intersection(changes)
                else ROLE_CHANGE_ACTION_UPDATED
            )
        ),
        actor_user_id=None,
        from_version=from_version,
        to_version=to_version,
        reason=(
            "Workable role restored with agent off"
            if restored
            else "Workable pull sync"
        ),
        request_id=f"workable-job:{job_id}",
    )


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
            text = sanitize_text_for_storage(public_workable_sync_error(err))
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
        """Read cancellation in a short phase and release the DB connection.

        This method is immediately followed by Workable I/O at several call
        sites.  Returning with an autobegun read transaction used to retain a
        pooled connection (and any prior candidate locks) for the whole remote
        request.  Committing also preserves completed candidate work; no dirty
        state is ever rolled back merely to reach a provider boundary.
        """

        organization_id = int(org.id)
        run_id = int(run.id) if run is not None else None
        run_cancelled = False
        if run_id is not None:
            run_row = (
                db.query(WorkableSyncRun.cancel_requested_at)
                .filter(
                    WorkableSyncRun.id == run_id,
                    WorkableSyncRun.organization_id == organization_id,
                )
                .first()
            )
            run_cancelled = run_row is None or run_row[0] is not None
        org_row = (
            db.query(Organization.workable_sync_cancel_requested_at)
            .filter(Organization.id == organization_id)
            .first()
        )
        org_cancelled = org_row is None or org_row[0] is not None
        finish_db_phase(db)
        return run_cancelled or org_cancelled

    def _discover_new_jobs(
        self,
        db: Session,
        org: Organization,
        all_jobs: list[dict],
        summary: dict,
        should_yield: Callable[[], bool] | None = None,
        expected_org_fingerprint: str | None = None,
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
        except Exception as exc:
            logger.error("discover_new_jobs: role-code query failed org_id=%s error_type=%s", org.id, type(exc).__name__)
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
                _role, created_new = self._upsert_role(
                    db, org, job, expected_org_fingerprint=expected_org_fingerprint,
                    should_yield=should_yield,
                )
                # One role lifecycle/configuration boundary per transaction.
                # This releases its row lock before discovery considers the
                # next job, matching Clear's deterministic lock discipline.
                db.commit()
            except WorkableSyncYielded:
                raise
            except WorkableRateLimitError:
                db.rollback()
                # A 429 during discovery must not abort the candidate sync this
                # rides on — stop discovering and let the primary sync proceed.
                logger.warning(
                    "discover_new_jobs: rate limited, stopping discovery org_id=%s",
                    org.id,
                )
                break
            except Exception as exc:
                db.rollback()
                logger.error("discover_new_jobs: upsert failed org_id=%s code=%s error_type=%s", org.id, code, type(exc).__name__)
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

    @bind_sync_lease_observer
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
            jobs_org_fingerprint = workable_org_auth_fingerprint(org)
            self._persist_progress(db, org, run, summary)

            _raise_if_sync_should_yield(should_yield)
            assert_provider_ready(db)
            all_jobs = self.client.list_open_jobs()
            _raise_if_sync_should_yield(should_yield)
            current_org = db.get(Organization, int(org.id))
            if (
                current_org is None
                or workable_org_auth_fingerprint(current_org) != jobs_org_fingerprint
            ):
                raise WorkableProviderLineageDrift(
                    "Workable organization changed during jobs provider read"
                )
            org = current_org
            finish_db_phase(db)
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
                    self._discover_new_jobs(
                        db, org, all_jobs, summary, should_yield, jobs_org_fingerprint,
                    )
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
                    role, created_role = self._upsert_role(
                        db, org, job, expected_org_fingerprint=jobs_org_fingerprint,
                        should_yield=should_yield,
                    )
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
                        # Do not retain several Role locks until the batched
                        # progress checkpoint; Clear locks all roles by id and
                        # must never deadlock against remote API ordering.
                        db.commit()
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

                    provider_role_claim = claim_role_provider_wave(
                        db,
                        org,
                        str(job.get("shortcode") or job.get("id") or ""),
                        int(role.id),
                        expected_org_fingerprint=jobs_org_fingerprint,
                    )
                    _raise_if_sync_should_yield(should_yield)
                    assert_provider_ready(db)
                    candidates = self._list_job_candidates_for_job(
                        job=job, role=None, should_yield=should_yield,
                    )
                    total_candidates = len(candidates)
                    if not candidates:
                        logger.info("list_job_candidates returned 0 role_id=%s", role.id)

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
                            _raise_if_sync_should_yield(should_yield)
                            assert_provider_ready(db)
                            prefetched_payloads = self._prefetch_full_candidate_payloads(candidates, should_yield=should_yield)
                            _raise_if_sync_should_yield(should_yield)
                            # Skip CV downloads for candidate_applications
                            # that already have one. Re-downloading the same
                            # PDF every sync was the dominant cost driver of
                            # the old 30-min sync_workable_orgs sweep and
                            # the proximate cause of Workable rate-limiting.
                            payloads_needing_cv = filter_payloads_missing_cv(
                                db,
                                organization_id=int(org.id),
                                role_id=int(role.id),
                                payloads_by_id=prefetched_payloads,
                            )
                            _raise_if_sync_should_yield(should_yield)
                            assert_provider_ready(db)
                            prefetched_resumes = self._prefetch_candidate_resumes(payloads_needing_cv, should_yield=should_yield)
                            _raise_if_sync_should_yield(should_yield)
                        except WorkableSyncYielded:
                            raise
                        except WorkableRateLimitError:
                            # Re-raise so the per-job try/except below
                            # records the rate-limit and stops the sync
                            # the same way it did before parallelisation.
                            raise
                        except Exception as exc:
                            logger.error(
                                "Workable prefetch wave failed role_id=%s error_type=%s; using sequential",
                                role.id, type(exc).__name__,
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
                                provider_role_claim=provider_role_claim,
                                should_yield=should_yield,
                            )
                            _raise_if_sync_should_yield(should_yield)
                            summary["candidates_upserted"] += synced.get("candidate_upserted", 0)
                            summary["applications_upserted"] += synced.get("application_upserted", 0)
                        except WorkableSyncCancelled:
                            raise
                        except WorkableSyncYielded:
                            raise
                        except WorkableProviderLineageDrift:
                            raise
                        except Exception as exc:
                            db.rollback()
                            logger.error("Failed syncing candidate role_id=%s error_type=%s", role.id, type(exc).__name__)
                            summary["errors"].append(public_workable_sync_error(exc))
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
                    summary["errors"].append(public_workable_sync_error(exc))
                    final_status = "partial"
                    break
                except WorkableSyncCancelled:
                    raise
                except WorkableSyncYielded:
                    raise
                except WorkableProviderLineageDrift as exc:
                    db.rollback()
                    summary["errors"].append(public_workable_sync_error(exc))
                    final_status = "partial"
                    break
                except Exception as exc:
                    db.rollback()
                    logger.error("Failed syncing job org_id=%s error_type=%s", org.id, type(exc).__name__)
                    summary["errors"].append(public_workable_sync_error(exc))
                    final_status = "partial"

                # Yielded mid-candidate-loop above: this job's progress is
                # persisted, now release the mutex to the waiting op.
                if yielded_for_op:
                    break

            _raise_if_sync_should_yield(should_yield)
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
            summary["errors"].append(public_workable_sync_error("Sync cancelled by user"))
            summary["phase"] = "cancelled"
            summary["current_step"] = None
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            org.workable_last_sync_at = _now()
            org.workable_last_sync_status = "cancelled"
            org.workable_last_sync_summary = sanitize_json_for_storage(dict(summary))
            self._persist_progress(db, org, run, summary, final_status="cancelled")
            return summary
        except WorkableSyncYielded:
            summary["errors"].append(
                "Paused for a pending Workable write or uncertain sync lease; "
                "remaining data resyncs on the next run."
            )
            summary["phase"] = "paused"
            summary["current_step"] = None
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            org.workable_last_sync_at = _now()
            org.workable_last_sync_status = "partial"
            org.workable_last_sync_summary = sanitize_json_for_storage(dict(summary))
            self._persist_progress(db, org, run, summary, final_status="partial")
            return summary
        except Exception as exc:
            logger.error("Workable org sync failed error_type=%s", type(exc).__name__)
            summary["errors"].append(public_workable_sync_error(exc))
            summary["phase"] = "failed"
            summary["current_step"] = None
            summary["db_snapshot"] = self._build_db_snapshot(db, org)
            org.workable_last_sync_at = _now()
            org.workable_last_sync_status = "failed"
            org.workable_last_sync_summary = sanitize_json_for_storage(dict(summary))
            self._persist_progress(db, org, run, summary, final_status="failed")
            raise

    def _job_identifiers(self, job: dict, role: Role | None = None) -> list[str]:
        return job_identifiers(job, role)

    def _list_job_candidates_for_job(
        self,
        *,
        job: dict,
        role: Role | None,
        should_yield: Callable[[], bool] | None = None,
    ) -> list[dict]:
        return list_job_candidates(
            self.client, job=job, role=role, should_yield=should_yield,
        )

    def _prefetch_full_candidate_payloads(
        self, candidate_refs: list[dict], *, should_yield: Callable[[], bool] | None = None,
    ) -> dict[str, dict]:
        return prefetch_full_candidate_payloads(
            self.client, candidate_refs, is_terminal=_is_terminal_candidate,
            should_yield=should_yield,
        )

    def _prefetch_candidate_resumes(
        self, payloads_by_id: dict[str, dict], *, should_yield: Callable[[], bool] | None = None,
    ) -> dict[str, tuple[str, bytes]]:
        return prefetch_candidate_resumes(self.client, payloads_by_id, should_yield=should_yield)

    def _filter_payloads_missing_cv(
        self, db: Session, org: Organization, role: Role, payloads_by_id: dict[str, dict],
    ) -> dict[str, dict]:
        """Compatibility wrapper around the detached bulk CV-presence read."""
        return filter_payloads_missing_cv(
            db, organization_id=int(org.id), role_id=int(role.id), payloads_by_id=payloads_by_id,
        )

    def _job_details_for_role(
        self,
        *,
        job: dict,
        role: Role | None = None,
        should_yield: Callable[[], bool] | None = None,
    ) -> dict:
        return job_details_for_role(
            self.client, self._job_details_cache, job=job, role=role,
            should_yield=should_yield,
        )

    def _refresh_role_stages(self, role: RoleProviderClaim, shortcode: str | None) -> list[dict] | None:
        return fetch_role_stages(self.client, role, shortcode, ttl=WORKABLE_STAGES_TTL)

    def _upsert_role(
        self, db: Session, org: Organization, job: dict, *,
        expected_org_fingerprint: str | None = None,
        should_yield: Callable[[], bool] | None = None,
    ) -> tuple[Role, bool]:
        # Prefer shortcode (used by Workable API for /jobs/:shortcode/candidates)
        job_id = sanitize_text_for_storage(str(job.get("shortcode") or job.get("id") or "").strip())
        title = sanitize_text_for_storage(
            str(job.get("title") or job.get("name") or f"Workable role {job_id or 'unknown'}").strip()
        )
        organization_id = int(org.id)
        provider_claim = build_role_provider_claim(db, org, job_id)
        if expected_org_fingerprint and provider_claim.organization_auth_fingerprint != expected_org_fingerprint:
            db.rollback()
            raise WorkableProviderLineageDrift(
                "Workable organization lineage changed before job provider read"
            )
        finish_db_phase(db)

        # Fetch the complete provider snapshot only after the read phase commits.
        _raise_if_sync_should_yield(should_yield)
        assert_provider_ready(db)
        details = self._job_details_for_role(
            job=job, role=None, should_yield=should_yield,
        )
        _raise_if_sync_should_yield(should_yield)
        assert_provider_ready(db)
        fetched_stages = self._refresh_role_stages(provider_claim, job_id)
        _raise_if_sync_should_yield(should_yield)

        current_org = db.get(Organization, organization_id)
        if (
            current_org is None
            or workable_org_auth_fingerprint(current_org)
            != provider_claim.organization_auth_fingerprint
        ):
            raise RuntimeError("Workable sync organization changed during provider read")
        org = current_org

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
        audit_before: dict[str, Any] | None = None
        audit_from_version: int | None = None
        if job_id:
            role = _locked_existing_role(
                db,
                Role.organization_id == organization_id,
                Role.workable_job_id == job_id,
            )
            if provider_claim.role_id is not None:
                if (
                    role is None
                    or int(role.id) != provider_claim.role_id
                    or int(role.version or 1) != provider_claim.role_version
                ):
                    raise RuntimeError("Workable sync role changed during provider read")
            elif role is not None:
                raise RuntimeError("Workable sync role appeared during provider read")
            if role is not None:
                audit_before = capture_role_change_snapshot(role)
                audit_from_version = int(role.version or 1)
        created = False
        if not role:
            # Bridge: before minting a fresh role, try to ADOPT the inactive
            # requisition job whose ref code is stamped in this Workable job's
            # spec (draft -> open, no duplicate). Adopted roles are treated as
            # existing so their brief-materialized criteria are preserved.
            adoption_audit: dict[str, Any] = {}
            role = _adopt_requisition_role(
                db,
                org,
                job_id=job_id,
                title=title,
                description=description,
                audit_context=adoption_audit,
            )
            if role is not None:
                audit_before = adoption_audit.get("before")
                audit_from_version = adoption_audit.get("from_version")
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
        restore_role_from_ats(role, restored_at=_now(), provider="Workable")
        role.source = "workable"
        role.workable_job_id = job_id or role.workable_job_id
        if isinstance(fetched_stages, list) and fetched_stages:
            # A concurrent role edit is fenced by the claim/version check above.
            # Re-check the live cache too: another successful stage refresh must
            # not be replaced by an older provider response.
            live_synced_at = role.workable_stages_synced_at
            live_cache_fresh = False
            if role.workable_stages and live_synced_at is not None:
                if live_synced_at.tzinfo is None:
                    live_synced_at = live_synced_at.replace(tzinfo=timezone.utc)
                live_cache_fresh = datetime.now(timezone.utc) - live_synced_at < WORKABLE_STAGES_TTL
            if not live_cache_fresh:
                role.workable_stages = fetched_stages
                role.workable_stages_synced_at = datetime.now(timezone.utc)
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
        next_job_data = material_boundary.preserve_material_change_marker(
            previous_job_data,
            next_job_data,
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
        spec_upload: tuple[str, bytes, str] | None = None
        if (created or spec_changed) and (role.job_spec_text or "").strip():
            spec_content = (role.job_spec_text or "").strip().encode("utf-8")
            spec_filename = sanitize_text_for_storage(
                f"job-spec-{role.name or role.id}.txt"
            ).replace("/", "-")
            spec_upload = (
                spec_filename,
                spec_content,
                generate_s3_key("job_spec", role.id, spec_filename),
            )
        if not isinstance(role.screening_pack_template, dict) or not isinstance(role.tech_interview_pack_template, dict):
            templates = build_role_interview_pack_templates(role)
            role.screening_pack_template = templates.get("screening")
            role.tech_interview_pack_template = templates.get("tech_stage_2")
        material_claim = None
        material_client = None
        material_retry = material_boundary.has_material_change_marker(role)
        if created:
            from ....services.role_criteria_service import sync_all_criteria

            sync_all_criteria(db, role)
        elif spec_changed or material_retry:
            if getattr(role, "agentic_mode_enabled", False):
                try:
                    material_claim = material_boundary.prepare_material_change_claim(db, role)
                except Exception as exc:
                    logger.error("Preparing detached material-change assessment failed error_type=%s", type(exc).__name__)
                    from ....services.role_criteria_service import sync_derived_criteria

                    material_claim = None
                    material_boundary.clear_material_change_marker(role)
                    sync_derived_criteria(db, role)
                if material_claim is not None and material_claim.provider_required:
                    try:
                        material_client = material_boundary.build_material_change_client(org)
                    except Exception:
                        logger.warning("Material-change client unavailable role_id=%s", role.id)
            else:
                from ....services.role_criteria_service import sync_derived_criteria

                material_boundary.clear_material_change_marker(role)
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
        provisioning_requested = False
        if (created or spec_changed) and (role.job_spec_text or "").strip():
            from ....platform.config import settings

            if getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
                from ....services.task_provisioning_service import (
                    request_assessment_task_provisioning,
                )

                provisioning_requested = bool(request_assessment_task_provisioning(
                    role,
                    reason=("workable_role_create" if created else "workable_spec_update"),
                    supersede_generated_drafts=bool(spec_changed),
                ))

        _record_workable_role_change(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from_version,
            job_id=job_id,
        )
        material_claim = material_boundary.stamp_material_change_version(role, material_claim)
        db.flush()
        role_id = int(role.id)
        expected_role_version = int(role.version or 1)
        expected_spec = role.job_spec_text or ""
        finish_db_phase(db)

        if provisioning_requested:
            assert_provider_ready(db)
            try:
                from ....tasks.assessment_tasks import generate_assessment_task_for_role

                generate_assessment_task_for_role.apply_async(
                    args=[role_id, organization_id], countdown=45,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("Auto-generate enqueue failed role_id=%s error_type=%s; durable sweep will retry", role_id, type(exc).__name__)

        if material_claim is not None:
            if not material_boundary.execute_material_change(db, material_claim, material_client):
                logger.info(
                    "Discarded stale Workable material-change result role_id=%s",
                    role_id,
                )

        if spec_upload is not None:
            spec_filename, spec_content, s3_key = spec_upload
            assert_provider_ready(db)
            try:
                spec_url = upload_bytes_to_s3(
                    spec_content,
                    s3_key,
                    content_type="text/plain",
                )
            except Exception as exc:
                logger.warning("Failed saving Workable job spec role_id=%s error_type=%s", role_id, type(exc).__name__)
                spec_url = None
            if spec_url:
                role = _locked_existing_role(
                    db,
                    Role.id == role_id,
                    Role.organization_id == organization_id,
                    Role.workable_job_id == job_id,
                )
                if (
                    role is not None
                    and int(role.version or 1) == expected_role_version
                    and (role.job_spec_text or "") == expected_spec
                ):
                    role.job_spec_file_url = spec_url
                    role.job_spec_filename = spec_filename
                    role.job_spec_uploaded_at = _now()
                    finish_db_phase(db)
                else:
                    db.rollback()
                    logger.info(
                        "Discarded stale Workable job-spec upload result role_id=%s",
                        role_id,
                    )
            else:
                logger.warning(
                    "Skipping Workable job-spec store for role_id=%s — object storage unavailable",
                    role_id,
                )

        role = db.get(Role, role_id)
        if role is None:
            raise RuntimeError("Workable sync role disappeared after provider write")
        if role.job_spec_manually_edited_at and role.job_spec_manually_edited_at.tzinfo is None:
            role.job_spec_manually_edited_at = role.job_spec_manually_edited_at.replace(tzinfo=timezone.utc)
        return role, created

    # Resolved (advanced/hired/rejected) candidates are frozen for
    # scoring/enrichment, but we still refresh their read-only Workable
    # activity feed so post-decision recruiter notes (comments + ratings)
    # appear on the profile. Debounced to this interval so re-reading the
    # feed for a growing pile of resolved candidates never reintroduces the
    # per-candidate API pressure the freeze was built to avoid.
    _RESOLVED_ACTIVITIES_REFRESH_INTERVAL = timedelta(hours=6)

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
        provider_role_claim: RoleProviderClaim | None = None,
        should_yield: Callable[[], bool] | None = None,
    ) -> dict:
        organization_id = int(org.id)
        role_id = int(role.id)
        run_id = int(run.id) if run is not None else None
        if self._is_cancel_requested(db, org, run):
            raise WorkableSyncCancelled()
        if provider_role_claim is None:
            provider_role_claim = claim_role_provider_wave(
                db, org, str(role.workable_job_id or ""), role_id,
            )
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
                _raise_if_sync_should_yield(should_yield)
                assert_provider_ready(db)
                full_payload = self.client.get_candidate(candidate_id)
                _raise_if_sync_should_yield(should_yield)
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
        email = _candidate_email(candidate_payload) or _candidate_email(candidate_ref)
        phone_key = _normalize_phone_for_match(_candidate_phone(candidate_payload))
        claim = build_candidate_claim(
            db,
            organization_id=organization_id,
            run_id=run_id,
            role_id=role_id,
            candidate_external_id=candidate_id,
            email=email,
            phone_normalized=phone_key,
            mode=mode,
            terminal=bool(ref_terminal or ref_disqualified),
            now=now,
            resolved_activities_interval=self._RESOLVED_ACTIVITIES_REFRESH_INTERVAL,
        )
        lineage_matches = candidate_claim_matches_role(claim, provider_role_claim)
        finish_db_phase(db)
        if not lineage_matches:
            raise WorkableProviderLineageDrift(
                "Workable provider lineage changed during candidate read"
            )

        activities_split = None
        if claim.activities_due:
            _raise_if_sync_should_yield(should_yield)
            assert_provider_ready(db)
            activities_split = fetch_candidate_activities(self.client, candidate_id)
            _raise_if_sync_should_yield(should_yield)

        resume_upload = None
        if claim.needs_resume:
            downloaded = prefetched_resume
            if downloaded is None:
                _raise_if_sync_should_yield(should_yield)
                assert_provider_ready(db)
                downloaded = self.client.download_candidate_resume(candidate_payload)
                _raise_if_sync_should_yield(should_yield)
            if downloaded:
                filename, content = downloaded
                prepared = prepare_resume_upload(filename, content)
                if prepared is not None:
                    entity_id: int | str = (
                        claim.application_id
                        or f"workable-{organization_id}-{role_id}-{candidate_id}"
                    )
                    s3_key = generate_s3_key("cv", entity_id, prepared.filename)
                    _raise_if_sync_should_yield(should_yield)
                    assert_provider_ready(db)
                    file_url = upload_bytes_to_s3(
                        prepared.content,
                        s3_key,
                        content_type=prepared.content_type,
                    )
                    _raise_if_sync_should_yield(should_yield)
                    if file_url:
                        resume_upload = (prepared, file_url, _now())
                    else:
                        logger.warning(
                            "Skipping CV store for Workable candidate=%s — "
                            "object storage unavailable",
                            candidate_id,
                        )

        workable_score = (None, None, None)
        if not (ref_terminal or ref_disqualified or claim.resolved):
            _raise_if_sync_should_yield(should_yield)
            assert_provider_ready(db)
            workable_score = self.client.extract_workable_score(
                candidate_payload=candidate_payload,
                ratings_payload=None,
            )
            _raise_if_sync_should_yield(should_yield)

        _raise_if_sync_should_yield(should_yield)
        org, run, role, existing, claimed_candidate = revalidate_candidate_claim(db, claim)
        if (
            org.workable_sync_cancel_requested_at is not None
            or (run is not None and run.cancel_requested_at is not None)
        ):
            raise WorkableSyncCancelled()

        if ref_terminal or ref_disqualified:
            # Existing candidates reaching a terminal Workable state provide a
            # realized outcome for model refinement. Record it through the shared
            # transition hooks, refresh the observed stage, and park them at
            # `advanced`. Do not import brand-new terminal candidates: Tali never
            # tracked or scored them, so there is no decision to pair with.
            if existing is None:
                return counters
            outcome = _terminal_outcome(candidate_payload, candidate_ref, disqualified=ref_disqualified)
            # Never partially advance while an outbound outcome is unresolved.
            fence_inbound_outcome_before_mutation(db, existing, outcome)
            fence_auto_reject_lifecycle_restore(db, existing, actor_type="sync")
            existing.workable_candidate_id = existing.workable_candidate_id or sanitize_text_for_storage(candidate_id)
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
            fence_auto_reject_lifecycle_restore(db, existing, actor_type="sync")
            existing.workable_candidate_id = existing.workable_candidate_id or sanitize_text_for_storage(candidate_id)
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
            if activities_split is not None and claimed_candidate is not None:
                comment_entries, other_entries = activities_split
                claimed_candidate.workable_comments = sanitize_json_for_storage(comment_entries)
                claimed_candidate.workable_activities = sanitize_json_for_storage(other_entries)
                existing.workable_comments = claimed_candidate.workable_comments
                existing.workable_activities = claimed_candidate.workable_activities
                activities_fetched_at = now.isoformat()

            replace_sync_state_preserving_writeback(existing, {
                    "last_sync_at": now.isoformat(),
                    "sync_status": "success",
                    "run_id": run.id if run else None,
                    "source": "workable",
                    "mode": mode,
                    "frozen": True,
                    "last_activities_fetch_at": activities_fetched_at,
            })
            counters["application_upserted"] += 1
            return counters

        if not email:
            logger.debug(
                "Candidate id=%s has no email in list payload; syncing by Workable ID only.",
                candidate_id,
            )

        candidate = claimed_candidate
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

        if activities_split is not None:
            comment_entries, other_entries = activities_split
            candidate.workable_comments = sanitize_json_for_storage(comment_entries)
            candidate.workable_activities = sanitize_json_for_storage(other_entries)

        db.flush()
        counters["candidate_upserted"] += 1

        app = existing
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

        fence_auto_reject_lifecycle_restore(db, app, actor_type="sync")
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
        except Exception as exc:  # pragma: no cover — never block the candidate sync
            logger.error("post-handover reconcile failed application_id=%s error_type=%s", app.id, type(exc).__name__)

        app.external_refs = sanitize_json_for_storage(
            {
                "workable_candidate_id": candidate_id,
                "workable_job_id": role.workable_job_id,
                "workable_role_shortcode": job.get("shortcode"),
                "workable_role_id": job.get("id"),
            }
        )
        replace_sync_state_preserving_writeback(app, {
                "last_sync_at": now.isoformat(),
                "sync_status": "success",
                "run_id": run.id if run else None,
                "source": "workable",
                "mode": mode,
        })
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

        # Skip ratings API during sync to stay under rate limit (10 req/10 sec);
        # the payload-only score was extracted in the detached provider phase.
        raw_score, normalized_score, score_source = workable_score
        # Only overwrite when we successfully extracted a score.
        if raw_score is not None or normalized_score is not None:
            app.workable_score_raw = raw_score
            app.workable_score = normalized_score
            app.workable_score_source = score_source

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
            if need_cv and resume_upload is not None:
                prepared, file_url, uploaded_at = resume_upload
                apply_resume_upload(
                    app=app,
                    candidate=candidate,
                    upload=prepared,
                    file_url=file_url,
                    uploaded_at=uploaded_at,
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
            paid_work_allowed = role_allows_new_paid_ats_work(role, db=db)
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
