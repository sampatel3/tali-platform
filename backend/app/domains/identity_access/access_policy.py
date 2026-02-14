from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass
class OrgAccessPolicyDecision:
    allowed: bool
    reason: str | None = None
    organization_id: int | None = None


def normalize_allowed_domains(domains: Any) -> List[str]:
    if domains is None:
        return []
    if isinstance(domains, str):
        raw_items = [item.strip() for item in domains.replace("\n", ",").split(",")]
    elif isinstance(domains, list):
        raw_items = [str(item).strip() for item in domains]
    else:
        raw_items = [str(domains).strip()]

    normalized: List[str] = []
    for item in raw_items:
        if not item:
            continue
        domain = item.lower().strip()
        if domain.startswith("@"):
            domain = domain[1:]
        if "@" in domain:
            domain = domain.split("@", 1)[1]
        domain = domain.strip(".")
        if domain and domain not in normalized:
            normalized.append(domain)
    return normalized


def email_domain(email: str) -> str:
    value = str(email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.rsplit("@", 1)[1].strip(".")


def is_email_allowed_for_domains(email: str, allowed_domains: List[str]) -> bool:
    normalized_domains = normalize_allowed_domains(allowed_domains)
    if not normalized_domains:
        return True
    domain = email_domain(email)
    if not domain:
        return False
    return domain in normalized_domains


def evaluate_login_access(*, email: str, sso_enforced: bool, organization_id: int | None = None) -> OrgAccessPolicyDecision:
    if sso_enforced:
        return OrgAccessPolicyDecision(
            allowed=False,
            reason="Organization enforces SSO. Use enterprise sign-in.",
            organization_id=organization_id,
        )
    return OrgAccessPolicyDecision(allowed=True, organization_id=organization_id)
