"""``ATSProvider`` arm for Bullhorn.

The Bullhorn twin of ``workable/provider.py`` — a thin adapter that satisfies the
shared :class:`~app.components.integrations.base.ATSProvider` protocol so the
provider-agnostic machinery (op_runner, candidate-context enrichment, CV fetch)
can drive Bullhorn without knowing which ATS it's talking to. Resolved via
``resolver.resolve_ats_provider`` (PR-1 seam) — build plan §6 line 89.

Reads (``get_candidate`` / ``download_candidate_resume``) delegate to the typed
:class:`BullhornService` client. Writes (``move_application`` / ``reject_application``
/ ``revert_application`` / ``post_note``) delegate to :mod:`write_back`, which
carries the reverse stage-mapping (Taali intent → the org's free-text status,
never guessed), the shared ``WorkableWritebackError`` gating contract, and the
local-write-wins stamp on success.

Unlike ``WorkableProvider`` (constructed with just the org), this provider also
carries the ``db`` session: the reverse stage-map lookup and the local-write
stamp both need it, and every call site that resolves a provider already has a
session in scope. ``candidate_id`` here is the Bullhorn id — for writes it is the
JobSubmission id (the write target); for reads it is the Candidate id.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import TYPE_CHECKING

from ....platform.config import settings
from ....platform.secrets import decrypt_text
from ....services.ats_note_policy import contains_assessment_lifecycle_content
from ....services.workable_actions_service import (
    WorkableWritebackError,
    build_workable_reject_note,
)
from .auth import BullhornAuth
from .credential_state import credential_generation, persist_rotated_credentials
from .errors import BullhornAuthError
from .service import BullhornService
from . import write_back

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ....models.candidate_application import CandidateApplication
    from ....models.organization import Organization
    from ....models.role import Role

logger = logging.getLogger("taali.bullhorn.provider")

# fileAttachment metadata fields we need to pick a resume (mirrors sync_candidates).
_FILE_ATTACHMENT_FIELDS = "id,name,type,contentType,dateAdded"
_TEXT_EXTS = {"pdf", "docx", "txt"}
_PREVIEW_EXTS = {"pdf", "png", "jpg", "jpeg", "webp"}
# Candidate profile fields (mirrors sync_candidates.CANDIDATE_FIELDS).
_CANDIDATE_FIELDS = "id,firstName,lastName,name,email,phone,mobile,occupation,address,dateLastModified"


def _make_persist_hook(live_org: Organization):
    """A ``persist_tokens`` hook re-encrypting + durably writing a rotated refresh
    token in its own transaction (the single-use rotation invariant). Identical to
    ``sync_runner._make_persist_hook`` — kept here so the write-back path does not
    import the sync runner just for the hook."""
    expected_generation = credential_generation(live_org)

    def _persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        encrypted_refresh, persisted_rest_url = persist_rotated_credentials(
            org_id=int(live_org.id),
            expected_generation=expected_generation,
            refresh_token=refresh_token,
            rest_url=rest_url,
        )
        # The durable CAS commit above is authoritative. Mirror it into the ORM
        # instance held by the caller for another client in this unit of work.
        live_org.bullhorn_refresh_token = encrypted_refresh
        if persisted_rest_url:
            live_org.bullhorn_rest_url = persisted_rest_url

    return _persist


class BullhornProvider:
    """Thin ``ATSProvider`` over the Bullhorn client + write-back helpers."""

    ats = "bullhorn"

    def __init__(self, org: Organization, db: Session):
        self.org = org
        self.db = db

    # --- client ------------------------------------------------------------

    def _client(self) -> BullhornService:
        """Construct an authed client from the org's stored (encrypted) creds.

        Same construction as ``sync_runner._build_service`` (decrypt secret +
        refresh token, persist-hook for rotation crash-safety).
        """
        org = self.org
        try:
            client_secret = decrypt_text(
                org.bullhorn_client_secret or "", settings.SECRET_KEY
            )
            refresh_token = decrypt_text(
                org.bullhorn_refresh_token or "", settings.SECRET_KEY
            )
        except Exception:
            raise BullhornAuthError(
                "Stored Bullhorn credentials are unavailable; reconnect required"
            ) from None
        auth = BullhornAuth(
            username=org.bullhorn_username,
            client_id=org.bullhorn_client_id,
            client_secret=client_secret,
            refresh_token=refresh_token or None,
            persist_tokens=_make_persist_hook(org),
            rest_url=org.bullhorn_rest_url,
        )
        return BullhornService(auth, client_id=org.bullhorn_client_id)

    # --- reads -------------------------------------------------------------

    def get_candidate(self, candidate_id: str) -> dict:
        """Fetch a Candidate's profile by Bullhorn id (mirrors the sync's dedup fetch)."""
        cand_id = str(candidate_id or "").strip()
        if not cand_id:
            return {}
        rows = self._client().search_candidates(fields=_CANDIDATE_FIELDS, query=f"id:{cand_id}")
        matched = next((r for r in rows if str(r.get("id")) == cand_id), None)
        return matched or (rows[0] if rows else {})

    def download_candidate_resume(self, candidate_payload: dict) -> tuple[str, bytes] | None:
        """Loose-match a Resume-typed fileAttachment → (filename, bytes).

        Returns None when the candidate has no resolvable resume. Mirrors
        ``sync_candidates._fetch_and_store_cv``'s attachment-pick, minus the
        storage/text-extraction (the caller owns that).
        """
        cand_id = str((candidate_payload or {}).get("id") or "").strip()
        if not cand_id:
            return None
        client = self._client()
        try:
            attachments = client.list_file_attachments(
                candidate_id=cand_id, fields=_FILE_ATTACHMENT_FIELDS
            )
        except Exception as exc:  # pragma: no cover — never hard-fail a CV fetch
            logger.error(
                "Bullhorn fileAttachments listing failed candidate=%s error_type=%s",
                cand_id,
                type(exc).__name__,
            )
            return None

        def _ext_ok(meta: dict) -> bool:
            name = str(meta.get("name") or "")
            ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
            return ext in (_TEXT_EXTS | _PREVIEW_EXTS)

        def _is_resume(meta: dict) -> bool:
            return "resume" in str(meta.get("type") or "").lower()

        meta = next((a for a in attachments if _is_resume(a) and _ext_ok(a)), None)
        if meta is None:
            meta = next((a for a in attachments if _ext_ok(a)), None)
        if meta is None:
            return None
        file_id = meta.get("id")
        filename = str(meta.get("name") or f"resume-{file_id}")
        try:
            content = client.get_file_raw(candidate_id=cand_id, file_id=file_id)
        except Exception as exc:  # pragma: no cover
            logger.error(
                "Bullhorn CV download failed candidate=%s file=%s error_type=%s",
                cand_id,
                file_id,
                type(exc).__name__,
            )
            return None
        if not content:
            # Fallback: convertToText for a doc extension we don't extract locally.
            return None
        # Guess a content-type-friendly filename extension if missing.
        if "." not in filename:
            ext = mimetypes.guess_extension(str(meta.get("contentType") or "")) or ""
            filename = f"{filename}{ext}"
        return filename, content

    # --- writes (delegate to write_back; JobSubmission is the target) -------

    def move_application(
        self, *, candidate_id: str, target_stage: str, role: Role | None = None
    ) -> dict:
        """Move the JobSubmission to the org's status for the Taali intent.

        ``candidate_id`` is the Bullhorn JobSubmission id (the write target);
        ``target_stage`` is the Taali intent (e.g. ``"advanced"``) — write_back
        reverse-maps it to the org's remote status (never guessed).
        """
        return write_back.move_submission_status(
            self.db,
            org=self.org,
            client=self._client(),
            submission_id=candidate_id,
            taali_intent=target_stage,
        )

    def reject_application(
        self,
        *,
        app: CandidateApplication | None,
        role: Role | None = None,
        reason: str | None = None,
        note_template: str | None = None,
        threshold_100: float | int | None = None,
        withdrew: bool = False,
        include_movement_note: bool = True,
    ) -> dict:
        submission_id = str(getattr(app, "bullhorn_job_submission_id", None) or "").strip()
        result = write_back.reject_submission(
            self.db, org=self.org, client=self._client(), submission_id=submission_id
        )
        if not result.get("success"):
            return result
        if result.get("skipped"):
            # Bullhorn already has the target rejection status. Reconcile
            # locally, but do not create a movement note for a no-op.
            return result
        if not include_movement_note:
            # Deferred/manual outcome jobs must checkpoint the confirmed status
            # before attempting Bullhorn's non-idempotent Note create. Their
            # handler calls ``post_rejection_movement_note`` after that commit.
            return result
        return self.post_rejection_movement_note(
            app=app,
            role=role,
            reason=reason,
            note_template=note_template,
            threshold_100=threshold_100,
            movement_result=result,
        )

    def post_rejection_movement_note(
        self,
        *,
        app: CandidateApplication | None,
        role: Role | None = None,
        reason: str | None = None,
        note_template: str | None = None,
        threshold_100: float | int | None = None,
        movement_result: dict | None = None,
    ) -> dict:
        """Best-effort summary for a rejection already confirmed in Bullhorn.

        Callers that need crash/redelivery safety must durably checkpoint the
        status movement before invoking this method. It never converts an
        already-confirmed movement into a retriable failure.
        """
        submission_id = str(
            getattr(app, "bullhorn_job_submission_id", None) or ""
        ).strip()
        result = dict(movement_result or {"success": True, "action": "move"})

        # The status movement is authoritative. A candidate note is useful ATS
        # context, but it is deliberately best-effort and happens only after
        # Bullhorn confirms the rejection. A note failure must never turn the
        # completed movement into a failed/retriable operation.
        config = dict(result.get("config") or {})
        result = {**result, "config": config}
        try:
            note = build_workable_reject_note(
                app=app,
                role=role or getattr(app, "role", None),
                template=note_template,
                reason=reason,
                threshold_100=threshold_100,
            )
            if not note:
                config["movement_note_status"] = "not_requested"
                return result
            note_role = role or getattr(app, "role", None)
            trusted_role_name = str(
                getattr(note_role, "name", None) or ""
            ).strip()
            if contains_assessment_lifecycle_content(
                note,
                trusted_role_values=(trusted_role_name,)
                if trusted_role_name
                else None,
            ):
                config["movement_note_status"] = "blocked_assessment_content"
                return result

            candidate = getattr(app, "candidate", None)
            candidate_id = str(
                getattr(candidate, "bullhorn_candidate_id", None) or ""
            ).strip()
            if not candidate_id:
                config["movement_note_status"] = "candidate_not_linked"
                return result

            note_result = write_back.post_note(
                self.db,
                org=self.org,
                client=self._client(),
                candidate_id=candidate_id,
                body=note,
                job_order_id=getattr(
                    role or getattr(app, "role", None),
                    "bullhorn_job_order_id",
                    None,
                ),
            )
            if note_result.get("success"):
                config["movement_note_status"] = "posted"
                return result

            config["movement_note_status"] = "failed"
            config["movement_note_code"] = str(note_result.get("code") or "unknown")
        except WorkableWritebackError as exc:
            # ``strict_workable_writes`` makes normal note failures raise. The
            # rejection is already confirmed remotely, so absorb the optional
            # note failure instead of replaying the status transition.
            config["movement_note_status"] = "failed"
            config["movement_note_code"] = exc.code
        except Exception as exc:  # pragma: no cover - defensive provider edge
            config["movement_note_status"] = "failed"
            config["movement_note_code"] = "unexpected_error"
            logger.warning(
                "Bullhorn rejection note preparation or post raised after "
                "confirmed movement "
                "submission_id=%s error_type=%s",
                submission_id,
                type(exc).__name__,
            )
        logger.warning(
            "Bullhorn rejection note failed after confirmed movement "
            "submission_id=%s code=%s",
            submission_id,
            config["movement_note_code"],
        )
        return result

    def revert_application(
        self, *, app: CandidateApplication | None, role: Role | None = None
    ) -> dict:
        submission_id = str(getattr(app, "bullhorn_job_submission_id", None) or "").strip()
        return write_back.revert_submission(
            self.db, org=self.org, client=self._client(), submission_id=submission_id
        )

    def post_note(
        self,
        *,
        candidate_id: str,
        member_id: str,
        body: str,
        role: Role | None = None,
        trusted_role_values: tuple[str, ...] | list[str] | None = None,
    ) -> dict:
        """Post a Note about the candidate. ``member_id`` is unused for Bullhorn
        (authorship is the API user's session), kept for protocol parity."""
        role_values = list(trusted_role_values or ())
        role_name = str(getattr(role, "name", None) or "").strip()
        if role_name:
            role_values.append(role_name)
        if contains_assessment_lifecycle_content(
            body, trusted_role_values=role_values
        ):
            code = "assessment_lifecycle_content_blocked"
            return {
                "success": False,
                "action": "note",
                "code": code,
                "error": code,
                "message": "Assessment lifecycle content stays in Taali",
                "config": {"ats": self.ats},
                "response": {"error": code},
            }
        return write_back.post_note(
            self.db,
            org=self.org,
            client=self._client(),
            candidate_id=candidate_id,
            body=body,
            job_order_id=getattr(role, "bullhorn_job_order_id", None),
        )
