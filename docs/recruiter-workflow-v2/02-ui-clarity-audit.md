# Recruiter Workflow V2 - UI Clarity Audit

Date: 2026-03-05
Owner: Engineering + Product
Status: Approved baseline for V2

## Findings

1. Action discoverability
- Legacy pages bury recruiter actions behind row actions and deep tabs.
- V2 requirement: action bar in right pane with explicit `Send invite`, `Resend`, `Retake`, `Move stage`, `Set outcome`.

2. Visual density
- Candidate detail overloads analytics above immediate decision context.
- V2 requirement: profile/timeline/actions first; advanced TAALI analytics collapsed under expandable section.

3. Information hierarchy
- Role-level stage health is not visible from first screen.
- V2 requirement: jobs cards show stage counts, active candidates, recent activity, sync badge.

4. Navigation rhythm
- Current flow requires bouncing across Dashboard/Candidates/Settings.
- V2 requirement: Jobs-first nav with consistent split-pane interactions between `/jobs/:roleId` and `/candidates`.

5. Mobile behavior
- Dense tables degrade quickly on narrow screens.
- V2 requirement: retain list-first selection with drawer/pane fallback behavior and preserved action accessibility.

## Priority List
- P0: Jobs hub discoverability, stage ergonomics, action placement.
- P1: Analytics collapse and secondary surfacing.
- P1: Mobile split-pane fallback parity.
- P2: additional sorting/filter polish and compact density modes.
