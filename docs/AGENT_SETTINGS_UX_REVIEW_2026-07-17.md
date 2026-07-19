# Job Agent Settings — detailed UX review

Date: 17 July 2026
Scope: Jobs → role detail → Agent settings
Artifacts reviewed: rendered pipeline preview, current React implementation, supporting criteria/history components, CSS, routing, API integration, and targeted tests.

## Executive conclusion

The tab is crowded because it has become four different products in one uninterrupted page:

1. Candidate guidance and fit configuration
2. Decision and approval policy
3. Spend monitoring and limits
4. Agent teaching and historical records

The current layout gives all four equal visual weight, inserts growing history between core settings, and mixes four persistence models. The redesign should not begin by shrinking cards. It should first establish a task-based local navigation model and a consistent save contract.

Recommended direction: a focused settings shell with an Overview plus four task sections:

- Guidance
- Decision policy
- Budget & limits
- Context history

Desktop uses a sticky vertical section rail; mobile uses a horizontally scrollable selector. One task family is visible at a time. The role header is compact on this tab, and the full funnel is hidden. The original interactive prototype also compared a scan-and-expand accordion alternative; the review below preserves that decision record, while the superseding focused-sections experience now lives in the React application.

## Implementation status

The focused-sections direction is now implemented as the platform design-system pattern and applied to Job → Agent settings. The live structure is Overview, Guidance, Decision rules, Budget & limits, and Recruiter answers, backed by `?view=role-fit&section=…` links. Sections mount on first visit and then remain mounted, preserving in-progress local drafts without loading every panel initially.

This implementation also resolves the highest-risk trust issues identified below:

- The full pipeline funnel is hidden while Agent settings is active, leaving the role summary and focused section index as the page context.
- The threshold preview now recalculates from the draft value and scored applications.
- “Review the N” navigates back to the Candidates view instead of targeting an unmounted element.
- The save action is labelled for the one field it persists and sits next to that threshold workflow.
- The inert pause-percentage selector is replaced with honest monthly-cap behavior.
- Provider cost and platform margin are removed from the recruiter-facing budget section.
- Workspace-default links now point to `/settings#agent`.

Still recommended as follow-up work: atomic threshold autosave, a unified feedback/Q&A history, replacement of the per-candidate dot grid with aggregate bins, the Criteria Editor mobile-entry fixes, and reduced prop surface in `RoleAgentSettingsTab`.

## What was on the page before this redesign

Before Agent settings begins, the role page already shows the breadcrumb and title, agent status/actions, budget, role facts, job actions, the full funnel, and four job-level tabs. The funnel is outside the active-view conditional and therefore remains above settings ([JobPipelinePage.jsx](../frontend/src/features/jobs/JobPipelinePage.jsx#L1353)).

Inside the tab, the maximum surface is:

| Block | What it contains | Current persistence |
|---|---|---|
| Intro | Role-only override explanation and org-default link | Link points to a missing local anchor |
| Role criteria | Inheritance summary, provenance legend, sync, reset, category selector, add field, three columns, edit/delete, hidden-item restore | Each operation saves immediately |
| Feedback to the agent | 4,000-character composer, shortcut, add action, request states, full feedback log | Add saves immediately |
| Recruiter Q&A | Up to 25 resolved questions, answers, types, and timestamps | Read-only, separately loaded |
| Reject threshold | Manual/managed mode, slider, rationale, current value, distribution, counts, review action | Mode saves immediately; slider waits for the bottom button |
| Autonomy | Workable warning, auto-reject, auto-promote | Each switch saves immediately |
| Bottom save bar | “Save role settings” | Saves only `score_threshold` |
| Budget | Spend, cap, meter, forecast, provider cost, margin, feature breakdown, cap editor | Cap has its own immediate save |
| Pause threshold | 70/80/90% selector | Does not save or affect runtime |
| Audit callout | Another role-only inheritance explanation | Link points to a missing local anchor |

The main component accepts 29 props, including an unused `recruiterCriteria` prop ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L13)).

## Highest-priority findings

### P0 — Save behavior is misleading

