"""Pure requisition-chat attachment policy parity tests."""

import pytest

from app.services.requisition_chat_attachment_policy import (
    has_valid_requisition_image_signature,
    is_supported_requisition_attachment,
)


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("brief.PDF", "application/pdf"),
        ("brief.docx", "application/octet-stream"),
        ("notes.markdown", ""),
        ("captions.srt", "application/x-subrip"),
        ("photo.jpeg", "image/jpeg"),
        ("upload-without-extension", "image/webp"),
    ],
)
def test_attachment_policy_accepts_supported_coherent_inputs(filename, content_type):
    assert is_supported_requisition_attachment(filename, content_type)


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("diagram.svg", "image/svg+xml"),
        ("photo.heic", "image/heic"),
        ("renamed.png", "image/heic"),
        ("renamed.pdf", "image/png"),
        ("payload.bin", "application/octet-stream"),
        ("upload-without-extension", "application/octet-stream"),
    ],
)
def test_attachment_policy_rejects_unsupported_or_mismatched_inputs(
    filename, content_type
):
    assert not is_supported_requisition_attachment(filename, content_type)


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0"),
        ("photo.png", "image/png", b"\x89PNG\r\n\x1a\nrest"),
        ("photo.gif", "image/gif", b"GIF89a"),
        ("photo.webp", "image/webp", b"RIFF\x04\x00\x00\x00WEBP"),
        ("notes.txt", "text/plain", b"not an image"),
    ],
)
def test_image_signature_policy_accepts_valid_formats_and_non_images(
    filename, content_type, content
):
    assert has_valid_requisition_image_signature(filename, content_type, content)


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("forged.jpg", "image/jpeg", b"not jpeg"),
        ("forged.png", "image/png", b"\x89PNG-but-not-the-signature"),
        ("forged.gif", "image/gif", b"GIF90a"),
        ("forged.webp", "image/webp", b"RIFF\x04\x00\x00\x00NOPE"),
    ],
)
def test_image_signature_policy_rejects_declared_images_with_wrong_bytes(
    filename, content_type, content
):
    assert not has_valid_requisition_image_signature(filename, content_type, content)
