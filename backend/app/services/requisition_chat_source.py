"""Durable source-material lifecycle for requisition chat turns."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from ..models.role_brief import RoleBrief
from .role_brief_service import update_brief_fields

_MAX_SOURCE_MATERIAL_CHARS = 200_000
_SOURCE_DIGEST_KEYS = {
    "messages": "recruiter_source_hydration_digest",
    "client_messages": "client_source_hydration_digest",
}


def source_material_for_transcript(brief: RoleBrief, transcript_attr: str) -> str:
    """Return durable source material visible to one intake transcript.

    ``raw_input`` retains recruiter-source provenance. Once a canonical or
    structured revision exists, only that active revision (plus an explicitly
    pending proposal) is model context. Public client-intake source remains
    isolated so recruiter notes never enter client context.
    """

    if transcript_attr == "messages":
        state = dict(brief.agent_state or {})
        current_spec = str(state.get("jd_override") or "").strip()
        pending_spec = str(state.get("pending_job_spec_source") or "").strip()
        if current_spec:
            source = f"[ACTIVE CANONICAL JOB SPEC]\n{current_spec}"
            if pending_spec:
                source += (
                    "\n\n[PENDING PROPOSED JOB SPEC — not yet applied]\n"
                    + pending_spec
                )
            return source
        if pending_spec:
            return f"[PENDING PROPOSED JOB SPEC — not yet applied]\n{pending_spec}"
        # Once a structured edit supersedes a verbatim spec, raw_input remains
        # provenance only.  Re-feeding it would resurrect fields intentionally
        # removed by a replacement.
        if state.get("canonical_spec_mode") == "structured":
            return ""
        return str(brief.raw_input or "").strip()
    state = dict(brief.agent_state or {})
    current_spec = str(state.get("client_canonical_source") or "").strip()
    pending_spec = str(state.get("client_pending_job_spec_source") or "").strip()
    if current_spec:
        source = f"[ACTIVE CANONICAL JOB SPEC]\n{current_spec}"
        if pending_spec:
            source += "\n\n[PENDING PROPOSED JOB SPEC — not yet applied]\n" + pending_spec
        return source
    if pending_spec:
        return f"[PENDING PROPOSED JOB SPEC — not yet applied]\n{pending_spec}"
    if state.get("client_canonical_spec_mode") == "structured":
        return ""
    return str(state.get("client_source_material") or "").strip()


def persist_source_material(
    db: Session,
    brief: RoleBrief,
    source_material: str,
    *,
    transcript_attr: str,
) -> None:
    """Append newly decoded attachment text without duplicating prior source."""

    new_source = str(source_material or "").strip()
    if not new_source:
        return
    # Read only the attachment bucket here. The model-facing source may also
    # include jd_override; copying that rendered spec into raw_input on every
    # upload would duplicate it indefinitely.
    existing = (
        str(brief.raw_input or "").strip()
        if transcript_attr == "messages"
        else str((brief.agent_state or {}).get("client_source_material") or "").strip()
    )
    if new_source in existing:
        return
    combined = f"{existing}\n\n{new_source}".strip() if existing else new_source
    if len(combined) > _MAX_SOURCE_MATERIAL_CHARS:
        combined = combined[-_MAX_SOURCE_MATERIAL_CHARS:]
    if transcript_attr == "messages":
        update_brief_fields(db, brief, raw_input=combined)
        return
    state = dict(brief.agent_state or {})
    state["client_source_material"] = combined
    update_brief_fields(db, brief, agent_state=state)


def _source_digest(source_material: str) -> str:
    return hashlib.sha256(source_material.encode("utf-8")).hexdigest()


def source_needs_hydration(
    brief: RoleBrief,
    source_material: str,
    transcript_attr: str,
) -> bool:
    source = str(source_material or "").strip()
    if not source:
        return False
    key = _SOURCE_DIGEST_KEYS.get(transcript_attr)
    if key is None:
        return True
    return (brief.agent_state or {}).get(key) != _source_digest(source)


def mark_source_hydrated(
    db: Session,
    brief: RoleBrief,
    source_material: str,
    transcript_attr: str,
) -> None:
    source = str(source_material or "").strip()
    key = _SOURCE_DIGEST_KEYS.get(transcript_attr)
    if not source or key is None:
        return
    state = dict(brief.agent_state or {})
    digest = _source_digest(source)
    if state.get(key) == digest:
        return
    state[key] = digest
    update_brief_fields(db, brief, agent_state=state)


__all__ = [
    "mark_source_hydrated",
    "persist_source_material",
    "source_material_for_transcript",
    "source_needs_hydration",
]
