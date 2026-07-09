from __future__ import annotations

from typing import TYPE_CHECKING

from ....domains.integrations_notifications.adapters import build_workable_adapter
from ....services.workable_actions_service import (
    disqualify_candidate_in_workable,
    move_candidate_in_workable,
    revert_candidate_disqualification_in_workable,
)

if TYPE_CHECKING:
    from ....models.candidate_application import CandidateApplication
    from ....models.organization import Organization
    from ....models.role import Role
    from .service import WorkableService


class WorkableProvider:
    """Thin ``ATSProvider`` over the existing Workable machinery.

    Every method is a 1:1 delegation onto ``WorkableService`` (reads/notes) or the
    org-keyed ``workable_actions_service`` write functions — no logic moves, no
    behavior change. The write functions carry the ``strict_workable_writes`` /
    ``WorkableWritebackError`` contract themselves, so this wrapper adds no
    try/except and no return-value massaging.
    """

    ats = "workable"

    def __init__(self, org: Organization):
        self.org = org

    def _client(self) -> WorkableService:
        return build_workable_adapter(
            access_token=self.org.workable_access_token,
            subdomain=self.org.workable_subdomain,
        )

    def get_candidate(self, candidate_id: str) -> dict:
        return self._client().get_candidate(candidate_id)

    def download_candidate_resume(self, candidate_payload: dict) -> tuple[str, bytes] | None:
        return self._client().download_candidate_resume(candidate_payload)

    def move_application(
        self, *, candidate_id: str, target_stage: str, role: Role | None = None
    ) -> dict:
        return move_candidate_in_workable(
            org=self.org, candidate_id=candidate_id, target_stage=target_stage, role=role
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
        return disqualify_candidate_in_workable(
            org=self.org,
            app=app,
            role=role,
            reason=reason,
            note_template=note_template,
            threshold_100=threshold_100,
            withdrew=withdrew,
        )

    def revert_application(
        self, *, app: CandidateApplication | None, role: Role | None = None
    ) -> dict:
        return revert_candidate_disqualification_in_workable(org=self.org, app=app, role=role)

    def post_note(self, *, candidate_id: str, member_id: str, body: str) -> dict:
        return self._client().post_candidate_comment(candidate_id, member_id, body)
