"""Requisition spec TEMPLATE — the org's canonical definition of a COMPLETE
requisition spec (NOT just a job description): compensation, location,
logistics, requirements AND the agent-context layers.

The conversational intake (``requisition_chat_service``) captures against this
template: it drives which questions the agent asks and which fields are required
for completeness. Field keys that match a ``RoleBrief`` column write to that
column; keys without a column land in ``RoleBrief.custom_fields``.

This module owns:
  * ``DEFAULT_REQUISITION_TEMPLATE`` — the built-in spec every org starts with.
  * ``resolve_template(org)`` — the org's column override, else the default.
  * ``validate_template(...)`` — shape/type/uniqueness/options checks.
  * ``get_template_for_org`` / ``set_template_for_org`` — the settings endpoints'
    service layer. Mutators flush but do NOT commit — the caller owns the txn.

Pure helpers (resolve/validate/coerce) are unit-tested without a DB or LLM.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.organization import Organization
# The built-in template data lives in ``requisition_template_defaults`` (split
# out to stay under the file-size gate). Re-exported so callers and tests keep
# importing both names from this module.
from .requisition_template_defaults import (  # noqa: F401  (re-export)
    DEFAULT_JD_TEMPLATE,
    DEFAULT_REQUISITION_TEMPLATE,
)

# Field types the template (and the chat tool coercion) understands.
FIELD_TYPES = frozenset(
    {"text", "longtext", "number", "date", "select", "list", "struct_list"}
)

# RoleBrief columns a template field key may write to directly. Any template
# field key NOT in this set is captured into RoleBrief.custom_fields. The chat
# service shares this set so it routes captured values the same way.
BRIEF_COLUMN_KEYS = frozenset(
    {
        "title",
        "summary",
        "department",
        "location_city",
        "location_country",
        "workplace_type",
        "employment_type",
        "seniority",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "openings",
        # The template field key is ``target_start_date`` but the RoleBrief
        # column is ``target_start`` — see TEMPLATE_KEY_TO_COLUMN below.
        "target_start",
        "must_haves",
        "preferred",
        "dealbreakers",
        "success_profile",
        "priorities",
        "tradeoffs",
        "calibration_exemplars",
        "sourcing_signals",
        "assessment_focus",
        "process",
        "evp",
    }
)

# A few template field keys differ from their RoleBrief column name. Resolve a
# template field key to its column (or to itself when they already match).
TEMPLATE_KEY_TO_COLUMN: dict[str, str] = {
    "target_start_date": "target_start",
}


def template_key_to_column(field_key: str) -> Optional[str]:
    """Return the RoleBrief column a template field key maps to, or ``None`` if
    the key has no column (→ goes into ``custom_fields``)."""
    column = TEMPLATE_KEY_TO_COLUMN.get(field_key, field_key)
    return column if column in BRIEF_COLUMN_KEYS else None


def resolve_template(org: Optional[Organization]) -> dict[str, Any]:
    """The template to capture against: the org's column override if present,
    else a deep copy of the built-in default (so callers never mutate the
    module-level constant)."""
    template: Optional[dict[str, Any]] = None
    if org is not None:
        override = getattr(org, "requisition_spec_template", None)
        if override:
            template = override
    if template is None:
        template = copy.deepcopy(DEFAULT_REQUISITION_TEMPLATE)
    # Back-compat: older saved overrides may predate the JD template — always
    # carry one so the live job-spec panel has something to render.
    if not template.get("jd_template"):
        template = {**template, "jd_template": DEFAULT_JD_TEMPLATE}
    return template


# The template section a consultancy CLIENT must never set — the consultancy
# owns the economics. Dropped from the client-scoped intake so the agent never
# asks about salary / pay period / bonus / equity.
CLIENT_SCOPED_DROP_SECTIONS = frozenset({"compensation"})


def client_scoped_template(template: dict[str, Any]) -> dict[str, Any]:
    """A CLIENT-scoped view of a requisition template: the same template with the
    ``compensation`` section removed (clients don't set pay — the consultancy
    owns economics). Returns a shallow-copied template with a filtered
    ``sections`` list; the original is never mutated. Drives both the
    client-scoped gap engine/completeness and the questions the client-facing
    agent asks."""
    sections = [
        s
        for s in (template.get("sections") or [])
        if s.get("key") not in CLIENT_SCOPED_DROP_SECTIONS
    ]
    return {**template, "sections": sections}


def iter_fields(template: dict[str, Any]):
    """Yield ``(section, field)`` for every field in template (display order)."""
    for section in template.get("sections") or []:
        for field in section.get("fields") or []:
            yield section, field


def validate_template(template: Any) -> dict[str, Any]:
    """Validate a requisition spec template's shape. Raises ``HTTPException(422)``
    on any problem; returns the validated template unchanged on success.

    Rules:
      * top-level is an object with a ``sections`` list;
      * each section has a non-empty string ``key``/``label`` and a ``fields`` list;
      * section keys are unique;
      * each field has a string ``key``/``label``, a ``type`` in ``FIELD_TYPES``;
      * field keys are unique within their section (scope);
      * ``select`` fields carry a non-empty ``options`` list of strings;
      * ``required``/``question`` (when present) are the right primitive types.
    """
    def _fail(detail: str) -> None:
        raise HTTPException(status_code=422, detail=f"Invalid requisition template: {detail}")

    if not isinstance(template, dict):
        _fail("top-level must be an object")
    sections = template.get("sections")
    if not isinstance(sections, list) or not sections:
        _fail("'sections' must be a non-empty list")

    seen_section_keys: set[str] = set()
    for s_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            _fail(f"section[{s_idx}] must be an object")
        s_key = section.get("key")
        if not isinstance(s_key, str) or not s_key.strip():
            _fail(f"section[{s_idx}].key must be a non-empty string")
        if not isinstance(section.get("label"), str) or not section["label"].strip():
            _fail(f"section[{s_idx}].label must be a non-empty string")
        if s_key in seen_section_keys:
            _fail(f"duplicate section key {s_key!r}")
        seen_section_keys.add(s_key)

        fields = section.get("fields")
        if not isinstance(fields, list) or not fields:
            _fail(f"section[{s_key}].fields must be a non-empty list")

        seen_field_keys: set[str] = set()
        for f_idx, field in enumerate(fields):
            where = f"section[{s_key}].field[{f_idx}]"
            if not isinstance(field, dict):
                _fail(f"{where} must be an object")
            f_key = field.get("key")
            if not isinstance(f_key, str) or not f_key.strip():
                _fail(f"{where}.key must be a non-empty string")
            if not isinstance(field.get("label"), str) or not field["label"].strip():
                _fail(f"{where}.label must be a non-empty string")
            f_type = field.get("type")
            if f_type not in FIELD_TYPES:
                _fail(
                    f"{where}.type must be one of {sorted(FIELD_TYPES)}, got {f_type!r}"
                )
            if f_key in seen_field_keys:
                _fail(f"duplicate field key {f_key!r} in section {s_key!r}")
            seen_field_keys.add(f_key)
            if "required" in field and not isinstance(field["required"], bool):
                _fail(f"{where}.required must be a boolean")
            if "question" in field and not isinstance(field["question"], str):
                _fail(f"{where}.question must be a string")
            if f_type == "select":
                options = field.get("options")
                if not isinstance(options, list) or not options:
                    _fail(f"{where} is a select and must carry a non-empty 'options' list")
                if not all(isinstance(o, str) for o in options):
                    _fail(f"{where}.options must all be strings")
    if "jd_template" in template and not isinstance(template["jd_template"], str):
        _fail("'jd_template' must be a string")
    return template


def get_template_for_org(org: Organization) -> dict[str, Any]:
    """The resolved template for the settings GET endpoint."""
    return resolve_template(org)


def set_template_for_org(
    db: Session, org: Organization, template: Any
) -> dict[str, Any]:
    """Validate + persist a template onto the org. Flushes; caller commits.
    Returns the saved template."""
    validated = validate_template(template)
    org.requisition_spec_template = validated
    db.add(org)
    db.flush()
    return validated
