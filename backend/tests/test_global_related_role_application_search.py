"""Ground truth for logical-role membership on ``GET /applications``."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.agent_chat.tools import dispatch_tool as dispatch_agent_chat_tool
from app.candidate_search.grounded_evidence import CriterionVerdict, Evidence
from app.candidate_search.role_scope import resolve_candidate_role_scope
from app.candidate_search.schemas import ParsedFilter, SearchOutput
from app.candidate_search.global_candidate_reader import read_global_candidate_page
from app.domains.public_api.router import (
    list_role_applications as list_public_role_applications,
    role_metrics as public_role_metrics,
)
from app.mcp import handlers as mcp_handlers
from app.mcp.operations import get_recruiting_overview
from app.mcp import server as mcp_server
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.taali_scoring import compute_taali_score
from app.services.logical_role_application_authority import (
    LogicalRoleApplicationAuthorizationError,
    authorize_logical_role_application,
)
from app.taali_chat import tool_registry as taali_tools
from tests.conftest import auth_headers


_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _mcp_call(
    client,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/mcp/",
        headers={**_MCP_HEADERS, **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    assert response.status_code == 200, response.text
    for line in response.text.splitlines():
        if line.strip().startswith("data:"):
            return json.loads(line.split("data:", 1)[1].strip())
    raise AssertionError(f"missing MCP SSE payload: {response.text!r}")


def _mcp_tool_payload(response: dict[str, Any]) -> Any:
    result = response["result"]
    assert result.get("isError") is not True, result
    structured = result.get("structuredContent")
    if structured is not None:
        return structured.get("result", structured)
    return json.loads(result["content"][0]["text"])


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
        "user": user,
        "owner": owner,
        "related": related,
        "shared": shared,
        "soft": soft_deleted_evidence,
        "direct": direct_related,
        "related_without_membership": related_without_membership,
        "owner_only": owner_only,
        "unrelated": unrelated_application,
    }


def _add_conflicting_assessment_truth(db, world: dict) -> None:
    """Give one physical application different owner and related scores."""

    shared = world["shared"]
    soft = world["soft"]
    direct = world["direct"]
    shared.assessment_score_cache_100 = 99
    soft.assessment_score_cache_100 = 5
    direct.assessment_score_cache_100 = 7
    world["owner_only"].assessment_score_cache_100 = 66
    world["unrelated"].assessment_score_cache_100 = 55
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            Assessment(
                organization_id=int(world["user"].organization_id),
                candidate_id=int(shared.candidate_id),
                role_id=int(world["owner"].id),
                application_id=int(shared.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=99,
                is_voided=False,
            ),
            Assessment(
                organization_id=int(world["user"].organization_id),
                candidate_id=int(shared.candidate_id),
                role_id=int(world["related"].id),
                application_id=int(shared.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=22,
                score_breakdown={"score_components": {"role_fit_score": 41}},
                is_voided=False,
            ),
            Assessment(
                organization_id=int(world["user"].organization_id),
                candidate_id=int(soft.candidate_id),
                role_id=int(world["related"].id),
                application_id=int(soft.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=88,
                score_breakdown={"score_components": {"role_fit_score": 88}},
                is_voided=False,
            ),
            Assessment(
                organization_id=int(world["user"].organization_id),
                candidate_id=int(direct.candidate_id),
                role_id=int(world["related"].id),
                application_id=int(direct.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=77,
                score_breakdown={"score_components": {"role_fit_score": 77}},
                is_voided=False,
            ),
        ]
    )
    db.commit()


def _add_related_role_assessment(
    db,
    world: dict,
    *,
    application: CandidateApplication,
    assessment_score: float,
    persisted_taali_score: float | None,
    frozen_role_fit_score: float | None = None,
    assessment_application: CandidateApplication | None = None,
) -> Assessment:
    assessment = Assessment(
        organization_id=int(world["user"].organization_id),
        candidate_id=int(application.candidate_id),
        role_id=int(world["related"].id),
        application_id=int((assessment_application or application).id),
        status=AssessmentStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        assessment_score=assessment_score,
        taali_score=persisted_taali_score,
        score_breakdown=(
            {"score_components": {"role_fit_score": frozen_role_fit_score}}
            if frozen_role_fit_score is not None
            else None
        ),
        is_voided=False,
    )
    db.add(assessment)
    db.commit()
    return assessment


def test_global_reader_uses_one_latest_assessment_per_logical_membership(client, db):
    """Repeated attempts cannot duplicate either owner or related membership."""

    world = _world(client, db)
    shared = world["shared"]
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    now = datetime.now(timezone.utc)
    shared.assessment_score_cache_100 = 94
    # Current schemas reject duplicate active attempts. Keep the logical reader
    # defensive against legacy/imported rows created before that invariant was
    # enforced, because one such row must not corrupt global totals or paging.
    db.execute(text("DROP INDEX IF EXISTS uq_assessments_candidate_role_active"))
    db.add_all(
        [
            Assessment(
                organization_id=int(world["user"].organization_id),
                candidate_id=int(shared.candidate_id),
                role_id=role_id,
                application_id=int(shared.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=completed_at,
                assessment_score=score,
                is_voided=False,
            )
            for role_id, completed_at, score in (
                (owner_id, now - timedelta(days=1), 12),
                (owner_id, now, 94),
                (related_id, now - timedelta(days=1), 87),
                (related_id, now, 23),
            )
        ]
    )
    db.commit()

    page = read_global_candidate_page(
        db,
        organization_id=int(world["user"].organization_id),
        score_field="assessment_score_cache_100",
        sort_field="assessment_score_cache_100",
        sort_order="desc",
        min_score=None,
        pipeline_stage=None,
        application_outcome=None,
        q="shared-owner-and-related",
        limit=1,
        offset=0,
        limit_ceiling=100,
        prioritize_advanced=False,
    )
    second_page = read_global_candidate_page(
        db,
        organization_id=int(world["user"].organization_id),
        score_field="assessment_score_cache_100",
        sort_field="assessment_score_cache_100",
        sort_order="desc",
        min_score=None,
        pipeline_stage=None,
        application_outcome=None,
        q="shared-owner-and-related",
        limit=1,
        offset=1,
        limit_ceiling=100,
        prioritize_advanced=False,
    )

    assert page.total == second_page.total == 2
    assert page.logical_membership_ids + second_page.logical_membership_ids == (
        f"{owner_id}:{int(shared.id)}",
        f"{related_id}:{int(shared.id)}",
    )
    assert [
        application.assessment_score_cache_100
        for application in page.applications + second_page.applications
    ] == [94, 23]


def test_global_taali_qualitative_search_uses_independent_logical_memberships(
    client,
    db,
):
    """Unbound Chat preserves owner, related, and related-only role truth."""

    world = _world(client, db)
    _add_conflicting_assessment_truth(db, world)
    shared = world["shared"]
    shared.pipeline_stage = "applied"
    shared.cv_match_details = {
        "summary": "Owner-role evidence summary. Owner-role evidence detail."
    }
    shared_membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(world["related"].id),
            SisterRoleEvaluation.source_application_id == int(shared.id),
        )
        .one()
    )
    shared_membership.details = {
        "summary": "Related-role evidence summary. Related-role evidence detail."
    }
    db.commit()

    criterion = "Agentforce delivery"

    def local_runner(**_kwargs):
        return SearchOutput(
            application_ids=[],
            parsed_filter=ParsedFilter(
                soft_criteria=[criterion],
                free_text=criterion,
            ),
            warnings=[],
            database_matches=6,
            retrieval_matches=6,
            capped=False,
            exhaustive=True,
            is_exact_empty=False,
        )

    qualifying_keys = {
        (int(world["owner"].id), int(shared.id)),
        (int(world["related"].id), int(shared.id)),
        (int(world["related"].id), int(world["soft"].id)),
    }

    def local_ground(applications, *, criteria, **_kwargs):
        [requested] = criteria
        grounded = []
        for application in applications:
            key = (int(application.role_id), int(application.id))
            verdict = (
                CriterionVerdict(
                    requested,
                    status="met",
                    grounded=True,
                    source="cv_citation",
                    evidence=[
                        Evidence(
                            quote=f"Agentforce evidence for logical membership {key}",
                            source="synthetic_cv",
                        )
                    ],
                )
                if key in qualifying_keys
                else CriterionVerdict(requested, status="missing")
            )
            grounded.append((application, [verdict]))
        return grounded

    with (
        patch("app.candidate_search.runner.run_search", side_effect=local_runner),
        patch(
            "app.candidate_search.top_candidates._ground_window",
            side_effect=local_ground,
        ),
        patch(
            "app.mcp.handlers._attach_shareable_candidate_report",
            side_effect=lambda _db, _user, **kwargs: kwargs["snapshot"],
        ),
    ):
        result = taali_tools.dispatch_tool(
            "find_top_candidates",
            {"query": criterion, "limit": 10, "rank_by": "taali"},
            db=db,
            user=world["user"],
        )

    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    shared_id = int(shared.id)
    soft_id = int(world["soft"].id)
    by_membership = {row["logical_membership_id"]: row for row in result["candidates"]}
    assert result["pool_size"] == result["role_roster_size"] == 6
    assert set(by_membership) == {
        f"{owner_id}:{shared_id}",
        f"{related_id}:{shared_id}",
        f"{related_id}:{soft_id}",
    }
    owner_row = by_membership[f"{owner_id}:{shared_id}"]
    related_row = by_membership[f"{related_id}:{shared_id}"]
    assert owner_row["pipeline_stage"] == "applied"
    assert owner_row["taali_score"] == 96
    assert owner_row["candidate_headline"] == "Owner-role evidence summary."
    assert related_row["pipeline_stage"] == "review"
    assert related_row["assessment_score"] == 22
    assert related_row["taali_score"] == compute_taali_score(22, 41)
    assert related_row["pre_screen_score"] == 41
    assert related_row["candidate_headline"] == "Related-role evidence summary."
    assert by_membership[f"{related_id}:{soft_id}"]["role_id"] == related_id
    assert world["soft"].deleted_at is not None
    assert int(world["related_without_membership"].id) not in {
        int(row["application_id"]) for row in result["candidates"]
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


def test_related_role_pipeline_min_taali_score_uses_projected_blended_score(
    client, db
):
    world = _world(client, db)
    presented = world["direct"]
    transport = CandidateApplication(
        organization_id=int(world["user"].organization_id),
        candidate_id=int(presented.candidate_id),
        role_id=int(world["owner"].id),
        source="manual",
        status="applied",
        pipeline_stage="review",
        application_outcome="open",
    )
    db.add(transport)
    db.flush()
    membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(world["related"].id),
            SisterRoleEvaluation.source_application_id == int(presented.id),
        )
        .one()
    )
    membership.ats_application_id = int(transport.id)
    # The displayed one-decimal truth is 70.5. Comparing the raw 70.45
    # persisted float would incorrectly exclude this candidate at 70.5.
    _add_related_role_assessment(
        db,
        world,
        application=presented,
        assessment_score=99,
        persisted_taali_score=70.45,
        frozen_role_fit_score=1,
        assessment_application=transport,
    )

    response = client.get(
        f"/api/v1/roles/{int(world['related'].id)}/pipeline",
        params={
            "search": "direct-related-member",
            "min_taali_score": 70.5,
        },
        headers=world["headers"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["stage_counts"]["all"] == 1
    assert [item["id"] for item in payload["items"]] == [int(presented.id)]
    assert payload["items"][0]["taali_score"] == 70.5
    assert payload["items"][0]["valid_assessment_id"] is not None
    applications_response = client.get(
        f"/api/v1/roles/{int(world['related'].id)}/applications",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=world["headers"],
    )
    assert applications_response.status_code == 200, applications_response.text
    projected = {
        int(item["id"]): item for item in applications_response.json()
    }[int(presented.id)]
    assert projected["taali_score"] == 70.5
    assert projected["valid_assessment_id"] == payload["items"][0][
        "valid_assessment_id"
    ]

    excluded = client.get(
        f"/api/v1/roles/{int(world['related'].id)}/pipeline",
        params={
            "search": "direct-related-member",
            "min_taali_score": 70.6,
        },
        headers=world["headers"],
    )
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["total"] == 0


def test_related_role_application_lists_taali_sort_use_projected_blended_score(
    client, db
):
    world = _world(client, db)
    shared = world["shared"]
    soft = world["soft"]
    shared_assessment = _add_related_role_assessment(
        db,
        world,
        application=shared,
        assessment_score=100,
        # Persisted lifecycle truth deliberately conflicts with the legacy
        # assessment/frozen-fit recomputation (70.5).
        persisted_taali_score=12.3,
        frozen_role_fit_score=41,
    )
    soft_assessment = _add_related_role_assessment(
        db,
        world,
        application=soft,
        assessment_score=100,
        # Legacy rows have no persisted score. Their fallback is frozen on this
        # attempt: (100 + 40) / 2 = 70, not current evaluation fit 88.
        persisted_taali_score=None,
        frozen_role_fit_score=40,
    )

    applications_response = client.get(
        f"/api/v1/roles/{int(world['related'].id)}/applications",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=world["headers"],
    )
    pipeline_response = client.get(
        f"/api/v1/roles/{int(world['related'].id)}/pipeline",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=world["headers"],
    )

    expected_ids = [int(world["direct"].id), int(soft.id), int(shared.id)]
    expected_scores = [77, 70, 12.3]
    assert applications_response.status_code == 200, applications_response.text
    assert pipeline_response.status_code == 200, pipeline_response.text
    assert [item["id"] for item in applications_response.json()] == expected_ids
    assert [
        item["taali_score"] for item in applications_response.json()
    ] == expected_scores
    assert [item["id"] for item in pipeline_response.json()["items"]] == expected_ids
    assert [
        item["taali_score"] for item in pipeline_response.json()["items"]
    ] == expected_scores

    # Stable offset pages need a unique final key after equal score + timestamp
    # ties. Exercise both role endpoints, not just their unpaginated first page.
    shared_assessment.taali_score = 77
    soft_assessment.taali_score = 77
    tied_at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(world["related"].id))
        .all()
    )
    for evaluation in evaluations:
        evaluation.role_fit_score = 77
        evaluation.created_at = tied_at
    db.commit()

    expected_tied_ids = sorted(
        (
            int(world["shared"].id),
            int(world["soft"].id),
            int(world["direct"].id),
        ),
        reverse=True,
    )
    application_page_ids = []
    pipeline_page_ids = []
    for offset in range(3):
        application_page = client.get(
            f"/api/v1/roles/{int(world['related'].id)}/applications",
            params={
                "sort_by": "taali_score",
                "sort_order": "desc",
                "limit": 1,
                "offset": offset,
            },
            headers=world["headers"],
        )
        pipeline_page = client.get(
            f"/api/v1/roles/{int(world['related'].id)}/pipeline",
            params={
                "sort_by": "taali_score",
                "sort_order": "desc",
                "limit": 1,
                "offset": offset,
            },
            headers=world["headers"],
        )
        assert application_page.status_code == 200, application_page.text
        assert pipeline_page.status_code == 200, pipeline_page.text
        application_page_ids.append(application_page.json()[0]["id"])
        pipeline_page_ids.append(pipeline_page.json()["items"][0]["id"])
    assert application_page_ids == expected_tied_ids
    assert pipeline_page_ids == expected_tied_ids


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
        item for item in payload["items"] if int(item["id"]) == int(world["shared"].id)
    ]
    assert len(shared_rows) == 2
    by_role = {int(item["logical_role_id"]): item for item in shared_rows}
    assert by_role[owner_id]["pipeline_stage"] == "advanced"
    assert by_role[owner_id]["taali_score"] == 96
    assert by_role[related_id]["pipeline_stage"] == "review"
    assert by_role[related_id]["taali_score"] == 41


def test_ordinary_runtime_excludes_related_assessment_on_shared_transport(
    client, db
):
    world = _world(client, db)
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    shared = world["shared"]
    related_assessment = _add_related_role_assessment(
        db,
        world,
        application=shared,
        assessment_score=91,
        persisted_taali_score=86,
        frozen_role_fit_score=81,
        # The related assessment deliberately shares the ordinary owner's
        # physical application as its transport metadata.
        assessment_application=shared,
    )

    global_response = client.get(
        "/api/v1/applications",
        params={
            "role_ids": f"{owner_id},{related_id}",
            "search": "shared-owner-and-related",
            "limit": 20,
        },
        headers=world["headers"],
    )
    owner_response = client.get(
        f"/api/v1/roles/{owner_id}/applications",
        params={"search": "shared-owner-and-related"},
        headers=world["headers"],
    )

    assert global_response.status_code == 200, global_response.text
    shared_rows = {
        int(item["logical_role_id"]): item
        for item in global_response.json()["items"]
        if int(item["id"]) == int(shared.id)
    }
    assert shared_rows[owner_id]["score_summary"]["assessment_id"] is None
    assert shared_rows[owner_id]["score_summary"]["assessment_status"] is None
    assert (
        shared_rows[related_id]["score_summary"]["assessment_id"]
        == int(related_assessment.id)
    )
    assert shared_rows[related_id]["taali_score"] == 86

    assert owner_response.status_code == 200, owner_response.text
    owner_row = next(
        item
        for item in owner_response.json()
        if int(item["id"]) == int(shared.id)
    )
    assert owner_row["score_summary"]["assessment_id"] is None
    assert owner_row["score_summary"]["assessment_status"] is None


def test_global_taali_sort_keeps_incomplete_assessment_unavailable(
    client, db
):
    world = _world(client, db)
    related_id = int(world["related"].id)
    shared = world["shared"]
    evaluations = {
        int(evaluation.source_application_id): evaluation
        for evaluation in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == related_id)
        .all()
    }
    evaluations[int(shared.id)].role_fit_score = 99
    evaluations[int(world["direct"].id)].role_fit_score = 80
    evaluations[int(world["soft"].id)].role_fit_score = 20
    partial = _add_related_role_assessment(
        db,
        world,
        application=shared,
        assessment_score=99,
        persisted_taali_score=99,
        frozen_role_fit_score=99,
    )
    partial.scoring_partial = True
    db.commit()

    global_response = client.get(
        "/api/v1/applications",
        params={
            "role_id": related_id,
            "sort_by": "taali_score",
            "sort_order": "desc",
            "limit": 20,
        },
        headers=world["headers"],
    )
    role_response = client.get(
        f"/api/v1/roles/{related_id}/applications",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=world["headers"],
    )

    assert global_response.status_code == 200, global_response.text
    assert role_response.status_code == 200, role_response.text
    expected_ids = [
        int(world["direct"].id),
        int(world["soft"].id),
        int(shared.id),
    ]
    assert [
        int(item["id"]) for item in global_response.json()["items"]
    ] == expected_ids
    assert [int(item["id"]) for item in role_response.json()] == expected_ids
    assert global_response.json()["items"][-1]["taali_score"] is None
    assert (
        global_response.json()["items"][-1]["score_mode"]
        == "rubric_grading_pending"
    )


def test_mixed_membership_pagination_uses_logical_role_as_final_tie_breaker(
    client, db
):
    world = _world(client, db)
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    shared = world["shared"]
    tied_at = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    shared.taali_score_cache_100 = 50
    shared.pre_screen_score_100 = 50
    shared.created_at = tied_at
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == related_id,
            SisterRoleEvaluation.source_application_id == int(shared.id),
        )
        .one()
    )
    evaluation.role_fit_score = 50
    evaluation.created_at = tied_at
    db.commit()

    def paged_role_ids(sort_order: str) -> list[int]:
        role_ids: list[int] = []
        for offset in range(2):
            response = client.get(
                "/api/v1/applications",
                params={
                    "role_ids": f"{owner_id},{related_id}",
                    "search": "shared-owner-and-related",
                    "sort_by": "taali_score",
                    "sort_order": sort_order,
                    "limit": 1,
                    "offset": offset,
                },
                headers=world["headers"],
            )
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 2
            [item] = response.json()["items"]
            role_ids.append(int(item["logical_role_id"]))
        return role_ids

    assert paged_role_ids("desc") == [related_id, owner_id]
    assert paged_role_ids("asc") == [owner_id, related_id]


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
    assert {item["logical_membership_id"] for item in payload["items"]} == {
        f"{owner_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['soft'].id)}",
        f"{related_id}:{int(world['direct'].id)}",
    }


def test_global_search_applications_matches_logical_membership_truth_in_mcp_and_taali(
    client,
    db,
):
    """Both global agent surfaces page the same independent role memberships."""

    world = _world(client, db)
    _add_conflicting_assessment_truth(db, world)
    user = world["user"]
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    unrelated_role_id = int(world["unrelated"].role_id)
    page_size = 2

    @contextmanager
    def borrowed_session(_ctx, _scopes):
        yield db, user

    def collect_pages(call):
        rows: list[dict] = []
        page_lengths: list[int] = []
        offset = 0
        while True:
            page = call(
                {
                    "application_outcome": "open",
                    "sort_by": "created_at",
                    "sort_order": "desc",
                    "limit": page_size,
                    "offset": offset,
                }
            )
            page_lengths.append(len(page))
            rows.extend(page)
            if len(page) < page_size:
                return rows, page_lengths
            offset += len(page)

    with patch.object(mcp_server, "_open_session", borrowed_session):
        mcp_rows, mcp_page_lengths = collect_pages(
            lambda args: mcp_server.search_applications(object(), **args)
        )
    taali_rows, taali_page_lengths = collect_pages(
        lambda args: taali_tools.dispatch_tool(
            "search_applications",
            args,
            db=db,
            user=user,
        )
    )

    expected_memberships = {
        f"{owner_id}:{int(world['shared'].id)}",
        f"{owner_id}:{int(world['owner_only'].id)}",
        f"{unrelated_role_id}:{int(world['unrelated'].id)}",
        f"{related_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['soft'].id)}",
        f"{related_id}:{int(world['direct'].id)}",
    }
    for rows, page_lengths in (
        (mcp_rows, mcp_page_lengths),
        (taali_rows, taali_page_lengths),
    ):
        assert page_lengths == [2, 2, 2, 0]
        assert len(rows) == len(expected_memberships)
        assert {row["logical_membership_id"] for row in rows} == (expected_memberships)
        assert len({row["logical_membership_id"] for row in rows}) == len(rows)
        assert int(world["related_without_membership"].id) not in {
            int(row["application_id"]) for row in rows
        }

        shared_rows = [
            row for row in rows if int(row["application_id"]) == int(world["shared"].id)
        ]
        assert len(shared_rows) == 2
        shared_by_role = {int(row["role_id"]): row for row in shared_rows}
        assert shared_by_role[owner_id]["pipeline_stage"] == "advanced"
        assert shared_by_role[owner_id]["taali_score"] == 96
        assert shared_by_role[related_id]["pipeline_stage"] == "review"
        assert shared_by_role[related_id]["assessment_score"] == 22
        assert shared_by_role[related_id]["taali_score"] == compute_taali_score(
            22,
            41,
        )
        assert shared_by_role[related_id]["pre_screen_score"] == 41
        assert shared_by_role[related_id]["current_state"]["pipeline_stage"] == (
            "review"
        )

    assert [row["logical_membership_id"] for row in mcp_rows] == [
        row["logical_membership_id"] for row in taali_rows
    ]

    assessment_args = {
        "application_outcome": "open",
        "score_type": "assessment",
        "min_score": 70,
        "sort_by": "assessment_score",
        "sort_order": "desc",
        "limit": 25,
        "offset": 0,
    }
    with patch.object(mcp_server, "_open_session", borrowed_session):
        mcp_assessment_rows = mcp_server.search_applications(
            object(),
            **assessment_args,
        )
    taali_assessment_rows = taali_tools.dispatch_tool(
        "search_applications",
        assessment_args,
        db=db,
        user=user,
    )
    expected_assessment_memberships = [
        f"{owner_id}:{int(world['shared'].id)}",
        f"{related_id}:{int(world['soft'].id)}",
        f"{related_id}:{int(world['direct'].id)}",
    ]
    for rows in (mcp_assessment_rows, taali_assessment_rows):
        assert [row["logical_membership_id"] for row in rows] == (
            expected_assessment_memberships
        )
        assert [row["assessment_score"] for row in rows] == [99, 88, 77]
        assert f"{related_id}:{int(world['shared'].id)}" not in {
            row["logical_membership_id"] for row in rows
        }


def test_global_search_removes_only_deleted_related_membership(
    client,
    db,
):
    """Membership deletion never resurrects related storage or erases owners."""

    world = _world(client, db)
    user = world["user"]
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)

    shared_membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == related_id,
            SisterRoleEvaluation.source_application_id == int(world["shared"].id),
        )
        .one()
    )
    shared_membership.deleted_at = datetime.now(timezone.utc)
    db.commit()

    shared_rows = taali_tools.dispatch_tool(
        "search_applications",
        {"q": "shared-owner-and-related", "limit": 25},
        db=db,
        user=user,
    )
    assert [row["logical_membership_id"] for row in shared_rows] == [
        f"{owner_id}:{int(world['shared'].id)}"
    ]
    assert shared_rows[0]["pipeline_stage"] == "advanced"
    assert shared_rows[0]["taali_score"] == 96

    soft_membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == related_id,
            SisterRoleEvaluation.source_application_id == int(world["soft"].id),
        )
        .one()
    )
    soft_membership.deleted_at = datetime.now(timezone.utc)
    db.commit()

    assert (
        taali_tools.dispatch_tool(
            "search_applications",
            {"q": "soft-deleted-related-evidence", "limit": 25},
            db=db,
            user=user,
        )
        == []
    )


def test_deleted_people_are_absent_from_all_canonical_role_and_global_reads(
    client,
    db,
):
    """Person erasure overrides both ordinary and related live memberships.

    A deleted application may remain evidence for a related role, but a
    deleted ``Candidate`` may not appear in any candidate roster, count, text
    search, or serialized response. The same shared readers back REST, public
    API, MCP, Taali Chat, and Agent Chat, so this single oracle checks their
    parity without provider calls.
    """

    world = _world(client, db)
    user = world["user"]
    owner = world["owner"]
    related = world["related"]
    ordinary_deleted = world["owner_only"]
    related_deleted = world["direct"]
    deleted_names = {
        str(ordinary_deleted.candidate.full_name),
        str(related_deleted.candidate.full_name),
    }
    deleted_emails = {
        str(ordinary_deleted.candidate.email),
        str(related_deleted.candidate.email),
    }
    ordinary_deleted.candidate.deleted_at = datetime.now(timezone.utc)
    related_deleted.candidate.deleted_at = datetime.now(timezone.utc)
    db.commit()

    owner_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(owner.id),
    )
    related_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(related.id),
    )
    assert owner_scope.roster_size(db) == 1
    assert related_scope.roster_size(db) == 2

    global_page = read_global_candidate_page(
        db,
        organization_id=int(user.organization_id),
        score_field="taali_score_cache_100",
        sort_field="created_at",
        sort_order="desc",
        min_score=None,
        pipeline_stage=None,
        application_outcome=None,
        q=None,
        limit=100,
        offset=0,
        limit_ceiling=100,
        prioritize_advanced=False,
    )
    assert global_page.total == 4
    assert {
        int(application.id) for application in global_page.applications
    }.isdisjoint({int(ordinary_deleted.id), int(related_deleted.id)})
    # A live person with a soft-deleted evidence application remains a valid
    # related-role member; only person deletion is absolute.
    assert (
        f"{int(related.id)}:{int(world['soft'].id)}"
        in global_page.logical_membership_ids
    )

    shared_rest = client.get(
        "/api/v1/applications",
        params={
            "role_ids": f"{int(owner.id)},{int(related.id)}",
            "application_outcome": "all",
            "limit": 50,
        },
        headers=world["headers"],
    )
    assert shared_rest.status_code == 200, shared_rest.text
    assert shared_rest.json()["total"] == 3
    assert {
        int(item["id"]) for item in shared_rest.json()["items"]
    }.isdisjoint({int(ordinary_deleted.id), int(related_deleted.id)})

    listed_roles = {
        int(item["role_id"]): item
        for item in mcp_handlers.list_roles(
            db,
            user,
            include_stage_counts=True,
        )
    }
    owner_detail = mcp_handlers.get_role(db, user, role_id=int(owner.id))
    related_detail = mcp_handlers.get_role(db, user, role_id=int(related.id))
    for payload, expected_total in (
        (listed_roles[int(owner.id)], 1),
        (owner_detail, 1),
        (listed_roles[int(related.id)], 2),
        (related_detail, 2),
    ):
        assert payload["applications_count"] == expected_total
    assert owner_detail["stage_counts"]["advanced"] == 1
    assert related_detail["stage_counts"]["review"] == 1
    assert related_detail["stage_counts"]["applied"] == 1

    for role, expected_total in ((owner, 1), (related, 2)):
        recruiting_overview = get_recruiting_overview(
            db,
            user,
            role_id=int(role.id),
        )
        assert recruiting_overview["applications"]["total"] == expected_total
        assert recruiting_overview["candidates"]["total"] == expected_total

        public_metrics = public_role_metrics(
            role_id=int(role.id),
            principal=type(
                "PublicMetricsPrincipal",
                (),
                {"organization_id": int(user.organization_id)},
            )(),
            db=db,
        )
        assert public_metrics.total_applications == expected_total

    jobs = client.get(
        "/api/v1/roles",
        params={"include_pipeline_stats": True, "limit": 500},
        headers=world["headers"],
    )
    assert jobs.status_code == 200, jobs.text
    jobs_by_id = {int(row["id"]): row for row in jobs.json()}
    assert jobs_by_id[int(owner.id)]["applications_count"] == 1
    assert jobs_by_id[int(owner.id)]["active_candidates_count"] == 1
    assert jobs_by_id[int(related.id)]["applications_count"] == 2
    assert jobs_by_id[int(related.id)]["active_candidates_count"] == 2

    for role, expected_total in ((owner, 1), (related, 2)):
        role_detail = client.get(
            f"/api/v1/roles/{int(role.id)}",
            headers=world["headers"],
        )
        assert role_detail.status_code == 200, role_detail.text
        assert role_detail.json()["applications_count"] == expected_total

    hub = client.get(
        "/api/v1/agent/roles/breakdown",
        headers=world["headers"],
    )
    assert hub.status_code == 200, hub.text
    hub_by_role = {int(row["role_id"]): row for row in hub.json()}
    assert hub_by_role[int(owner.id)]["stage_counts"]["advanced"] == 1
    assert hub_by_role[int(related.id)]["stage_counts"]["completed"] == 1
    assert hub_by_role[int(related.id)]["stage_counts"]["scored"] == 1

    owner_overview = dispatch_agent_chat_tool(
        "get_role_overview",
        {},
        db=db,
        role=owner,
        user=user,
    )
    related_overview = dispatch_agent_chat_tool(
        "get_role_overview",
        {},
        db=db,
        role=related,
        user=user,
    )
    assert owner_overview["open_candidates"] == 1
    assert related_overview["open_candidates"] == 2

    for application, role in (
        (ordinary_deleted, owner),
        (related_deleted, related),
    ):
        pii_markers = {
            str(application.candidate.full_name),
            str(application.candidate.email),
        }
        searched_rest = client.get(
            "/api/v1/applications",
            params={
                "role_id": int(role.id),
                "search": str(application.candidate.email),
                "application_outcome": "all",
            },
            headers=world["headers"],
        )
        assert searched_rest.status_code == 200, searched_rest.text
        assert searched_rest.json()["total"] == 0
        assert searched_rest.json()["items"] == []
        assert all(marker not in searched_rest.text for marker in pii_markers)

        detail_rest = client.get(
            f"/api/v1/applications/{int(application.id)}",
            params={"view_role_id": int(role.id)},
            headers=world["headers"],
        )
        assert detail_rest.status_code == 404
        assert all(marker not in detail_rest.text for marker in pii_markers)

        mcp_role = mcp_handlers.search_role_candidates(
            db,
            user,
            role_id=int(role.id),
            q=str(application.candidate.email),
            application_outcome=None,
            limit=25,
        )
        assert mcp_role["total"] == 0
        assert mcp_role["items"] == []
        assert all(marker not in str(mcp_role["items"]) for marker in pii_markers)
        with pytest.raises(ValueError):
            mcp_handlers.get_role_candidate(
                db,
                user,
                role_id=int(role.id),
                application_id=int(application.id),
                include_cv_text=True,
            )
        with pytest.raises(ValueError):
            mcp_handlers.get_application(
                db,
                user,
                application_id=int(application.id),
                include_cv_text=True,
            )
        with pytest.raises(ValueError):
            mcp_handlers.get_candidate(
                db,
                user,
                candidate_id=int(application.candidate_id),
            )
        compared = mcp_handlers.compare_applications(
            db,
            user,
            application_ids=[int(application.id), int(world["shared"].id)],
        )
        assert int(application.id) in compared["missing_ids"]
        assert all(
            marker not in str(compared["applications"]) for marker in pii_markers
        )

        agent_role = dispatch_agent_chat_tool(
            "search_role_candidates",
            {
                "q": str(application.candidate.email),
                "application_outcome": None,
                "limit": 25,
            },
            db=db,
            role=role,
            user=user,
        )
        assert agent_role["total"] == 0
        assert agent_role["items"] == []
        assert all(marker not in str(agent_role["items"]) for marker in pii_markers)

        with pytest.raises(LogicalRoleApplicationAuthorizationError):
            authorize_logical_role_application(
                db,
                role=role,
                application_id=int(application.id),
            )

    for deleted_marker in deleted_names | deleted_emails:
        taali_rows = taali_tools.dispatch_tool(
            "search_applications",
            {
                "q": deleted_marker,
                "application_outcome": None,
                "limit": 25,
            },
            db=db,
            user=user,
        )
        assert taali_rows == []

    public_principal = type(
        "PublicPrincipal",
        (),
        {"organization_id": int(user.organization_id)},
    )()
    owner_public = list_public_role_applications(
        role_id=int(owner.id),
        principal=public_principal,
        db=db,
        limit=50,
        offset=0,
        workable_stage=None,
        pipeline_stage=None,
    )
    related_public = list_public_role_applications(
        role_id=int(related.id),
        principal=public_principal,
        db=db,
        limit=50,
        offset=0,
        workable_stage=None,
        pipeline_stage=None,
    )
    assert owner_public.total == 1
    assert related_public.total == 2
    public_text = owner_public.model_dump_json() + related_public.model_dump_json()
    assert all(marker not in public_text for marker in deleted_names | deleted_emails)


def test_public_mcp_role_detail_compare_and_resource_follow_membership_truth(
    client,
    db,
):
    """Only role-aware reads certify state; removal affects only that role."""

    world = _world(client, db)
    headers = world["headers"]
    owner_id = int(world["owner"].id)
    related_id = int(world["related"].id)
    shared_id = int(world["shared"].id)
    soft_id = int(world["soft"].id)

    related_detail = _mcp_tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "get_role_candidate",
                "arguments": {
                    "role_id": related_id,
                    "application_id": shared_id,
                },
            },
        )
    )
    assert related_detail["role_id"] == related_id
    assert related_detail["taali_score"] == 41
    assert related_detail["current_state"]["pipeline_stage"] == "review"

    owner_detail = _mcp_tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "get_role_candidate",
                "arguments": {
                    "role_id": owner_id,
                    "application_id": shared_id,
                },
            },
        )
    )
    assert owner_detail["role_id"] == owner_id
    assert owner_detail["taali_score"] == 96
    assert owner_detail["current_state"]["pipeline_stage"] == "advanced"

    comparison = _mcp_tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "compare_role_applications",
                "arguments": {
                    "role_id": related_id,
                    "application_ids": [shared_id, soft_id],
                },
            },
        )
    )
    assert comparison["role"]["id"] == related_id
    assert [row["scores"]["taali"] for row in comparison["applications"]] == [
        41,
        88,
    ]
    assert [
        row["current_state"]["pipeline_stage"] for row in comparison["applications"]
    ] == ["review", "applied"]

    legacy = _mcp_tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "get_application",
                "arguments": {"application_id": shared_id},
            },
        )
    )
    assert legacy["record_scope"] == "physical_application_evidence_only"
    assert legacy["logical_role_state_included"] is False
    assert "role_id" not in legacy
    assert "pipeline_stage" not in legacy
    assert "taali_score" not in legacy

    role_resource = _mcp_call(
        client,
        headers,
        "resources/read",
        {"uri": f"tali://role/{related_id}/application/{shared_id}"},
    )["result"]["contents"][0]["text"]
    assert world["related"].name in role_resource
    assert "Current stage `review`" in role_resource
    assert "taali: 41" in role_resource

    membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == related_id,
            SisterRoleEvaluation.source_application_id == shared_id,
        )
        .one()
    )
    membership.deleted_at = datetime.now(timezone.utc)
    db.commit()

    removed_detail = _mcp_call(
        client,
        headers,
        "tools/call",
        {
            "name": "get_role_candidate",
            "arguments": {
                "role_id": related_id,
                "application_id": shared_id,
            },
        },
    )
    assert removed_detail["result"]["isError"] is True

    removed_comparison = _mcp_tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "compare_role_applications",
                "arguments": {
                    "role_id": related_id,
                    "application_ids": [shared_id, soft_id],
                },
            },
        )
    )
    assert [
        int(row["application_id"]) for row in removed_comparison["applications"]
    ] == [soft_id]
    assert removed_comparison["missing_ids"] == [shared_id]

    removed_resource = _mcp_call(
        client,
        headers,
        "resources/read",
        {"uri": f"tali://role/{related_id}/application/{shared_id}"},
    )
    assert "error" in removed_resource

    # Removing the related membership cannot erase or mutate the ordinary
    # owner's independent application.
    owner_after = _mcp_tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "get_role_candidate",
                "arguments": {
                    "role_id": owner_id,
                    "application_id": shared_id,
                },
            },
        )
    )
    assert owner_after["current_state"]["pipeline_stage"] == "advanced"
    assert owner_after["taali_score"] == 96
