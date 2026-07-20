"""Exact bounds for the cumulative RoleIntent free-text projection."""

from __future__ import annotations

import pytest

from app.services.role_intent_text import (
    ROLE_INTENT_FREE_TEXT_MAX_CHARS,
    compact_role_intent_free_text,
    derive_latest_free_text,
)


_EARLIER_MARKER = "[... earlier role-intent notes omitted ...]\n"
_MIDDLE_MARKER = "\n[... middle of latest role-intent answer omitted ...]\n"


@pytest.mark.parametrize(
    ("free_text", "expected"),
    (
        (None, ""),
        ("", ""),
        ("short recruiter answer", "short recruiter answer"),
        ("first line\r\n\r\nsecond line", "first line\r\n\r\nsecond line"),
        (
            "x" * ROLE_INTENT_FREE_TEXT_MAX_CHARS,
            "x" * ROLE_INTENT_FREE_TEXT_MAX_CHARS,
        ),
    ),
)
def test_compactor_preserves_text_at_or_below_cap(free_text, expected):
    assert compact_role_intent_free_text(free_text) == expected


@pytest.mark.parametrize(
    ("free_text", "previous_free_text", "expected"),
    (
        (None, None, None),
        ("first answer", None, "first answer"),
        ("first answer", "", "first answer"),
        (
            "prior answer\n\nLATEST START\n\nlatest second paragraph",
            "  prior answer  ",
            "LATEST START\n\nlatest second paragraph",
        ),
        ("manually rewritten value", "old value", None),
    ),
)
def test_latest_answer_derivation_requires_exact_prior_prefix(
    free_text,
    previous_free_text,
    expected,
):
    assert derive_latest_free_text(
        free_text,
        previous_free_text=previous_free_text,
    ) == expected


def test_compactor_preserves_paragraphs_in_proven_latest_answer():
    prior = "OLDEST ANSWER\n" + ("prior context " * 180)
    latest = "LATEST MUST-HAVE\n\nKeep this entire second paragraph."
    free_text = f"{prior.strip()}\n\n{latest}"
    compacted = compact_role_intent_free_text(
        free_text,
        latest_free_text=latest,
    )

    assert len(compacted) == ROLE_INTENT_FREE_TEXT_MAX_CHARS
    assert compacted.startswith(_EARLIER_MARKER)
    assert compacted.endswith(latest)
    assert latest in compacted
    assert "OLDEST ANSWER" not in compacted
    assert compact_role_intent_free_text(compacted) == compacted


@pytest.mark.parametrize(
    "free_text",
    (
        "x" * (ROLE_INTENT_FREE_TEXT_MAX_CHARS + 1),
        "old\r\n\r\n" + ("middle\n" * 300) + "LATEST\r\nANSWER",
        "旧" * 2_000 + "🧭最新答案：候选人必须重叠迪拜上午。",
    ),
)
def test_compactor_treats_unbounded_fallback_as_one_standalone_answer(free_text):
    compacted = compact_role_intent_free_text(free_text)
    available = ROLE_INTENT_FREE_TEXT_MAX_CHARS - len(_MIDDLE_MARKER)
    beginning_chars = available // 2
    ending_chars = available - beginning_chars

    assert len(compacted) == ROLE_INTENT_FREE_TEXT_MAX_CHARS
    assert compacted == (
        f"{free_text[:beginning_chars]}{_MIDDLE_MARKER}"
        f"{free_text[-ending_chars:]}"
    )


def test_compactor_keeps_both_ends_when_latest_answer_alone_exceeds_cap():
    prior = "OLD ANSWER " + ("old " * 400)
    latest = (
        "LATEST MUST-HAVE BEGINNING: retain regulatory ownership. "
        + ("新🧭" * 1_000)
        + " :LATEST FINAL SENTINEL"
    )
    free_text = f"{prior.strip()}\n\n{latest}"
    compacted = compact_role_intent_free_text(
        free_text,
        latest_free_text=latest,
    )

    assert "OLD ANSWER" not in compacted
    assert "LATEST MUST-HAVE BEGINNING" in compacted
    assert _MIDDLE_MARKER in compacted
    assert compacted.endswith(":LATEST FINAL SENTINEL")
    assert len(compacted) == ROLE_INTENT_FREE_TEXT_MAX_CHARS


def test_compactor_prefers_complete_latest_when_marker_cannot_fit():
    latest = "L" * (ROLE_INTENT_FREE_TEXT_MAX_CHARS - 10)
    free_text = f"old context\n\n{latest}"

    assert compact_role_intent_free_text(
        free_text,
        latest_free_text=latest,
    ) == latest
