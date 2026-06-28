"""Unit tests for ``role_pipeline_counts_bulk``.

The Hub's /agent/roles/breakdown surfaces each role's candidate-pipeline
standing (advanced / review / rejected …) so a recruiter knows the
denominator before advancing more from the queue. It iterates the role
list, so it uses the batched helper to stay at two queries instead of
N+1. These tests lock that the batched counts match the per-role helper
and stay org-scoped.
"""

from __future__ import annotations

from app.domains.assessments_runtime.pipeline_service import (
    FUNNEL_BUCKETS,
    role_pipeline_counts,
    role_pipeline_counts_bulk,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from tests.conftest import TestingSessionLocal


def _seed_app(db, *, organization_id, role_id, stage, outcome="open", n=1):
    for i in range(n):
        cand = Candidate(
            organization_id=organization_id,
            email=f"{stage}-{outcome}-{role_id}-{i}@x.test",
            full_name=f"{stage} {i}",
        )
        db.add(cand)
        db.flush()
        db.add(
            CandidateApplication(
                organization_id=organization_id,
                candidate_id=cand.id,
                role_id=role_id,
                status="applied",
                pipeline_stage=stage,
                pipeline_stage_source="recruiter",
                application_outcome=outcome,
                source="manual",
            )
        )


def _mk_org_role(db, *, org_name, role_name):
    org = Organization(name=org_name)
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name=role_name, source="manual")
    db.add(role)
    db.flush()
    return int(org.id), int(role.id)


def test_bulk_matches_per_role_and_counts_advanced_and_rejected(db):
    sess = TestingSessionLocal()
    try:
        org_id, role_a = _mk_org_role(sess, org_name="OrgA", role_name="Role A")
        _, role_b = _mk_org_role(sess, org_name="OrgB", role_name="Role B")
        # Role A: a funnel with several advanced + some rejected.
        _seed_app(sess, organization_id=org_id, role_id=role_a, stage="advanced", n=10)
        _seed_app(sess, organization_id=org_id, role_id=role_a, stage="review", n=3)
        _seed_app(sess, organization_id=org_id, role_id=role_a, stage="invited", n=2)
        # Assessments in progress: still roll up into the `invited` bucket, but
        # are also tracked separately as the `in_assessment` sub-count.
        _seed_app(sess, organization_id=org_id, role_id=role_a, stage="in_assessment", n=4)
        # Rejected is an outcome, orthogonal to stage — counted across all.
        _seed_app(sess, organization_id=org_id, role_id=role_a, stage="advanced",
                  outcome="rejected", n=5)
        sess.commit()

        bulk = role_pipeline_counts_bulk(
            sess, organization_id=org_id, role_ids=[role_a, role_b]
        )
        per_role = role_pipeline_counts(sess, organization_id=org_id, role_id=role_a)

        # Batched result equals the per-role helper for role A.
        assert bulk[role_a] == per_role
        # The number the Hub cares about: already-advanced (open) candidates.
        assert bulk[role_a]["advanced"] == 10
        # `review` stage surfaces as the "completed" funnel bucket.
        assert bulk[role_a]["completed"] == 3
        # Both `invited` (2) and `in_assessment` (4) roll up into the funnel's
        # `invited` bucket; `in_assessment` is also surfaced as its own sub-count.
        assert bulk[role_a]["invited"] == 6
        assert bulk[role_a]["in_assessment"] == 4
        # Rejected counted across all stages, not just the open funnel.
        assert bulk[role_a]["rejected"] == 5

        # A requested role with no applications gets a zero-filled dict
        # (every funnel bucket + the not_yet_decided & in_assessment sub-counts),
        # never a missing key.
        assert bulk[role_b] == {**{b: 0 for b in FUNNEL_BUCKETS}, "not_yet_decided": 0, "in_assessment": 0}
    finally:
        sess.close()


def test_bulk_is_org_scoped(db):
    sess = TestingSessionLocal()
    try:
        org_a, role_a = _mk_org_role(sess, org_name="Scoped A", role_name="R A")
        org_b, role_b = _mk_org_role(sess, org_name="Scoped B", role_name="R B")
        _seed_app(sess, organization_id=org_a, role_id=role_a, stage="advanced", n=4)
        _seed_app(sess, organization_id=org_b, role_id=role_b, stage="advanced", n=9)
        sess.commit()

        # Querying org_a for both role ids must not leak org_b's role.
        bulk = role_pipeline_counts_bulk(
            sess, organization_id=org_a, role_ids=[role_a, role_b]
        )
        assert bulk[role_a]["advanced"] == 4
        assert bulk[role_b]["advanced"] == 0
    finally:
        sess.close()


def test_bulk_empty_role_ids_returns_empty():
    sess = TestingSessionLocal()
    try:
        assert role_pipeline_counts_bulk(sess, organization_id=1, role_ids=[]) == {}
    finally:
        sess.close()
