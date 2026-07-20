"""Bounded text projection for cumulative recruiter role intent."""

from __future__ import annotations


ROLE_INTENT_FREE_TEXT_MAX_CHARS = 1200
_APPEND_SEPARATOR = "\n\n"
_EARLIER_NOTES_MARKER = "[... earlier role-intent notes omitted ...]\n"
_LATEST_ANSWER_MARKER = (
    "\n[... middle of latest role-intent answer omitted ...]\n"
)


def derive_latest_free_text(
    free_text: str | None,
    *,
    previous_free_text: str | None,
) -> str | None:
    """Return the appended answer only when the stored prefix proves it.

    Recruiter answers may contain blank lines themselves, so splitting on a
    paragraph boundary is unsafe. The write path appends exactly
    ``previous.strip() + "\\n\\n" + answer``; only that exact prefix (or an
    empty previous value) establishes the latest-answer boundary.
    """
    if free_text is None:
        return None
    if not previous_free_text or not previous_free_text.strip():
        return free_text
    prefix = f"{previous_free_text.strip()}{_APPEND_SEPARATOR}"
    if free_text.startswith(prefix):
        return free_text[len(prefix):]
    return None


def compact_role_intent_free_text(
    free_text: str | None,
    *,
    latest_free_text: str | None = None,
) -> str:
    """Bound cumulative intent while prioritising the proven latest answer.

    Values at or below the cap are returned byte-for-byte. When an exact
    latest-answer boundary is supplied, a normal-sized latest answer remains
    whole and the remaining window carries recent prior context. If that latest
    answer alone exceeds the cap, its beginning and end are retained around an
    explicit omission marker. Callers without a proven boundary treat the full
    value as one standalone answer and keep both ends; the helper never guesses
    by splitting paragraphs.
    """
    if not free_text or len(free_text) <= ROLE_INTENT_FREE_TEXT_MAX_CHARS:
        return free_text or ""

    latest = latest_free_text or None
    if latest == free_text:
        prior_with_separator = ""
    elif latest and free_text.endswith(f"{_APPEND_SEPARATOR}{latest}"):
        prior_with_separator = free_text[:-len(latest)]
    else:
        # Without a proven boundary, the whole value is one standalone answer.
        # This keeps both ends instead of guessing where paragraphs begin.
        latest = free_text
        prior_with_separator = ""

    if len(latest) > ROLE_INTENT_FREE_TEXT_MAX_CHARS:
        available = (
            ROLE_INTENT_FREE_TEXT_MAX_CHARS - len(_LATEST_ANSWER_MARKER)
        )
        beginning_chars = available // 2
        ending_chars = available - beginning_chars
        return (
            f"{latest[:beginning_chars]}{_LATEST_ANSWER_MARKER}"
            f"{latest[-ending_chars:]}"
        )

    remaining_chars = ROLE_INTENT_FREE_TEXT_MAX_CHARS - len(latest)
    if remaining_chars >= len(_EARLIER_NOTES_MARKER):
        prior_chars = remaining_chars - len(_EARLIER_NOTES_MARKER)
        recent_prior = (
            prior_with_separator[-prior_chars:] if prior_chars else ""
        )
        return f"{_EARLIER_NOTES_MARKER}{recent_prior}{latest}"

    # The latest answer itself fits, but leaves no room for an honest
    # omission marker. Prefer the complete current answer over stale prior
    # context or silently removing part of the answer.
    return latest


__all__ = [
    "ROLE_INTENT_FREE_TEXT_MAX_CHARS",
    "compact_role_intent_free_text",
    "derive_latest_free_text",
]
