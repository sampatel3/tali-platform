"""Email suppression list — the legal/deliverability guardrail for outreach.

Two kinds of rows share one table, distinguished by ``organization_id``:

- **Platform-global** (``organization_id`` NULL) — hard bounces + spam
  complaints reported by Resend's webhook. These protect the shared sender
  domain across every org: once an address hard-bounces or complains, no org
  may mail it.
- **Org-scoped** (``organization_id`` set) — that org's own unsubscribes
  (public one-click link) and manual blocks. Scoped to the org that owns the
  relationship.

Enforcement (campaign send paths, built in the next PR) checks *both* a global
row and an org row for each recipient. This PR only lands the model, the
service, and the webhook + unsubscribe writers.

NOTE on the unique constraint: Postgres treats NULL as distinct in a UNIQUE
constraint, so ``(NULL, 'a@x')`` can be inserted twice. The service dedupes
global rows in code (query-first-then-insert) rather than relying on the DB —
see ``email_suppression_service.suppress``.
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..platform.database import Base


# Suppression reasons, ordered weakest → strongest. A stronger reason may
# overwrite a weaker one on the same (org, email) row (e.g. a later complaint
# overrides an earlier unsubscribe); the service enforces this precedence.
SUPPRESSION_REASON_MANUAL = "manual"
SUPPRESSION_REASON_UNSUBSCRIBED = "unsubscribed"
SUPPRESSION_REASON_BOUNCED = "bounced"
SUPPRESSION_REASON_COMPLAINED = "complained"

SUPPRESSION_REASONS = (
    SUPPRESSION_REASON_UNSUBSCRIBED,
    SUPPRESSION_REASON_BOUNCED,
    SUPPRESSION_REASON_COMPLAINED,
    SUPPRESSION_REASON_MANUAL,
)

# Higher rank wins on conflict. Order: complained > bounced > unsubscribed > manual.
SUPPRESSION_REASON_RANK = {
    SUPPRESSION_REASON_MANUAL: 1,
    SUPPRESSION_REASON_UNSUBSCRIBED: 2,
    SUPPRESSION_REASON_BOUNCED: 3,
    SUPPRESSION_REASON_COMPLAINED: 4,
}


class EmailSuppression(Base):
    __tablename__ = "email_suppressions"
    __table_args__ = (
        # Upsert key. NULL org = platform-global; the service dedupes those in
        # code since Postgres won't (NULL is distinct in a UNIQUE constraint).
        UniqueConstraint(
            "organization_id", "email_normalized", name="uq_email_suppression_org_email"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    # NULL = platform-global (protects the shared sender domain across orgs).
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=True
    )
    email_normalized = Column(String, nullable=False, index=True)
    # One of SUPPRESSION_REASONS.
    reason = Column(String, nullable=False)
    # webhook | link | recruiter — how the suppression was recorded.
    source = Column(String, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
