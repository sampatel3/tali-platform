from __future__ import annotations


# Current, reviewed Haiku. The 3.x aliases below are retired and remain only so
# an explicit legacy request is recognised and redirected. They must never be
# returned as provider candidates merely because historical pricing still exists.
CURRENT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
PRIMARY_HAIKU_MODEL = "claude-3-5-haiku-latest"
SNAPSHOT_HAIKU_MODEL = "claude-3-5-haiku-20241022"
LEGACY_HAIKU_MODEL = "claude-3-haiku-20240307"

_HAIKU_ALIASES = {
    CURRENT_HAIKU_MODEL,
    PRIMARY_HAIKU_MODEL,
    SNAPSHOT_HAIKU_MODEL,
    LEGACY_HAIKU_MODEL,
}


def candidate_models_for(model: str | None) -> list[str]:
    """Return reviewed provider candidates for a requested Claude model.

    A retired Haiku alias redirects to the current Haiku without probing known
    dead models. The metered provider boundary validates unrelated model ids.
    """
    resolved = (model or "").strip()
    if not resolved:
        resolved = CURRENT_HAIKU_MODEL

    candidates: list[str] = []

    def _add(value: str) -> None:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    if resolved.lower() in _HAIKU_ALIASES:
        # Known retired aliases resolve directly to the reviewed current id.
        _add(CURRENT_HAIKU_MODEL)
    else:
        _add(resolved)

    return candidates


def is_model_not_found_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    if not text:
        return False
    return (
        "not_found_error" in text
        or ("model" in text and "not found" in text)
        or ("error code: 404" in text and "model" in text)
    )
