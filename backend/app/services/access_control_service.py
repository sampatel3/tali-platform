from __future__ import annotations

from typing import Any, List


def normalize_allowed_domains(domains: Any) -> List[str]:
    if domains is None:
        return []
    if isinstance(domains, str):
        raw_items = [item.strip() for item in domains.replace("\n", ",").split(",")]
    elif isinstance(domains, list):
        raw_items = [str(item).strip() for item in domains]
    else:
        raw_items = [str(domains).strip()]

    normalized = []
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
