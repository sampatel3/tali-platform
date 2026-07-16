from __future__ import annotations


# Current, account-available Haiku. The 3.x aliases below are retired for our
# Anthropic account — all three 404 as of 2026-06 (verified against the prod
# key) — and remain only so an explicit legacy request still detects as a Haiku
# alias and resolves to a working model via the fallback chain. ``CURRENT`` is
# offered as the FIRST fallback so any Haiku-family request resolves even when
# the requested snapshot is dead. This is what was silently failing the rubric/
# cv/fit Haiku calls: every candidate model in the old chain 404'd, so the call
# raised instead of degrading.
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
    """Return a deterministic fallback chain for known Claude Haiku model aliases.

    Any Haiku-family request always includes the current, account-available
    Haiku so a request for a retired snapshot still resolves to a working model.
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
        # Known retired aliases must not impose a guaranteed provider 404 on
        # every otherwise-successful call. Resolve directly to the current
        # account-available model, while retaining the requested identifier and
        # other snapshots as deeper compatibility fallbacks if availability
        # changes again.
        _add(CURRENT_HAIKU_MODEL)
        _add(resolved)
        _add(PRIMARY_HAIKU_MODEL)
        _add(SNAPSHOT_HAIKU_MODEL)
        _add(LEGACY_HAIKU_MODEL)
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
