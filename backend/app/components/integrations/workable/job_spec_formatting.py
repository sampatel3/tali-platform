"""Formatting and provenance helpers for Workable job specifications."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from ....services.document_service import sanitize_text_for_storage


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
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            rf"\n{'#' * level} \1\n",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    # Bold/strong/emphasis to markdown
    text = re.sub(
        r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL
    )
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
                        if (
                            "'country'" in chunk
                            or '"country"' in chunk
                            or "'city'" in chunk
                            or '"city"' in chunk
                        ):
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
        region = (
            value.get("region")
            or value.get("subregion")
            or value.get("state_code")
            or value.get("state")
        )
        country = value.get("country") or value.get("country_name")
        if isinstance(city, str) and city.strip():
            parts.append(city.strip())
        if (
            isinstance(region, str)
            and region.strip()
            and region.strip() != (city or "").strip()
        ):
            parts.append(region.strip())
        if isinstance(country, str) and country.strip():
            parts.append(country.strip())
        location_str = ", ".join(parts)
        workplace = value.get("workplace_type")
        if isinstance(workplace, str) and workplace.strip():
            location_str = (
                f"{location_str} ({workplace.strip()})"
                if location_str
                else workplace.strip()
            )
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
        if isinstance((current or {}).get(key), str)
        and (current or {}).get(key).strip()
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
        details = merged.get(
            "details"
        )  # Re-get after merge so we capture nested details
        if isinstance(details, dict):
            merged = {**merged, **details}
        merged.pop("job", None)
        merged.pop("details", None)
        if not isinstance(merged.get("job"), dict) and not isinstance(
            merged.get("details"), dict
        ):
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
