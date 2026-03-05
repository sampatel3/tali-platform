# Recruiter Workflow V2 - Integration Readiness Audit

Date: 2026-03-05
Owner: Engineering
Status: V1-ready (read-only Workable)

## Workable Field Coverage
- External identifiers and metadata preserved via `external_refs`.
- External stage tracking fields: `external_stage_raw`, `external_stage_normalized`.
- Sync health envelope: `integration_sync_state` (`last_sync_at`, `sync_status`, `run_id`, `last_error`).

## Stage Semantics Compatibility
- Local `pipeline_stage` is canonical for active workflow (`applied`, `invited`, `in_assessment`, `review`).
- Local terminal state is `application_outcome` (`open`, `rejected`, `withdrawn`, `hired`).
- Workable stage does not overwrite local stage for existing applications.

## Failure/Retry Handling
- Sync failures recorded in integration metadata; retries are non-destructive to local recruiter stage/outcome.
- Drift surfaced via derived `pipeline_external_drift` boolean.

## Future Write-Back Seams
- Existing metadata fields and normalized stage mapping provide non-breaking seam for future bidirectional sync.
- Append-only application events can anchor outbound stage-write auditability.

## Risks and Mitigations
- Risk: external stage drift growth if recruiter teams diverge from ATS stage names.
  - Mitigation: monitor drift rate and expose sync status badges on jobs/pipeline surfaces.
- Risk: stale sync metadata may confuse triage.
  - Mitigation: monitor `integration_sync_state.sync_status` and `last_sync_at`; alert on stale windows.
