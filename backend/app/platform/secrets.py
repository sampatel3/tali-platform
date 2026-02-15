"""Helpers for encrypting/decrypting sensitive values at rest."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


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
    try:
        value = _fernet(secret_key).decrypt(ciphertext.encode("utf-8"))
        return value.decode("utf-8")
    except InvalidToken:
        return ""
