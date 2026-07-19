"""Strict exact-entity reads used before event-driven mutations.

An event is only a dirty flag.  These reads prove either one uniquely matching
entity or a complete zero-row result; malformed, truncated, duplicate, or
wrong-id payloads raise so the destructive event checkpoint remains replayable.
"""

from __future__ import annotations

from .errors import BullhornApiError
from .service_paging import SEARCH_PAGE_CAP


def _positive_id(value: object) -> str | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).isascii()
        or not str(value).isdigit()
        or int(value) <= 0
    ):
        return None
    return str(int(value))


def _association_id(row: dict, name: str) -> str | None:
    association = row.get(name)
    return _positive_id(association.get("id")) if isinstance(association, dict) else None


class BullhornExactReadsMixin:
    """Mixin for :class:`BullhornService`; transport remains owned there."""

    def _exact_snapshot(
        self,
        *,
        kind: str,
        entity: str,
        entity_id: str | int,
        fields: str,
        selector: str,
    ) -> dict | None:
        expected_id = _positive_id(entity_id)
        if expected_id is None:
            raise ValueError(f"exact {entity} reads require a positive id")
        requested = {field.strip() for field in fields.split(",") if field.strip()}
        if "id" not in requested:
            raise ValueError(f"exact {entity} reads require the id field")
        rows = self._paged(  # type: ignore[attr-defined]
            kind,
            entity,
            fields=fields,
            selector=selector,
            count=SEARCH_PAGE_CAP,
            require_complete=True,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise BullhornApiError(
                f"Bullhorn exact {entity} read returned duplicate rows"
            )
        row = rows[0]
        if _positive_id(row.get("id")) != expected_id:
            raise BullhornApiError(
                f"Bullhorn exact {entity} read returned the wrong id"
            )
        return dict(row)

    def get_job_order_exact(self, job_order_id: str | int, *, fields: str) -> dict | None:
        row = self._exact_snapshot(
            kind="search",
            entity="JobOrder",
            entity_id=job_order_id,
            fields=fields,
            selector=f"id:{int(job_order_id)}",
        )
        if row is not None and type(row.get("isOpen")) is not bool:
            raise BullhornApiError("Bullhorn exact JobOrder read had an invalid shape")
        return row

    def get_candidate_exact(self, candidate_id: str | int, *, fields: str) -> dict | None:
        return self._exact_snapshot(
            kind="search",
            entity="Candidate",
            entity_id=candidate_id,
            fields=fields,
            selector=f"id:{int(candidate_id)}",
        )

    def get_job_submission_exact(
        self,
        job_submission_id: str | int,
        *,
        fields: str,
    ) -> dict | None:
        row = self._exact_snapshot(
            kind="query",
            entity="JobSubmission",
            entity_id=job_submission_id,
            fields=fields,
            selector=f"id={int(job_submission_id)}",
        )
        if row is not None and (
            type(row.get("isDeleted")) is not bool
            or _association_id(row, "jobOrder") is None
            or _association_id(row, "candidate") is None
        ):
            raise BullhornApiError(
                "Bullhorn exact JobSubmission read had an invalid shape"
            )
        return row

    def get_note_exact(self, note_id: str | int, *, fields: str) -> dict | None:
        row = self._exact_snapshot(
            kind="query",
            entity="Note",
            entity_id=note_id,
            fields=fields,
            selector=f"id={int(note_id)}",
        )
        if row is not None and _association_id(row, "personReference") is None:
            raise BullhornApiError("Bullhorn exact Note read had an invalid shape")
        return row

    def query_notes_complete(
        self,
        *,
        candidate_id: str | int,
        fields: str,
    ) -> list[dict]:
        expected_parent = _positive_id(candidate_id)
        if expected_parent is None:
            raise ValueError("complete Note reads require a candidate id")
        required = {"id", "comments", "personReference"}
        if not required.issubset({part.strip() for part in fields.split(",")}):
            raise ValueError("complete Note reads require id,comments,personReference")
        rows = self._paged(  # type: ignore[attr-defined]
            "query",
            "Note",
            fields=fields,
            selector=f"personReference.id={int(expected_parent)}",
            count=SEARCH_PAGE_CAP,
            require_complete=True,
        )
        self._validate_scoped_rows(
            rows,
            entity="Note",
            parent_field="personReference",
            expected_parent=expected_parent,
        )
        if any(not isinstance(row.get("comments"), str) for row in rows):
            raise BullhornApiError("Bullhorn complete Note read had an invalid shape")
        return rows

    def get_job_submission_history_complete(
        self,
        *,
        job_submission_id: str | int,
        fields: str,
    ) -> list[dict]:
        expected_parent = _positive_id(job_submission_id)
        if expected_parent is None:
            raise ValueError("complete JobSubmissionHistory reads require a submission id")
        required = {"id", "status", "jobSubmission"}
        if not required.issubset({part.strip() for part in fields.split(",")}):
            raise ValueError(
                "complete JobSubmissionHistory reads require id,status,jobSubmission"
            )
        rows = self._paged(  # type: ignore[attr-defined]
            "query",
            "JobSubmissionHistory",
            fields=fields,
            selector=f"jobSubmission.id={int(expected_parent)}",
            count=SEARCH_PAGE_CAP,
            require_complete=True,
        )
        self._validate_scoped_rows(
            rows,
            entity="JobSubmissionHistory",
            parent_field="jobSubmission",
            expected_parent=expected_parent,
        )
        if any(not isinstance(row.get("status"), str) for row in rows):
            raise BullhornApiError(
                "Bullhorn complete JobSubmissionHistory read had an invalid shape"
            )
        return rows

    def list_file_attachments_strict(
        self,
        *,
        candidate_id: str | int,
        fields: str,
    ) -> list[dict]:
        expected = _positive_id(candidate_id)
        if expected is None:
            raise ValueError("fileAttachment reads require a candidate id")
        payload = self._request(  # type: ignore[attr-defined]
            "GET",
            f"entity/Candidate/{int(expected)}/fileAttachments",
            params={"fields": fields},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise BullhornApiError("Bullhorn fileAttachment read returned malformed data")
        seen: set[str] = set()
        for row in data:
            attachment_id = _positive_id(row.get("id"))
            if attachment_id is None or attachment_id in seen:
                raise BullhornApiError("Bullhorn fileAttachment read had an invalid id")
            if not isinstance(row.get("name"), str):
                raise BullhornApiError("Bullhorn fileAttachment read had an invalid shape")
            seen.add(attachment_id)
        return [dict(row) for row in data]

    @staticmethod
    def _validate_scoped_rows(
        rows: list[dict],
        *,
        entity: str,
        parent_field: str,
        expected_parent: str,
    ) -> None:
        seen: set[str] = set()
        for row in rows:
            row_id = _positive_id(row.get("id"))
            if row_id is None or row_id in seen:
                raise BullhornApiError(
                    f"Bullhorn complete {entity} read had an invalid or duplicate id"
                )
            if _association_id(row, parent_field) != expected_parent:
                raise BullhornApiError(
                    f"Bullhorn complete {entity} read violated its parent scope"
                )
            seen.add(row_id)
