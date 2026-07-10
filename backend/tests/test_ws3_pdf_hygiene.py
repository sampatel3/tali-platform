"""WS3: PDF-bytes hygiene combiner + ingest-time stash.

The bytes-level scans (`scan_pdf_metadata` + `scan_pdf_render_state`) already
have unit coverage; here we cover the WS3 combiner that bundles them for the
report and the ingest-time stash that carries the result to score time. Flag-only
throughout — nothing changes a score.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.document_hygiene import (
    PENDING_PDF_HYGIENE_KEY,
    scan_pdf_document_hygiene,
    stash_pdf_hygiene_on_application,
)


class _App:
    """Minimal stand-in for CandidateApplication (only the field the stash touches)."""

    def __init__(self, cv_match_details=None):
        self.cv_match_details = cv_match_details


def test_combiner_returns_none_for_non_pdf():
    # docx / txt / empty carry no PDF metadata or content stream — guarded out.
    assert scan_pdf_document_hygiene(b"anything", "docx") is None
    assert scan_pdf_document_hygiene(b"anything", "txt") is None
    assert scan_pdf_document_hygiene(b"", "pdf") is None


def test_combiner_fails_open_on_junk_pdf_bytes():
    # Unparseable bytes → both sub-scans return checked=False; combiner still
    # returns a well-formed, untriggered dict (never raises, never blocks ingest).
    out = scan_pdf_document_hygiene(b"%PDF-not-really", "pdf")
    assert out is not None
    assert out["triggered"] is False
    assert out["metadata"]["checked"] is False
    assert out["render"]["checked"] is False


def test_combiner_triggers_when_a_subscan_fires():
    with patch(
        "app.services.document_hygiene.scan_pdf_metadata",
        return_value={"checked": True, "keyword_count": 99, "metadata_keyword_stuffing": True},
    ), patch(
        "app.services.document_hygiene.scan_pdf_render_state",
        return_value={"checked": True, "triggered": False, "invisible_render_chars": 0},
    ):
        out = scan_pdf_document_hygiene(b"%PDF-1.4 ...", "pdf")
    assert out["triggered"] is True
    assert out["metadata"]["metadata_keyword_stuffing"] is True


def test_stash_writes_pending_key_on_application():
    app = _App(cv_match_details=None)
    fake = {"triggered": True, "metadata": {"checked": True}, "render": {"checked": True}}
    with patch(
        "app.services.document_hygiene.scan_pdf_document_hygiene", return_value=fake
    ):
        stash_pdf_hygiene_on_application(app, b"%PDF-1.4", "pdf")
    assert isinstance(app.cv_match_details, dict)
    assert app.cv_match_details[PENDING_PDF_HYGIENE_KEY] == fake


def test_stash_is_noop_for_non_pdf():
    app = _App(cv_match_details=None)
    stash_pdf_hygiene_on_application(app, b"docx-bytes", "docx")
    assert app.cv_match_details is None


def test_stash_preserves_existing_cv_match_details():
    app = _App(cv_match_details={"existing": 1})
    fake = {"triggered": False}
    with patch(
        "app.services.document_hygiene.scan_pdf_document_hygiene", return_value=fake
    ):
        stash_pdf_hygiene_on_application(app, b"%PDF-1.4", "pdf")
    assert app.cv_match_details["existing"] == 1
    assert app.cv_match_details[PENDING_PDF_HYGIENE_KEY] == fake


def test_pdf_scan_feeds_triangulation_and_warnings():
    """The promoted document_hygiene.pdf block must surface: invisible
    render-mode text is a deterministic artifact (strong_review), metadata
    stuffing is advisory (warning only)."""
    from app.services.fraud_detection import aggregate_triangulation, build_integrity_warnings

    signals = {
        "document_hygiene": {
            "pdf": {
                "triggered": True,
                "render": {"checked": True, "triggered": True, "invisible_render_chars": 240},
                "metadata": {"checked": True, "metadata_keyword_stuffing": True},
            }
        }
    }
    tri = aggregate_triangulation(signals)
    assert tri["verdict"] == "strong_review"
    warnings = build_integrity_warnings(signals)
    assert any("invisible render mode" in w for w in warnings)
    assert any("metadata is stuffed" in w for w in warnings)


def test_pdf_metadata_stuffing_alone_is_advisory():
    from app.services.fraud_detection import aggregate_triangulation, build_integrity_warnings

    signals = {
        "document_hygiene": {
            "pdf": {
                "triggered": True,
                "render": {"checked": True, "triggered": False},
                "metadata": {"checked": True, "metadata_keyword_stuffing": True},
            }
        }
    }
    tri = aggregate_triangulation(signals)
    assert tri["verdict"] == "ok"
    assert any("metadata is stuffed" in w for w in build_integrity_warnings(signals))
