"""Bounded, provider-neutral diagnostics for S3-compatible storage."""

from __future__ import annotations

PROVIDER_CODE_STATUS = {
    "InvalidAccessKeyId": "credentials_rejected",
    "ExpiredToken": "credentials_rejected",
    "SignatureDoesNotMatch": "credentials_rejected",
    "InvalidToken": "credentials_rejected",
    "TokenRefreshRequired": "credentials_rejected",
    "AccessDenied": "access_denied",
    "Forbidden": "access_denied",
    "AllAccessDisabled": "access_denied",
    "NoSuchBucket": "bucket_not_found",
    "NotFound": "bucket_not_found",
    "PermanentRedirect": "region_mismatch",
    "AuthorizationHeaderMalformed": "region_mismatch",
    "IllegalLocationConstraintException": "region_mismatch",
    "RequestTimeout": "timeout",
    "RequestTimeoutException": "timeout",
    "SlowDown": "rate_limited",
    "Throttling": "rate_limited",
    "ThrottlingException": "rate_limited",
    "TooManyRequestsException": "rate_limited",
    "InternalError": "provider_unavailable",
    "ServiceUnavailable": "provider_unavailable",
}
CREDENTIAL_ERROR_CODES = frozenset(
    code for code, status in PROVIDER_CODE_STATUS.items() if status == "credentials_rejected"
)
HEALTH_STATUSES = frozenset(
    {
        "ok",
        "disabled",
        "credentials_missing",
        "bucket_missing",
        "credentials_rejected",
        "access_denied",
        "bucket_not_found",
        "region_mismatch",
        "timeout",
        "rate_limited",
        "provider_unavailable",
        "provider_rejected",
        "provider_error",
        "transport_error",
        "probe_error",
        "runtime_credentials_rejected",
        "unknown",
    }
)


def provider_failure(
    exc: BaseException,
    *,
    default_status: str,
) -> tuple[str, str | None, int | None]:
    """Return bounded diagnostics without copying a provider message/body."""
    raw_code: object = None
    raw_status: object = None
    try:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error = response.get("Error")
            metadata = response.get("ResponseMetadata")
            raw_code = error.get("Code") if isinstance(error, dict) else None
            raw_status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    except Exception:
        pass
    if not (isinstance(raw_status, int) and not isinstance(raw_status, bool)):
        for attr in ("status_code", "http_status"):
            try:
                candidate = getattr(exc, attr, None)
            except Exception:
                continue
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                raw_status = candidate
                break
    status_code = raw_status if isinstance(raw_status, int) and 100 <= raw_status <= 599 else None
    code = raw_code if isinstance(raw_code, str) and raw_code in PROVIDER_CODE_STATUS else None
    if code:
        status = PROVIDER_CODE_STATUS[code]
    elif status_code == 401:
        status = "credentials_rejected"
    elif status_code == 403:
        status = "access_denied"
    elif status_code == 404:
        status = "bucket_not_found"
    elif status_code in {408, 504}:
        status = "timeout"
    elif status_code == 429:
        status = "rate_limited"
    elif status_code is not None and status_code >= 500:
        status = "provider_unavailable"
    elif status_code is not None and status_code >= 400:
        status = "provider_rejected"
    else:
        status = default_status
    return status, code, status_code


def status_payload(
    ok: object,
    status: object,
    provider_code: object = None,
    provider_status_code: object = None,
) -> dict:
    """Build the stable admin-health contract from controlled values."""
    from ..platform.config import settings

    safe_status = status if isinstance(status, str) and status in HEALTH_STATUSES else "probe_error"
    safe_ok = ok if isinstance(ok, bool) else False
    safe_code = (
        provider_code
        if isinstance(provider_code, str) and provider_code in PROVIDER_CODE_STATUS
        else None
    )
    safe_provider_status = (
        provider_status_code
        if isinstance(provider_status_code, int)
        and not isinstance(provider_status_code, bool)
        and 100 <= provider_status_code <= 599
        else None
    )
    bucket = (settings.AWS_S3_BUCKET or "").strip() or None
    region = (settings.AWS_REGION or "").strip() or None
    configured = bool(
        not getattr(settings, "S3_DISABLED", False)
        and settings.AWS_ACCESS_KEY_ID
        and settings.AWS_SECRET_ACCESS_KEY
        and bucket
    )
    payload = {
        "available": safe_ok,
        "ok": safe_ok,
        "configured": configured,
        "bucket": bucket,
        "region": region,
        "status": safe_status,
        "reason": safe_status,
    }
    if safe_code is not None:
        payload["provider_code"] = safe_code
    if safe_provider_status is not None:
        payload["provider_status_code"] = safe_provider_status
    return payload


def sanitize_status_payload(payload: object) -> dict:
    """Rebuild an S3 status mapping from its narrow allowlisted fields."""
    if not isinstance(payload, dict):
        return status_payload(False, "probe_error")
    try:
        return status_payload(
            payload.get("ok", payload.get("available", False)),
            payload.get("status", payload.get("reason", "probe_error")),
            payload.get("provider_code"),
            payload.get("provider_status_code"),
        )
    except Exception:
        return status_payload(False, "probe_error")


__all__ = [
    "CREDENTIAL_ERROR_CODES",
    "provider_failure",
    "sanitize_status_payload",
    "status_payload",
]
