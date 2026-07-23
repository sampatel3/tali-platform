"""Transaction and public-data safety for shareable candidate reports."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.top_reports.routes import view_top_report
from app.domains.top_reports.service import (
    create_report,
    scrub_public_query,
    scrub_public_snapshot,
)
from app.models.organization import Organization
from app.models.top_candidates_report import TopCandidatesReport
from app.models.user import User


def _principal(db) -> tuple[Organization, User]:
    org = Organization(name="Report Org", slug=f"report-org-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"report-{id(db)}@example.com",
        hashed_password="x",
        full_name="Report Owner",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.commit()
    return org, user


def test_create_report_uses_savepoint_and_leaves_outer_transaction_in_charge(db):
    org, user = _principal(db)
    original_name = org.name
    org.name = "Uncommitted chat state"

    with (
        patch.object(db, "commit") as commit,
        patch.object(db, "rollback") as rollback,
    ):
        report = create_report(
            db,
            organization_id=org.id,
            created_by_user_id=user.id,
            role_id=None,
            query="top candidates",
            snapshot={"candidates": []},
        )

        assert report.id is not None
        assert db.query(TopCandidatesReport).filter_by(token=report.token).one()
        commit.assert_not_called()
        rollback.assert_not_called()

    # The caller can still atomically abandon both its chat write and report.
    db.rollback()
    assert db.query(TopCandidatesReport).filter_by(token=report.token).first() is None
    assert db.query(Organization).filter_by(id=org.id).one().name == original_name


def test_report_without_other_pending_writes_still_waits_for_outer_commit(db):
    org, user = _principal(db)
    report = create_report(
        db,
        organization_id=org.id,
        created_by_user_id=user.id,
        role_id=None,
        query="top candidates",
        snapshot={"candidates": []},
    )

    db.rollback()
    assert db.query(TopCandidatesReport).filter_by(token=report.token).first() is None


def test_create_report_never_stages_raw_query_or_snapshot_secrets(db):
    org, user = _principal(db)
    report = create_report(
        db,
        organization_id=org.id,
        created_by_user_id=user.id,
        role_id=None,
        query=(
            "Find ada@example.com via https://ats.test/private?token=secret "
            "with api_key=abcdefghijklmnopqrst"
        ),
        snapshot={
            "candidates": [
                {
                    "candidate_name": "Ada",
                    "candidate_email": "ada@example.com",
                    "frontend_url": "https://taali.test/candidates/42",
                }
            ]
        },
    )

    encoded = json.dumps({"query": report.query, "snapshot": report.snapshot})
    assert report.query == scrub_public_query(
        "Find ada@example.com via https://ats.test/private?token=secret "
        "with api_key=abcdefghijklmnopqrst"
    )
    assert report.snapshot == {"candidates": [{"candidate_name": "Ada"}]}
    for secret in ("ada@example.com", "https://", "abcdefghijklmnopqrst"):
        assert secret not in encoded
    db.rollback()


def test_report_token_collision_rolls_back_only_its_savepoint(db, monkeypatch):
    org, user = _principal(db)
    monkeypatch.setattr(
        "app.domains.top_reports.service.generate_report_token",
        lambda: "rpt_same_secure_token",
    )
    first = create_report(
        db,
        organization_id=org.id,
        created_by_user_id=user.id,
        role_id=None,
        query="first",
        snapshot={"candidates": []},
    )
    org.name = "Outer state survives"

    with (
        patch.object(db, "commit") as commit,
        patch.object(db, "rollback") as rollback,
        pytest.raises(IntegrityError),
    ):
        create_report(
            db,
            organization_id=org.id,
            created_by_user_id=user.id,
            role_id=None,
            query="collision",
            snapshot={"candidates": []},
        )

    commit.assert_not_called()
    rollback.assert_not_called()
    assert org.name == "Outer state survives"
    assert db.query(TopCandidatesReport).filter_by(token=first.token).count() == 1
    # Proves the session is usable and its owner can commit after the fallback.
    db.commit()
    assert db.query(Organization).filter_by(id=org.id).one().name == "Outer state survives"


def test_public_scrub_is_recursive_and_removes_links_pii_and_credentials():
    raw = {
        "ranking_key": "taali",
        "candidates": [
            {
                "candidate_name": "Ada Lovelace",
                "candidate_email": "ada@example.com",
                "frontend_url": "https://taali.test/candidates/42?token=secret",
                "ats": {
                    "workableProfileUrl": "https://workable.test/candidate/42",
                    "apiKey": "sk-proj-abcdefghijklmnopqrstuvwxyz",
                    "contact": {
                        "mobile": "+971 50 123 4567",
                        "homeAddress": "1 Private Street",
                    },
                },
                "criteria": [
                    {
                        "evidence": [
                            {
                                "quote": (
                                    "Email ada@example.com, call +971 50 123 4567, "
                                    "open https://ats.test/private?access_token=secret, "
                                    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
                                )
                            }
                        ]
                    }
                ],
            }
        ],
        "metadata": {
            "access_token": "secret-token-value",
            "internalLink": "https://internal.test/report",
            "authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
            "generated_at": datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
        },
    }

    safe = scrub_public_snapshot(raw)
    encoded = json.dumps(safe)
    assert safe["ranking_key"] == "taali"  # "key" alone is not over-scrubbed
    candidate = safe["candidates"][0]
    assert candidate["candidate_name"] == "Ada Lovelace"
    assert "candidate_email" not in candidate
    assert "frontend_url" not in candidate
    assert candidate["ats"] == {"contact": {}}
    assert "access_token" not in safe["metadata"]
    assert "internalLink" not in safe["metadata"]
    assert "authorization" not in safe["metadata"]
    assert safe["metadata"]["generated_at"] == "2026-07-22T12:00:00+00:00"
    for secret in (
        "ada@example.com",
        "+971 50 123 4567",
        "secret-token-value",
        "abcdefghijklmnopqrstuvwxyz",
        "https://",
    ):
        assert secret not in encoded
    assert "[email redacted]" in encoded
    assert "[phone redacted]" in encoded
    assert "[link redacted]" in encoded
    assert "[credential redacted]" in encoded
    # Scrubbing always works on a copy.
    assert raw["candidates"][0]["candidate_email"] == "ada@example.com"


def test_public_route_rescrubs_legacy_snapshot_and_query(db):
    org, _user = _principal(db)
    report = TopCandidatesReport(
        organization_id=org.id,
        token="rpt_legacy_unsafe",
        query=(
            "Find ada@example.com at https://ats.test/private?token=secret "
            "using api_key=abcdefghijklmnopqrst"
        ),
        snapshot={
            "candidates": [
                {
                    "candidate_name": "Ada",
                    "candidate_email": "ada@example.com",
                    "frontend_url": "https://taali.test/candidates/42",
                    "nested": {"password": "do-not-publish"},
                }
            ]
        },
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(report)
    db.commit()

    result = view_top_report(report.token, db=db, _user=None)
    encoded = json.dumps(result)
    assert result["query"] == scrub_public_query(report.query)
    assert result["snapshot"] == scrub_public_snapshot(report.snapshot)
    for secret in (
        "ada@example.com",
        "https://",
        "abcdefghijklmnopqrst",
        "do-not-publish",
    ):
        assert secret not in encoded
    assert result["snapshot"]["candidates"][0] == {"candidate_name": "Ada", "nested": {}}
