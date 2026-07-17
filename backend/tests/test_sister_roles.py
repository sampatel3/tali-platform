from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event, inspect as sa_inspect

from app.domains.assessments_runtime.role_support import (
    role_family_load_options,
    role_family_response,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.job_hiring_team import TEAM_ROLE_HIRING_MANAGER, JobHiringTeam
from app.models.job_page import JobPage
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.role_brief import BRIEF_STATUS_APPLIED, RoleBrief
from app.models.role_criterion import RoleCriterion
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.sister_role_service import related_role_advance_note
from app.services.related_role_service import related_role_roster_counts
from app.services.sister_role_service import ensure_sister_evaluations
from tests.conftest import auth_headers


def _related_role_authorization(preview: dict, *, monthly_cap: int | None = None):
    return {
        "expected_source_role_id": preview["source_role_id"],
        "expected_source_role_name": preview["source_role_name"],
        "expected_source_role_version": preview["source_role_version"],
        "expected_default_monthly_budget_cents": preview[
            "proposed_monthly_budget_cents"
        ],
        "approved_max_candidates_total": preview["candidates_total"],
        "approved_max_scoreable_count": preview["candidates_scoreable"],
        "approved_monthly_budget_cents": monthly_cap
        or preview["proposed_monthly_budget_cents"],
    }


def test_related_role_advance_note_names_both_role_references():
    owner = SimpleNamespace(id=31, name="Data Platform Lead")
    related = SimpleNamespace(id=47, name="AI Engineer")

    note = related_role_advance_note(related, owner)

    assert "AI Engineer #47" in note
    assert "Data Platform Lead #31" in note


def _source_role_with_candidates(db, *, organization_id: int) -> tuple[Role, list[CandidateApplication]]:
    role = Role(
        organization_id=organization_id,
        name="AI Engineer",
        source="workable",
        workable_job_id="AI-ENG",
        workable_job_data={"state": "published"},
        job_spec_text="Original AI engineer role specification with Python and production ML systems.",
    )
    db.add(role)
    db.flush()
    applications = []
    for index, (has_cv, outcome, score) in enumerate(
        [(True, "open", 72.0), (False, "rejected", 41.0)]
    ):
        candidate = Candidate(
            organization_id=organization_id,
            email=f"candidate-{index}@example.com",
            full_name=f"Candidate {index}",
            cv_text="Python ML engineer with deployed LLM systems." if has_cv else None,
        )
        db.add(candidate)
        db.flush()
        application = CandidateApplication(
            organization_id=organization_id,
            candidate_id=candidate.id,
            role_id=role.id,
            source="workable",
            workable_candidate_id=f"workable-{index}",
            workable_stage="Sourced",
            application_outcome=outcome,
            cv_text=candidate.cv_text,
            taali_score_cache_100=score,
            role_fit_score_cache_100=score,
            score_mode_cache="role_fit_only",
        )
        db.add(application)
        applications.append(application)
    db.commit()
    return role, applications


def _scorable_evaluation(db, *, organization_id: int):
    source, applications = _source_role_with_candidates(
        db, organization_id=organization_id
    )
    source.agentic_mode_enabled = True
    source.monthly_usd_budget_cents = 5000
    sister = Role(
        organization_id=organization_id,
        name="AI Engineer · Durable evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text=(
            "A detailed related-role specification requiring production Python, "
            "RAG evaluation, distributed systems, and operational reliability."
        ),
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_skip_assessment=True,
    )
    db.add(sister)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=organization_id,
        role_id=sister.id,
        source_application_id=applications[0].id,
        status="pending",
        spec_fingerprint="initial",
    )
    db.add(evaluation)
    db.commit()
    return source, evaluation


def _match_output(*, ok: bool):
    return SimpleNamespace(
        scoring_status=SimpleNamespace(value="ok" if ok else "failed"),
        role_fit_score=88.0 if ok else None,
        summary="Strong production evidence." if ok else None,
        error_reason=None if ok else "claude_call_failed: temporary upstream outage",
        model_version="test-model",
        prompt_version="test-prompt",
        trace_id="trace-durable",
        cache_hit=False,
        model_dump=lambda **_: {"role_fit_score": 88.0},
    )


