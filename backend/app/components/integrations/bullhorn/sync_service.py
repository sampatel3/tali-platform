"""Bullhorn full-sync orchestrator.

Mirrors Workable's ``WorkableSyncService.sync_org`` but against Bullhorn's data
model and the ``org.bullhorn_sync_*`` progress columns (Bullhorn has no
per-run table — progress + cancellation live in the ``bullhorn_sync_progress``
JSON, the same way the connect surface reads it).

Walk shape (build plan §6):
  1. ``search/JobOrder`` (open) → :func:`sync_jobs.upsert_role_from_job_order`.
  2. Per JobOrder: ``query/JobSubmission`` (this order, not deleted) → resolve
     each Candidate, then :func:`sync_candidates.sync_submission` (Candidate +
     CandidateApplication upsert, stage mapping via AtsStageMap, CV, gated
     fresh-candidate scoring).
  3. Per submission: :func:`sync_events.import_submission_history` (append-only
     status-change events) + :func:`sync_events.import_notes` (candidate context).

Progress is persisted to ``org.bullhorn_sync_progress`` after each JobOrder so
the connect UI can poll it. Cancellation is checkpointed the same way Workable
does: the runner writes ``cancel_requested`` into the progress JSON and the loop
raises :class:`BullhornSyncCancelled` at the next checkpoint.

Cost safety: this module never triggers paid re-scoring. New candidates are
scored once via the shared enqueue path inside :mod:`sync_candidates`, gated on
``role.starred_for_auto_sync`` exactly like the Workable import.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....services.document_service import sanitize_json_for_storage
from . import sync_candidates, sync_events, sync_jobs
from .service import BullhornService

logger = logging.getLogger("taali.bullhorn.sync")

# Read contracts. Bullhorn returns only requested fields.
JOB_ORDER_FIELDS = (
    "id,title,name,status,isOpen,employmentType,address,clientCorporation,"
    "categories,description,publicDescription,dateLastModified"
)
# JobSubmission carries the candidate + jobOrder associations + the free-text
# status we map. Bare association names (``candidate`` / ``jobOrder``) come back
# as ``{"id": N}``; we resolve the full Candidate separately (a to-one expand via
# ``candidate(...)`` would nest the fields, but keeping the read simple + always
# resolving the candidate by id is what the importer's dedup ladder needs).
# ``dateAdded`` is the JobSubmission's remote-ATS applied date (epoch millis);
# we map it onto ``CandidateApplication.workable_created_at`` so the applied-date
# decision surfaces (main #900) have a real date for Bullhorn apps.
JOB_SUBMISSION_FIELDS = "id,status,isDeleted,dateAdded,dateLastModified,jobOrder,candidate"


class BullhornSyncCancelled(Exception):
    """Raised at a checkpoint when the runner requested cancellation."""


class BullhornSyncService:
    """Runs one full Bullhorn sync for an org against a live client."""

    def __init__(self, client: BullhornService):
        self.client = client

    # --- progress + cancellation -------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _db_snapshot(self, db: Session, org: Organization) -> dict:
        return {
            "roles_active": db.query(Role)
            .filter(Role.organization_id == org.id, Role.deleted_at.is_(None))
            .count(),
            "applications_active": db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.deleted_at.is_(None),
            )
            .count(),
            "candidates_active": db.query(Candidate)
            .filter(Candidate.organization_id == org.id, Candidate.deleted_at.is_(None))
            .count(),
        }

    def _persist_progress(self, db: Session, org: Organization, progress: dict) -> None:
        """Write the progress JSON to the org row and commit so the UI can poll."""
        org.bullhorn_sync_progress = sanitize_json_for_storage(progress)
        db.add(org)
        db.commit()

    def _is_cancel_requested(self, db: Session, org: Organization) -> bool:
        """Re-read the org's progress JSON; True when the runner asked to cancel.

        Mirrors Workable's cancel checkpoint (which reads a dedicated column) —
        Bullhorn parks the flag inside ``bullhorn_sync_progress`` since there's
        no separate column. ``db.refresh`` picks up a flag written by the
        cancel route in another session.
        """
        try:
            db.refresh(org, attribute_names=["bullhorn_sync_progress"])
        except Exception:  # pragma: no cover — refresh best-effort
            pass
        progress = org.bullhorn_sync_progress if isinstance(org.bullhorn_sync_progress, dict) else {}
        return bool(progress.get("cancel_requested"))

    # --- the walk -----------------------------------------------------------

    def sync_org(self, db: Session, org: Organization, *, mode: str = "full") -> dict:
        """Full sync: JobOrders → Roles → JobSubmissions → Candidates/apps/events.

        Returns the final progress dict. Raises :class:`BullhornSyncCancelled` if
        cancellation was requested at a checkpoint.
        """
        now = self._now()
        progress: dict = {
            "phase": "job_orders",
            "mode": mode,
            "started_at": now.isoformat(),
            "finished_at": None,
            "jobs_total": 0,
            "jobs_processed": 0,
            "candidates_seen": 0,
            "candidates_upserted": 0,
            "applications_upserted": 0,
            "notes_imported": 0,
            "history_events": 0,
            "errors": [],
            "cancel_requested": False,
        }
        self._persist_progress(db, org, progress)

        job_orders = self.client.search_job_orders(fields=JOB_ORDER_FIELDS, query="isOpen:true")
        progress["jobs_total"] = len(job_orders)
        progress["phase"] = "candidates"
        self._persist_progress(db, org, progress)

        for job_order in job_orders:
            if self._is_cancel_requested(db, org):
                raise BullhornSyncCancelled()
            try:
                self._sync_one_job_order(db, org, job_order, progress, now)
            except BullhornSyncCancelled:
                raise
            except Exception as exc:  # pragma: no cover — isolate a bad job order
                logger.exception(
                    "Bullhorn JobOrder sync failed org_id=%s job_order_id=%s",
                    org.id,
                    job_order.get("id"),
                )
                progress["errors"].append(
                    f"JobOrder {job_order.get('id')}: {type(exc).__name__}"
                )
            progress["jobs_processed"] += 1
            self._persist_progress(db, org, progress)

        progress["phase"] = "completed"
        progress["finished_at"] = self._now().isoformat()
        progress["db_snapshot"] = self._db_snapshot(db, org)
        self._persist_progress(db, org, progress)
        return progress

    def _sync_one_job_order(
        self, db: Session, org: Organization, job_order: dict, progress: dict, now: datetime
    ) -> None:
        role, _created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
        if role is None:
            return
        db.commit()  # commit the role before walking its submissions

        job_order_id = str(job_order.get("id") or "").strip()
        submissions = self.client.query_job_submissions(
            fields=JOB_SUBMISSION_FIELDS,
            where=f"jobOrder.id={int(job_order_id)} AND isDeleted=false" if job_order_id.isdigit() else "",
        )
        for submission in submissions:
            if self._is_cancel_requested(db, org):
                raise BullhornSyncCancelled()
            if not isinstance(submission, dict):
                continue
            if submission.get("isDeleted"):
                continue
            # Defensive re-scope to this JobOrder: real Bullhorn honours the
            # ``where`` above, but we never trust the transport to have filtered —
            # a submission for another order must not attach to this role.
            sub_order_id = str((submission.get("jobOrder") or {}).get("id") or "").strip()
            if sub_order_id and job_order_id and sub_order_id != job_order_id:
                continue
            candidate_payload = self._resolve_candidate_payload(submission)
            progress["candidates_seen"] += 1
            try:
                counters = sync_candidates.sync_submission(
                    db=db,
                    org=org,
                    role=role,
                    submission=submission,
                    candidate_payload=candidate_payload,
                    client=self.client,
                    now=now,
                )
                progress["candidates_upserted"] += counters.get("candidate_upserted", 0)
                progress["applications_upserted"] += counters.get("application_upserted", 0)

                app = self._application_for_submission(db, org, submission)
                if app is not None:
                    progress["history_events"] += sync_events.import_submission_history(
                        db=db, app=app, submission_id=str(submission.get("id")), client=self.client
                    )
                    bullhorn_candidate_id = str(
                        (submission.get("candidate") or {}).get("id")
                        or candidate_payload.get("id")
                        or ""
                    ).strip()
                    if bullhorn_candidate_id:
                        progress["notes_imported"] += sync_events.import_notes(
                            db=db,
                            app=app,
                            bullhorn_candidate_id=bullhorn_candidate_id,
                            client=self.client,
                            now=now,
                        )
                db.commit()
            except BullhornSyncCancelled:
                raise
            except Exception:  # pragma: no cover — isolate a bad submission
                db.rollback()
                logger.exception(
                    "Bullhorn submission sync failed org_id=%s submission_id=%s",
                    org.id,
                    submission.get("id"),
                )

    def _resolve_candidate_payload(self, submission: dict) -> dict:
        """Resolve the full Candidate for a submission.

        The submission's ``candidate`` association comes back id-only
        (``{"id": N}``), so we fetch the candidate's profile fields by id. The
        returned rows are filtered to the target id (defensive: a fake/relaxed
        backend may echo more than the id-scoped query asked for).
        """
        nested = submission.get("candidate")
        if isinstance(nested, dict) and (nested.get("email") or nested.get("name") or nested.get("firstName")):
            return nested
        cand_id = str((nested or {}).get("id") or "").strip()
        if not cand_id.isdigit():
            return nested if isinstance(nested, dict) else {}
        try:
            rows = self.client.search_candidates(
                fields=sync_candidates.CANDIDATE_FIELDS, query=f"id:{cand_id}"
            )
        except Exception:  # pragma: no cover
            logger.exception("Bullhorn candidate fetch failed id=%s", cand_id)
            return nested if isinstance(nested, dict) else {}
        matched = [r for r in rows if str(r.get("id")) == cand_id]
        if matched:
            return matched[0]
        return nested if isinstance(nested, dict) else {}

    def _application_for_submission(
        self, db: Session, org: Organization, submission: dict
    ) -> CandidateApplication | None:
        submission_id = str(submission.get("id") or "").strip()
        if not submission_id:
            return None
        return (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.bullhorn_job_submission_id == submission_id,
            )
            .first()
        )
