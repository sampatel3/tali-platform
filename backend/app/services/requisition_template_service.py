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


# The built-in template. Mirrors the fixed contract the frontend is built
# against — sections in display order; ``question`` is the natural prompt the
# agent asks; ``required`` drives completeness + the gap engine; ``options``
# is present only on ``select`` fields.
DEFAULT_REQUISITION_TEMPLATE: dict[str, Any] = {
    "version": 1,
    "sections": [
        {
            "key": "role_basics",
            "label": "Role basics",
            "fields": [
                {
                    "key": "title",
                    "label": "Title",
                    "type": "text",
                    "required": True,
                    "question": "What role are you hiring for?",
                },
                {
                    "key": "department",
                    "label": "Department",
                    "type": "text",
                    "required": False,
                    "question": "Which team or department is this in?",
                },
                {
                    "key": "seniority",
                    "label": "Seniority",
                    "type": "select",
                    "required": False,
                    "question": "What seniority level?",
                    "options": [
                        "Intern",
                        "Junior",
                        "Mid",
                        "Senior",
                        "Staff",
                        "Lead",
                        "Principal",
                        "Director",
                        "VP",
                    ],
                },
                {
                    "key": "summary",
                    "label": "One-line summary",
                    "type": "longtext",
                    "required": False,
                    "question": "In one line, what will this person do?",
                },
            ],
        },
        {
            "key": "logistics",
            "label": "Logistics",
            "fields": [
                {
                    "key": "location_city",
                    "label": "City",
                    "type": "text",
                    "required": False,
                    "question": "Which city is this based in?",
                },
                {
                    "key": "location_country",
                    "label": "Country",
                    "type": "text",
                    "required": False,
                    "question": "Which country?",
                },
                {
                    "key": "workplace_type",
                    "label": "Workplace type",
                    "type": "select",
                    "required": True,
                    "question": "Is this onsite, hybrid, or remote?",
                    "options": ["Onsite", "Hybrid", "Remote"],
                },
                {
                    "key": "employment_type",
                    "label": "Employment type",
                    "type": "select",
                    "required": True,
                    "question": "Full-time, part-time, contract, or temporary?",
                    "options": ["Full-time", "Part-time", "Contract", "Temporary"],
                },
                {
                    "key": "openings",
                    "label": "Openings",
                    "type": "number",
                    "required": True,
                    "question": "How many are you hiring?",
                },
                {
                    "key": "urgency",
                    "label": "Hiring urgency",
                    "type": "select",
                    "required": True,
                    "question": "How urgent is this hire?",
                    "options": ["Low", "Normal", "High", "Urgent"],
                },
                {
                    "key": "target_start_date",
                    "label": "Target start date",
                    "type": "date",
                    "required": False,
                    "question": "When do you want them to start?",
                },
            ],
        },
        {
            "key": "compensation",
            "label": "Compensation",
            "fields": [
                {
                    "key": "salary_min",
                    "label": "Salary (min)",
                    "type": "number",
                    "required": True,
                    "question": "What's the bottom of the salary range?",
                },
                {
                    "key": "salary_max",
                    "label": "Salary (max)",
                    "type": "number",
                    "required": True,
                    "question": "And the top of the range?",
                },
                {
                    "key": "salary_currency",
                    "label": "Currency",
                    "type": "select",
                    "required": True,
                    "question": "Which currency?",
                    "options": ["GBP", "USD", "EUR", "AED", "SAR", "INR"],
                },
                {
                    "key": "salary_period",
                    "label": "Pay period",
                    "type": "select",
                    "required": False,
                    "question": "Per year, month, day, or hour?",
                    "options": ["year", "month", "day", "hour"],
                },
                {
                    "key": "bonus",
                    "label": "Bonus",
                    "type": "text",
                    "required": False,
                    "question": "Any bonus?",
                },
                {
                    "key": "equity",
                    "label": "Equity",
                    "type": "text",
                    "required": False,
                    "question": "Any equity?",
                },
                {
                    "key": "benefits",
                    "label": "Benefits",
                    "type": "list",
                    "required": False,
                    "question": "What benefits come with this role?",
                },
            ],
        },
        {
            "key": "requirements",
            "label": "Requirements",
            "fields": [
                {
                    "key": "must_haves",
                    "label": "Must-haves",
                    "type": "list",
                    "required": True,
                    "question": "What are the non-negotiables?",
                },
                {
                    "key": "preferred",
                    "label": "Nice-to-haves",
                    "type": "list",
                    "required": False,
                    "question": "What's nice to have but not essential?",
                },
                {
                    "key": "dealbreakers",
                    "label": "Dealbreakers",
                    "type": "list",
                    "required": False,
                    "question": "Any automatic no?",
                },
            ],
        },
        {
            "key": "context",
            "label": "Hiring context",
            "fields": [
                {
                    "key": "success_profile",
                    "label": "Success profile",
                    "type": "longtext",
                    "required": False,
                    "question": "What does great look like in 6 months?",
                },
                {
                    "key": "priorities",
                    "label": "Weighted priorities",
                    "type": "struct_list",
                    "required": False,
                    "question": "What matters most — and how would you weight it?",
                },
                {
                    "key": "tradeoffs",
                    "label": "Trade-offs",
                    "type": "list",
                    "required": False,
                    "question": "What would you trade off?",
                },
                {
                    "key": "calibration_exemplars",
                    "label": "Calibration examples",
                    "type": "struct_list",
                    "required": False,
                    "question": "Anyone (strong or weak) that calibrates the bar?",
                },
                {
                    "key": "sourcing_signals",
                    "label": "Sourcing signals",
                    "type": "list",
                    "required": False,
                    "question": "Where do great candidates come from?",
                },
                {
                    "key": "assessment_focus",
                    "label": "Assessment focus",
                    "type": "list",
                    "required": False,
                    "question": "What should we test for?",
                },
                {
                    "key": "process",
                    "label": "Interview process",
                    "type": "longtext",
                    "required": False,
                    "question": "What's the interview process?",
                },
                {
                    "key": "evp",
                    "label": "Why this job",
                    "type": "list",
                    "required": False,
                    "question": "Why would someone want this job?",
                },
            ],
        },
    ],
}


def resolve_template(org: Optional[Organization]) -> dict[str, Any]:
    """The template to capture against: the org's column override if present,
    else a deep copy of the built-in default (so callers never mutate the
    module-level constant)."""
    if org is not None:
        override = getattr(org, "requisition_spec_template", None)
        if override:
            return override
    return copy.deepcopy(DEFAULT_REQUISITION_TEMPLATE)


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