def test_role_list_and_detail_include_complete_named_role_family(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source = Role(
        organization_id=user.organization_id,
        name="Platform Engineer",
        source="workable",
        workable_job_id="PLATFORM-ENG",
        workable_job_data={"state": "published"},
    )
    db.add(source)
    db.flush()
    related_z = Role(
        organization_id=user.organization_id,
        name="Platform Engineer · Zero Trust",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
    )
    related_a = Role(
        organization_id=user.organization_id,
        name="Platform Engineer · API",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
    )
    db.add_all([related_z, related_a])
    db.commit()

    expected_family = {
        "owner": {"id": source.id, "name": source.name},
        "related": [
            {"id": related_a.id, "name": related_a.name},
            {"id": related_z.id, "name": related_z.name},
        ],
    }

    listing = client.get(
        "/api/v1/roles",
        params={"sort_by": "name"},
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    listed_by_id = {row["id"]: row for row in listing.json()}
    assert listed_by_id[source.id]["role_family"] == expected_family
    assert listed_by_id[related_a.id]["role_family"] == expected_family
    assert listed_by_id[related_z.id]["role_family"] == expected_family

    source_detail = client.get(
        f"/api/v1/roles/{source.id}", params={"shell": True}, headers=headers
    )
    related_detail = client.get(
        f"/api/v1/roles/{related_z.id}", params={"shell": True}, headers=headers
    )
    assert source_detail.status_code == 200, source_detail.text
    assert related_detail.status_code == 200, related_detail.text
    assert source_detail.json()["role_family"] == expected_family
    assert related_detail.json()["role_family"] == expected_family


def test_role_family_responses_exclude_cross_organization_links(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    other_org = Organization(name="Other tenant family org")
    db.add(other_org)
    db.flush()

    local_owner = Role(
        organization_id=user.organization_id,
        name="Local owner",
        source="workable",
    )
    foreign_owner = Role(
        organization_id=other_org.id,
        name="Foreign private owner",
        source="workable",
    )
    db.add_all([local_owner, foreign_owner])
    db.flush()
    foreign_related = Role(
        organization_id=other_org.id,
        name="Foreign private related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=local_owner.id,
    )
    local_malformed_related = Role(
        organization_id=user.organization_id,
        name="Local malformed related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=foreign_owner.id,
    )
    db.add_all([foreign_related, local_malformed_related])
    db.commit()

    owner_response = client.get(
        f"/api/v1/roles/{local_owner.id}", params={"shell": True}, headers=headers
    )
    related_response = client.get(
        f"/api/v1/roles/{local_malformed_related.id}",
        params={"shell": True},
        headers=headers,
    )

    assert owner_response.status_code == 200, owner_response.text
    assert owner_response.json()["role_family"] == {
        "owner": {"id": local_owner.id, "name": local_owner.name},
        "related": [],
    }
    assert "Foreign private related role" not in owner_response.text

    assert related_response.status_code == 200, related_response.text
    related_payload = related_response.json()
    assert related_payload["ats_owner_role_name"] is None
    assert related_payload["role_family"] == {
        "owner": {
            "id": local_malformed_related.id,
            "name": local_malformed_related.name,
        },
        "related": [],
    }
    assert "Foreign private owner" not in related_response.text


def test_role_family_serializer_filters_foreign_siblings_without_scoped_loader():
    owner = Role(
        id=91_001,
        organization_id=801,
        name="Local transient owner",
        source="workable",
    )
    foreign_related = Role(
        id=91_002,
        organization_id=802,
        name="Foreign transient related",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
    )
    owner.sister_roles.append(foreign_related)

    assert role_family_response(owner).model_dump() == {
        "owner": {"id": owner.id, "name": owner.name},
        "related": [],
    }


def test_role_family_loader_keeps_sibling_job_specs_deferred(db):
    org = Organization(name="Lightweight family loader org")
    db.add(org)
    db.flush()
    owner = Role(
        organization_id=org.id,
        name="Reference-only owner",
        source="workable",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org.id,
        name="Reference-only related",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_spec_text="Large related-role job specification " * 100,
    )
    db.add(related)
    db.commit()
    owner_id = int(owner.id)
    org_id = int(org.id)
    db.expunge_all()

    loaded_owner = (
        db.query(Role)
        .options(*role_family_load_options(organization_id=org_id))
        .filter(Role.id == owner_id)
        .one()
    )
    loaded_related = loaded_owner.sister_roles[0]
    unloaded = sa_inspect(loaded_related).unloaded

    assert loaded_related.name == "Reference-only related"
    assert "job_spec_text" in unloaded
    assert "screening_pack_template" in unloaded


def test_create_sister_role_persists_separate_scores_and_projects_source_roster(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(
        db, organization_id=user.organization_id
    )
    updated_spec = (
        "Updated AI engineer role requiring production RAG, evaluation design, "
        "Python, distributed systems, and ownership of model observability."
    )
    preview = client.get(
        f"/api/v1/roles/{source.id}/sisters/preview", headers=headers
    ).json()

    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{source.id}/sisters",
            json={
                "name": "AI Engineer · RAG",
                "job_spec_text": updated_spec,
                "related_role_authorization": _related_role_authorization(preview),
            },
            headers=headers,
        )

    assert response.status_code == 201, response.text
    body = response.json()
    sister = body["role"]
    assert sister["role_kind"] == ROLE_KIND_SISTER
    assert sister["ats_owner_role_id"] == source.id
    assert sister["ats_owner_role_name"] == source.name
    assert sister["workable_job_id"] is None
    assert sister["effective_workable_job_id"] == source.workable_job_id
    assert sister["applications_count"] == 2
    assert sister["agentic_mode_enabled"] is True
    assert sister["monthly_usd_budget_cents"] > 0
    assert body["evaluation_counts"] == {
        "total": 2,
        "pending": 1,
        "unscorable": 0,
        "excluded": 1,
    }
    dispatch.assert_called_once()
    fallback_membership = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.role_id == sister["id"],
            JobHiringTeam.user_id == user.id,
        )
        .one()
    )
    assert fallback_membership.team_role == TEAM_ROLE_HIRING_MANAGER

    source_detail = client.get(f"/api/v1/roles/{source.id}", headers=headers)
    assert source_detail.status_code == 200
    assert source_detail.json()["sister_role_count"] == 1

    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == sister["id"])
        .order_by(SisterRoleEvaluation.source_application_id)
        .all()
    )
    assert [item.status for item in evaluations] == ["pending", "excluded"]

    open_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "open"},
        headers=headers,
    )
    assert open_rows.status_code == 200, open_rows.text
    row = open_rows.json()[0]
    assert row["id"] == applications[0].id
    assert row["role_id"] == sister["id"]
    assert row["operational_role_id"] == source.id
    assert row["source_role_score"] == 72.0
    assert row["taali_score"] is None
    assert row["score_status"] == "pending"

    evaluations[0].status = "retry_wait"
    evaluations[0].last_error_code = "authority_blocked"
    db.commit()
    waiting_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "open"},
        headers=headers,
    )
    assert waiting_rows.status_code == 200, waiting_rows.text
    assert waiting_rows.json()[0]["score_status"] == "retry_wait"

    rejected_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "rejected"},
        headers=headers,
    )
    assert rejected_rows.status_code == 200
    assert rejected_rows.json()[0]["id"] == applications[1].id
    assert rejected_rows.json()[0]["score_status"] == "excluded"


