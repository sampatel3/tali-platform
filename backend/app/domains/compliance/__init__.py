"""Compliance domain: GDPR data-subject requests + voluntary EEO self-ID.

Both surfaces are deliberately held apart from the scoring/decision path:
- ``eeo_service`` exposes ONLY ``record_response`` (write) + ``aggregate_report``
  (org/role-scoped counts). There is NO per-candidate read — the scoring/decision
  agent must never see a protected characteristic.
- ``data_subject_service`` runs GDPR access-export / erasure and keeps a durable
  request log that outlives an erased candidate.

Admin surfaces are org-owner-gated (``require_org_owner``). The public voluntary
EEO write lives on the job-pages public router, keyed by an opaque per-application
token — never a raw application_id.
"""

from .routes import router

__all__ = ["router"]
