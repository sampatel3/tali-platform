"""P1: public careers — published-role queries + schema.org JobPosting JSON-LD.

Powers the public, no-auth careers site (read-only). The JSON-LD renderer is a
pure function (Google for Jobs / schema.org JobPosting) so it is fully unit-
tested; the routes are thin wrappers. Only roles with status='published' and a
slug are exposed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ...models.organization import Organization
from ...models.role import ROLE_STATUS_PUBLISHED, Role

# Taali employment_type -> schema.org employmentType.
_EMPLOYMENT_TYPE_SCHEMA = {
    "full_time": "FULL_TIME",
    "part_time": "PART_TIME",
    "contract": "CONTRACTOR",
    "contractor": "CONTRACTOR",
    "temporary": "TEMPORARY",
    "internship": "INTERN",
    "intern": "INTERN",
}

# Taali salary_period -> schema.org QuantitativeValue.unitText.
_SALARY_PERIOD_SCHEMA = {
    "year": "YEAR",
    "annual": "YEAR",
    "month": "MONTH",
    "week": "WEEK",
    "day": "DAY",
    "hour": "HOUR",
}


def list_published_roles(db: Session, org_slug: str) -> tuple[Organization, list[Role]]:
    """The org + its published, slugged, non-deleted roles. Returns (None, []) if
    the org slug is unknown."""
    org = (
        db.query(Organization)
        .filter(Organization.slug == (org_slug or "").strip().lower())
        .first()
    )
    if org is None:
        return None, []
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.status == ROLE_STATUS_PUBLISHED,
            Role.slug.isnot(None),
            Role.deleted_at.is_(None),
        )
        .order_by(Role.created_at.desc(), Role.id.desc())
        .all()
    )
    return org, roles


def get_published_role(
    db: Session, org_slug: str, role_slug: str
) -> tuple[Organization, Role] | tuple[None, None]:
    org = (
        db.query(Organization)
        .filter(Organization.slug == (org_slug or "").strip().lower())
        .first()
    )
    if org is None:
        return None, None
    role = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.slug == role_slug,
            Role.status == ROLE_STATUS_PUBLISHED,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        return None, None
    return org, role


def build_job_posting_jsonld(role: Role, org: Organization) -> dict:
    """A schema.org JobPosting dict for the role's public posting page (Google for
    Jobs). Empty/unknown fields are omitted."""
    posting: dict = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": role.name,
        "hiringOrganization": {"@type": "Organization", "name": org.name},
    }
    description = (role.description or role.job_spec_text or "").strip()
    if description:
        posting["description"] = description
    if role.created_at is not None:
        posting["datePosted"] = role.created_at.date().isoformat()
    schema_employment = _EMPLOYMENT_TYPE_SCHEMA.get((role.employment_type or "").lower())
    if schema_employment:
        posting["employmentType"] = schema_employment
    if (role.workplace_type or "").lower() == "remote":
        posting["jobLocationType"] = "TELECOMMUTE"
    elif role.location_city or role.location_country:
        address: dict = {"@type": "PostalAddress"}
        if role.location_city:
            address["addressLocality"] = role.location_city
        if role.location_country:
            address["addressCountry"] = role.location_country
        posting["jobLocation"] = {"@type": "Place", "address": address}
    if role.salary_min or role.salary_max:
        value: dict = {"@type": "QuantitativeValue"}
        if role.salary_min is not None:
            value["minValue"] = role.salary_min
        if role.salary_max is not None:
            value["maxValue"] = role.salary_max
        unit = _SALARY_PERIOD_SCHEMA.get((role.salary_period or "").lower())
        if unit:
            value["unitText"] = unit
        posting["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": role.salary_currency or "USD",
            "value": value,
        }
    posting["directApply"] = True
    return posting
