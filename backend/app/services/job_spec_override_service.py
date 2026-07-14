"""Protect legacy Taali-authored job specs from ATS pull-sync replacement.

Migration 161 adds an explicit ``job_spec_manually_edited_at`` marker, but
historic uploads and agent edits predate that column. Those paths wrote
``job_spec_text`` (and ``job_spec_uploaded_at``) without changing the cached
ATS payload; older agent/upload code also left ``description`` at the last ATS
value. This helper recognizes that provenance once, stamps the new marker, and
lets all later syncs use the explicit fast path.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models.role import Role


def _normalized(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _stamp_legacy_override(role: Role) -> bool:
    role.job_spec_manually_edited_at = (
        getattr(role, "job_spec_uploaded_at", None) or datetime.now(timezone.utc)
    )
    # Repair the compatibility reader at the same time. Current edit/upload
    # paths keep these fields together; only legacy paths left description at
    # the prior ATS value.
    role.description = _normalized(getattr(role, "job_spec_text", None))
    return True


def has_manual_job_spec_override(
    role: Role,
    *,
    ats_source: str,
    cached_ats_spec: str | None = None,
) -> bool:
    """Return whether Taali's current spec must win over the ATS payload.

    ``cached_ats_spec`` must be formatted from the *previously stored* raw ATS
    payload, before the caller replaces that snapshot with the current pull.
    It is only useful when the old payload contained real description content;
    metadata-only payloads are too weak to distinguish a manual edit from an
    older successful rich sync.

    The ``description == job_spec_text`` invariant is retained as a safety
    signal for normal legacy ATS rows. Historic ATS importers wrote both fields
    together, whereas Taali upload/agent-edit paths wrote only job_spec_text.
    This prevents formatting-code drift from freezing ordinary ATS-owned specs.
    """

    if getattr(role, "job_spec_manually_edited_at", None) is not None:
        return True

    current_spec = _normalized(getattr(role, "job_spec_text", None))
    if not current_spec:
        return False

    prior_source = _normalized(getattr(role, "source", None)).lower()
    if prior_source != ats_source.strip().lower():
        # A requisition/manual role adopted by an ATS already had Taali-owned
        # content before the first remote snapshot existed.
        return _stamp_legacy_override(role)

    previous_ats_spec = _normalized(cached_ats_spec)
    legacy_description = _normalized(getattr(role, "description", None))

    if previous_ats_spec and current_spec == previous_ats_spec:
        return False
    if legacy_description and current_spec == legacy_description:
        # Normal ATS importers historically wrote both fields atomically. If a
        # formatter changed since the cached payload was stored, trust this
        # stronger provenance signal and continue syncing.
        return False

    if previous_ats_spec or (legacy_description and current_spec != legacy_description):
        return _stamp_legacy_override(role)

    filename = _normalized(getattr(role, "job_spec_filename", None)).lower()
    generated_ats_attachment = filename.startswith("job-spec-") and filename.endswith(
        ".txt"
    )
    if filename and not generated_ats_attachment:
        # User uploads keep their original filename; ATS attachments use the
        # generated job-spec-<role>.txt convention.
        return _stamp_legacy_override(role)

    return False


__all__ = ["has_manual_job_spec_override"]
