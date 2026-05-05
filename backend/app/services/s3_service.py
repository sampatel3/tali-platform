"""S3-compatible object storage service.

Talks to whichever S3-compatible store ``AWS_S3_ENDPOINT_URL`` points
at — AWS S3 (default), Tigris, Cloudflare R2, MinIO, etc. The env vars
are still named ``AWS_*`` for backwards compat; only the endpoint URL
distinguishes providers. Leave ``AWS_S3_ENDPOINT_URL`` unset to use
AWS S3.

Provides durable file storage instead of ephemeral local filesystem
(critical for Railway deployments).

Falls back to local filesystem when credentials are missing OR when
the configured creds prove invalid (e.g. rotated, expired). Health is
probed once per process and cached: every subsequent upload skips
silently if the store is unavailable, instead of logging
InvalidAccessKeyId on every CV fetch (which previously buried real
errors during bulk scoring).

Use ``s3_status()`` from /health or admin tooling to surface whether
durable storage is wired up. ``S3_DISABLED`` env var or empty
``AWS_ACCESS_KEY_ID`` short-circuits the probe entirely.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("taali.s3")


# Cached health verdict for the lifetime of the process.
# None  → not yet probed
# True  → store reachable + bucket accessible
# False → creds missing OR probe failed (e.g. InvalidAccessKeyId)
_health_cache: Optional[bool] = None
_health_reason: str = ""
_probe_lock = threading.Lock()


def _build_object_url(bucket: str, key: str) -> str:
    """Public URL for a stored object.

    For AWS S3 (no endpoint URL configured) we keep the existing
    virtual-hosted style ``https://<bucket>.s3.amazonaws.com/<key>`` so
    historical ``cv_file_url`` rows still parse the same way. For any
    other endpoint (Tigris, R2, MinIO, ...) we use path-style
    ``<endpoint>/<bucket>/<key>`` since custom-domain virtual-hosted
    style requires DNS work the platform can't assume.
    """
    from ..platform.config import settings

    endpoint_url = (getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip().rstrip("/")
    if endpoint_url:
        return f"{endpoint_url}/{bucket}/{key}"
    return f"https://{bucket}.s3.amazonaws.com/{key}"


def extract_key_from_url(file_url: str) -> Optional[tuple[str, str]]:
    """Parse a stored URL into ``(bucket, key)`` for any of our URL styles.

    Recognises:
    - AWS virtual-hosted: ``https://<bucket>.s3[.<region>].amazonaws.com/<key>``
    - Endpoint path-style: ``<endpoint>/<bucket>/<key>`` (Tigris, R2, ...)
    - Endpoint virtual-hosted: ``<bucket>.<endpoint_host>/<key>``

    Returns ``None`` when the URL doesn't look like one of ours
    (e.g. local filesystem path, raw HTTPS to Workable's S3, etc.).
    """
    location = (file_url or "").strip()
    if not location:
        return None
    parsed = urlparse(location)
    if parsed.scheme not in {"http", "https"}:
        return None

    # AWS virtual-hosted style first — covers historical rows even
    # when an endpoint URL is now configured for new uploads.
    if parsed.netloc.endswith("amazonaws.com"):
        host_parts = parsed.netloc.split(".")
        if len(host_parts) >= 3 and host_parts[1] == "s3":
            bucket = host_parts[0]
            key = parsed.path.lstrip("/")
            if key:
                return bucket, key
        return None

    from ..platform.config import settings

    endpoint_url = (getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip()
    if not endpoint_url:
        return None
    endpoint_host = urlparse(endpoint_url).netloc
    if not endpoint_host:
        return None

    configured_bucket = settings.AWS_S3_BUCKET or ""

    # Path-style: <endpoint_host>/<bucket>/<key>
    if parsed.netloc == endpoint_host:
        path = parsed.path.lstrip("/")
        if not path:
            return None
        bucket, _, key = path.partition("/")
        if bucket and key:
            return bucket, key
        return None

    # Virtual-hosted on the endpoint: <bucket>.<endpoint_host>/<key>
    if configured_bucket and parsed.netloc == f"{configured_bucket}.{endpoint_host}":
        key = parsed.path.lstrip("/")
        if key:
            return configured_bucket, key
    return None


def _probe_health() -> tuple[bool, str]:
    """One-time check: can we actually use the store? Cached afterwards.

    Tries a cheap HeadBucket call. Returns (ok, reason).
    """
    from ..platform.config import settings

    if getattr(settings, "S3_DISABLED", False):
        return False, "S3_DISABLED env var is set"
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        return False, "credentials not configured"
    if not settings.AWS_S3_BUCKET:
        return False, "AWS_S3_BUCKET not configured"

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        endpoint_url = (getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip() or None
        client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION,
            endpoint_url=endpoint_url,
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
            logger.info("Object storage is healthy (bucket reachable)")
        else:
            logger.warning(
                "Object storage unavailable — files persist locally only (ephemeral on Railway). Reason: %s",
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

    endpoint_url = (getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip() or None
    client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
        endpoint_url=endpoint_url,
    )
    return client, settings.AWS_S3_BUCKET


def _flip_health_off_on_auth_error(exc: Exception) -> bool:
    """Mid-stream credential failure handler. Returns True if the cache
    was flipped (so callers can short-circuit subsequent retries)."""
    global _health_cache, _health_reason
    if "InvalidAccessKeyId" in str(exc) or "ExpiredToken" in str(exc) or "SignatureDoesNotMatch" in str(exc):
        with _probe_lock:
            if _health_cache is not False:
                logger.warning(
                    "Object storage credentials rejected mid-stream — disabling uploads for this process. Error: %s",
                    exc,
                )
            _health_cache = False
            _health_reason = f"runtime_failure: {exc}"
        return True
    return False


def upload_to_s3(local_path: str, key: str) -> Optional[str]:
    """Upload a local file and return the public URL.

    Returns None when storage is unavailable. Failures are logged at
    debug level after the first one (the warning at startup is enough
    — every subsequent CV upload would otherwise spam ERROR logs that
    drown out real issues).
    """
    client, bucket = _get_client()
    if client is None:
        return None

    try:
        client.upload_file(local_path, bucket, key)
        return _build_object_url(bucket, key)
    except Exception as exc:
        if not _flip_health_off_on_auth_error(exc):
            logger.debug("Object upload failed for %s: %s", local_path, exc)
        return None


def upload_bytes_to_s3(content: bytes, key: str, *, content_type: str = "application/octet-stream") -> Optional[str]:
    """Upload raw bytes (no temp file). Returns the public URL.

    Used for derived artefacts (cached PDF reports, etc.) that we can
    regenerate from source data, so a None return is never fatal — the
    caller falls back to streaming the bytes directly.
    """
    client, bucket = _get_client()
    if client is None:
        return None
    try:
        client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
        return _build_object_url(bucket, key)
    except Exception as exc:
        if not _flip_health_off_on_auth_error(exc):
            logger.debug("Object put_object failed for %s: %s", key, exc)
        return None


def s3_object_exists(key: str) -> bool:
    """HEAD check — used for cached-artefact lookup before redirecting."""
    client, bucket = _get_client()
    if client is None:
        return False
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def download_from_s3(key: str) -> Optional[bytes]:
    """Download a file. Returns None when storage is unavailable."""
    client, bucket = _get_client()
    if client is None:
        return None

    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except Exception as exc:
        logger.debug("Object download failed for %s: %s", key, exc)
        return None


def generate_presigned_url(
    key: str,
    *,
    expires_in: int = 600,
    download_filename: str | None = None,
    response_cache_control: str = "private, max-age=600",
) -> Optional[str]:
    """Return a presigned GET URL the browser can fetch directly.

    Lets us redirect CV/document downloads to the storage backend
    instead of streaming the bytes through FastAPI — frees the worker,
    lets the browser cache, and supports range requests for inline PDF
    preview.

    ``download_filename`` forces an attachment Content-Disposition.
    Returns None when storage is unavailable.
    """
    client, bucket = _get_client()
    if client is None:
        return None

    params: dict[str, str] = {
        "Bucket": bucket,
        "Key": key,
        "ResponseCacheControl": response_cache_control,
    }
    if download_filename:
        safe = download_filename.replace('"', "")
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'

    try:
        return client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=int(expires_in),
        )
    except Exception as exc:
        logger.debug("Presign failed for %s: %s", key, exc)
        return None


def delete_from_s3(key: str) -> bool:
    """Delete a file. Returns False when storage is unavailable."""
    client, bucket = _get_client()
    if client is None:
        return False

    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        logger.debug("Object delete failed for %s: %s", key, exc)
        return False


def generate_s3_key(entity_type: str, entity_id: int, filename: str) -> str:
    """Generate a structured object key."""
    safe_filename = filename.replace(" ", "_").replace("/", "_")
    return f"uploads/{entity_type}/{entity_id}/{safe_filename}"
