"""Unit tests for service modules — document_service, s3_service, and security."""

import io
import os
import zipfile

import pytest
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException


# ===================================================================
# document_service tests
# ===================================================================

from app.services.document_service import (
    MAX_FILE_SIZE,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_txt,
    extract_text,
    sanitize_json_for_storage,
    sanitize_text_for_storage,
    process_document_upload,
    read_upload_content,
    validate_upload,
    save_file_locally,
)


class _FakeUploadFile:
    """Minimal stand-in for FastAPI's UploadFile."""

    def __init__(self, filename: str):
        self.filename = filename


class _RecordingFile:
    def __init__(self, content: bytes):
        self.content = content
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.content[:size]


class _FakeReadableUpload(_FakeUploadFile):
    def __init__(self, filename: str, content: bytes):
        super().__init__(filename)
        self.file = _RecordingFile(content)


class TestExtractTextFromTxt:

    def test_basic_utf8(self):
        content = b"Hello, world!"
        assert extract_text_from_txt(content) == "Hello, world!"

    def test_strips_whitespace(self):
        content = b"  some text  \n\n"
        assert extract_text_from_txt(content) == "some text"

    def test_empty_bytes(self):
        assert extract_text_from_txt(b"") == ""

    def test_non_utf8_fallback(self):
        # Invalid UTF-8 bytes should still return something (replacement chars)
        content = b"\x80\x81\x82"
        result = extract_text_from_txt(content)
        assert isinstance(result, str)


class TestSanitizeForStorage:

    def test_sanitize_text_removes_nul_and_unsafe_controls(self):
        raw = "hello\x00world\x07!\nline"
        assert sanitize_text_for_storage(raw) == "helloworld!\nline"

    def test_sanitize_json_recursively_strips_controls(self):
        payload = {"a": "x\x00y", "nested": [{"k\x00": "v\x07"}]}
        cleaned = sanitize_json_for_storage(payload)
        assert cleaned == {"a": "xy", "nested": [{"k": "v"}]}


class TestExtractTextFromPdf:

    def test_corrupt_bytes_returns_empty(self):
        result = extract_text_from_pdf(b"this is not a pdf")
        assert result == ""

    def test_empty_bytes_returns_empty(self):
        result = extract_text_from_pdf(b"")
        assert result == ""


