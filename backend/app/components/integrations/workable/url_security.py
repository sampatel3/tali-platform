"""Outbound URL boundaries for the Workable integration.

Workable API pagination and provider callbacks are credential-bearing and must
never be allowed to choose an arbitrary origin. Resume/document downloads may
use presigned storage hosts, so those are allowed only over public HTTPS and
never receive the Workable bearer token outside the tenant's exact API origin.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit


def _parsed_https_url(value: str):
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL port") from exc
    if port not in (None, 443):
        raise ValueError("Only the default HTTPS port is allowed")
    if parsed.fragment:
        raise ValueError("URL fragments are not allowed")
    return parsed


def _canonical(parsed) -> str:
    host = (parsed.hostname or "").lower().rstrip(".")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def validate_workable_callback_url(value: str) -> str:
    parsed = _parsed_https_url(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "workable.com" and not host.endswith(".workable.com"):
        raise ValueError("Callback URL must be hosted by workable.com")
    return _canonical(parsed)


def validate_workable_api_url(value: str, *, expected_host: str, base_url: str) -> str:
    absolute = urljoin(base_url.rstrip("/") + "/", str(value or "").strip())
    parsed = _parsed_https_url(absolute)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != expected_host.lower().rstrip("."):
        raise ValueError("Workable pagination cannot change API origin")
    if not (parsed.path or "").startswith("/spi/v3/"):
        raise ValueError("Workable pagination URL is outside the SPI API")
    return _canonical(parsed)


def _require_public_hostname(host: str) -> None:
    normalized = host.lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".local"):
        raise ValueError("Local download hosts are not allowed")
    try:
        literal = ipaddress.ip_address(normalized)
        addresses = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(normalized, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("Download host could not be resolved") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Download URL resolves to a non-public address")


def validate_public_download_url(value: str) -> str:
    parsed = _parsed_https_url(value)
    _require_public_hostname(parsed.hostname or "")
    return _canonical(parsed)


def same_https_origin(value: str, *, host: str) -> bool:
    try:
        parsed = _parsed_https_url(value)
    except ValueError:
        return False
    return (parsed.hostname or "").lower().rstrip(".") == host.lower().rstrip(".")


__all__ = [
    "same_https_origin",
    "validate_public_download_url",
    "validate_workable_api_url",
    "validate_workable_callback_url",
]