Criteria, feedback, threshold mode, autonomy, and budget save immediately. The manual threshold waits for “Save role settings,” and Pause threshold never saves. The bottom action actually sends only `score_threshold` ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L329), [JobPipelinePage.jsx](../frontend/src/features/jobs/JobPipelinePage.jsx#L863)).

Why it matters: the recruiter has no reliable way to know whether a change is pending, committed, or inert. This is a trust problem, not just a wording problem.

Recommendation:

- Adopt atomic autosave consistently.
- Save the threshold on release or after a short debounce.
- Display `Saving…`, `Saved`, and `Retry` beside the affected control.
- Confirm destructive reset operations.
- Remove the global save bar.

If autosave is rejected, the alternative must be one real staged transaction covering every editable field. The current hybrid should not remain.

### P0 — Pause threshold is a false control

The selector uses `defaultValue={80}` without a controlled value, accessible label, change callback, role field, or API mutation ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L456)). The runtime currently pauses at the actual cap, not at this displayed percentage.

Recommendation: remove it until the backend supports a persisted role-level percentage, or render honest read-only copy such as “Automatically pauses at the monthly cap.” If implemented, connect it to the budget guard and verify persistence after reload.

### P0 — Threshold impact can contradict the control

The displayed “Currently N%” reads the unsaved `thresholdDraft`, while below/above counts use the persisted `role.score_threshold` ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L44), [JobPipelinePage.jsx](../frontend/src/features/jobs/JobPipelinePage.jsx#L577)). Dragging the slider therefore changes the number while leaving its impact visualization unchanged.

There are two more semantic mismatches:

- The copy calls the value a CV score, but the count is calculated from `pre_screen_score` ([JobPipelinePage.jsx](../frontend/src/features/jobs/JobPipelinePage.jsx#L581)).
- Agent-managed mode displays “Dynamic,” but the distribution still uses the stored manual threshold rather than the effective managed threshold.

A missing threshold also falls back visually to 55%, although saving an untouched empty draft sends `null` ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L46)).

Recommendation: calculate the preview from the current draft and the same score dimension named in the UI. In managed mode, use the real effective threshold or avoid showing an impact number. Prefer a server-owned summary containing effective value, source, below count, and histogram bins.

### P0 — Several destinations are dead or inconsistent

- “Review the N” tries to scroll to `#pipeline-table`, but no element with that ID exists. The Candidates view is also unmounted while Agent settings is active ([JobPipelinePage.jsx](../frontend/src/features/jobs/JobPipelinePage.jsx#L1523)).
- Agent questions generate `?tab=agent-settings`, while the role UI reads the `view` query parameter.
- All `#org-defaults` links target a nonexistent anchor; the real destination is the agent area in Settings ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L124)).
- A task-assignment question can link to Agent settings even though task editing lives in Job spec.

Recommendation: define canonical, URL-backed destinations such as `?view=role-fit&section=decisions`; support legacy links as aliases; route task questions to the actual task editor; and make “Review candidates” navigate to a filtered Candidates view.

### P0 — Mobile criteria entry can become unreachable

Only the three result columns stack at 720px. The category selector, input, and Add button remain in a single non-wrapping row, while the category control cannot shrink ([CriteriaEditor.css](../frontend/src/shared/ui/CriteriaEditor.css#L71), [CriteriaEditor.css](../frontend/src/shared/ui/CriteriaEditor.css#L183)). Global overflow clipping can hide the input or action at phone widths and high zoom.

Recommendation: at mobile widths, stack the category selector above a shrinkable input/action row, set `min-width: 0` on flex children, reduce card padding, and verify at 320px and 400% zoom with zero document-level horizontal overflow.

## Information-architecture findings

### The page has no local navigation

Editable settings, operational telemetry, teaching input, and historical evidence are all full-size cards in one stack. There is no summary, local index, deep link, or progressive disclosure.

The feedback and Q&A blocks sit between criteria and threshold. As those histories grow, the controls that determine candidate outcomes move farther down the page.

### The mobile order buries important controls

At widths below 1100px, the two-column layout becomes one column. Because the sidebar follows the entire main column in DOM order, Budget and Pause threshold appear only after criteria, full feedback history, Q&A history, threshold, autonomy, and the save bar ([20-role-agent-tab.css](../frontend/src/styles/20-role-agent-tab.css#L3)).

### All cards receive the same emphasis

The user cannot distinguish:

- frequent decisions from rare administration;
- controls from history;
- dangerous automatic actions from informational telemetry;
- inherited values from role overrides;
- saved state from unsaved state.

Long explanatory paragraphs repeat information that labels, summaries, and contextual help should carry.

### The page repeats context instead of summarizing it

- Inheritance is explained in the intro, criteria state bar, save bar, and sidebar callout.
- Spend appears in the persistent role agent header and the budget sidebar.
- The full pipeline funnel stays visible even though it does not help configure the agent.
- The distribution creates one dot per candidate even though the exact below/above counts already communicate the result.

The page can fetch up to 2,000 active applications, so the dot grid can add thousands of DOM elements for a weak visualization.

### Internal platform economics are exposed

The budget card shows raw Anthropic cost, platform margin, and margin percentage. Those are internal commercial metrics, not recruiter-facing role controls. They add density and disclose the platform’s markup.

Recommendation: show charged role spend, monthly cap, forecast, and recruiter-facing work categories only. Move provider cost and margin to restricted internal analytics.

## Accessibility and responsive findings

### Interaction semantics

- The category selector uses buttons with `role="radio"` but does not implement arrow-key radio behavior or roving tab index ([CriteriaEditor.jsx](../frontend/src/shared/ui/CriteriaEditor.jsx#L27)). Use native radios or a complete radio pattern.
- Autonomy controls look like switches but are exposed as pressed buttons and do not programmatically connect the consequence text ([RoleAgentSettingsTab.jsx](../frontend/src/features/jobs/RoleAgentSettingsTab.jsx#L312)). Prefer a native checkbox or `role="switch"` with `aria-describedby`.
- The 38×22px switch target is too small for comfortable touch use ([12-agentbar.css](../frontend/src/styles/12-agentbar.css#L276)).
- Repeated criterion actions are named only “Remove” and “Add back,” so assistive technology cannot distinguish them ([CriteriaEditor.jsx](../frontend/src/shared/ui/CriteriaEditor.jsx#L144)). Include the criterion text in each accessible name.
- Delete actions are visually hidden until hover/focus and are very small. Keep them discoverable on coarse pointers and target at least 24×24px, preferably 44×44px on mobile.

### Labels, status, and validation

- The feedback textarea relies on placeholder text instead of a persistent label ([RoleFeedbackNotes.jsx](../frontend/src/features/jobs/RoleFeedbackNotes.jsx#L102)).
- Loading, success, validation, and failure states are ordinary text rather than announced status or alert regions.
- Invalid budget values silently do nothing.
- The budget input removes its native outline without adding a wrapper focus ring ([20-role-agent-tab.css](../frontend/src/styles/20-role-agent-tab.css#L538)).

Recommendation: add visible labels and descriptions, `aria-invalid`, polite status regions, alert semantics for failures, and predictable focus restoration after add/edit/delete actions.

### Typography and contrast

The global root font is reduced to 12.8px, which makes several local rem sizes extremely small. Muted light-theme text is used widely and does not reach 4.5:1 contrast on white.

Recommendation: restore a normal root size, implement density locally, keep supporting copy around 14px, and validate all normal text to 4.5:1 in both themes.

### Visual provenance

Four sources are encoded by similar tiny purple dots. The legend is visually dense, while the dots are not sufficiently distinct for quick scanning or non-color perception.

Recommendation: keep source information but move the full legend into a “Sources” disclosure. Use a concise textual badge or distinct shape for the selected item when provenance matters.

## Recommended experience

### 1. Compact role context on Agent settings

Keep the role title, agent on/off/pause state, pending decisions, and budget status. Hide the full funnel while this tab is active. The user already has the Candidates and Pipeline tabs when they need operational detail.

### 2. Read-first overview

The landing state answers four questions without requiring a scroll:

- What guidance is this role using?
- What can the agent decide automatically?
- How close is the role to its limit?
- What context have recruiters added?

Show warnings and setup gaps here, not duplicate controls.

### 3. Task-based local navigation

Desktop:

```text
Agent settings
┌───────────────────────┬──────────────────────────────────────────┐
│ Overview              │ Current section                          │
│ Guidance           9  │                                          │
│ Decision policy   55% │ One task family visible at a time        │
│ Budget & limits   84% │                                          │
│ Context history     6 │                                          │
└───────────────────────┴──────────────────────────────────────────┘
```

Mobile: convert the rail to a horizontally scrollable section selector or native select. Preserve URL state so Back, Forward, refresh, and shared links work.

Recommended URLs:

- `?view=role-fit&section=overview`
- `?view=role-fit&section=guidance`
- `?view=role-fit&section=decisions`
- `?view=role-fit&section=budget`
- `?view=role-fit&section=history`

### 4. Guidance

Answers “Who should this agent prioritize?”

- Criteria inheritance summary
- Sync workspace
- Reset in an overflow menu with confirmation
- Must / Preferred / Constraint groups
- Inline criterion editing
- Hidden criteria disclosure
- Standing-feedback composer

Move the old feedback log to Context history. Keep provenance indicators on criteria, but collapse the full legend.

### 5. Decision policy

Answers “What should the agent do with each candidate?”

- Manual versus agent-managed quality bar
- Manual slider only in Manual mode
- Managed rationale only in Agent-managed mode
- Compact histogram or split bar based on the effective value
- Direct link to filtered candidates
- Reject action: `Ask me` versus `Act automatically`
- Assessment/interview action: `Ask me` versus `Act automatically`
- Workable warning beside the actions it affects

Outcome-based choices are clearer than unlabeled on/off switches. Do not split assessment sending and interview advancing into independent settings until the backend supports separate fields.

### 6. Budget & limits

Answers “How much may the agent spend, and when must it stop?”

- Charged spend and cap
- Forecast and remaining time
- Inline cap edit
- Real, persisted pause rule
- Collapsed recruiter-facing breakdown

Do not show raw provider cost or margin.

### 7. Context history

Answers “What have we told the agent, and what has it asked?”

- Unified timeline with Feedback and Q&A filters
- Latest items first
- Pagination or “View all”
- Deep links from threshold/budget answers back to the relevant setting
- Read-only presentation separate from the save flow

## Current-to-recommended mapping

| Current content/control | Recommended destination | Disclosure |
|---|---|---|
| Long configuration intro | Compact overview summary | One line |
| Org-default explanations | Overview plus per-section inherited/customized state | One real Settings link |
| Agent on/off/pause | Compact role header | Always visible outside local settings |
| Criteria counts and inheritance | Guidance header | Always visible |
| Sync workspace | Guidance header | Secondary action |
| Reset defaults | Guidance overflow menu | Confirm first |
| Source legend | Guidance → Sources | Collapsed/popover |
| Add/edit/remove/restore criteria | Guidance | Inline |
| Standing feedback composer | Guidance | Prominent |
| Feedback log | Context history → Feedback | Latest three, then View all |
| Recruiter Q&A | Context history → Q&A | Timeline |
| Threshold mode and manual slider | Decision policy → Quality bar | Conditional by mode |
| Dynamic rationale | Decision policy → Quality bar | Managed mode only |
| Candidate dots | Decision policy → Current impact | Compact histogram/split bar |
| Below/above counts | Decision policy → Current impact | Always visible |
| Review candidates | Decision policy → Current impact | Real filtered Candidates URL |
| Auto-reject | Decision policy → Reject action | Ask me / Automatic |
| Auto-promote | Decision policy → Assessment & interview | Ask me / Automatic |
| Workable warning | Decision policy | Only when relevant |
| Spend, cap, forecast | Budget & limits | Always visible |
| Cap editor | Budget & limits | Inline |
| Pause percentage | Budget & limits | Only after real persistence exists |
| Feature breakdown | Budget & limits | Collapsed by default |
| Provider cost and margin | Internal admin analytics | Remove from recruiter surface |
| Bottom save bar | Remove | Atomic autosave state |
| Repeated role-only footers | Remove | Covered by overview and provenance |

## Alternative navigation concepts

### A. Focused sections — recommended

Sticky local rail, one active workspace, and a read-first overview. It gives the criteria editor full width, separates history from configuration, supports deep links, and maps well to existing component boundaries.

### B. Scan and expand — included in the mockup

Keep one-page browsing but collapse every group to a current-state summary row. This is good for infrequent inspection and low navigation overhead. It is weaker for deep editing and can become another long accordion if every group is left open.

### C. Overview cards with editing side sheets

Show four summary cards—Target profile, Quality bar, Permissions, Spend guardrail—and open a deep-linkable side sheet for detailed editing. This produces the cleanest scan but puts important settings one click deeper and requires careful URL/back-button behavior.

### D. Pipeline-policy rules builder

Express the configuration as a causal flow:

```text
1  Evaluate against 9 criteria
2  If score is below 55 → Reject                 Approval required
3  If eligible → Send assessment                 Automatic
4  After assessment → Advance                    Automatic
5  Stop at 80% of a $50 monthly cap
```

This makes threshold/action relationships exceptionally clear, but it is the most bespoke and highest-risk design. Prototype after the behavior and persistence model are corrected.

Ranking: A, B, C, D.

## Implementation sequence

### Phase 0 — restore trust before redesign

1. Establish canonical job-view and local-section URLs; alias legacy links.
2. Fix or remove Pause threshold.
3. Replace the hybrid save contract.
4. Make threshold copy, effective value, preview counts, and score field agree.
5. Route Review candidates to a real filtered view.
6. Replace dead org-default links.
7. Remove provider cost and margin from recruiter roles.

### Phase 1 — introduce the settings shell

Suggested component boundaries:

- `AgentSettingsShell`
- `AgentSettingsOverview`
- `AgentGuidancePanel`
- `AgentDecisionPolicyPanel`
- `AgentBudgetLimitsPanel`
- `AgentContextHistoryPanel`

Move mutation logic into focused hooks instead of passing 29 props through one component. Lazy-load usage, feedback, and Q&A only when their section opens.

### Phase 2 — progressive disclosure and density

1. Hide the funnel on Agent settings.
2. Replace the dot grid with aggregate impact.
3. Collapse source legend, spend breakdown, and long histories.
4. Reduce repeated inheritance/help copy.
5. Move feedback history and Q&A out of the core configuration sequence.

### Phase 3 — accessibility and responsive hardening

1. Implement native/complete radio and switch semantics.
2. Add labels, descriptions, validation, status announcements, and focus restoration.
3. Make all actions touch-discoverable.
4. Repair typography and contrast.
5. Validate 320, 375, 720, 1024, and 1440px; test 200% and 400% zoom.

## Test and acceptance plan

The targeted `JobPipelinePage` suite currently passes 15 tests, but it logs repeated unwrapped asynchronous updates from `RecruiterAnswersLog`. Existing coverage checks only basic tab opening, slider presence, criterion creation/inheritance, and derived criteria display.

Minimum additions:

- Deep-link each local section and support the legacy agent-settings URL.
- Threshold draft changes its impact immediately and saves with visible status.
- Managed threshold uses the real managed value.
- Review candidates changes view and applies the expected filter.
- Cap and pause-rule changes persist after reload; error states roll back correctly.
- Autonomy choices persist, prevent rapid duplicate writes, and expose Workable consequences.
- Feedback and Q&A loading, empty, populated, pagination, and error states.
- Criteria create, edit, remove, sync, reset, and restore, including focus restoration.
- Keyboard-only navigation, radio arrows, switches, slider keys, and unique accessible names.
- Browser-level responsive/zoom reflow and `scrollWidth` assertions.
- Automated accessibility scan in light and dark themes.
- Zero React `act(...)` warnings in the focused panel tests.

## Success measures

Track after release:

- Median time to find and change a named setting
- Percentage of sessions that navigate directly via section deep link
- Save failure/retry rate by control
- Threshold changes followed by a candidate review
- Reversal rate for autonomy changes
- Help/support events related to “did this save?” or budget pause behavior
- Mobile completion rate for adding a criterion

The redesign succeeds when a recruiter can answer the page’s four core questions from the overview, reach any control in one local navigation action, and always know what is inherited, what changed, and whether it saved.
