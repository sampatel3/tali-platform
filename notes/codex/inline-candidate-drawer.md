# Inline candidate drawer

## Pattern

The candidate drawer is a triage sidecar, not a replacement for the full report.
Use it for the next common recruiter action while preserving the candidate report
as the destination for timeline, transcripts, and scoring depth.

| Surface | Anchoring | Reason |
| --- | --- | --- |
| `candidates.html` / candidates directory | Inline beneath the clicked row | Table rows have enough width for the drawer to read as part of the row. |
| `job-pipeline.html` / kanban | Direct link to the full report | Kanban cards are compact status objects; selection should move straight to the report. |

The candidates directory drawer is the triage sidecar. The kanban is a quick
pipeline overview that hands off to the full report for candidate depth.

## Drawer Content

- Identity: avatar, name, role, email, and source metadata.
- Three score cards: pre-screen, Taali, Workable.
- Stage segmented control wired to `MoveApplicationStage`.
- Send Taali assessment with task picker wired to `AssessmentFromApplicationCreate`.
- Other actions: View full report, Reject.
- Last activity and import/source footer.

## Backend Wiring

- `AssessmentFromApplicationCreate`: `POST /applications/{application_id}/assessments`
- `MoveApplicationStage`: `PATCH /applications/{application_id}/stage`
- `RejectApplication`: `PATCH /applications/{application_id}/outcome` with `application_outcome = "rejected"`

This repo's current canonical stage enum is `applied`, `invited`,
`in_assessment`, and `review`; keep drawer stage values aligned with that
backend contract unless the API is migrated first.

## Interaction Rules

- Keep a single candidate drawer open at a time.
- Candidate directory row click toggles the inline drawer.
- Kanban card click opens the full report directly.
- Cmd/Ctrl/middle-click opens the full report in a new tab.
- Clicks on interactive children do not toggle the drawer.
- Esc closes the open drawer.
- Enter/Space on a focused candidate directory row toggles the drawer.
- Reject uses a two-step inline confirmation: `Reject` then `Confirm reject`.
