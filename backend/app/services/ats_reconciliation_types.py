"""Stable value types shared by ATS reconciliation service boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ReceiptIdentity:
    receipt_key: str
    operation_id: str
    provider: str
    provider_target_id: str


@dataclass(frozen=True)
class ReceiptSnapshot:
    application_id: int
    organization_id: int
    application_version: int
    application_outcome: str
    identity: ReceiptIdentity
    receipt_fingerprint: str


ProviderLookup = Callable[[Session, ReceiptSnapshot], dict[str, Any]]


__all__ = ["ProviderLookup", "ReceiptIdentity", "ReceiptSnapshot"]
