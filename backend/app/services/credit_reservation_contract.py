"""Serializable identity for one provider credit hold."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CreditReservation:
    """One ledger-backed hold, serializable into a metering payload."""

    organization_id: int
    feature: str
    amount: int
    external_ref: str
    live: bool
    role_id: int | None = None
    shadow_proof: str | None = None
    version: int = 1
    user_id: int | None = None
    entity_id: str | None = None
    candidate_id: int | None = None
    provider: str | None = None
    model: str | None = None
    request_sha256: str | None = None

    def as_metering_payload(self) -> dict[str, Any]:
        parsed = reservation_from_payload(self)
        if parsed is None:
            raise ValueError("credit reservation identity is malformed")
        payload = {
            "organization_id": parsed.organization_id,
            "feature": parsed.feature,
            "amount": parsed.amount,
            "external_ref": parsed.external_ref,
            "live": parsed.live,
        }
        if parsed.version == 2:
            payload.update(
                {
                    "version": 2,
                    "role_id": parsed.role_id,
                    "user_id": parsed.user_id,
                    "entity_id": parsed.entity_id,
                    "candidate_id": parsed.candidate_id,
                    "provider": parsed.provider,
                    "model": parsed.model,
                    "request_sha256": parsed.request_sha256,
                    "shadow_proof": parsed.shadow_proof,
                }
            )
            return payload
        if parsed.role_id is not None:
            payload["role_id"] = parsed.role_id
        if parsed.shadow_proof is not None:
            payload["shadow_proof"] = parsed.shadow_proof
        return payload


def reservation_from_payload(
    value: CreditReservation | dict[str, Any] | None,
) -> CreditReservation | None:
    """Parse only complete, well-typed reservation identities."""

    if isinstance(value, CreditReservation):
        raw: dict[str, Any] = {
            "organization_id": value.organization_id,
            "feature": value.feature,
            "amount": value.amount,
            "external_ref": value.external_ref,
            "live": value.live,
            "role_id": value.role_id,
            "shadow_proof": value.shadow_proof,
            "version": value.version,
        }
        if value.version == 2:
            raw.update(
                {
                    "user_id": value.user_id,
                    "entity_id": value.entity_id,
                    "candidate_id": value.candidate_id,
                    "provider": value.provider,
                    "model": value.model,
                    "request_sha256": value.request_sha256,
                }
            )
    elif type(value) is dict:
        raw = value
    else:
        return None
    version = raw.get("version", 1)
    if type(version) is not int or version not in {1, 2}:
        return None
    v1_fields = {
        "organization_id",
        "feature",
        "amount",
        "external_ref",
        "live",
        "role_id",
        "shadow_proof",
        "version",
    }
    v2_fields = {
        "version",
        "organization_id",
        "feature",
        "amount",
        "external_ref",
        "live",
        "role_id",
        "user_id",
        "entity_id",
        "candidate_id",
        "provider",
        "model",
        "request_sha256",
        "shadow_proof",
    }
    if (version == 1 and not set(raw).issubset(v1_fields)) or (
        version == 2 and set(raw) != v2_fields
    ):
        return None
    try:
        organization_id = raw["organization_id"]
        feature = raw["feature"]
        amount = raw["amount"]
        live = raw["live"]
        external_ref = raw["external_ref"]
        role_id = raw.get("role_id")
        shadow_proof = raw.get("shadow_proof")
        user_id = raw.get("user_id")
        entity_id = raw.get("entity_id")
        candidate_id = raw.get("candidate_id")
        provider = raw.get("provider")
        model = raw.get("model")
        request_sha256 = raw.get("request_sha256")
        if (
            type(organization_id) is not int
            or organization_id <= 0
            or type(feature) is not str
            or not feature.strip()
            or feature != feature.strip()
            or type(amount) is not int
            or amount < 0
            or type(live) is not bool
            or type(external_ref) is not str
            or not external_ref.strip()
            or external_ref != external_ref.strip()
            or (role_id is not None and (type(role_id) is not int or role_id <= 0))
            or (
                shadow_proof is not None
                and (type(shadow_proof) is not str or not shadow_proof)
            )
        ):
            return None
        if version == 2 and (
            (user_id is not None and (type(user_id) is not int or user_id <= 0))
            or (
                candidate_id is not None
                and (type(candidate_id) is not int or candidate_id <= 0)
            )
            or (
                entity_id is not None
                and (
                    type(entity_id) is not str
                    or not entity_id.strip()
                    or entity_id != entity_id.strip()
                )
            )
            or (
                provider is not None
                and (
                    type(provider) is not str
                    or not provider.strip()
                    or provider != provider.strip()
                )
            )
            or (
                model is not None
                and (
                    type(model) is not str
                    or not model.strip()
                    or model != model.strip()
                )
            )
            or (
                request_sha256 is not None
                and (
                    type(request_sha256) is not str
                    or len(request_sha256) != 64
                    or any(char not in "0123456789abcdef" for char in request_sha256)
                )
            )
            or (live and shadow_proof is not None)
            or (not live and shadow_proof is None)
        ):
            return None
        return CreditReservation(
            organization_id=organization_id,
            feature=feature,
            amount=amount,
            external_ref=external_ref,
            live=live,
            role_id=role_id,
            shadow_proof=shadow_proof,
            version=version,
            user_id=user_id,
            entity_id=entity_id,
            candidate_id=candidate_id,
            provider=provider,
            model=model,
            request_sha256=request_sha256,
        )
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["CreditReservation", "reservation_from_payload"]
