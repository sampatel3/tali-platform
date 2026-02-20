from __future__ import annotations


PRIMARY_HAIKU_MODEL = "claude-3-5-haiku-latest"
SNAPSHOT_HAIKU_MODEL = "claude-3-5-haiku-20241022"
LEGACY_HAIKU_MODEL = "claude-3-haiku-20240307"


def candidate_models_for(model: str | None) -> list[str]:
    """Return a deterministic fallback chain for known Claude Haiku model aliases."""
    resolved = (model or "").strip()
    if not resolved:
        resolved = PRIMARY_HAIKU_MODEL

    candidates: list[str] = []

    def _add(value: str) -> None:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    _add(resolved)

    lower = resolved.lower()
    if lower in {
        PRIMARY_HAIKU_MODEL,
        SNAPSHOT_HAIKU_MODEL,
        LEGACY_HAIKU_MODEL,
    }:
        _add(PRIMARY_HAIKU_MODEL)
        _add(SNAPSHOT_HAIKU_MODEL)
        _add(LEGACY_HAIKU_MODEL)

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
