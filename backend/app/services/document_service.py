"""Document processing service — extracts text from PDF and DOCX files."""

from __future__ import annotations

import io
from datetime import date, datetime
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile

from .pdf_text import extract_text_from_pdf

logger = logging.getLogger("taali.documents")

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
_UNSAFE_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def load_stored_document_bytes(file_url: str | None) -> bytes | None:
    location = str(file_url or "").strip()
    if not location:
        return None

    local_path = Path(location)
    if local_path.exists() and local_path.is_file():
        try:
            return local_path.read_bytes()
        except Exception as exc:
            logger.warning("Failed to read local document bytes from %s: %s", location, exc)
            return None

    from .s3_service import extract_key_from_url, download_from_s3

    bucket_key = extract_key_from_url(location)
    if bucket_key:
        _, key = bucket_key
        try:
            return download_from_s3(key)
        except Exception as exc:
            logger.warning("Failed to download object bytes from %s: %s", location, exc)
            return None

    return None


def stored_document_s3_key(file_url: str | None) -> str | None:
    """Extract the object key from a stored file_url, or None for
    non-object-store URLs (e.g. local filesystem paths).

    Used by download endpoints that redirect to a presigned URL instead
    of streaming bytes back through FastAPI. Recognises both AWS-style
    and Tigris/R2/MinIO endpoint-style URLs via
    ``s3_service.extract_key_from_url``.
    """
    from .s3_service import extract_key_from_url

    bucket_key = extract_key_from_url(file_url or "")
    if bucket_key is None:
        return None
    _, key = bucket_key
    return key


def sanitize_text_for_storage(value: str | None) -> str:
    """Strip NUL and non-printable control characters unsafe for DB text fields."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    return _UNSAFE_CONTROL_CHARS_RE.sub("", text)


def sanitize_json_for_storage(value: Any) -> Any:
    """Recursively sanitize strings inside JSON-like payloads."""
    if isinstance(value, str):
        return sanitize_text_for_storage(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [sanitize_json_for_storage(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json_for_storage(item) for item in value]
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = sanitize_text_for_storage(key) if isinstance(key, str) else key
            out[safe_key] = sanitize_json_for_storage(item)
        return out
    return value


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_docx(content: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    try:
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()
    except Exception as exc:
        logger.warning("DOCX text extraction failed: %s", exc)
        return ""


def extract_text_from_txt(content: bytes) -> str:
    """Extract text from plain text bytes."""
    try:
        return content.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.warning("TXT text extraction failed: %s", exc)
        return ""


def extract_text(content: bytes, extension: str) -> str:
    """Route to the appropriate extractor based on file extension."""
    ext = extension.lower().lstrip(".")
    if ext == "pdf":
        return sanitize_text_for_storage(extract_text_from_pdf(content))
    elif ext == "docx":
        return sanitize_text_for_storage(extract_text_from_docx(content))
    elif ext == "txt":
        return sanitize_text_for_storage(extract_text_from_txt(content))
    return ""


# ---------------------------------------------------------------------------
# Upload processing
# ---------------------------------------------------------------------------

def validate_upload(upload: UploadFile, allowed_extensions: set[str] | None = None) -> tuple[str, str]:
    """Validate an upload file. Returns (filename, extension).

    Raises HTTPException on validation failure.
    """
    exts = allowed_extensions or ALLOWED_EXTENSIONS
    filename = (upload.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in exts:
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(sorted(exts)).upper()} files are allowed",
        )
    return filename, ext


def read_upload_content(upload: UploadFile) -> bytes:
    """Read and validate upload content size. Returns raw bytes."""
    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File must be {MAX_FILE_SIZE // (1024 * 1024)}MB or smaller",
        )
    return content


def save_file_locally(content: bytes, directory: str, prefix: str, ext: str) -> str:
    """Local-disk fallback for development and tests when no S3 creds
    are configured.

    Production deployments must always have ``AWS_*`` env vars set
    (which keeps this path inactive). Files written here live on
    ephemeral disk and will be wiped on container restart — fine for
    local dev / unit tests, dangerous for prod.
    """
    uploads_dir = Path(__file__).resolve().parents[2] / "uploads" / directory
    uploads_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{prefix}-{int(time.time())}.{ext}"
    target_path = uploads_dir / stored_name
    target_path.write_bytes(content)
    return str(target_path)


def _object_storage_is_configured() -> bool:
    """True when the operator has wired up S3 credentials.

    A configured-but-unhealthy store is still considered "configured"
    — production should fail loudly rather than silently fall back to
    ephemeral disk. Tests / local dev typically leave creds blank,
    which keeps the local fallback path active.
    """
    from ..platform.config import settings

    return bool((settings.AWS_ACCESS_KEY_ID or "").strip() and (settings.AWS_SECRET_ACCESS_KEY or "").strip())


def process_document_upload(
    upload: UploadFile,
    entity_id: int,
    doc_type: str,
    allowed_extensions: set[str] | None = None,
) -> Dict[str, Any]:
    """Process a document upload: validate, upload to object storage,
    extract text.

    Bytes go directly to the configured S3-compatible store (Tigris in
    production) — no local-disk hop, no temp files. When storage
    credentials aren't configured (local dev / tests), falls back to
    writing to ``backend/uploads/`` so the test suite doesn't need a
    mocked S3 backend. When credentials are configured but the upload
    fails, raises HTTP 503 so prod misconfigurations surface loudly
    instead of silently corrupting state.

    Args:
        upload: The uploaded file.
        entity_id: ID of the entity (candidate, assessment, etc.).
        doc_type: Type of document ("cv" or "job_spec").
        allowed_extensions: Override allowed extensions.

    Returns:
        Dict with file_url, filename, extracted_text.
    """
    import mimetypes as _mt
    from .s3_service import generate_s3_key, upload_bytes_to_s3

    filename, ext = validate_upload(upload, allowed_extensions)
    content = read_upload_content(upload)

    s3_key = generate_s3_key(doc_type, entity_id, filename)
    content_type = _mt.guess_type(filename)[0] or "application/octet-stream"
    file_url = upload_bytes_to_s3(content, s3_key, content_type=content_type)
    if not file_url:
        if _object_storage_is_configured():
            raise HTTPException(
                status_code=503,
                detail={
                    "reason": "storage_unavailable",
                    "message": (
                        "Object storage is configured but not reachable; cannot save uploads. "
                        "Check AWS_* env vars and the storage health probe at /health."
                    ),
                },
            )
        # Dev/test fallback only — prod should never hit this branch.
        file_url = save_file_locally(
            content=content,
            directory=doc_type,
            prefix=f"{doc_type}-{entity_id}",
            ext=ext,
        )

    extracted_text = sanitize_text_for_storage(extract_text(content, ext))
    text_preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text

    logger.info(
        "Document uploaded: type=%s entity=%s file=%s chars_extracted=%d url=%s",
        doc_type, entity_id, filename, len(extracted_text), file_url,
    )

    return {
        "file_url": file_url,
        "filename": filename,
        "extracted_text": extracted_text,
        "text_preview": text_preview,
    }
