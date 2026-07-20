from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ...models.candidate_application import CandidateApplication
    from ...models.role import Role


class ATSProvider(Protocol):
    """The shared ATS surface used by provider-agnostic machinery.

    Only the operations the shared code (op_runner, invite hand-off, candidate
    context enrichment, CV fetch) actually calls today live here — Workable-only
    surfaces (OAuth connect, sync internals, assessment result push, note
    templating / actor-member resolution) stay direct. A Bullhorn arm implements
    the same six methods.

    The write methods return the Workable result dict
    ``{success, action, code, message, config, response}`` (callers read
    ``result["config"]["actor_member_id"]`` off the move result); they do NOT
    return ``None``. ``strict_workable_writes`` / ``WorkableWritebackError`` are
    engaged by the caller AROUND these methods and are honoured by the backing
    functions — providers must purely delegate.
    """

    ats: str

    def get_candidate(self, candidate_id: str) -> dict: ...

    def download_candidate_resume(self, candidate_payload: dict) -> tuple[str, bytes] | None: ...

    def move_application(
        self, *, candidate_id: str, target_stage: str, role: Role | None = None
    ) -> dict: ...

    def reject_application(
        self,
        *,
        app: CandidateApplication | None,
        role: Role | None = None,
        reason: str | None = None,
        note_template: str | None = None,
        threshold_100: float | int | None = None,
        withdrew: bool = False,
    ) -> dict: ...

    def revert_application(
        self, *, app: CandidateApplication | None, role: Role | None = None
    ) -> dict: ...

    def post_note(
        self,
        *,
        candidate_id: str,
        member_id: str,
        body: str,
        role: Role | None = None,
        trusted_role_values: tuple[str, ...] | list[str] | None = None,
    ) -> dict: ...
