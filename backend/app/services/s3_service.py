"""S3 file storage service.

Provides durable file storage via AWS S3 instead of ephemeral local
filesystem (critical for Railway deployments).

Falls back to local filesystem when AWS credentials are not configured,
logging a warning. This allows local development without S3.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("taali.s3")


def _get_client():
    """Lazy-create S3 client. Returns None if credentials missing."""
    from ..platform.config import settings

    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        return None, None

    import boto3

    client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )
    return client, settings.AWS_S3_BUCKET


def upload_to_s3(local_path: str, key: str) -> Optional[str]:
    """Upload a local file to S3 and return the S3 URL.

    Args:
        local_path: Path to the local file.
        key: S3 object key (e.g., "uploads/cv/123/resume.pdf").

    Returns:
        S3 URL string, or None if S3 is not configured.
    """
    client, bucket = _get_client()
    if client is None:
        logger.warning("S3 not configured — file remains on local filesystem: %s", local_path)
        return None

    try:
        client.upload_file(local_path, bucket, key)
        url = f"https://{bucket}.s3.amazonaws.com/{key}"
        logger.info("Uploaded to S3: %s -> %s", local_path, url)
        return url
    except Exception as e:
        logger.error("S3 upload failed for %s: %s", local_path, e)
        return None


def download_from_s3(key: str) -> Optional[bytes]:
    """Download a file from S3 and return its bytes.

    Args:
        key: S3 object key.

    Returns:
        File bytes, or None if S3 is not configured or download fails.
    """
    client, bucket = _get_client()
    if client is None:
        logger.warning("S3 not configured — cannot download: %s", key)
        return None

    try:
        response = client.get_object(Bucket=bucket, Key=key)
        data = response["Body"].read()
        logger.info("Downloaded from S3: %s (%d bytes)", key, len(data))
        return data
    except Exception as e:
        logger.error("S3 download failed for %s: %s", key, e)
        return None


def delete_from_s3(key: str) -> bool:
    """Delete a file from S3.

    Args:
        key: S3 object key.

    Returns:
        True if deleted, False otherwise.
    """
    client, bucket = _get_client()
    if client is None:
        return False

    try:
        client.delete_object(Bucket=bucket, Key=key)
        logger.info("Deleted from S3: %s", key)
        return True
    except Exception as e:
        logger.error("S3 delete failed for %s: %s", key, e)
        return False


def generate_s3_key(entity_type: str, entity_id: int, filename: str) -> str:
    """Generate a structured S3 key.

    Args:
        entity_type: Type of entity ("cv", "job_spec", etc.).
        entity_id: ID of the entity.
        filename: Original filename.

    Returns:
        S3 key string.
    """
    safe_filename = filename.replace(" ", "_").replace("/", "_")
    return f"uploads/{entity_type}/{entity_id}/{safe_filename}"
