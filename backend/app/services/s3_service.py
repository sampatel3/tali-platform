"""S3 file storage service.

Provides durable file storage via AWS S3 instead of ephemeral local
filesystem (critical for Railway deployments).

Falls back to local filesystem when AWS credentials are missing OR when
the configured creds prove invalid (e.g. rotated, expired). Health is
probed once per process and cached: every subsequent upload skips
silently if S3 is unavailable, instead of logging InvalidAccessKeyId on
every CV fetch (which previously buried real errors during bulk
scoring).

Use ``s3_status()`` from /health or admin tooling to surface whether
durable storage is wired up. ``S3_DISABLED`` env var or empty
``AWS_ACCESS_KEY_ID`` short-circuits the probe entirely.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("taali.s3")


# Cached health verdict for the lifetime of the process.
# None  → not yet probed
# True  → S3 reachable + bucket accessible
# False → creds missing OR probe failed (e.g. InvalidAccessKeyId)
_health_cache: Optional[bool] = None
_health_reason: str = ""
_probe_lock = threading.Lock()


def _probe_health() -> tuple[bool, str]:
    """One-time check: can we actually use S3? Cached afterwards.

    Tries a cheap HeadBucket call. Returns (ok, reason).
    """
    from ..platform.config import settings

    if getattr(settings, "S3_DISABLED", False):
        return False, "S3_DISABLED env var is set"
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        return False, "AWS credentials not configured"
    if not settings.AWS_S3_BUCKET:
        return False, "AWS_S3_BUCKET not configured"

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION,
        )
        try:
            client.head_bucket(Bucket=settings.AWS_S3_BUCKET)
            return True, "ok"
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code", "")
            return False, f"head_bucket failed: {code or str(exc)}"
        except BotoCoreError as exc:
            return False, f"boto error: {exc}"
    except Exception as exc:  # pragma: no cover — defensive
        return False, f"probe failed: {exc}"


def _ensure_probed() -> bool:
    """Lazy probe; idempotent. Returns the cached verdict."""
    global _health_cache, _health_reason
    if _health_cache is not None:
        return _health_cache
    with _probe_lock:
        if _health_cache is not None:
            return _health_cache
        ok, reason = _probe_health()
        _health_cache = ok
        _health_reason = reason
        if ok:
            logger.info("S3 storage is healthy (bucket reachable)")
        else:
            logger.warning(
                "S3 storage unavailable — files persist locally only (ephemeral on Railway). Reason: %s",
                reason,
            )
    return _health_cache


def s3_status() -> dict:
    """Public health probe for /health and admin tooling.

    Returns ``{"available": bool, "reason": str}``. Triggers a probe if
    none has run yet.
    """
    ok = _ensure_probed()
    return {"available": bool(ok), "reason": _health_reason or ("ok" if ok else "unknown")}


def reset_s3_health_cache() -> None:
    """Force a fresh probe on next use. Used by tests + admin recovery
    after credential rotation."""
    global _health_cache, _health_reason
    with _probe_lock:
        _health_cache = None
        _health_reason = ""


def _get_client():
    """Build an S3 client + bucket name. Returns (None, None) when
    credentials are missing or the cached probe says unavailable.
    """
    from ..platform.config import settings

    if not _ensure_probed():
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

    Returns None when S3 is unavailable. Failures are logged at debug
    level after the first one (the warning at startup is enough — every
    subsequent CV upload would otherwise spam ERROR logs that drown out
    real issues).
    """
    client, bucket = _get_client()
    if client is None:
        return None

    try:
        client.upload_file(local_path, bucket, key)
        url = f"https://{bucket}.s3.amazonaws.com/{key}"
        return url
    except Exception as exc:
        # Mid-stream credential failure (e.g. key rotated while running) — flip
        # the cache off so subsequent uploads short-circuit silently. Log once.
        if "InvalidAccessKeyId" in str(exc) or "ExpiredToken" in str(exc):
            global _health_cache, _health_reason
            with _probe_lock:
                if _health_cache is not False:
                    logger.warning(
                        "S3 credentials rejected mid-stream — disabling S3 uploads for this process. Error: %s",
                        exc,
                    )
                _health_cache = False
                _health_reason = f"runtime_failure: {exc}"
        else:
            logger.debug("S3 upload failed for %s: %s", local_path, exc)
        return None


def download_from_s3(key: str) -> Optional[bytes]:
    """Download a file from S3. Returns None when S3 is unavailable."""
    client, bucket = _get_client()
    if client is None:
        return None

    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except Exception as exc:
        logger.debug("S3 download failed for %s: %s", key, exc)
        return None


def delete_from_s3(key: str) -> bool:
    """Delete a file from S3. Returns False when S3 is unavailable."""
    client, bucket = _get_client()
    if client is None:
        return False

    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        logger.debug("S3 delete failed for %s: %s", key, exc)
        return False


def generate_s3_key(entity_type: str, entity_id: int, filename: str) -> str:
    """Generate a structured S3 key."""
    safe_filename = filename.replace(" ", "_").replace("/", "_")
    return f"uploads/{entity_type}/{entity_id}/{safe_filename}"


def generate_presigned_get_url(
    key: str,
    *,
    expires_in: int = 3600,
    content_disposition: str | None = None,
) -> Optional[str]:
    """Generate a short-lived presigned GET URL so browsers can download
    directly from S3 without the file flowing through Railway.

    Returns ``None`` when S3 is unavailable. ``content_disposition`` lets
    callers force ``inline; filename="…"`` or ``attachment; filename="…"``
    on the response (S3 honours ``ResponseContentDisposition``).
    """
    client, bucket = _get_client()
    if client is None:
        return None
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if content_disposition:
        params["ResponseContentDisposition"] = content_disposition
    try:
        return client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=int(expires_in),
        )
    except Exception as exc:
        logger.debug("Failed to generate presigned URL for %s: %s", key, exc)
        return None
