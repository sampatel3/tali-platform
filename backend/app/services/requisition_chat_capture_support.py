"""Pure deterministic requisition-capture helpers.

This module owns gap/completeness calculation, template value lookup, the
opening prompt, and field coercion. The capture module re-exports these names
to preserve the established import surface.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel

from ..models.role_brief import RoleBrief
from .requisition_template_service import iter_fields, template_key_to_column

# --------------------------------------------------------------------------- #
# Deterministic helpers (no LLM, no DB) — gap engine, opening message,
# completeness, value coercion.
# --------------------------------------------------------------------------- #
def _is_empty(value: Any) -> bool:
    """A field counts as unfilled when None, empty string/whitespace, or an
    empty list/dict."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _brief_value_for_field(brief: RoleBrief, field_key: str) -> Any:
    """Current brief value for a template field key: the mapped column, else
    ``custom_fields[key]``."""
    column = template_key_to_column(field_key)
    if column is not None:
        return getattr(brief, column, None)
    return (brief.custom_fields or {}).get(field_key)


def compute_gaps(brief: RoleBrief, template: dict[str, Any]) -> list[dict[str, str]]:
    """Required template fields whose brief value is empty/blank, in template
    order. Each gap is ``{key, label, section}``."""
    gaps: list[dict[str, str]] = []
    for section, field in iter_fields(template):
        if not field.get("required"):
            continue
        if _is_empty(_brief_value_for_field(brief, field["key"])):
            gaps.append(
                {
                    "key": field["key"],
                    "label": field.get("label") or field["key"],
                    "section": section.get("key") or "",
                }
            )
    return gaps


def compute_completeness(brief: RoleBrief, template: dict[str, Any]) -> int:
    """``round(100 * filled_required / total_required)`` over required fields.
    100 when the template has no required fields."""
    total = 0
    filled = 0
    for _section, field in iter_fields(template):
        if not field.get("required"):
            continue
        total += 1
        if not _is_empty(_brief_value_for_field(brief, field["key"])):
            filled += 1
    if total == 0:
        return 100
    return round(100 * filled / total)


def _field_by_key(template: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    for _section, field in iter_fields(template):
        if field.get("key") == key:
            return field
    return None


def _first_required_field(template: dict[str, Any]) -> Optional[dict[str, Any]]:
    for _section, field in iter_fields(template):
        if field.get("required"):
            return field
    return None


def _select_options(field: Optional[dict[str, Any]]) -> list[str]:
    """The tappable options for a select field (else empty)."""
    if field and field.get("type") == "select":
        return [str(o) for o in (field.get("options") or []) if str(o).strip()][:6]
    return []


def opening_message(template: dict[str, Any]) -> str:
    """Deterministic OPENING turn — a strong free-text brief request with a clear
    checklist. We deliberately ask for the role in the user's OWN words first
    (no tappable options on this turn) so the brief is grounded in what they
    actually want, not the agent's guesses; the multiple-choice refinement comes
    on later turns. ``template`` is accepted for signature stability."""
    return (
        "Hi — I'll help you turn this into a sharp role brief. The best way to "
        "start is to tell me about the role **in your own words** — a few "
        "sentences is plenty, and you can talk (tap the mic) or paste notes, a "
        "draft JD, or a call transcript.\n\n"
        "The more you give me here, the sharper everything downstream — try to "
        "cover:\n\n"
        "- **The role + the domain/industry** it sits in (e.g. banking, "
        "healthcare, e-commerce) — it changes what “good” looks like\n"
        "- **What this person will actually build or own** day-to-day\n"
        "- **The genuine must-haves** — skills/experience that are truly "
        "non-negotiable\n"
        "- **What “great in 6 months” looks like**\n"
        "- **Any hard nos / dealbreakers**\n\n"
        "Don't worry about structure — write it however it comes out and I'll "
        "organise it, then ask a few focused follow-ups."
    )


def _field_type_index(template: dict[str, Any]) -> dict[str, str]:
    """Map every template field key → its declared type (for coercion)."""
    return {field["key"]: field.get("type", "text") for _s, field in iter_fields(template)}


def _coerce_value(value: Any, field_type: str) -> Any:
    """Coerce a captured value per the template field type. Returns ``None``
    when the value can't be coerced / is empty (so it never overwrites)."""
    if value is None:
        return None
    if field_type == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip().replace(",", "")
        if text == "":
            return None
        if text.lstrip("-").isdigit():
            return int(text)
        try:
            return float(text)
        except (TypeError, ValueError):
            pass
        # Natural-language answer / quick-reply chip like "2 openings",
        # "around 3", "AED 200k" — pull the first number out rather than
        # rejecting the whole answer (which 422s the deterministic recorder).
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if match is None:
            return None
        num = match.group(0)
        return float(num) if "." in num else int(num)
    if field_type == "list":
        if isinstance(value, list):
            out = [str(v).strip() for v in value if str(v).strip()]
            return out or None
        text = str(value).strip()
        return [text] if text else None
    if field_type == "struct_list":
        if isinstance(value, list):
            out = []
            for item in value:
                if isinstance(item, BaseModel):
                    out.append(item.model_dump(exclude_none=True))
                elif isinstance(item, dict):
                    out.append(item)
                elif str(item).strip():
                    out.append({"value": str(item).strip()})
            return out or None
        return None
    if field_type in ("text", "longtext", "date", "select"):
        if isinstance(value, (dict, list)):
            return value or None
        text = str(value).strip()
        return text or None
    if field_type == "json":
        # Dict-shaped standard columns (sourcing_signals, process): store the
        # already-correctly-shaped value as-is.
        return value if not _is_empty(value) else None
    # Unknown type → pass through if non-empty.
    return value if not _is_empty(value) else None


# The intrinsic shape of each STANDARD capture key, derived from its Pydantic
# type — NOT from the org template's declared field type. The capture schema
# already guarantees these shapes, so re-coercing a typed dict/list-of-dict to
# whatever scalar type a template author happened to declare would corrupt it.
# Custom (org-added) keys still coerce by the template's declared type.
_STANDARD_KEY_INTRINSIC_TYPE: dict[str, str] = {
    "openings": "number",
    "salary_min": "number",
    "salary_max": "number",
    "must_haves": "list",
    "preferred": "list",
    "dealbreakers": "list",
    "tradeoffs": "list",
    "assessment_focus": "list",
    "evp": "list",
    "benefits": "list",
    "responsibilities": "list",
    "priorities": "struct_list",
    "calibration_exemplars": "struct_list",
    "sourcing_signals": "list",
    "process": "longtext",
    # everything else (title, summary, department, seniority, location_*,
    # workplace_type, employment_type, salary_currency, salary_period,
    # success_profile, target_start_date) is scalar text/date/select.
}
