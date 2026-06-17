"""Unit tests for the column-aware PDF extraction helpers.

These exercise the geometry (``_detect_column_split`` / ``_fragments_to_lines``)
with synthetic fragments — no real PDF needed. Fragments are
``(x, y, text, font_size)`` as produced by the PyPDF2 visitor.
"""

from __future__ import annotations

from app.services.document_service import _detect_column_split, _fragments_to_lines


PAGE_W = 540.0


def _two_column_frags():
    """Left sidebar at x=12, right main column at x=300; both span the page."""
    frags = []
    y = 720.0
    while y > 120.0:
        frags.append((12.0, y, "Skill", 10.0))          # narrow left column
        frags.append((300.0, y, "A longer experience line", 10.0))  # right column
        y -= 28.0
    return frags


def _single_column_frags():
    frags = []
    y = 720.0
    while y > 120.0:
        frags.append((20.0, y, "A normal full-width paragraph line of text", 10.0))
        y -= 28.0
    return frags


def test_detects_two_column_gutter():
    split = _detect_column_split(_two_column_frags(), PAGE_W)
    assert split is not None
    # Gutter sits between the left column (~x=12..~40) and the right (~x=300).
    assert 40.0 < split < 300.0


def test_single_column_has_no_split():
    assert _detect_column_split(_single_column_frags(), PAGE_W) is None


def test_too_few_fragments_no_split():
    assert _detect_column_split(_two_column_frags()[:6], PAGE_W) is None


def test_fragments_to_lines_orders_top_down_left_right():
    # Two fragments on one visual line (same y), out of x order, plus a lower line.
    frags = [
        (200.0, 500.0, "world", 10.0),
        (10.0, 500.0, "hello", 10.0),
        (10.0, 480.0, "next", 10.0),
    ]
    lines = _fragments_to_lines(frags)
    assert lines[0] == "hello world"   # same y → joined left-to-right
    assert lines[1] == "next"          # lower y → next line
