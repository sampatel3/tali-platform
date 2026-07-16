"""Related-role drafts hydrate safe structured fields from the cloned JD."""

from __future__ import annotations

import pytest

from app.models.organization import Organization
from app.models.role import Role
from app.models.role_brief import RoleBrief
from app.models.user import User
from app.services.related_role_service import create_related_role_draft
from app.services.related_role_spec_hydration import extract_explicit_responsibilities
from app.services.requisition_chat_capture import compute_completeness, compute_gaps
from app.services.requisition_template_service import DEFAULT_REQUISITION_TEMPLATE


FULL_JD = """# Senior AI Engineer

## About the role
Build reliable AI products for regulated customers.

## Key responsibilities
- Design production RAG evaluation systems.
- Own model observability and reliability.
- Partner with product and data teams on safe delivery.

## Requirements
- Strong Python engineering experience.
- Production LLM experience.
"""


def _source_role(db, *, suffix: str) -> tuple[Organization, User, Role]:
    org = Organization(name=f"Hydration Org {suffix}", slug=f"hydration-{suffix}")
    db.add(org)
    db.flush()
    user = User(
        email=f"hydration-{suffix}@example.com",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        role="owner",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    source = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="workable",
        workable_job_id=f"AI-{suffix}",
        job_spec_text=FULL_JD,
    )
    db.add_all([user, source])
    db.commit()
    return org, user, source


@pytest.mark.parametrize(
    "heading",
    ["Responsibilities", "Key responsibilities", "What you'll do"],
)
def test_explicit_responsibility_heading_variants_are_parsed(heading):
    spec = f"""# Engineer

## {heading}
1. Build reliable services.
2. Own production operations.

## Requirements
- Python
"""

    assert extract_explicit_responsibilities(spec) == [
        "Build reliable services.",
        "Own production operations.",
    ]


def test_unknown_plain_headings_stop_responsibility_capture():
    spec = """Responsibilities
- Build reliable APIs.
- Own production operations.

Required Skills
- Python
- PostgreSQL

Education
- BSc or equivalent
"""

    assert extract_explicit_responsibilities(spec) == [
        "Build reliable APIs.",
        "Own production operations.",
    ]


def test_unbulleted_known_heading_stops_unbulleted_responsibilities():
    spec = """Responsibilities

Build reliable APIs.

Own production operations.

Education

Bachelor's degree required.
"""

    assert extract_explicit_responsibilities(spec) == [
        "Build reliable APIs.",
        "Own production operations.",
    ]


def test_title_case_bullet_continuation_is_not_mistaken_for_heading():
    spec = """Responsibilities
- Lead go-to-market delivery for
  Enterprise Sales
- Own production operations.

Required Skills
- Forecasting
"""

    assert extract_explicit_responsibilities(spec) == [
        "Lead go-to-market delivery for Enterprise Sales",
        "Own production operations.",
    ]


def test_uppercase_and_colon_bullets_remain_responsibilities():
    spec = """Responsibilities
- OWN PRODUCTION
- Own incident response:
1. NEW PLATFORM DELIVERY

Education
- BSc
"""

    assert extract_explicit_responsibilities(spec) == [
        "OWN PRODUCTION",
        "Own incident response:",
        "NEW PLATFORM DELIVERY",
    ]


def test_bold_unbulleted_responsibility_paragraphs_are_not_headings():
    spec = """## Responsibilities

**Build reliable systems**

**Own production**

## Requirements
- Python
"""

    assert extract_explicit_responsibilities(spec) == [
        "**Build reliable systems**",
        "**Own production**",
    ]


def test_ats_source_without_brief_hydrates_responsibilities_and_raw_input(db):
    org, user, source = _source_role(db, suffix="new")
    assert (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == org.id, RoleBrief.role_id == source.id)
        .count()
        == 0
    )

    draft = create_related_role_draft(
        db,
        role_id=source.id,
        organization_id=org.id,
        creator_user_id=user.id,
        template=DEFAULT_REQUISITION_TEMPLATE,
    )

    assert draft.raw_input == FULL_JD.strip()
    assert draft.agent_state["jd_override"] == FULL_JD.strip()
    assert draft.custom_fields["responsibilities"] == [
        "Design production RAG evaluation systems.",
        "Own model observability and reliability.",
        "Partner with product and data teams on safe delivery.",
    ]
    assert "responsibilities" not in {
        gap["key"] for gap in compute_gaps(draft, DEFAULT_REQUISITION_TEMPLATE)
    }
    assert draft.completeness == compute_completeness(
        draft, DEFAULT_REQUISITION_TEMPLATE
    )


def test_existing_structured_responsibilities_win_over_cloned_spec(db):
    org, user, source = _source_role(db, suffix="existing")
    source_brief = RoleBrief(
        organization_id=org.id,
        role_id=source.id,
        title=source.name,
        custom_fields={
            "domain": "Artificial intelligence",
            "responsibilities": ["Keep the recruiter-confirmed responsibility."],
        },
    )
    db.add(source_brief)
    db.commit()

    revised_spec = FULL_JD.replace(
        "Design production RAG evaluation systems.",
        "Replace this only if no structured value exists.",
    )
    draft = create_related_role_draft(
        db,
        role_id=source.id,
        organization_id=org.id,
        creator_user_id=user.id,
        template=DEFAULT_REQUISITION_TEMPLATE,
        job_spec_text=revised_spec,
    )

    assert draft.custom_fields["responsibilities"] == [
        "Keep the recruiter-confirmed responsibility."
    ]
    assert draft.custom_fields["domain"] == "Artificial intelligence"
    assert draft.raw_input == revised_spec.strip()
    assert draft.agent_state["jd_override"] == revised_spec.strip()
    db.refresh(source_brief)
    assert source_brief.custom_fields["responsibilities"] == [
        "Keep the recruiter-confirmed responsibility."
    ]
