"""Workable Assessments-Provider — the marketplace add-on.

Taali appears inside Workable as an assessment a recruiter attaches to a
pipeline stage. Workable calls our endpoints (``GET /tests``,
``POST /assessments``) authenticated with the org's Taali API key; results are
pushed back to Workable's per-assessment ``callback_url`` via a durable outbox.

Self-contained and purely additive: it reuses the importable building blocks
(creation gate, repository branch, invite dispatch, share links) rather than
touching the recruiter ``create_assessment`` hot path. Inert until
``WORKABLE_PROVIDER_ENABLED`` is set. See
``docs/WORKABLE_ASSESSMENTS_PROVIDER_SPEC.md``.
"""
