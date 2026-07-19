"""One row per Message Batches API submission — the metering anchor.

The Batches API splits one logical operation across processes and time:
the submitting worker knows the attribution (feature, org, per-request
entity ids) but has no token usage yet; the polling worker sees the usage
in the results stream but — without this row — would know nothing about
who to bill. ``MeteredAnthropicClient`` writes this row at
``messages.batches.create`` time and reads it back at
``messages.batches.results`` time, so every batch result lands a
``claude_call_log`` + ``usage_events`` pair priced at the batch tier
(50% of standard) with the right attribution.

``metered_at`` doubles as the whole-batch idempotency latch. Per-result
idempotency lives in ``anthropic_batch_result_receipts`` so receipt writes stay
linear instead of rewriting a growing JSON document after every result.

``context`` holds the per-custom_id attribution map supplied at submit
time (``{custom_id: {"entity_id": ..., "role_id": ..., "user_id": ...}}``).
Legacy rows without a durable submission claim retain best-effort attribution.
Version-2 claimed submissions require an exact result identity set before any
entry is metered or the whole-batch latch advances.
"""
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.sql import func

from ..platform.database import Base


ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_INDEX = (
    "ix_anthropic_batch_jobs_known_accepted_recovery"
)
ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_PREDICATE = (
    "status = 'submission_ambiguous' "
    "AND organization_id IS NOT NULL "
    "AND (context -> '_submission_claim' ->> 'version') = '2' "
    "AND (context -> '_submission_claim' ->> 'state') = "
    "'provider_accepted_anchor_finalize_failed' "
    "AND (context -> '_submission_claim' ->> 'claim_batch_id') = batch_id "
    "AND COALESCE(context -> '_submission_claim' ->> 'attempt_id', '') <> '' "
    "AND COALESCE(context -> '_submission_claim' ->> 'provider_batch_id', '') "
    "<> '' "
    "AND (context -> '_submission_claim' ->> 'provider_batch_id') <> batch_id "
    "AND (context -> '_submission_claim' ->> 'request_count') = "
    "CAST(request_count AS TEXT) "
    "AND (feature <> 'cv_parse' "
    "OR (context -> '_submission_claim' ->> 'claim_batch_id') = "
    "'claim:cv_parse:' || (context -> '_submission_claim' ->> 'request_sha256')) "
    "AND COALESCE(context -> '_submission_recovery' ->> 'state', '') "
    "NOT IN ('invalid_known_accepted_claim', 'provider_id_collision')"
)


class AnthropicBatchJob(Base):
    __tablename__ = "anthropic_batch_jobs"

    id = Column(Integer, primary_key=True, index=True)
    # Anthropic's batch id (``msgbatch_...``). Unique — one row per batch.
    batch_id = Column(String, unique=True, index=True, nullable=False)
    # A batch goes through ONE API key, so it is single-org by construction
    # (multi-org batches would need splitting — deliberately unsupported).
    # Nullable for shared-key batches with no org context; those still get
    # claude_call_log rows, just no usage_events (which require an org).
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    # pricing_service.Feature value for every request in the batch.
    feature = Column(String, nullable=False)
    model = Column(String, nullable=True)
    request_count = Column(Integer, nullable=False, default=0)
    # submitting | submission_ambiguous | submission_failed | submitted |
    # ended | results_applied | canceled | expired | failed. ``ended`` means
    # metered provider results still need local application; ``results_applied``
    # is committed atomically with that application. The metering latch remains
    # ``metered_at``.
    status = Column(String, nullable=False, default="submitted")
    # Per-custom_id attribution map captured at submit time. Polling adds
    # reserved attribution-validation and ``_result_application`` evidence.
    # Pre-v188 rows may also contain legacy ``_metered_results`` receipts;
    # those remain readable but new receipts use the normalized table.
    context = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set once, when results() has metered every entry. The idempotency
    # latch — a non-null value means a second results() pass records nothing.
    metered_at = Column(DateTime(timezone=True), nullable=True)
    metered_count = Column(Integer, nullable=False, default=0)
