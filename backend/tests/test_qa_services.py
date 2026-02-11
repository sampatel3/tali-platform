"""
QA Test Suite: Service Layer — document_service, s3_service, email templates, fit_matching
Covers: text extraction, file validation, S3 fallback, email HTML, scoring analytics.
~30 tests
"""
import io
import pytest
from unittest.mock import patch, MagicMock


# ===========================================================================
# A. DOCUMENT SERVICE
# ===========================================================================
class TestDocumentService:
    def test_extract_text_from_txt(self):
        from app.services.document_service import extract_text
        text = extract_text(b"Hello world, this is a test.", ".txt")
        assert "Hello world" in text

    def test_extract_text_empty_content(self):
        from app.services.document_service import extract_text
        text = extract_text(b"", ".txt")
        assert text == ""

    def test_extract_text_unknown_extension(self):
        from app.services.document_service import extract_text
        text = extract_text(b"binary data", ".xyz")
        assert text == ""

    def test_extract_text_pdf_invalid(self):
        from app.services.document_service import extract_text
        text = extract_text(b"not a real pdf", ".pdf")
        assert isinstance(text, str)

    def test_extract_text_docx_invalid(self):
        from app.services.document_service import extract_text
        text = extract_text(b"not a real docx", ".docx")
        assert isinstance(text, str)

    def test_validate_upload_valid_txt(self):
        from app.services.document_service import validate_upload
        mock_upload = MagicMock()
        mock_upload.filename = "cv.txt"
        mock_upload.content_type = "text/plain"
        filename, ext = validate_upload(mock_upload, {"pdf", "docx", "txt"})
        assert ext in [".txt", "txt"]
        assert filename == "cv.txt"

    def test_validate_upload_valid_pdf(self):
        from app.services.document_service import validate_upload
        mock_upload = MagicMock()
        mock_upload.filename = "cv.pdf"
        mock_upload.content_type = "application/pdf"
        filename, ext = validate_upload(mock_upload, {"pdf", "docx", "txt"})
        assert "pdf" in ext

    def test_validate_upload_invalid_extension(self):
        from app.services.document_service import validate_upload
        from fastapi import HTTPException
        mock_upload = MagicMock()
        mock_upload.filename = "hack.exe"
        mock_upload.content_type = "application/octet-stream"
        with pytest.raises(HTTPException) as exc_info:
            validate_upload(mock_upload, {"pdf", "docx", "txt"})
        assert exc_info.value.status_code == 400

    def test_validate_upload_no_filename(self):
        from app.services.document_service import validate_upload
        from fastapi import HTTPException
        mock_upload = MagicMock()
        mock_upload.filename = None
        with pytest.raises(HTTPException):
            validate_upload(mock_upload, {"pdf"})

    def test_save_file_locally(self, tmp_path):
        from app.services.document_service import save_file_locally
        content = b"test content"
        path = save_file_locally(content, str(tmp_path), "test", ".txt")
        assert path.endswith(".txt")
        with open(path, "rb") as f:
            assert f.read() == content


# ===========================================================================
# B. S3 SERVICE (with mock)
# ===========================================================================
class TestS3Service:
    def test_upload_returns_none_without_config(self):
        from app.services.s3_service import upload_to_s3
        result = upload_to_s3("/tmp/nonexistent.txt", "test/key.txt")
        assert result is None

    def test_generate_s3_key(self):
        from app.services.s3_service import generate_s3_key
        key = generate_s3_key("cv", 42, "resume.pdf")
        assert "cv" in key
        assert "42" in key
        assert key.endswith(".pdf")

    def test_generate_s3_key_different_types(self):
        from app.services.s3_service import generate_s3_key
        key1 = generate_s3_key("cv", 1, "a.pdf")
        key2 = generate_s3_key("job_spec", 1, "a.pdf")
        assert key1 != key2

    def test_download_returns_none_without_config(self):
        from app.services.s3_service import download_from_s3
        result = download_from_s3("nonexistent/key.txt")
        assert result is None

    def test_delete_returns_false_without_config(self):
        from app.services.s3_service import delete_from_s3
        result = delete_from_s3("nonexistent/key.txt")
        assert result is False


