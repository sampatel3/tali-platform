# Recruiter Workflow V2 - IA and Interaction Spec

Date: 2026-03-05
Owner: Product + Engineering
Status: Locked

## IA
- Primary nav: `Jobs`, `Candidates`, `Reports`, `Settings`.
- `Tasks` remains available by direct route but removed from primary nav in V2.

## Route Spec
- `/jobs`: jobs hub with role cards and stage summaries.
- `/jobs/:roleId`: role pipeline split-pane.
- `/candidates`: global application directory with same right-side candidate workspace.

## Role Pipeline Interaction Model
1. Recruiter opens role pipeline from `/jobs`.
2. Stage tabs across top (`applied`, `invited`, `in_assessment`, `review`).
3. Left panel lists applications for selected stage/outcome filters.
4. Right pane shows candidate core details, timeline, and actions.
5. Stage/outcome changes write events and refresh list/counts/detail in one interaction cycle.

## Candidate Workspace Rules
- Core-first section: identity, role, source, stage/outcome, recency.
- Timeline: latest workflow events with actor and reason.
- Actions:
  - Recruiter moves: `applied -> invited`, `review -> invited`.
  - System-driven moves: `invited -> in_assessment`, `in_assessment -> review`.
  - Outcome controls: `open|rejected|withdrawn|hired`.
- Advanced analytics hidden behind expandable analysis panel.