def test_related_role_preview_and_evaluation_rosters_share_validity_rules(db):
    organization = Organization(name="Roster parity", slug=f"roster-parity-{id(db)}")
    db.add(organization)
    db.flush()
    source, _ = _source_role_with_candidates(
        db, organization_id=int(organization.id)
    )
    no_cv = Candidate(
        organization_id=int(organization.id),
        email="no-cv-parity@example.com",
        full_name="No CV",
    )
    deleted_candidate = Candidate(
        organization_id=int(organization.id),
        email="deleted-candidate-parity@example.com",
        full_name="Deleted Candidate",
        cv_text="This deleted candidate must never enter paid scope.",
        deleted_at=datetime.now(timezone.utc),
    )
    deleted_application_candidate = Candidate(
        organization_id=int(organization.id),
        email="deleted-application-parity@example.com",
        full_name="Deleted Application",
        cv_text="This deleted application must never enter paid scope.",
    )
    db.add_all([no_cv, deleted_candidate, deleted_application_candidate])
    db.flush()
    db.add_all(
        [
            CandidateApplication(
                organization_id=int(organization.id),
                candidate_id=int(no_cv.id),
                role_id=int(source.id),
                source="workable",
                application_outcome="open",
            ),
            CandidateApplication(
                organization_id=int(organization.id),
                candidate_id=int(deleted_candidate.id),
                role_id=int(source.id),
                source="workable",
                application_outcome="open",
                cv_text=deleted_candidate.cv_text,
            ),
            CandidateApplication(
                organization_id=int(organization.id),
                candidate_id=int(deleted_application_candidate.id),
                role_id=int(source.id),
                source="workable",
                application_outcome="open",
                cv_text=deleted_application_candidate.cv_text,
                deleted_at=datetime.now(timezone.utc),
            ),
        ]
    )
    sister = Role(
        organization_id=int(organization.id),
        name="Roster parity view",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(source.id),
        job_spec_text="A complete related-role specification for roster parity.",
    )
    db.add(sister)
    db.flush()

    preview_counts = related_role_roster_counts(db, source)
    evaluation_counts = ensure_sister_evaluations(db, sister)

    assert preview_counts == {
        "total": 3,
        "with_cv": 1,
        "missing_cv": 1,
        "scoreable": 1,
        "unscorable": 1,
        "excluded": 1,
    }
    assert evaluation_counts == {
        "total": 3,
        "pending": 1,
        "unscorable": 1,
        "excluded": 1,
    }