class TestExtractTextFromDocx:

    def test_corrupt_bytes_returns_empty(self):
        result = extract_text_from_docx(b"this is not a docx")
        assert result == ""

    def test_empty_bytes_returns_empty(self):
        result = extract_text_from_docx(b"")
        assert result == ""

    def test_rejects_compressed_docx_with_oversized_main_xml(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(
            stream, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("word/document.xml", b"x" * (9 * 1024 * 1024))

        assert len(stream.getvalue()) < 20_000
        assert extract_text_from_docx(stream.getvalue()) == ""


class TestExtractText:

    def test_routes_to_txt(self):
        assert extract_text(b"hello txt", "txt") == "hello txt"

    def test_routes_to_txt_with_dot(self):
        assert extract_text(b"hello txt", ".txt") == "hello txt"

    def test_routes_to_txt_uppercase(self):
        assert extract_text(b"hello txt", "TXT") == "hello txt"

    def test_routes_to_pdf_with_bad_bytes(self):
        # Should gracefully return empty for corrupt pdf content
        assert extract_text(b"bad", "pdf") == ""

    def test_routes_to_docx_with_bad_bytes(self):
        assert extract_text(b"bad", "docx") == ""

    def test_unknown_extension_returns_empty(self):
        assert extract_text(b"content", "xyz") == ""


class TestValidateUpload:

    def test_valid_pdf_upload(self):
        upload = _FakeUploadFile("resume.pdf")
        filename, ext = validate_upload(upload)
        assert filename == "resume.pdf"
        assert ext == "pdf"

    def test_valid_docx_upload(self):
        upload = _FakeUploadFile("report.docx")
        filename, ext = validate_upload(upload)
        assert filename == "report.docx"
        assert ext == "docx"

    def test_valid_txt_upload(self):
        upload = _FakeUploadFile("notes.txt")
        filename, ext = validate_upload(upload)
        assert filename == "notes.txt"
        assert ext == "txt"

    def test_disallowed_extension_raises(self):
        upload = _FakeUploadFile("malware.exe")
        with pytest.raises(HTTPException) as exc_info:
            validate_upload(upload)
        assert exc_info.value.status_code == 400

    def test_empty_filename_raises(self):
        upload = _FakeUploadFile("")
        with pytest.raises(HTTPException) as exc_info:
            validate_upload(upload)
        assert exc_info.value.status_code == 400
        assert "Filename is required" in exc_info.value.detail

    def test_no_extension_raises(self):
        upload = _FakeUploadFile("noextension")
        with pytest.raises(HTTPException) as exc_info:
            validate_upload(upload)
        assert exc_info.value.status_code == 400

    def test_custom_allowed_extensions(self):
        upload = _FakeUploadFile("image.png")
        filename, ext = validate_upload(upload, allowed_extensions={"png", "jpg"})
        assert ext == "png"

    def test_custom_allowed_rejects_others(self):
        upload = _FakeUploadFile("doc.pdf")
        with pytest.raises(HTTPException):
            validate_upload(upload, allowed_extensions={"png", "jpg"})


class TestReadUploadContent:

    def test_oversized_read_is_bounded_before_downstream_processing(self, monkeypatch):
        upload = _FakeReadableUpload("oversized.txt", b"x" * (MAX_FILE_SIZE + 1))

        def _boom(*args, **kwargs):  # pragma: no cover - size guard must win
            raise AssertionError("oversized content must not reach storage or extraction")

        monkeypatch.setattr("app.services.s3_service.upload_bytes_to_s3", _boom)
        monkeypatch.setattr("app.services.document_service.extract_text", _boom)

        with pytest.raises(HTTPException) as exc_info:
            process_document_upload(upload, entity_id=1, doc_type="job_spec")

        assert exc_info.value.status_code == 400
        assert upload.file.read_sizes == [MAX_FILE_SIZE + 1]

    def test_small_read_uses_same_explicit_bound(self):
        upload = _FakeReadableUpload("notes.txt", b"small")

        assert read_upload_content(upload) == b"small"
        assert upload.file.read_sizes == [MAX_FILE_SIZE + 1]


class TestSaveFileLocally:

    def test_saves_file_and_returns_path(self, tmp_path):
        content = b"file content here"
        # Patch the uploads_dir calculation to use tmp_path
        with patch("app.services.document_service.Path") as mock_path_cls:
            uploads_dir = tmp_path / "uploads" / "cv"
            uploads_dir.mkdir(parents=True, exist_ok=True)

            # Make the patched Path chain return our tmp-based dir
            mock_resolve = mock_path_cls.return_value.resolve.return_value
            mock_resolve.parents.__getitem__ = lambda self, idx: tmp_path
            mock_path_cls.return_value.resolve.return_value.parents = {2: tmp_path}

            # Call the real function but redirect output via a simpler approach:
            # Just call directly — the function will create under its own uploads dir
        # Simpler approach: call the function and verify the file exists at the returned path
        result_path = save_file_locally(content, "cv", "test-doc", "pdf")
        assert result_path.endswith(".pdf")
        assert os.path.exists(result_path)
        with open(result_path, "rb") as f:
            assert f.read() == content
        # Clean up
        os.remove(result_path)


# ===================================================================
# s3_service tests
# ===================================================================

from app.services.s3_service import (
    generate_s3_key,
    upload_to_s3,
    download_from_s3,
    delete_from_s3,
)


class TestGenerateS3Key:

    def test_basic_key(self):
        key = generate_s3_key("cv", 42, "resume.pdf")
        assert key == "uploads/cv/42/resume.pdf"

    def test_spaces_replaced(self):
        key = generate_s3_key("job_spec", 7, "my resume file.pdf")
        assert key == "uploads/job_spec/7/my_resume_file.pdf"

    def test_slashes_replaced(self):
        key = generate_s3_key("cv", 1, "path/to/file.pdf")
        assert key == "uploads/cv/1/path_to_file.pdf"

    def test_combined_unsafe_chars(self):
        key = generate_s3_key("cv", 99, "my file / name.docx")
        assert " " not in key
        assert key.count("/") == 3  # uploads / cv / 99 / filename


class TestUploadToS3NoConfig:

    def test_returns_none_when_aws_not_configured(self):
        # Without AWS credentials set, _get_client returns None
        result = upload_to_s3("/tmp/fake.pdf", "uploads/cv/1/fake.pdf")
        assert result is None


class TestDownloadFromS3NoConfig:

    def test_returns_none_when_aws_not_configured(self):
        result = download_from_s3("uploads/cv/1/fake.pdf")
        assert result is None


class TestDeleteFromS3NoConfig:

    def test_returns_false_when_aws_not_configured(self):
        result = delete_from_s3("uploads/cv/1/fake.pdf")
        assert result is False


# ===================================================================
# security tests
# ===================================================================

from app.platform.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
)


class TestPasswordHashing:

    def test_hash_and_verify_roundtrip(self):
        password = "SuperSecret123!"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_wrong_password_fails(self):
        hashed = get_password_hash("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_unicode_password_at_bcrypt_byte_boundary(self):
        password = "a" * 71 + "€"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_different_passwords_different_hashes(self):
        h1 = get_password_hash("password-one")
        h2 = get_password_hash("password-two")
        assert h1 != h2

    def test_same_password_different_hashes(self):
        # bcrypt includes a random salt, so hashing the same password twice
        # should produce different hash strings
        h1 = get_password_hash("identical")
        h2 = get_password_hash("identical")
        assert h1 != h2

    def test_hash_is_string(self):
        hashed = get_password_hash("anything")
        assert isinstance(hashed, str)
        assert len(hashed) > 0


class TestAccessToken:

    def test_create_and_decode_token(self):
        data = {"user_id": 42, "sub": "alice@example.com"}
        token = create_access_token(data)
        payload = decode_token(token)
        assert payload is not None
        assert payload["user_id"] == 42
        assert payload["sub"] == "alice@example.com"
        assert "exp" in payload

    def test_token_with_custom_expiry(self):
        data = {"user_id": 1}
        token = create_access_token(data, expires_delta=timedelta(hours=2))
        payload = decode_token(token)
        assert payload is not None
        assert payload["user_id"] == 1

    def test_expired_token_returns_none(self):
        data = {"user_id": 99}
        # Create a token that expired 10 seconds ago
        token = create_access_token(data, expires_delta=timedelta(seconds=-10))
        payload = decode_token(token)
        assert payload is None

    def test_invalid_token_returns_none(self):
        payload = decode_token("this.is.not.a.valid.jwt")
        assert payload is None

    def test_tampered_token_returns_none(self):
        import jwt as pyjwt
        data = {"user_id": 5, "exp": 9999999999}
        # Create a token with a DIFFERENT secret — should fail decode
        tampered_token = pyjwt.encode(
            data,
            "wrong-secret-key-that-is-at-least-32-bytes",
            algorithm="HS256",
        )
        payload = decode_token(tampered_token)
        assert payload is None

    def test_empty_string_token_returns_none(self):
        payload = decode_token("")
        assert payload is None
