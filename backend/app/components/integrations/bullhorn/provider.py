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
    ) -> dict:
        submission_id = str(getattr(app, "bullhorn_job_submission_id", None) or "").strip()
        return write_back.reject_submission(
            self.db, org=self.org, client=self._client(), submission_id=submission_id
        )

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
    ) -> dict:
        """Post a Note about the candidate. ``member_id`` is unused for Bullhorn
        (authorship is the API user's session), kept for protocol parity."""
        return write_back.post_note(
            self.db,
            org=self.org,
            client=self._client(),
            candidate_id=candidate_id,
            body=body,
            job_order_id=getattr(role, "bullhorn_job_order_id", None),
        )
