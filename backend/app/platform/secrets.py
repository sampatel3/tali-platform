"""Helpers for encrypting/decrypting sensitive values at rest."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_INTEGRATION_PREFIX = "enc:v1:"


def _fernet(secret_key: str) -> Fernet:
    # Fernet requires a URL-safe base64-encoded 32-byte key.
    digest = hashlib.sha256((secret_key or "").encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_text(plaintext: str, secret_key: str) -> str:
    if not plaintext:
        return ""
    token = _fernet(secret_key).encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(ciphertext: str, secret_key: str) -> str:
    if not ciphertext:
        return ""
    if str(ciphertext).startswith(_INTEGRATION_PREFIX):
        ciphertext = str(ciphertext)[len(_INTEGRATION_PREFIX):]
    try:
        value = _fernet(secret_key).decrypt(ciphertext.encode("utf-8"))
        return value.decode("utf-8")
    except InvalidToken:
        return ""


def _integration_keys() -> list[str]:
    # Import lazily to keep this low-level module free of config import cycles.
    from .config import settings

    configured = (getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "") or "").strip()
    previous = (getattr(settings, "INTEGRATION_ENCRYPTION_KEY_PREVIOUS", "") or "").strip()
    legacy = (settings.SECRET_KEY or "").strip()
    keys: list[str] = []
    for value in (configured or legacy, previous, legacy):
        if value and value not in keys:
            keys.append(value)
    return keys


def encrypt_integration_secret(plaintext: str) -> str:
    """Encrypt a provider credential with the dedicated, rotatable key.

    The explicit prefix distinguishes new ciphertext from legacy plaintext
    fields without a destructive migration. Readers accept both forms while a
    reconnect or settings save upgrades the stored value in place.
    """
    if not plaintext:
        return ""
    keys = _integration_keys()
    if not keys:
        raise RuntimeError("Integration encryption key is not configured")
    return _INTEGRATION_PREFIX + encrypt_text(plaintext, keys[0])


def decrypt_integration_secret(ciphertext: str | None, *, allow_plaintext: bool = False) -> str:
    if not ciphertext:
        return ""
    raw = str(ciphertext)
    if raw.startswith(_INTEGRATION_PREFIX):
        token = raw[len(_INTEGRATION_PREFIX):]
        for key in _integration_keys():
            value = decrypt_text(token, key)
            if value:
                return value
        return ""

    # Existing encrypted Fireflies/Bullhorn values were unversioned Fernet
    # tokens derived from SECRET_KEY. Try every configured key before treating
    # a legacy Workable value as plaintext.
    for key in _integration_keys():
        value = decrypt_text(raw, key)
        if value:
            return value
    return raw if allow_plaintext else ""


def is_encrypted_integration_secret(value: str | None) -> bool:
    return bool(value and str(value).startswith(_INTEGRATION_PREFIX))
