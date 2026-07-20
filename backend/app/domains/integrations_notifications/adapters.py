from __future__ import annotations

from typing import Protocol

from ...components.integrations.e2b.service import E2BService
from ...components.integrations.workable.service import WorkableService
from ...components.notifications.email_client import EmailService
from ...platform.config import settings


class SandboxAdapter(Protocol):
    def create_sandbox(self): ...


class WorkableAdapter(Protocol):
    def post_candidate_comment(
        self,
        candidate_id: str,
        member_id: str,
        body: str,
        *,
        trusted_role_values: tuple[str, ...] | list[str] | None = None,
    ) -> dict: ...
    def move_candidate(self, *, candidate_id: str, member_id: str, target_stage: str) -> dict: ...
    def disqualify_candidate(self, *, candidate_id: str, member_id: str, disqualify_reason_id: str | None = None, disqualify_note: str | None = None, withdrew: bool = False) -> dict: ...
    def revert_candidate_disqualification(self, *, candidate_id: str, member_id: str) -> dict: ...


class EmailAdapter(Protocol):
    def send_assessment_invite(self, **kwargs) -> dict: ...


class StripeAdapter(Protocol):
    def create_customer(self, email: str, name: str) -> dict: ...


def build_sandbox_adapter() -> E2BService:
    return E2BService(settings.E2B_API_KEY)


def build_workable_adapter(*, access_token: str, subdomain: str) -> WorkableService:
    return WorkableService(access_token=access_token, subdomain=subdomain)


def build_email_adapter() -> EmailService:
    return EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
