from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.components.integrations.bullhorn import sync_candidates
from app.components.integrations.bullhorn.errors import BullhornFileTooLargeError
from app.components.integrations.bullhorn.provider import BullhornProvider
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.services.document_service import MAX_FILE_SIZE


def test_oversized_bullhorn_resume_is_rejected_before_parse_or_store(
    monkeypatch,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        sync_candidates,
        "extract_text",
        lambda *_args: touched.append("parse") or "text",
    )
    monkeypatch.setattr(
        sync_candidates,
        "upload_bytes_to_s3",
        lambda *_args, **_kwargs: touched.append("upload") or "stored",
    )

    stored = sync_candidates._store_resume(
        app=SimpleNamespace(id=1),
        candidate=SimpleNamespace(id=2),
        filename="resume.pdf",
        content=b"x" * (MAX_FILE_SIZE + 1),
        now=datetime.now(timezone.utc),
    )

    assert stored is False
    assert touched == []


class _OversizedResumeClient:
    def list_file_attachments_strict(self, *, candidate_id, fields):
        return [
            {
                "id": 77,
                "name": "resume.pdf",
                "type": "Resume",
                "contentType": "application/pdf",
            }
        ]

    def list_file_attachments(self, *, candidate_id, fields):
        return self.list_file_attachments_strict(
            candidate_id=candidate_id,
            fields=fields,
        )

    def get_file_raw(self, *, candidate_id, file_id, max_bytes):
        assert max_bytes == MAX_FILE_SIZE
        raise BullhornFileTooLargeError("bounded rejection")

    def convert_resume_to_text(self, **_kwargs):
        raise AssertionError("oversized bytes must not reach resume conversion")


def test_oversized_resume_keeps_candidate_metadata_syncable(
    db,
    monkeypatch,
) -> None:
    org = Organization(name="Oversized Bullhorn CV org")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="9001",
        bullhorn_job_data={"id": 9001, "isOpen": True},
        job_status=JOB_STATUS_OPEN,
    )
    db.add(role)
    db.flush()
    created: list[int] = []
    monkeypatch.setattr(
        sync_candidates,
        "on_application_created",
        lambda app, **_kwargs: created.append(int(app.id)),
    )

    result = sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission={
            "id": "7001",
            "candidate": {"id": "8001"},
            "jobOrder": {"id": "9001"},
            "status": "New Lead",
        },
        candidate_payload={
            "id": "8001",
            "name": "Metadata Candidate",
            "email": "oversized-bullhorn@example.test",
        },
        client=_OversizedResumeClient(),
        now=datetime.now(timezone.utc),
    )

    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.bullhorn_job_submission_id == "7001")
        .one()
    )
    assert result["application_upserted"] == 1
    assert app.source == "bullhorn"
    assert app.cv_text is None
    assert app.cv_file_url is None
    assert created == [int(app.id)]


def test_provider_treats_oversized_resume_as_metadata_only(monkeypatch) -> None:
    provider = BullhornProvider(
        SimpleNamespace(),
        SimpleNamespace(),
    )
    client = _OversizedResumeClient()
    monkeypatch.setattr(provider, "_client", lambda: client)

    result = provider.download_candidate_resume({"id": "8001"})

    assert result is None
