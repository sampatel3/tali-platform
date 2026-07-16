"""Per-job and role-less authorization for outreach campaign routes."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.deps import get_current_user
from app.main import app as fastapi_app
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.outreach_campaign import (
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_PENDING,
    OutreachCampaign,
    OutreachMessage,
)
from app.models.role import Role
from app.models.user import User


def _user(db, org_id: int, name: str, *, org_role: str = "member") -> User:
    user = User(
        organization_id=org_id,
        email=f"{name}@outreach-authz.test",
        full_name=name.title(),
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role=org_role,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def campaign_subjects(db):
    org = Organization(name="Outreach Authz", slug="outreach-authz")
    other_org = Organization(name="Other Outreach", slug="other-outreach-authz")
    db.add_all([org, other_org])
    db.flush()

    role = Role(organization_id=org.id, name="Platform Engineer", source="manual")
    db.add(role)
    db.flush()

    subjects = {
        "org": org,
        "role": role,
        "owner": _user(db, org.id, "owner", org_role="owner"),
        "recruiter": _user(db, org.id, "recruiter"),
        "manager": _user(db, org.id, "manager"),
        "interviewer": _user(db, org.id, "interviewer"),
        "coordinator": _user(db, org.id, "coordinator"),
        "unassigned": _user(db, org.id, "unassigned"),
        "other_member": _user(db, other_org.id, "other-member"),
    }
    for actor, team_role in (
        ("recruiter", "recruiter"),
        ("manager", "hiring_manager"),
        ("interviewer", "interviewer"),
        ("coordinator", "coordinator"),
    ):
        db.add(
            JobHiringTeam(
                organization_id=org.id,
                role_id=role.id,
                user_id=subjects[actor].id,
                team_role=team_role,
            )
        )

    campaign = OutreachCampaign(
        organization_id=org.id,
        role_id=role.id,
        name="Role campaign",
        status="draft",
        created_by_user_id=subjects["owner"].id,
    )
    roleless_campaign = OutreachCampaign(
        organization_id=org.id,
        role_id=None,
        name="Private pool campaign",
        status="draft",
        created_by_user_id=subjects["recruiter"].id,
    )
    db.add_all([campaign, roleless_campaign])
    db.flush()

    message = OutreachMessage(
        campaign_id=campaign.id,
        organization_id=org.id,
        email="draft@outreach-authz.test",
        subject="Hello",
        body="Draft body",
        status=MESSAGE_STATUS_DRAFT,
    )
    roleless_message = OutreachMessage(
        campaign_id=roleless_campaign.id,
        organization_id=org.id,
        email="pool@outreach-authz.test",
        subject="Hello",
        body="Pool body",
        status=MESSAGE_STATUS_APPROVED,
    )
    db.add_all([message, roleless_message])
    db.commit()
    subjects.update(
        campaign=campaign,
        roleless_campaign=roleless_campaign,
        message=message,
        roleless_message=roleless_message,
    )
    return subjects


def _act_as(user: User) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.parametrize("actor", ["interviewer", "coordinator", "unassigned"])
def test_role_campaign_create_denies_non_editors(client, campaign_subjects, actor):
    _act_as(campaign_subjects[actor])
    response = client.post(
        "/api/v1/outreach/campaigns",
        json={"name": "Forbidden", "role_id": campaign_subjects["role"].id},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


@pytest.mark.parametrize("actor", ["interviewer", "coordinator", "unassigned"])
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("patch", "", {"name": "Nope"}),
        ("post", "/archive", None),
        ("post", "/audience", {}),
        ("post", "/generate", {"confirm": True}),
        ("post", "/messages/approve", {"all_drafts": True}),
        ("post", "/messages/{mid}", {"subject": "Nope"}),
        ("post", "/messages/{mid}/reject", None),
        ("post", "/send", {"confirm": True}),
        ("post", "/approve-and-send", {"confirm": True}),
    ],
)
def test_every_role_campaign_write_denies_non_editors_and_non_controllers(
    client, campaign_subjects, actor, method, path, payload
):
    _act_as(campaign_subjects[actor])
    campaign = campaign_subjects["campaign"]
    path = path.format(mid=campaign_subjects["message"].id)
    response = client.request(
        method,
        f"/api/v1/outreach/campaigns/{campaign.id}{path}",
        json=payload,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_assigned_recruiter_can_create_edit_and_dispatch(
    client, db, campaign_subjects
):
    recruiter = campaign_subjects["recruiter"]
    role = campaign_subjects["role"]
    _act_as(recruiter)

    created = client.post(
        "/api/v1/outreach/campaigns",
        json={"name": "Recruiter wave", "role_id": role.id},
    )
    assert created.status_code == 200, created.text
    campaign_id = created.json()["id"]
    patched = client.patch(
        f"/api/v1/outreach/campaigns/{campaign_id}",
        json={"brief": "A deliberately edited pitch."},
    )
    assert patched.status_code == 200, patched.text

    pending = OutreachMessage(
        campaign_id=campaign_id,
        organization_id=role.organization_id,
        email="pending@outreach-authz.test",
        status=MESSAGE_STATUS_PENDING,
    )
    db.add(pending)
    db.commit()
    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        generated = client.post(
            f"/api/v1/outreach/campaigns/{campaign_id}/generate",
            json={"confirm": True},
        )
    assert generated.status_code == 200, generated.text
    delay.assert_called_once_with(campaign_id)

    campaign = campaign_subjects["campaign"]
    message = campaign_subjects["message"]
    message.status = MESSAGE_STATUS_APPROVED
    db.commit()
    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        sent = client.post(
            f"/api/v1/outreach/campaigns/{campaign.id}/send",
            json={"confirm": True},
        )
    assert sent.status_code == 200, sent.text
    delay.assert_called_once_with(campaign.id)


def test_role_campaign_reads_flow_through_same_org_view_policy(
    client, campaign_subjects
):
    """The established VIEW matrix allows active same-org members to read."""
    _act_as(campaign_subjects["unassigned"])
    campaign_id = campaign_subjects["campaign"].id
    detail = client.get(f"/api/v1/outreach/campaigns/{campaign_id}")
    assert detail.status_code == 200, detail.text
    listed_ids = {
        item["id"]
        for item in client.get("/api/v1/outreach/campaigns").json()["campaigns"]
    }
    assert campaign_id in listed_ids


def test_roleless_campaign_is_creator_or_owner_only_for_reads_and_writes(
    client, campaign_subjects
):
    campaign = campaign_subjects["roleless_campaign"]

    _act_as(campaign_subjects["unassigned"])
    detail = client.get(f"/api/v1/outreach/campaigns/{campaign.id}")
    assert detail.status_code == 403
    listed_ids = {
        item["id"]
        for item in client.get("/api/v1/outreach/campaigns").json()["campaigns"]
    }
    assert campaign.id not in listed_ids
    denied = client.patch(
        f"/api/v1/outreach/campaigns/{campaign.id}",
        json={"name": "Stolen"},
    )
    assert denied.status_code == 403

    _act_as(campaign_subjects["recruiter"])
    creator_edit = client.patch(
        f"/api/v1/outreach/campaigns/{campaign.id}",
        json={"name": "Creator edit"},
    )
    assert creator_edit.status_code == 200, creator_edit.text
    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        creator_send = client.post(
            f"/api/v1/outreach/campaigns/{campaign.id}/send",
            json={"confirm": True},
        )
    assert creator_send.status_code == 200, creator_send.text
    delay.assert_called_once_with(campaign.id)

    _act_as(campaign_subjects["owner"])
    owner_read = client.get(f"/api/v1/outreach/campaigns/{campaign.id}")
    assert owner_read.status_code == 200, owner_read.text
    owner_edit = client.patch(
        f"/api/v1/outreach/campaigns/{campaign.id}",
        json={"name": "Owner recovery"},
    )
    assert owner_edit.status_code == 200, owner_edit.text


def test_cross_org_campaign_detail_still_conceals_existence(
    client, campaign_subjects
):
    _act_as(campaign_subjects["other_member"])
    campaign_id = campaign_subjects["campaign"].id
    response = client.get(f"/api/v1/outreach/campaigns/{campaign_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Campaign not found"
