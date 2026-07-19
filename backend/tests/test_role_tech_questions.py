"""Role-level tech-screening questions: generate once, cache, invalidate
on job-spec / criteria changes.

Replaces the per-candidate path in ``maybe_generate_tech_questions``
that was firing ~302 Anthropic calls/day (one per CV scoring event).
New shape: ~1-5 calls/day total — one per role per job-spec edit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.platform.config import settings


@pytest.fixture(autouse=True)
def _stub_anthropic_key(monkeypatch):
    """Without this, ``get_or_regenerate`` early-returns None on every
    call because the test env has ANTHROPIC_API_KEY empty. The string
    value doesn't matter — the real LLM call is mocked downstream."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import CRITERION_SOURCE_DERIVED, RoleCriterion
from app.services.role_tech_questions_service import (
    compute_signature,
    get_or_regenerate,
    invalidate,
)


def _seed_role(db, *, job_spec_text: str = "build distributed systems") -> Role:
    slug = f"o-{uuid.uuid4().hex[:8]}"
    org = Organization(name="O", slug=slug)
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text=job_spec_text,
    )
    db.add(role); db.flush()
    return role


def _add_criterion(db, role: Role, *, text: str, must_have: bool = True, source: str = "recruiter") -> RoleCriterion:
    c = RoleCriterion(
        role_id=role.id,
        text=text,
        must_have=must_have,
        bucket="must" if must_have else "preferred",
        source=source,
        ordering=0,
    )
    db.add(c); db.flush()
    role.criteria.append(c)
    db.flush()
    return c


def test_signature_includes_job_spec_and_recruiter_criteria(db):
    role_a = _seed_role(db, job_spec_text="A spec")
    _add_criterion(db, role_a, text="Python")
    role_b = _seed_role(db, job_spec_text="A spec")
    _add_criterion(db, role_b, text="Python")
    role_c = _seed_role(db, job_spec_text="B spec")  # different spec
    _add_criterion(db, role_c, text="Python")
    db.flush()

    assert compute_signature(role_a) == compute_signature(role_b)
    assert compute_signature(role_a) != compute_signature(role_c)


def test_signature_ignores_derived_criteria(db):
    """Derived (model-generated) criteria don't trigger regen — would
    cause an infinite invalidation loop because the next regen output
    might tweak the derived list which would invalidate the cache again."""
    role = _seed_role(db)
    _add_criterion(db, role, text="Python", source="recruiter")
    sig_before = compute_signature(role)

    _add_criterion(db, role, text="Some derived inferred from CV", source=CRITERION_SOURCE_DERIVED)
    db.flush()
    sig_after = compute_signature(role)

    assert sig_before == sig_after


def test_invalidate_nulls_signature(db):
    role = _seed_role(db)
    role.tech_questions_signature = "abc"
    role.tech_questions_cached = [{"question": "q1"}]
    db.flush()

    invalidate(role)
    assert role.tech_questions_signature is None
    # Payload stays as graceful fallback while regen runs.
    assert role.tech_questions_cached == [{"question": "q1"}]


def test_get_or_regenerate_returns_cache_when_signature_matches(db):
    role = _seed_role(db)
    _add_criterion(db, role, text="Python")
    db.flush()

    # Pre-populate cache with the matching signature.
    sig = compute_signature(role)
    role.tech_questions_cached = [{"question": "cached q"}]
    role.tech_questions_cached_at = datetime.now(timezone.utc)
    role.tech_questions_signature = sig
    db.flush()

    with patch("app.services.role_tech_questions_service.generate_tech_questions") as m:
        result = get_or_regenerate(db, role)
        m.assert_not_called()  # signature matched → no LLM call
    assert result == [{"question": "cached q"}]


def test_get_or_regenerate_regenerates_when_signature_drifts(db):
    role = _seed_role(db)
    _add_criterion(db, role, text="Python")
    db.flush()

    # Pre-populate cache with a STALE signature.
    role.tech_questions_cached = [{"question": "old q"}]
    role.tech_questions_signature = "stale_sig"
    db.flush()

    fresh_questions = [{"question": "new q1"}, {"question": "new q2"}]
    with patch("app.services.role_tech_questions_service.generate_tech_questions", return_value=fresh_questions):
        result = get_or_regenerate(db, role)

    assert result == fresh_questions
    assert role.tech_questions_cached == fresh_questions
    assert role.tech_questions_signature == compute_signature(role)
    assert role.tech_questions_cached_at is not None


def test_get_or_regenerate_passes_only_role_inputs(db):
    """Confirms the prompt is called with candidate-specific kwargs set
    to None. That's the whole point of the refactor — no per-candidate
    branching, one question set per role."""
    role = _seed_role(db, job_spec_text="hire backend engineers")
    _add_criterion(db, role, text="Python")
    db.flush()

    with patch("app.services.role_tech_questions_service.generate_tech_questions", return_value=[{"q": 1}]) as m:
        get_or_regenerate(db, role)

    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["job_spec_text"] == "hire backend engineers"
    assert kwargs["requirements_assessment"] is None
    assert kwargs["transcript_text"] is None
    assert kwargs["recruiter_notes"] is None
    assert kwargs["pre_screen_evidence"] is None
    # Metering tag must be set for the call_log + UsageEvent attribution.
    assert kwargs["metering"]["feature"] == "interview_tech"
    assert kwargs["metering"]["entity_id"] == f"role:{role.id}"


def test_get_or_regenerate_keeps_old_cache_on_failure(db, caplog):
    """If the LLM call fails, return the previous cache as graceful
    fallback rather than nulling everything."""
    role = _seed_role(db)
    _add_criterion(db, role, text="Python")
    role.tech_questions_cached = [{"question": "previous"}]
    db.flush()

    secret = "anthropic-secret in role question response"
    with patch(
        "app.services.role_tech_questions_service.generate_tech_questions",
        side_effect=RuntimeError(secret),
    ):
        result = get_or_regenerate(db, role)
    assert result == [{"question": "previous"}]
    # Cache stays — signature stays stale so the next attempt retries.
    assert role.tech_questions_cached == [{"question": "previous"}]
    assert "role_tech_questions:RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_get_or_regenerate_returns_none_without_job_spec(db):
    role = _seed_role(db, job_spec_text="")
    db.flush()
    assert get_or_regenerate(db, role) is None
