# Recruiter Workflow V2 - API and Migration Spec

Date: 2026-03-05
Owner: Backend Engineering
Status: Locked and implemented

## Canonical Application Fields
- `pipeline_stage` (NOT NULL, default `applied`)
- `pipeline_stage_updated_at` (NOT NULL)
- `pipeline_stage_source` (`system|recruiter|sync`)
- `application_outcome` (`open|rejected|withdrawn|hired`, default `open`)
- `application_outcome_updated_at` (NOT NULL)
- `external_refs` (JSON)
- `external_stage_raw` (string)
- `external_stage_normalized` (string)
- `integration_sync_state` (JSON)
- `version` (int, optimistic concurrency)

## Event Table
- `candidate_application_events` append-only ledger for stage/outcome transitions and initialization events.

## Backward Compatibility
- Legacy `status` retained in API responses.
- Existing patch endpoints accept `status` and map to guarded stage/outcome transitions.
- Dual-write keeps `status` mirror synchronized from canonical fields.

## New/Expanded Endpoints
- `GET /roles?include_pipeline_stats=true`
- `GET /roles/{id}/pipeline`
- `GET /applications` (global directory)
- `PATCH /applications/{id}/stage`
- `PATCH /applications/{id}/outcome`
- `GET /applications/{id}/events`
- `PATCH /organizations/me` with `recruiter_workflow_v2_enabled`

## Legacy Backfill Mapping
- `invited|pending|assessment_sent -> invited/open`
- `in_progress|started -> in_assessment/open`
- `review|completed|completed_due_to_timeout|scored -> review/open`
- `rejected|declined|disqualified -> review/rejected`
- `withdrawn -> review/withdrawn`
- `hired|offer_accepted -> review/hired`
- fallback: `applied/open`
