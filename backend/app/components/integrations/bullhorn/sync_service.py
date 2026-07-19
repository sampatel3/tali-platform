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
the shared enabled + unpaused + lifecycle-ready agent policy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....services.document_service import sanitize_json_for_storage
from . import sync_candidates, sync_events, sync_jobs
from .errors import BullhornAuthError, BullhornRateLimitError
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


class BullhornSyncIncomplete(Exception):
    """One or more remote entities failed; the same full run must retry."""


class BullhornSyncLeaseLost(Exception):
    """The per-org provider lease can no longer be proven owned.

    Continuing after this point could overlap another worker and replay remote
    reads/writes against partially-mutated local state.  Treat this as a failed,
    retryable run rather than a user-requested cancellation.
    """


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
        progress["updated_at"] = self._now().isoformat()
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

    def sync_org(
        self,
        db: Session,
        org: Organization,
        *,
        mode: str = "full",
        ownership_lost: Callable[[], bool] | None = None,
    ) -> dict:
        """Full sync: JobOrders → Roles → JobSubmissions → Candidates/apps/events.

        Returns the final progress dict. Raises :class:`BullhornSyncCancelled` if
        cancellation was requested at a checkpoint.
        """
        now = self._now()
        provider_guard = lambda: self._provider_checkpoint(
            db, org, ownership_lost=ownership_lost
        )
        provider_guard()
        queued = (
            org.bullhorn_sync_progress
            if isinstance(org.bullhorn_sync_progress, dict)
            else {}
        )
        tracking = {
            key: queued[key]
            for key in (
                "run_id",
                "trigger",
                "queued_at",
                "dispatch_attempts",
                "run_attempts",
                "last_dispatched_at",
            )
            if queued.get(key) is not None
        }
        progress: dict = {
            **tracking,
            "phase": "job_orders",
            "mode": mode,
            "started_at": now.isoformat(),
            "finished_at": None,
            "jobs_total": 0,
            "jobs_processed": 0,
            "roles_closed": 0,
            "candidates_seen": 0,
            "candidates_upserted": 0,
            "applications_upserted": 0,
            "applications_deleted": 0,
            "authority_skipped": 0,
            "notes_imported": 0,
            "history_events": 0,
            "errors": [],
            "cancel_requested": bool(queued.get("cancel_requested", False)),
        }
        provider_guard()
        self._persist_progress(db, org, progress)
        provider_guard()

        # A full sync is also the durable missed-close repair. Fetch a complete,
        # stable open-id set before changing any local lifecycle state; partial
        # pagination raises and leaves all active roles untouched.
        job_orders = self.client.search_open_job_orders_complete(fields=JOB_ORDER_FIELDS)
        provider_guard()
        _open_ids, repair = sync_jobs.repair_roles_from_complete_open_snapshot(
            db,
            org,
            job_orders,
            closed_at=now,
        )
        progress["roles_closed"] = repair["roles_closed"]
        progress["job_order_repair"] = {
            "checked_at": now.isoformat(),
            "source": "full_sync",
            **repair,
        }
        progress["jobs_total"] = len(job_orders)
        progress["phase"] = "candidates"
        provider_guard()
        self._persist_progress(db, org, progress)

        remote_submission_ids: set[str] = set()
        for job_order in job_orders:
            provider_guard()
            if not isinstance(job_order, dict):
                progress["errors"].append("JobOrder unknown: invalid_payload")
                progress["jobs_processed"] += 1
                provider_guard()
                self._persist_progress(db, org, progress)
                continue
            try:
                job_submission_ids = self._sync_one_job_order(
                    db,
                    org,
                    job_order,
                    progress,
                    now,
                    provider_guard=provider_guard,
                )
                duplicate_ids = remote_submission_ids.intersection(job_submission_ids)
                if duplicate_ids:
                    raise ValueError(
                        "complete JobSubmission snapshots contained a duplicate id"
                    )
                remote_submission_ids.update(job_submission_ids)
            except BullhornSyncCancelled:
                raise
            except BullhornSyncLeaseLost:
                raise
            except (BullhornAuthError, BullhornRateLimitError):
                raise
            except Exception as exc:  # pragma: no cover — isolate a bad job order
                db.rollback()
                logger.error(
                    "Bullhorn JobOrder sync failed org_id=%s job_order_id=%s error_type=%s",
                    org.id,
                    job_order.get("id"),
                    type(exc).__name__,
                )
                progress["errors"].append(
                    f"JobOrder {job_order.get('id')}: {type(exc).__name__}"
                )
            progress["jobs_processed"] += 1
            provider_guard()
            self._persist_progress(db, org, progress)

        if not progress["errors"]:
            provider_guard()
            progress["applications_deleted"] = (
                sync_candidates.repair_missing_active_submissions(
                    db,
                    org,
                    remote_submission_ids=remote_submission_ids,
                    deleted_at=now,
                    provider_guard=provider_guard,
                )
            )
            provider_guard()

        progress["phase"] = "failed" if progress["errors"] else "completed"
        progress["finished_at"] = self._now().isoformat()
        progress["db_snapshot"] = self._db_snapshot(db, org)
        provider_guard()
        self._persist_progress(db, org, progress)
        if progress["errors"]:
            raise BullhornSyncIncomplete(
                f"Bullhorn full sync had {len(progress['errors'])} failed entities"
            )
        return progress

    def _sync_one_job_order(
        self,
        db: Session,
        org: Organization,
        job_order: dict,
        progress: dict,
        now: datetime,
        *,
        provider_guard: Callable[[], None],
    ) -> set[str]:
        job_order_id = str(job_order.get("id") or "").strip()
        if not job_order_id.isdigit():
            raise ValueError("JobOrder id is missing")
        role, _created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
        if role is None:
            raise ValueError("JobOrder could not be materialized")
        if str(role.workable_job_id or "").strip():
            progress["authority_skipped"] += 1
            return set()
        provider_guard()
        db.commit()  # commit the role before walking its submissions

        provider_guard()
        submissions = self.client.query_job_submissions_complete(
            job_order_id=int(job_order_id),
            fields=JOB_SUBMISSION_FIELDS,
        )
        provider_guard()
        active_submission_ids: set[str] = set()
        for submission in submissions:
            provider_guard()
            if not isinstance(submission, dict):
                progress["errors"].append(
                    "JobSubmission unknown: invalid_payload"
                )
                continue
            if submission.get("isDeleted"):
                continue
            # Defensive re-scope to this JobOrder: real Bullhorn honours the
            # ``where`` above, but we never trust the transport to have filtered —
            # a submission for another order must not attach to this role.
            sub_order_id = str((submission.get("jobOrder") or {}).get("id") or "").strip()
            if sub_order_id and job_order_id and sub_order_id != job_order_id:
                progress["errors"].append(
                    f"JobSubmission {submission.get('id')}: scope_mismatch"
                )
                continue
            raw_submission_id = submission.get("id")
            if (
                isinstance(raw_submission_id, bool)
                or not isinstance(raw_submission_id, (str, int))
                or not str(raw_submission_id).isdigit()
                or int(raw_submission_id) <= 0
            ):
                progress["errors"].append(
                    f"JobSubmission {raw_submission_id}: invalid_id"
                )
                continue
            submission_id = str(int(raw_submission_id))
            if submission_id in active_submission_ids:
                progress["errors"].append(
                    f"JobSubmission {submission_id}: duplicate_id"
                )
                continue
            active_submission_ids.add(submission_id)
            try:
                candidate_payload = self._resolve_candidate_payload(
                    submission, provider_guard=provider_guard
                )
                progress["candidates_seen"] += 1
                counters = sync_candidates.sync_submission(
                    db=db,
                    org=org,
                    role=role,
                    submission=submission,
                    candidate_payload=candidate_payload,
                    client=self.client,
                    now=now,
                    provider_guard=provider_guard,
                )
                provider_guard()
                progress["candidates_upserted"] += counters.get("candidate_upserted", 0)
                progress["applications_upserted"] += counters.get("application_upserted", 0)
                progress["authority_skipped"] += counters.get("authority_skipped", 0)
                if counters.get("authority_skipped"):
                    continue
                if not counters.get("application_upserted"):
                    raise RuntimeError("JobSubmission application was not materialized")

                app = self._application_for_submission(db, org, submission)
                if app is None:
                    raise RuntimeError("JobSubmission application lookup failed")
                progress["history_events"] += sync_events.import_submission_history(
                    db=db,
                    app=app,
                    submission_id=submission_id,
                    client=self.client,
                    provider_guard=provider_guard,
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
                        provider_guard=provider_guard,
                    )
                provider_guard()
                db.commit()
            except BullhornSyncCancelled:
                raise
            except BullhornSyncLeaseLost:
                raise
            except (BullhornAuthError, BullhornRateLimitError):
                raise
            except Exception as exc:  # pragma: no cover — isolate a bad submission
                db.rollback()
                logger.error(
                    "Bullhorn submission sync failed org_id=%s submission_id=%s error_type=%s",
                    org.id,
                    submission.get("id"),
                    type(exc).__name__,
                )
                progress["errors"].append(
                    f"JobSubmission {submission.get('id')}: {type(exc).__name__}"
                )
        return active_submission_ids

    def _resolve_candidate_payload(
        self,
        submission: dict,
        *,
        provider_guard: Callable[[], None],
    ) -> dict:
        """Resolve the full Candidate for a submission.

        The submission's ``candidate`` association comes back id-only
        (``{"id": N}``), so we fetch the candidate's profile fields by id. The
        returned rows are filtered to the target id (defensive: a fake/relaxed
        backend may echo more than the id-scoped query asked for).
        """
        nested = submission.get("candidate")
        cand_id = str((nested or {}).get("id") or "").strip()
        if not cand_id.isdigit():
            raise ValueError("JobSubmission candidate id is missing")
        provider_guard()
        matched = self.client.get_candidate_exact(
            cand_id,
            fields=sync_candidates.CANDIDATE_FIELDS,
        )
        provider_guard()
        if matched is not None:
            return matched
        raise LookupError(f"Bullhorn Candidate {cand_id} was not returned")

    def _provider_checkpoint(
        self,
        db: Session,
        org: Organization,
        *,
        ownership_lost: Callable[[], bool] | None,
    ) -> None:
        """Stop at every safe provider boundary.

        User cancellation retains priority when both signals arrive together.
        The ownership callback is evaluated before and after every Bullhorn
        operation, so an ambiguous completed call is never replayed by this
        worker after its lease is lost.
        """
        if self._is_cancel_requested(db, org):
            raise BullhornSyncCancelled()
        if ownership_lost is not None and ownership_lost():
            raise BullhornSyncLeaseLost()

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
