"""Requisition spec template — default, resolve, validation."""
import copy

import pytest
from fastapi import HTTPException

from app.models import Organization
from app.services.requisition_template_service import (
    DEFAULT_JD_TEMPLATE,
    DEFAULT_REQUISITION_TEMPLATE,
    resolve_template,
    set_template_for_org,
    template_key_to_column,
    validate_template,
)


def _org(db, **kw):
    org = Organization(name="Acme", slug="acme", **kw)
    db.add(org)
    db.flush()
    return org


def test_default_template_is_valid_and_has_required_fields():
    validate_template(DEFAULT_REQUISITION_TEMPLATE)
    section_keys = [s["key"] for s in DEFAULT_REQUISITION_TEMPLATE["sections"]]
    assert section_keys == [
        "role_basics",
        "logistics",
        "compensation",
        "requirements",
        "context",
    ]
    # title is the first required field.
    first_field = DEFAULT_REQUISITION_TEMPLATE["sections"][0]["fields"][0]
    assert first_field["key"] == "title" and first_field["required"] is True


def test_resolve_returns_default_deep_copy_when_no_override(db):
    org = _org(db)
    resolved = resolve_template(org)
    # A deep copy — mutating it must not touch the module constant.
    resolved["sections"][0]["fields"][0]["label"] = "MUTATED"
    assert DEFAULT_REQUISITION_TEMPLATE["sections"][0]["fields"][0]["label"] == "Title"


def test_resolve_returns_org_override(db):
    custom = {
        "version": 1,
        "sections": [
            {
                "key": "basics",
                "label": "Basics",
                "fields": [
                    {"key": "title", "label": "Title", "type": "text", "required": True},
                ],
            }
        ],
    }
    org = _org(db, requisition_spec_template=custom)
    result = resolve_template(org)
    # The override's sections are returned verbatim...
    assert result["sections"] == custom["sections"]
    assert result["version"] == 1
    # ...and resolve injects the default JD template when the override lacks one.
    assert result["jd_template"] == DEFAULT_JD_TEMPLATE


def test_template_key_to_column_mapping():
    assert template_key_to_column("title") == "title"
    # target_start_date (template key) maps to the target_start column.
    assert template_key_to_column("target_start_date") == "target_start"
    # bonus/equity/benefits/visa_sponsorship have no column → custom_fields.
    assert template_key_to_column("bonus") is None
    assert template_key_to_column("visa_sponsorship") is None


def test_set_template_for_org_persists_and_returns(db):
    org = _org(db)
    custom = {
        "version": 1,
        "sections": [
            {
                "key": "basics",
                "label": "Basics",
                "fields": [
                    {"key": "title", "label": "Title", "type": "text", "required": True},
                    {
                        "key": "region",
                        "label": "Region",
                        "type": "select",
                        "required": False,
                        "options": ["EMEA", "APAC"],
                    },
                ],
            }
        ],
    }
    saved = set_template_for_org(db, org, custom)
    assert saved == custom
    assert org.requisition_spec_template == custom


@pytest.mark.parametrize(
    "bad",
    [
        "not-an-object",
        {"sections": "nope"},
        {"sections": []},  # empty
        {"sections": [{"label": "no key", "fields": [{"key": "a", "label": "A", "type": "text"}]}]},
        {"sections": [{"key": "s", "label": "S", "fields": []}]},  # empty fields
        # bad field type
        {"sections": [{"key": "s", "label": "S", "fields": [{"key": "a", "label": "A", "type": "wat"}]}]},
        # select without options
        {"sections": [{"key": "s", "label": "S", "fields": [{"key": "a", "label": "A", "type": "select"}]}]},
        # select with empty options
        {
            "sections": [
                {"key": "s", "label": "S", "fields": [{"key": "a", "label": "A", "type": "select", "options": []}]}
            ]
        },
        # duplicate section keys
        {
            "sections": [
                {"key": "s", "label": "S", "fields": [{"key": "a", "label": "A", "type": "text"}]},
                {"key": "s", "label": "S2", "fields": [{"key": "b", "label": "B", "type": "text"}]},
            ]
        },
        # duplicate field keys within a section
        {
            "sections": [
                {
                    "key": "s",
                    "label": "S",
                    "fields": [
                        {"key": "a", "label": "A", "type": "text"},
                        {"key": "a", "label": "A2", "type": "text"},
                    ],
                }
            ]
        },
        # non-bool required
        {"sections": [{"key": "s", "label": "S", "fields": [{"key": "a", "label": "A", "type": "text", "required": "yes"}]}]},
    ],
)
def test_validate_rejects_bad_shapes(bad):
    with pytest.raises(HTTPException) as e:
        validate_template(bad)
    assert e.value.status_code == 422


def test_validate_accepts_field_keys_repeated_across_sections():
    # Same key in two DIFFERENT sections is allowed (uniqueness is per-section).
    ok = {
        "version": 1,
        "sections": [
            {"key": "s1", "label": "S1", "fields": [{"key": "a", "label": "A", "type": "text"}]},
            {"key": "s2", "label": "S2", "fields": [{"key": "a", "label": "A", "type": "text"}]},
        ],
    }
    assert validate_template(copy.deepcopy(ok)) is not None
