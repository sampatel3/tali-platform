"""Independent related-role stage semantics on the pipeline endpoint."""

from datetime import datetime, timezone

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def test_related_pipeline_counts_filters_and_activity_use_local_stage(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    source = Role(
        organization_id=user.organization_id,
        name="Canonical ATS role",
        source="workable",
        job_spec_text="Canonical role specification.",
    )
    candidate = Candidate(
        organization_id=user.organization_id,
        email="local-stage@example.com",
        full_name="Local Stage Candidate",
    )
    db.add_all([source, candidate])
    db.flush()
    source_activity = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    application = CandidateApplication(
        organization_id=user.organization_id,
        candidate_id=candidate.id,
        role_id=source.id,
        source="workable",
        pipeline_stage="review",
        pipeline_stage_updated_at=source_activity,
        application_outcome="open",
    )
    related = Role(
        organization_id=user.organization_id,
        name="Independent related funnel",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="Independent related-role specification.",
    )
    db.add_all([application, related])
    db.flush()
    related_activity = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)
    db.add(
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=related.id,
            source_application_id=application.id,
            status="done",
            pipeline_stage="invited",
            pipeline_stage_updated_at=related_activity,
            pipeline_stage_source="recruiter",
            spec_fingerprint="spec",
            role_fit_score=82,
        )
    )
    hidden_candidate = Candidate(
        organization_id=user.organization_id,
        email="deleted-local-stage@example.com",
        full_name="Deleted Local Stage Candidate",
        deleted_at=datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc),
    )
    db.add(hidden_candidate)
    db.flush()
    hidden_application = CandidateApplication(
        organization_id=user.organization_id,
        candidate_id=hidden_candidate.id,
        role_id=source.id,
        source="workable",
        pipeline_stage="applied",
        application_outcome="open",
    )
    db.add(hidden_application)
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=related.id,
            source_application_id=hidden_application.id,
            status="done",
            pipeline_stage="review",
            pipeline_stage_updated_at=datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc),
            spec_fingerprint="hidden-spec",
        )
    )
    db.commit()

    invited = client.get(
        f"/api/v1/roles/{related.id}/pipeline",
        params={"stages": "invited", "sort_by": "pipeline_stage_updated_at"},
        headers=headers,
    )

    assert invited.status_code == 200, invited.text
    payload = invited.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == application.id
    assert payload["items"][0]["pipeline_stage"] == "invited"
    assert payload["stage_counts"]["invited"] == 1
    assert payload["stage_counts"]["review"] == 0
    assert payload["active_candidates_count"] == 1
    assert payload["last_candidate_activity_at"].startswith("2026-07-16T12:30:00")

    source_stage = client.get(
        f"/api/v1/roles/{related.id}/pipeline",
        params={"stages": "review"},
        headers=headers,
    )
    assert source_stage.status_code == 200, source_stage.text
    assert source_stage.json()["total"] == 0
