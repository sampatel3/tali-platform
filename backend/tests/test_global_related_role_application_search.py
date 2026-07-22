"""Ground truth for logical-role membership on ``GET /applications``."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.candidate_search.schemas import ParsedFilter, SearchOutput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def _application(
    db,
    *,
    organization_id: int,
    role: Role,
    label: str,
    stage: str,
    score: float,
    deleted: bool = False,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization_id,
        email=f"{label}@logical-search.test",
        full_name=label,
        position="AI Engineer",
        cv_text=f"Grounded profile for {label}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        source="manual",
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome="open",
        taali_score_cache_100=score,
        pre_screen_score_100=score,
        cv_match_score=score,
        deleted_at=(datetime.now(timezone.utc) if deleted else None),
    )
    db.add(application)
    db.flush()
    return application


def _membership(
    db,
    *,
    related: Role,
    application: CandidateApplication,
    stage: str,
    score: float,
    source: str = "initial_snapshot",
) -> SisterRoleEvaluation:
    evaluation = SisterRoleEvaluation(
        organization_id=int(related.organization_id),
        role_id=int(related.id),
        candidate_id=int(application.candidate_id),
        source_application_id=int(application.id),
        ats_application_id=(
            int(application.id)
            if int(application.role_id) == int(related.ats_owner_role_id)
            else None
        ),
        status="done",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome="open",
        application_outcome_source="recruiter",
        membership_source=source,
        spec_fingerprint=f"spec-{related.id}",
        role_fit_score=score,
        summary=f"Related evidence for {application.id}",
        details={"grounded": True},
        scored_at=datetime.now(timezone.utc),
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def _world(client, db) -> dict:
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=int(user.organization_id),
        name="ATS owner",
        source="manual",
    )
    unrelated = Role(
        organization_id=int(user.organization_id),
        name="Unrelated role",
        source="manual",
    )
    db.add_all([owner, unrelated])
    db.flush()
    related = Role(
        organization_id=int(user.organization_id),
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
    )
    db.add(related)
    db.flush()

    shared = _application(
        db,
        organization_id=int(user.organization_id),
        role=owner,
        label="shared-owner-and-related",
        stage="advanced",
        score=96,
    )
    _membership(db, related=related, application=shared, stage="review", score=41)
    soft_deleted_evidence = _application(
        db,
        organization_id=int(user.organization_id),
        role=owner,
        label="soft-deleted-related-evidence",
        stage="advanced",
        score=5,
        deleted=True,
    )
    _membership(
        db,
        related=related,
        application=soft_deleted_evidence,
        stage="applied",
        score=88,
    )
    direct_related = _application(
        db,
        organization_id=int(user.organization_id),
        role=related,
        label="direct-related-member",
        stage="review",
        score=7,
    )
    _membership(
        db,
        related=related,
        application=direct_related,
        stage="invited",
        score=77,
        source="direct_application",
    )
    related_without_membership = _application(
        db,
        organization_id=int(user.organization_id),
        role=related,
        label="related-storage-without-membership",
        stage="advanced",
        score=100,
    )
    owner_only = _application(
        db,
        organization_id=int(user.organization_id),
        role=owner,
        label="owner-only-distractor",
        stage="applied",
        score=99,
    )
    unrelated_application = _application(
        db,
        organization_id=int(user.organization_id),
        role=unrelated,
        label="unrelated-distractor",
        stage="applied",
        score=100,
    )
    db.commit()
    return {
        "headers": headers,
        "owner": owner,
        "related": related,
        "shared": shared,
        "soft": soft_deleted_evidence,
        "direct": direct_related,
        "related_without_membership": related_without_membership,
        "owner_only": owner_only,
        "unrelated": unrelated_application,
    }


def test_related_role_global_list_uses_local_state_score_counts_and_pagination(
    client, db
):
    world = _world(client, db)
    role_id = int(world["related"].id)

    response = client.get(
        "/api/v1/applications",
        params={
            "role_id": role_id,
            "sort_by": "taali_score",
            "sort_order": "desc",
            "limit": 2,
        },
        headers=world["headers"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert payload["stage_counts"] == {
        "all": 3,
        "applied": 1,
        "invited": 1,
        "in_assessment": 0,
        "review": 1,
    }
    assert [item["id"] for item in payload["items"]] == [
        int(world["soft"].id),
        int(world["direct"].id),
    ]
    assert [item["taali_score"] for item in payload["items"]] == [88, 77]
    assert [item["pipeline_stage"] for item in payload["items"]] == [
        "applied",
        "invited",
    ]
    assert all(item["role_id"] == role_id for item in payload["items"])
    assert all(
        item["logical_membership_id"] == f"{role_id}:{item['id']}"
        for item in payload["items"]
    )

    second_page = client.get(
        "/api/v1/applications",
        params={
            "role_id": role_id,
            "sort_by": "taali_score",
            "sort_order": "desc",
            "limit": 1,
            "offset": 2,
        },
        headers=world["headers"],
    )
    assert second_page.status_code == 200, second_page.text
    [last] = second_page.json()["items"]
    assert last["id"] == int(world["shared"].id)
    assert last["taali_score"] == 41
    assert last["pipeline_stage"] == "review"

    thresholded = client.get(
        "/api/v1/applications",
        params={"role_id": role_id, "min_taali_score": 80},
        headers=world["headers"],
    )
    assert thresholded.status_code == 200, thresholded.text
    assert [item["id"] for item in thresholded.json()["items"]] == [
        int(world["soft"].id)
    ]


def test_mixed_roles_preserve_owner_and_related_memberships_without_duplicates(
    client, db
):
    world = _world(client, db)
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    response = client.get(
        "/api/v1/applications",
        params={
            "role_ids": f"{owner_id},{related_id}",
            "application_outcome": "open",
            "sort_by": "created_at",
            "limit": 50,
        },
        headers=world["headers"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5
    keys = [item["logical_membership_id"] for item in payload["items"]]
    assert len(keys) == len(set(keys)) == 5
    expected = {
        f"{owner_id}:{int(world['shared'].id)}",
        f"{owner_id}:{int(world['owner_only'].id)}",
        f"{related_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['soft'].id)}",
        f"{related_id}:{int(world['direct'].id)}",
    }
    assert set(keys) == expected
    assert int(world["unrelated"].id) not in {
        int(item["id"]) for item in payload["items"]
    }
    assert int(world["related_without_membership"].id) not in {
        int(item["id"]) for item in payload["items"]
    }

    shared_rows = [
        item
        for item in payload["items"]
        if int(item["id"]) == int(world["shared"].id)
    ]
    assert len(shared_rows) == 2
    by_role = {int(item["logical_role_id"]): item for item in shared_rows}
    assert by_role[owner_id]["pipeline_stage"] == "advanced"
    assert by_role[owner_id]["taali_score"] == 96
    assert by_role[related_id]["pipeline_stage"] == "review"
    assert by_role[related_id]["taali_score"] == 41


def test_mixed_role_nl_results_expand_back_to_logical_memberships(client, db):
    world = _world(client, db)
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    matched_application_ids = [
        int(world["shared"].id),
        int(world["soft"].id),
        int(world["direct"].id),
    ]

    with patch(
        "app.candidate_search.runner.run_search",
        return_value=SearchOutput(
            application_ids=matched_application_ids,
            parsed_filter=ParsedFilter(),
            warnings=[],
            database_matches=3,
            retrieval_matches=3,
            exhaustive=True,
            is_exact_empty=False,
        ),
    ):
        response = client.get(
            "/api/v1/applications",
            params={
                "role_ids": f"{owner_id},{related_id}",
                "nl_query": "grounded role members",
                "limit": 50,
            },
            headers=world["headers"],
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 4
    assert {
        item["logical_membership_id"] for item in payload["items"]
    } == {
        f"{owner_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['soft'].id)}",
        f"{related_id}:{int(world['direct'].id)}",
    }