def test_direct_related_role_create_requires_adequate_confirmed_paid_scope(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    source, _ = _source_role_with_candidates(
        db, organization_id=int(user.organization_id)
    )
    spec = (
        "Updated AI engineer role requiring production Python, evaluation, "
        "distributed systems, observability, and reliable delivery ownership."
    )
    preview = client.get(
        f"/api/v1/roles/{source.id}/sisters/preview", headers=headers
    ).json()
    missing = client.post(
        f"/api/v1/roles/{source.id}/sisters",
        json={"name": "Missing confirmation", "job_spec_text": spec},
        headers=headers,
    )
    inadequate = client.post(
        f"/api/v1/roles/{source.id}/sisters",
        json={
            "name": "Inadequate cap",
            "job_spec_text": spec,
            "related_role_authorization": _related_role_authorization(
                preview, monthly_cap=8
            ),
        },
        headers=headers,
    )
    wrong_identity = _related_role_authorization(preview)
    wrong_identity["expected_source_role_name"] = "Another source role"
    identity_drift = client.post(
        f"/api/v1/roles/{source.id}/sisters",
        json={
            "name": "Changed source identity",
            "job_spec_text": spec,
            "related_role_authorization": wrong_identity,
        },
        headers=headers,
    )
    source.version = int(source.version or 1) + 1
    db.commit()
    version_drift = client.post(
        f"/api/v1/roles/{source.id}/sisters",
        json={
            "name": "Changed source version",
            "job_spec_text": spec,
            "related_role_authorization": _related_role_authorization(preview),
        },
        headers=headers,
    )
    refreshed_preview = client.get(
        f"/api/v1/roles/{source.id}/sisters/preview", headers=headers
    ).json()
    user.organization.default_role_budget_cents = 6000
    db.commit()
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        default_drift = client.post(
            f"/api/v1/roles/{source.id}/sisters",
            json={
                "name": "Changed workspace default",
                "job_spec_text": spec,
                "related_role_authorization": _related_role_authorization(
                    refreshed_preview
                ),
            },
            headers=headers,
        )

    assert missing.status_code == 409
    assert missing.json()["detail"]["reason"] == "confirmation_required"
    assert inadequate.status_code == 409
    assert inadequate.json()["detail"]["reason"] == "initial_scope_over_monthly_cap"
    assert identity_drift.status_code == 409
    assert identity_drift.json()["detail"]["reason"] == "source_role_changed"
    assert version_drift.status_code == 409
    assert version_drift.json()["detail"]["reason"] == "source_role_version_changed"
    assert default_drift.status_code == 409
    assert default_drift.json()["detail"]["reason"] == "default_monthly_cap_changed"
    assert default_drift.json()["detail"]["current_scope"][
        "current_default_monthly_budget_cents"
    ] == 6000
    dispatch.assert_not_called()
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0


def test_related_roles_keep_independent_stages_but_share_global_rejection(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source = Role(
        organization_id=user.organization_id,
        name="Shared ATS role",
        source="workable",
        workable_job_id="SHARED-ATS",
        workable_job_data={"state": "published"},
        job_spec_text="Original role with a complete enough specification for testing.",
    )
    candidate = Candidate(
        organization_id=user.organization_id,
        email="shared-related@example.com",
        full_name="Shared Candidate",
        cv_text="Production Python and distributed systems experience.",
    )
    db.add_all([source, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=user.organization_id,
        candidate_id=candidate.id,
        role_id=source.id,
        source="manual",
        pipeline_stage="applied",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()
    related_roles = []
    for name in ("Related A", "Related B"):
        role = Role(
            organization_id=user.organization_id,
            name=name,
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=source.id,
            job_spec_text=f"{name} complete role specification with enough screening detail.",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5000,
            auto_skip_assessment=True,
        )
        db.add(role)
        db.flush()
        db.add(
            SisterRoleEvaluation(
                organization_id=user.organization_id,
                role_id=role.id,
                source_application_id=application.id,
                status="done",
                spec_fingerprint=name,
                role_fit_score=80,
            )
        )
        related_roles.append(role)
    db.commit()

    moved = client.patch(
        f"/api/v1/roles/{related_roles[0].id}/applications/{application.id}/stage",
        json={"pipeline_stage": "advanced"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["pipeline_stage"] == "advanced"

    untouched = client.get(
        f"/api/v1/roles/{related_roles[1].id}/applications",
        headers=headers,
    ).json()[0]
    assert untouched["pipeline_stage"] == "applied"
    db.expire_all()
    assert db.get(CandidateApplication, application.id).pipeline_stage == "applied"

    rejected = client.patch(
        f"/api/v1/applications/{application.id}/outcome",
        json={
            "application_outcome": "rejected",
            "reason": "Not proceeding",
            "expected_version": application.version,
            "expected_role_family": {
                "owner": {"id": source.id, "name": source.name},
                "related": [
                    {"id": role.id, "name": role.name}
                    for role in related_roles
                ],
            },
        },
        headers=headers,
    )
    assert rejected.status_code == 200, rejected.text
    for role in related_roles:
        row = client.get(
            f"/api/v1/roles/{role.id}/applications",
            params={"application_outcome": "rejected"},
            headers=headers,
        ).json()[0]
        assert row["application_outcome"] == "rejected"
        assert row["related_role_availability"] == "disqualified"
    statuses = {
        row.status
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    }
    assert statuses == {"excluded"}


def test_related_role_uses_cloned_requisition_chat_then_creates_coupled_scoring_role(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, _ = _source_role_with_candidates(
        db, organization_id=user.organization_id
    )

    created_draft = client.post(
        "/api/v1/requisitions",
        json={"source_role_id": source.id},
        headers=headers,
    )
    assert created_draft.status_code == 201, created_draft.text
    draft = created_draft.json()
    assert draft["brief_kind"] == "related_role"
    assert draft["source_role_id"] == source.id
    assert draft["source_role"] == {
        "role_id": source.id,
        "name": source.name,
        "ats_provider": "workable",
        "version": int(source.version or 1),
    }
    assert draft["jd_override"] == source.job_spec_text
    assert draft["related_role_preview"]["candidates_total"] == 2
    assert draft["related_role_preview"]["candidates_with_cv"] == 1
    assert "Tell me what should change" in draft["messages"][0]["content"]

    completed = client.patch(
        f"/api/v1/requisitions/{draft['id']}",
        json={
            "title": "AI Engineer · Evaluation Platform",
            "seniority": "senior",
            "summary": "Own reliable evaluation systems for production AI products.",
            "workplace_type": "remote",
            "employment_type": "full_time",
            "openings": 1,
            "must_haves": ["Python", "Production AI evaluation"],
            "success_profile": "Ships measurable model-quality improvements end-to-end.",
            "custom_fields": {
                "domain": "Artificial intelligence",
                "urgency": "high",
                "responsibilities": [
                    "Design production evaluation systems",
                    "Own reliability and observability",
                ],
            },
        },
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["gaps"] == []

    updated_spec = (
        "Senior AI engineer responsible for production evaluation platforms, "
        "Python services, RAG quality measurement, model observability, and "
        "reliable delivery across distributed systems."
    )
    publish_body = {
        "jd_markdown": updated_spec,
        "related_role_authorization": _related_role_authorization(
            draft["related_role_preview"]
        ),
    }

    def inflated_prepared_scope(*args, **kwargs):
        counts = ensure_sister_evaluations(*args, **kwargs)
        return {**counts, "pending": int(counts["pending"]) + 1}

    with (
        patch(
            "app.services.related_role_service.ensure_sister_evaluations",
            side_effect=inflated_prepared_scope,
        ),
        patch(
            "app.services.related_role_service.score_sister_role.apply_async"
        ) as blocked_dispatch,
    ):
        drifted = client.post(
            f"/api/v1/requisitions/{draft['id']}/publish",
            json=publish_body,
            headers=headers,
        )

    assert drifted.status_code == 409, drifted.text
    assert drifted.json()["detail"]["code"] == "RELATED_ROLE_PAID_SCOPE_CHANGED"
    assert drifted.json()["detail"]["reason"] == "scoreable_roster_grew"
    blocked_dispatch.assert_not_called()
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0
    db.expire_all()
    assert db.get(RoleBrief, draft["id"]).role_id is None

    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        published = client.post(
            f"/api/v1/requisitions/{draft['id']}/publish",
            json=publish_body,
            headers=headers,
        )

    assert published.status_code == 200, published.text
    receipt = published.json()
    assert receipt["related_role"] is True
    assert receipt["source_role_id"] == source.id
    assert receipt["source_role_name"] == source.name
    assert receipt["status"] == BRIEF_STATUS_APPLIED
    assert receipt["evaluation_counts"] == {
        "total": 2,
        "pending": 1,
        "unscorable": 0,
        "excluded": 1,
    }
    dispatch.assert_called_once()

    related = db.get(Role, receipt["role_id"])
    brief = db.get(RoleBrief, draft["id"])
    assert related.role_kind == ROLE_KIND_SISTER
    assert related.ats_owner_role_id == source.id
    assert related.job_spec_text == updated_spec
    assert related.name == "AI Engineer · Evaluation Platform"
    assert brief.role_id == related.id
    assert brief.status == BRIEF_STATUS_APPLIED
    assert {
        criterion.text
        for criterion in db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == related.id)
        .all()
    }.issuperset({"Python", "Production AI evaluation"})
    assert db.query(JobPage).filter(JobPage.brief_id == brief.id).count() == 0


def test_create_related_role_from_bullhorn_uses_same_shared_roster(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source = Role(
        organization_id=user.organization_id,
        name="Bullhorn Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="BH-9001",
        bullhorn_job_data={"id": 9001, "isOpen": True},
        job_spec_text=(
            "Original platform engineer role requiring Python, distributed "
            "systems, production ownership, and observability."
        ),
    )
    db.add(source)
    db.flush()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="bullhorn-related@example.com",
        full_name="Bullhorn Candidate",
        cv_text="Python platform engineer with production distributed systems experience.",
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=source.id,
            source="bullhorn",
            bullhorn_job_submission_id="BH-SUB-1",
            cv_text=candidate.cv_text,
            application_outcome="open",
        )
    )
    db.commit()

    preview = client.get(
        f"/api/v1/roles/{source.id}/sisters/preview",
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_ats_provider"] == "bullhorn"

    updated_spec = (
        "Updated platform role requiring Python, distributed systems, reliable "
        "data services, production observability, and technical leadership."
    )
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{source.id}/sisters",
            json={
                "name": "Platform Engineer · Data",
                "job_spec_text": updated_spec,
                "related_role_authorization": _related_role_authorization(
                    preview.json()
                ),
            },
            headers=headers,
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"]["ats_provider"] == "bullhorn"
    assert body["role"]["external_job_id"] == "BH-9001"
    assert body["role"]["applications_count"] == 1
    assert body["evaluation_counts"] == {"total": 1, "pending": 1, "unscorable": 0}
    dispatch.assert_called_once()


def test_sister_role_can_enable_scoring_agent_but_not_candidate_automation(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, _ = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="Alternate AI Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A complete alternate specification for an AI engineer with strong platform depth.",
        auto_skip_assessment=True,
    )
    db.add(sister)
    db.commit()

    response = client.patch(
        f"/api/v1/roles/{sister.id}",
        json={"expected_version": int(sister.version or 1), "agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    sister = db.get(Role, sister.id)
    response = client.patch(
        f"/api/v1/roles/{sister.id}",
        json={"expected_version": int(sister.version or 1), "auto_advance": True},
        headers=headers,
    )
    assert response.status_code == 409
    assert "shared" in response.json()["detail"]


def test_preview_rejects_non_ats_source(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    role = Role(organization_id=user.organization_id, name="Native role")
    db.add(role)
    db.commit()

    response = client.get(
        f"/api/v1/roles/{role.id}/sisters/preview", headers=headers
    )
    assert response.status_code == 409
    assert "Workable- or Bullhorn-linked" in response.json()["detail"]


def test_sister_evaluation_task_persists_holistic_score(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    source.agentic_mode_enabled = True
    source.monthly_usd_budget_cents = 5000
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A detailed alternate AI engineer specification focused on RAG evaluation and reliability.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_skip_assessment=True,
    )
    db.add(sister)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=user.organization_id,
        role_id=sister.id,
        source_application_id=applications[0].id,
        status="pending",
        spec_fingerprint="spec",
    )
    db.add(evaluation)
    db.commit()

    output = SimpleNamespace(
        scoring_status=SimpleNamespace(value="ok"),
        role_fit_score=86.0,
        summary="Strong RAG evaluation evidence.",
        error_reason=None,
        model_version="test-model",
        prompt_version="test-prompt",
        trace_id="trace-1",
        cache_hit=False,
        model_dump=lambda **_: {"role_fit_score": 86.0, "summary": "Strong RAG evaluation evidence."},
    )
    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match", return_value=output
        ) as holistic_match,
        patch("app.services.claude_client_resolver.get_metered_client", return_value=object()),
        patch("app.services.workable_context_service.format_workable_context", return_value=""),
    ):
        from app.tasks.sister_role_tasks import score_sister_evaluation

        result = score_sister_evaluation.run(evaluation.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["status"] == "done"
    assert saved.status == "done"
    assert saved.role_fit_score == 86.0
    assert saved.details["role_fit_score"] == 86.0
    metering = holistic_match.call_args.kwargs["metering_context"]
    assert metering["organization_id"] == user.organization_id
    assert metering["role_id"] == sister.id
    assert metering["entity_id"] == f"sister_evaluation:{evaluation.id}"
    assert metering["role_id"] != source.id


def test_related_role_transient_failures_continue_after_fast_retry_budget(
    client, db
):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_evaluation,
    )

    outputs = [_match_output(ok=False) for _ in range(4)] + [
        _match_output(ok=True)
    ]
    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=outputs,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        first = score_sister_evaluation.run(evaluation.id)
        assert first["status"] == "retry_wait"
        for _ in range(4):
            db.expire_all()
            saved = db.get(SisterRoleEvaluation, evaluation.id)
            assert saved.status == "retry_wait"
            saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            recover_sister_role_evaluations.run()

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "done"
    assert saved.attempts == 5
    assert saved.role_fit_score == 88.0


def test_related_role_hard_admission_uses_its_own_budget(
    client, db, monkeypatch
):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    # One cent is deliberately below the provider hold used by the fake
    # scoring boundary. This fails if metering regresses to the source role
    # instead of the related role's independent Agent budget.
    sister = db.get(Role, evaluation.role_id)
    sister.monthly_usd_budget_cents = 1
    db.commit()

    from app.services.pricing_service import Feature
    from app.services.provider_usage_admission import reserve_provider_usage
    from app.tasks.sister_role_tasks import score_sister_evaluation

    monkeypatch.setattr(
        "app.platform.config.settings.USAGE_METER_LIVE", False
    )

    def _admitted_match(*_args, metering_context, **_kwargs):
        reserve_provider_usage(
            organization_id=int(metering_context["organization_id"]),
            role_id=int(metering_context["role_id"]),
            feature=Feature.SCORE,
            trace_id="related-role-source-budget-test",
            entity_id=str(metering_context["entity_id"]),
            amount=20_000,
        )
        return _match_output(ok=True)

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_admitted_match,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = score_sister_evaluation.run(evaluation.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["status"] == "retry_wait"
    assert result["error_code"] == "provider_exception"
    assert saved.status == "retry_wait"
    assert saved.attempts == 1
    assert saved.role_fit_score is None


def test_related_role_pause_holds_then_recovers_without_paid_work(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    sister = db.get(Role, evaluation.role_id)
    sister.agent_paused_at = datetime.now(timezone.utc)
    db.commit()

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_evaluation,
    )

    with patch("app.cv_matching.holistic.run_holistic_match") as paid_call:
        held = score_sister_evaluation.run(evaluation.id)
    assert held["status"] == "authority_blocked"
    paid_call.assert_not_called()
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "retry_wait"
    assert saved.attempts == 0

    status_response = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    )
    assert status_response.status_code == 200, status_response.text
    status_body = status_response.json()
    assert status_body["status"] == "waiting"
    assert status_body["waiting_reason"] == "agent_paused"
    assert status_body["scoreable_total"] == 1
    assert status_body["scored"] == 0
    assert status_body["progress_percent"] == 0

    sister = db.get(Role, evaluation.role_id)
    sister.agent_paused_at = None
    saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            return_value=_match_output(ok=True),
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        recover_sister_role_evaluations.run()

    db.expire_all()
    assert db.get(SisterRoleEvaluation, evaluation.id).status == "done"


def test_related_role_progress_status_is_three_bounded_queries_and_never_hashes_cvs(
    db,
):
    """The three-second poll must not hydrate/hash the full candidate corpus."""

    organization = Organization(name="Bounded related-role status")
    db.add(organization)
    db.flush()
    _, evaluation = _scorable_evaluation(
        db,
        organization_id=int(organization.id),
    )
    role = db.get(Role, int(evaluation.role_id))
    statements: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = db.get_bind()
    with (
        patch(
            "app.services.related_role_scope_snapshot."
            "active_source_applications_for_related_role"
        ) as materialize_roster,
        patch(
            "app.services.related_role_scope_snapshot.text_fingerprint"
        ) as hash_cv,
    ):
        event.listen(bind, "before_cursor_execute", record)
        try:
            from app.domains.assessments_runtime.sister_role_routes import (
                _scoring_status,
            )

            status = _scoring_status(db, role)
        finally:
            event.remove(bind, "before_cursor_execute", record)

    assert status.cohort_total == 2
    assert status.cohort_scoreable == 1
    assert status.cohort_excluded == 1
    assert len(statements) <= 3
    materialize_roster.assert_not_called()
    hash_cv.assert_not_called()
    assert all("SELECT candidate_applications.cv_text" not in sql for sql in statements)


def test_related_role_rescore_accepts_equal_or_smaller_prepared_scope(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    endpoint = f"/api/v1/roles/{evaluation.role_id}/sister-rescore"
    status = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    )
    assert status.status_code == 200, status.text
    approved = status.json()
    assert approved["scoreable_total"] == 1
    assert approved["cohort_scoreable"] == 1

    with patch(
        "app.domains.assessments_runtime.sister_role_routes."
        "score_sister_role.apply_async"
    ) as dispatch:
        exact = client.post(
            endpoint,
            json={
                "expected_version": approved["role_version"],
                "approved_max_scoreable_count": approved["cohort_scoreable"],
            },
            headers=headers,
        )
        assert exact.status_code == 200, exact.text
        assert exact.json()["scoreable_total"] == 1

        application = db.get(
            CandidateApplication, evaluation.source_application_id
        )
        application.application_outcome = "rejected"
        db.commit()
        smaller = client.post(
            endpoint,
            json={
                "expected_version": approved["role_version"],
                "approved_max_scoreable_count": approved["cohort_scoreable"],
            },
            headers=headers,
        )

    assert smaller.status_code == 200, smaller.text
    assert smaller.json()["scoreable_total"] == 0
    assert dispatch.call_count == 2


def test_related_role_status_separates_fresh_and_visible_stale_scores(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    evaluation.status = "stale"
    evaluation.role_fit_score = 88.0
    evaluation.summary = "Preserved score from the previous specification."
    db.commit()

    response = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "stale"
    assert body["counts"]["done"] == 0
    assert body["counts"]["stale"] == 1
    assert body["scored"] == 0
    assert body["stale_scored"] == 1
    assert body["visible_scored"] == 1
    assert body["top_candidates"] == []
    db.expire_all()
    assert db.get(SisterRoleEvaluation, evaluation.id).role_fit_score == 88.0


def test_related_role_rescore_rejects_version_drift_without_dispatch(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    status = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    ).json()
    role = db.get(Role, evaluation.role_id)
    role.version = int(role.version or 1) + 1
    db.commit()

    with patch(
        "app.domains.assessments_runtime.sister_role_routes."
        "score_sister_role.apply_async"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{evaluation.role_id}/sister-rescore",
            json={
                "expected_version": status["role_version"],
                "approved_max_scoreable_count": status["scoreable_total"],
            },
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"
    dispatch.assert_not_called()
    assert (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == evaluation.role_id)
        .count()
        == 1
    )


def test_related_role_rescore_rolls_back_when_preparation_finds_growth(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    status = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    ).json()
    assert status["scoreable_total"] == 1

    candidate = Candidate(
        organization_id=user.organization_id,
        email="rescore-growth@example.com",
        full_name="Rescore Growth",
        cv_text="Production Python and AI evaluation systems experience.",
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=source.id,
            source="workable",
            workable_candidate_id="rescore-growth",
            workable_stage="Sourced",
            application_outcome="open",
            cv_text=candidate.cv_text,
        )
    )
    db.commit()
    refreshed = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    ).json()
    assert refreshed["total"] == 1
    assert refreshed["scoreable_total"] == 1
    assert refreshed["cohort_total"] == 3
    assert refreshed["cohort_scoreable"] == 2
    assert refreshed["cohort_unscorable"] == 0
    assert refreshed["cohort_excluded"] == 1
    assert refreshed["estimated_rescore_cost_usd"] == 0.17

    with patch(
        "app.domains.assessments_runtime.sister_role_routes."
        "score_sister_role.apply_async"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{evaluation.role_id}/sister-rescore",
            json={
                "expected_version": status["role_version"],
                "approved_max_scoreable_count": status["scoreable_total"],
            },
            headers=headers,
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "RELATED_ROLE_PAID_SCOPE_CHANGED"
    assert detail["reason"] == "scoreable_roster_grew"
    assert detail["current_scope"]["scoreable_count"] == 2
    dispatch.assert_not_called()
    assert (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == evaluation.role_id)
        .count()
        == 1
    )

    with patch(
        "app.domains.assessments_runtime.sister_role_routes."
        "score_sister_role.apply_async"
    ) as refreshed_dispatch:
        accepted = client.post(
            f"/api/v1/roles/{evaluation.role_id}/sister-rescore",
            json={
                "expected_version": refreshed["role_version"],
                "approved_max_scoreable_count": refreshed["cohort_scoreable"],
            },
            headers=headers,
        )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["scoreable_total"] == 2
    refreshed_dispatch.assert_called_once()


def test_related_role_resume_kick_immediately_releases_authority_wait(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    sister = db.get(Role, evaluation.role_id)
    sister.agent_paused_at = datetime.now(timezone.utc)
    db.commit()

    from app.tasks.sister_role_tasks import (
        score_sister_evaluation,
        score_sister_role,
    )

    assert score_sister_evaluation.run(evaluation.id)["status"] == "authority_blocked"
    sister = db.get(Role, evaluation.role_id)
    sister.agent_paused_at = None
    db.commit()

    published: list[int] = []
    with patch.object(
        score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: published.append(args[0]),
    ):
        result = score_sister_role.run(sister.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["queued"] == 1
    assert published == [evaluation.id]
    assert saved.status == "pending"
    assert saved.next_attempt_at is None


def test_related_role_stale_worker_and_secret_broker_error_self_recover(
    client, db, caplog
):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    evaluation.status = "running"
    evaluation.attempts = 1
    evaluation.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_evaluation,
    )

    secret = "redis://:SECRET@host"
    with (
        caplog.at_level("ERROR", logger="taali.tasks.sister_roles"),
        patch.object(
            score_sister_evaluation,
            "apply_async",
            side_effect=RuntimeError(secret),
        ),
    ):
        result = recover_sister_role_evaluations.run()

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "retry_wait"
    assert saved.last_error_code == "queue_unavailable_runtimeerror"
    assert secret not in str(result)
    assert secret not in str(saved.error_message)
    assert secret not in caplog.text

    saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    published: list[int] = []
    with patch.object(
        score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: published.append(args[0]),
    ):
        recover_sister_role_evaluations.run()
    assert published == [evaluation.id]


def test_related_role_duplicate_delivery_does_not_repeat_paid_call(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            return_value=_match_output(ok=True),
        ) as paid_call,
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        assert score_sister_evaluation.run(evaluation.id)["status"] == "done"
        assert score_sister_evaluation.run(evaluation.id)["status"] == "skipped"
    paid_call.assert_called_once()


def test_related_role_only_deterministic_provider_failure_is_terminal(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    failed = _match_output(ok=False)
    failed.error_reason = (
        "holistic_score_failed: validation_failed_after_retry: redis://:SECRET@host"
    )

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            return_value=failed,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = score_sister_evaluation.run(evaluation.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["status"] == "error"
    assert saved.status == "error"
    assert saved.last_error_code == "validation_failed"
    assert "SECRET" not in repr(result)
    assert "SECRET" not in str(saved.error_message)


def test_new_or_changed_workable_cv_queues_only_its_sister_evaluation(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Live sister",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A detailed sister specification requiring RAG, evaluation, and reliable Python services.",
    )
    db.add(sister)
    db.commit()

    from app.services.sister_role_service import ensure_application_sister_evaluations

    queued = ensure_application_sister_evaluations(
        db, applications[0], sister_roles=[sister]
    )
    db.commit()
    assert len(queued) == 1

    evaluation = db.get(SisterRoleEvaluation, queued[0])
    evaluation.status = "done"
    evaluation.role_fit_score = 84.0
    db.commit()
    assert ensure_application_sister_evaluations(
        db, applications[0], sister_roles=[sister]
    ) == []

    applications[0].cv_text = f"{applications[0].cv_text} Added agent evaluation ownership."
    changed = ensure_application_sister_evaluations(
        db, applications[0], sister_roles=[sister]
    )
    assert changed == [evaluation.id]
    assert evaluation.status == "pending"
    assert evaluation.role_fit_score is None
    assert evaluation.history[-1]["role_fit_score"] == 84.0


def test_workable_candidate_without_cv_becomes_scorable_when_cv_arrives(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Deferred CV",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A detailed sister specification requiring production AI systems and evaluation depth.",
    )
    db.add(sister)
    db.commit()

    from app.services.sister_role_service import ensure_application_sister_evaluations

    application = applications[1]
    application.application_outcome = "open"
    assert ensure_application_sister_evaluations(
        db, application, sister_roles=[sister]
    ) == []
    db.commit()
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == sister.id,
            SisterRoleEvaluation.source_application_id == application.id,
        )
        .one()
    )
    assert evaluation.status == "unscorable"
    assert evaluation.error_message == "No CV text available"

    application.cv_text = "Python AI engineer with production RAG and model evaluation experience."
    queued = ensure_application_sister_evaluations(
        db, application, sister_roles=[sister]
    )
    assert queued == [evaluation.id]
    assert evaluation.status == "pending"
    assert evaluation.error_message is None


def test_sister_application_api_ranks_by_alternate_score(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    # Make both source applications active so one response can prove ranking.
    applications[1].application_outcome = "open"
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Ranked sister",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A full alternate specification emphasizing AI reliability, RAG, and evaluation systems.",
    )
    db.add(sister)
    db.flush()
    db.add_all([
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=sister.id,
            source_application_id=applications[0].id,
            status="done",
            spec_fingerprint="spec",
            role_fit_score=55.0,
        ),
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=sister.id,
            source_application_id=applications[1].id,
            status="done",
            spec_fingerprint="spec",
            role_fit_score=93.0,
        ),
    ])
    db.commit()

    response = client.get(
        f"/api/v1/roles/{sister.id}/applications",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows] == [applications[1].id, applications[0].id]
    assert [row["taali_score"] for row in rows] == [93.0, 55.0]

    pipeline = client.get(
        f"/api/v1/roles/{sister.id}/pipeline",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=headers,
    )
    assert pipeline.status_code == 200, pipeline.text
    assert [row["id"] for row in pipeline.json()["items"]] == [
        applications[1].id, applications[0].id,
    ]

    detail = client.get(
        f"/api/v1/applications/{applications[1].id}",
        params={"view_role_id": sister.id},
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["role_id"] == sister.id
    assert detail.json()["operational_role_id"] == source.id
    assert detail.json()["taali_score"] == 93.0