# ===========================================================================
# C. EMAIL TEMPLATES
# ===========================================================================
class TestEmailTemplates:
    def test_assessment_invite_html(self):
        from app.components.notifications.templates import assessment_invite_html
        html = assessment_invite_html("Jane Doe", "Acme Corp", "Engineer", "https://example.com/start")
        assert "Jane" in html
        assert "Acme" in html
        assert "https://example.com/start" in html
        # Should be valid HTML
        assert "<" in html and ">" in html

    def test_results_notification_html(self):
        from app.components.notifications.templates import results_notification_html
        html = results_notification_html("Jane Doe", 85.5, "https://example.com/results")
        assert "Jane" in html
        assert "https://example.com/results" in html
        # Score is rendered as percentage (86% for 85.5)
        assert "%" in html

    def test_email_verification_html(self):
        from app.components.notifications.templates import email_verification_html
        html = email_verification_html("John", "https://example.com/verify?token=abc")
        assert "John" in html
        assert "https://example.com/verify?token=abc" in html

    def test_password_reset_html(self):
        from app.components.notifications.templates import password_reset_html
        html = password_reset_html("https://example.com/reset?token=xyz")
        assert "https://example.com/reset?token=xyz" in html


# ===========================================================================
# D. PROMPT ANALYTICS
# ===========================================================================
class TestPromptAnalytics:
    def _make_prompts(self, n=5):
        return [{"role": "user", "content": f"Prompt {i} with some text", "timestamp": 1000 + i * 60} for i in range(n)]

    def test_compute_all_heuristics_no_crash(self):
        from app.components.scoring.analytics import compute_all_heuristics
        mock_assessment = MagicMock()
        mock_assessment.started_at = None
        mock_assessment.ai_prompts = None
        mock_assessment.tab_switch_count = 0
        mock_assessment.tests_passed = 0
        mock_assessment.tests_total = 0
        result = compute_all_heuristics(mock_assessment, [])
        assert isinstance(result, dict)

    def test_prompt_length_stats(self):
        from app.components.scoring.analytics import compute_prompt_length_stats
        prompts = self._make_prompts(5)
        result = compute_prompt_length_stats(prompts)
        assert "avg_words" in result
        assert "signal" in result

    def test_self_correction_rate(self):
        from app.components.scoring.analytics import compute_self_correction_rate
        prompts = [
            {"role": "user", "content": "How do I fix this error in my code?"},
            {"role": "user", "content": "Wait, I meant how do I fix the TypeError in my function?"},
        ]
        result = compute_self_correction_rate(prompts)
        assert "rate" in result
        assert "signal" in result

    def test_tab_switch_count(self):
        from app.components.scoring.analytics import compute_tab_switch_count
        mock = MagicMock()
        mock.tab_switch_count = 5
        result = compute_tab_switch_count(mock)
        assert "count" in result
        assert result["count"] == 5

    def test_prompt_frequency(self):
        from app.components.scoring.analytics import compute_prompt_frequency
        prompts = self._make_prompts(5)
        result = compute_prompt_frequency(prompts, 1800)
        assert "signal" in result


# ===========================================================================
# E. FIT MATCHING SERVICE
# ===========================================================================
class TestFitMatchingService:
    def test_clamp_score_normal(self):
        from app.services.fit_matching_service import _clamp_score
        # Clamps to 0-10 scale
        result = _clamp_score(5)
        assert isinstance(result, (float, int))
        assert 0 <= result <= 10

    def test_clamp_score_zero(self):
        from app.services.fit_matching_service import _clamp_score
        assert _clamp_score(0) == 0.0

    def test_clamp_score_none(self):
        from app.services.fit_matching_service import _clamp_score
        assert _clamp_score(None) is None

    def test_clamp_score_string(self):
        from app.services.fit_matching_service import _clamp_score
        result = _clamp_score("invalid")
        assert result is None or isinstance(result, (float, int))


# ===========================================================================
# F. SECURITY UTILS
# ===========================================================================
class TestSecurityUtils:
    def test_password_hash_and_verify(self):
        from app.core.security import get_password_hash, verify_password
        hashed = get_password_hash("MyPassword123!")
        assert hashed != "MyPassword123!"
        assert verify_password("MyPassword123!", hashed) is True
        assert verify_password("WrongPassword!", hashed) is False

    def test_create_and_decode_token(self):
        from app.core.security import create_access_token, decode_token
        from datetime import timedelta
        token = create_access_token(
            data={"sub": "test@example.com", "user_id": 1, "org_id": 1},
            expires_delta=timedelta(minutes=30),
        )
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "test@example.com"

    def test_decode_invalid_token(self):
        from app.core.security import decode_token
        result = decode_token("invalid.token.here")
        assert result is None
