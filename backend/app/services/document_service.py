"""Document processing service — extracts text from PDF and DOCX files."""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException, UploadFile

logger = logging.getLogger("tali.documents")

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes using PyPDF2."""
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages).strip()
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


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
        return extract_text_from_pdf(content)
    elif ext == "docx":
        return extract_text_from_docx(content)
    elif ext == "txt":
        return extract_text_from_txt(content)
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
    """Save file to local filesystem. Returns the file path.

    NOTE: For production on Railway (ephemeral disk), files should be
    uploaded to S3 instead. See Phase 4 of PRODUCT_PLAN.md.
    """
    uploads_dir = Path(__file__).resolve().parents[2] / "uploads" / directory
    uploads_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{prefix}-{int(time.time())}.{ext}"
    target_path = uploads_dir / stored_name
    target_path.write_bytes(content)
    return str(target_path)


def process_document_upload(
    upload: UploadFile,
    entity_id: int,
    doc_type: str,
    allowed_extensions: set[str] | None = None,
) -> Dict[str, Any]:
    """Process a document upload: validate, save, extract text.

    Saves to S3 when AWS credentials are configured; falls back to local
    filesystem otherwise (with a warning in logs).

    Args:
        upload: The uploaded file.
        entity_id: ID of the entity (candidate, assessment, etc.).
        doc_type: Type of document ("cv" or "job_spec").
        allowed_extensions: Override allowed extensions.

    Returns:
        Dict with file_url, filename, extracted_text.
    """
    filename, ext = validate_upload(upload, allowed_extensions)
    content = read_upload_content(upload)

    # Always save locally first (needed for text extraction and as fallback)
    local_path = save_file_locally(
        content=content,
        directory=doc_type,
        prefix=f"{doc_type}-{entity_id}",
        ext=ext,
    )

    # Try S3 upload — returns S3 URL if configured, None otherwise
    file_url = local_path
    try:
        from .s3_service import upload_to_s3, generate_s3_key

        s3_key = generate_s3_key(doc_type, entity_id, filename)
        s3_url = upload_to_s3(local_path, s3_key)
        if s3_url:
            file_url = s3_url
    except Exception as exc:
        logger.warning("S3 upload skipped (falling back to local): %s", exc)

    extracted_text = extract_text(content, ext)
    text_preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text

    logger.info(
        "Document uploaded: type=%s entity=%s file=%s chars_extracted=%d storage=%s",
        doc_type, entity_id, filename, len(extracted_text),
        "s3" if file_url.startswith("https://") else "local",
    )

    return {
        "file_url": file_url,
        "filename": filename,
        "extracted_text": extracted_text,
        "text_preview": text_preview,
    }
