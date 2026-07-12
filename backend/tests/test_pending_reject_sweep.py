"""Toggle-on sweep offer for pending pre-screen reject cards.

Turning ``auto_reject`` / ``auto_reject_pre_screen`` on is forward-only —
already-pending ``skip_assessment_reject`` cards are never executed silently.
Instead the PATCH posts a ``pending_reject_sweep`` confirm card into the
role's agent chat; Approve funnels the CURRENT pending queue through
``approve_decision.enqueue_batch`` (same path as the Hub's bulk approve),
Dismiss keeps the cards for manual review.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


# SQLite BigInteger PK workaround. Offset high so ids can't collide with
# rows created by other test modules' listeners in a batch run.
_BIG_PK_COUNTER = {"n": 91000}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    if target.id is None:
        _BIG_PK_COUNTER["n"] += 1
        target.id = _BIG_PK_COUNTER["n"]


event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _org_id(db, email: str) -> int:
    return int(db.query(User).filter(User.email == email).first().organization_id)


def _role(db, org_id, *, name="Backend") -> Role:
    role = Role(
        organization_id=org_id,
        name=name,
        source="manual",
        score_threshold=70,
    )
    db.add(role)
    db.flush()
    return role


def _pending_reject(db, org_id, role, *, name) -> AgentDecision:
    cand = Candidate(organization_id=org_id, email=f"{name}@x.test", full_name=name)
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=20,
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org_id,
        role_id=role.id,
        application_id=app.id,
        decision_type="skip_assessment_reject",
        recommendation="reject",
        status="pending",
        reasoning="Pre-screen score below threshold",
        model_version="deterministic",
        prompt_version="deterministic",
        idempotency_key=f"test-sweep-{org_id}-{role.id}-{name}",
    )
    db.add(decision)
    db.flush()
    return decision


def _sweep_cards(client, role_id, headers) -> list[dict]:
    r = client.get(f"/api/v1/agent-chat/conversations/{role_id}/timeline", headers=headers)
    assert r.status_code == 200, r.text
    cards = []
    for item in r.json()["timeline"]:
        if item.get("kind") != "message":
            continue
        for card in item.get("actions") or []:
            if card.get("type") == "pending_reject_sweep":
                cards.append(card)
    return cards


def _patch_role(client, role_id, headers, body) -> None:
    r = client.patch(f"/api/v1/roles/{role_id}", headers=headers, json=body)
    assert r.status_code == 200, r.text


def test_toggle_on_with_pending_cards_posts_offer(client, db):
    headers, email = auth_headers(client, organization_name="SweepOfferOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    _pending_reject(db, org_id, role, name="ada")
    _pending_reject(db, org_id, role, name="grace")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    cards = _sweep_cards(client, role.id, headers)
    assert len(cards) == 1
    assert cards[0]["pending_count"] == 2
    assert cards[0]["status"] == "offered"
    assert cards[0]["role_id"] == role.id


def test_broad_auto_reject_toggle_also_offers(client, db):
    headers, email = auth_headers(client, organization_name="SweepBroadOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    _pending_reject(db, org_id, role, name="alan")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject": True})

    cards = _sweep_cards(client, role.id, headers)
    assert len(cards) == 1
    assert cards[0]["pending_count"] == 1


def test_toggle_on_with_empty_queue_is_silent(client, db):
    headers, email = auth_headers(client, organization_name="SweepSilentOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    assert _sweep_cards(client, role.id, headers) == []


def test_retoggle_does_not_duplicate_open_offer(client, db):
    headers, email = auth_headers(client, organization_name="SweepDupOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    _pending_reject(db, org_id, role, name="edsger")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})
    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": False})
    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    cards = _sweep_cards(client, role.id, headers)
    assert len(cards) == 1  # the open offer is reused, not re-posted


def test_already_on_does_not_offer(client, db):
    """A PATCH that keeps the effective value True (e.g. turning the narrow
    toggle on while the broad one is already on) must not post an offer."""
    headers, email = auth_headers(client, organization_name="SweepNoFlipOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    role.auto_reject = True
    _pending_reject(db, org_id, role, name="barbara")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    assert _sweep_cards(client, role.id, headers) == []


def test_apply_enqueues_batch_and_resolves_offer(client, db):
    headers, email = auth_headers(client, organization_name="SweepApplyOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    d1 = _pending_reject(db, org_id, role, name="tim")
    d2 = _pending_reject(db, org_id, role, name="vint")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    with patch("app.services.workable_op_runner.enqueue_workable_op") as op:
        r = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/pending-rejects/apply",
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 2
    assert body["failures"] == []
    # Same path as the Hub's bulk approve: one serialized Workable op.
    op.assert_called_once()
    assert sorted(op.call_args.kwargs["payload"]["decision_ids"]) == sorted(
        [d1.id, d2.id]
    )

    db.expire_all()
    assert d1.status == "processing"
    assert d2.status == "processing"

    cards = _sweep_cards(client, role.id, headers)
    assert len(cards) == 1
    assert cards[0]["status"] == "applied"
    assert cards[0]["applied_count"] == 2


def test_dismiss_keeps_cards_pending(client, db):
    headers, email = auth_headers(client, organization_name="SweepDismissOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    decision = _pending_reject(db, org_id, role, name="donald")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    r = client.post(
        f"/api/v1/agent-chat/conversations/{role.id}/pending-rejects/dismiss",
        headers=headers,
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    assert decision.status == "pending"

    cards = _sweep_cards(client, role.id, headers)
    assert len(cards) == 1
    assert cards[0]["status"] == "dismissed"


def test_apply_with_cleared_queue_is_a_clean_noop(client, db):
    """The offer carries no ids — if the recruiter cleared the queue by hand
    before clicking Approve, apply resolves the card at 0 without erroring."""
    headers, email = auth_headers(client, organization_name="SweepClearedOrg")
    org_id = _org_id(db, email)
    role = _role(db, org_id)
    decision = _pending_reject(db, org_id, role, name="radia")
    db.commit()

    _patch_role(client, role.id, headers, {"auto_reject_pre_screen": True})

    decision.status = "discarded"
    db.commit()

    with patch("app.services.workable_op_runner.enqueue_workable_op") as op:
        r = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/pending-rejects/apply",
            headers=headers,
        )
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 0
    op.assert_not_called()

    cards = _sweep_cards(client, role.id, headers)
    assert cards[0]["status"] == "applied"
    assert cards[0]["applied_count"] == 0
