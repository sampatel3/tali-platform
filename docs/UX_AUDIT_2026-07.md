# Taali UI/UX + performance audit — July 2026

Full-platform review covering every page and surface: UI/UX consistency (navigation
and design), desktop + mobile behaviour, frontend and backend performance, and
async-state clarity ("is something happening right now?"). 18 surface/dimension
audits plus external best-practice research; every P0/P1 finding was independently
re-verified against the code before making this document.

**Bottom line:** the product's bones are good — optimistic decision actions, greyed
re-score states, stale-while-revalidate caching on the hub, and per-criterion
evidence already exist. The gaps cluster in four places:

1. **Payload and round-trip waste** that directly hurts UAE users on a us-east4 API
   (uncompressed multi-MB JSON, list endpoints shipping full transcripts/job specs,
   a header that fans out 26 requests every 30s, a pipeline that downloads up to
   4,000 full rows and re-downloads them after every action).
2. **Invisible background work.** Several surfaces literally tell the recruiter to
   refresh the page ("Full CV evaluation queued. Refresh in a few seconds."), show
   'Completed' while grading is still running, or show a success checkmark on failed
   jobs. This is the top-priority theme: a recruiter must always be able to tell
   *waiting* from *done* from *failed*.
3. **A handful of genuinely dangerous interaction bugs** — Enter submits a bulk
   approve while skipping the required Workable stage pick; keyboard shortcuts fire
   under open modals and can approve/snooze the decision behind the modal; the
   candidate report lets you approve a decision that is mid-re-score.
4. **Consistency drift** — five different error-message helpers, ~143 hardcoded
   color literals, traffic-light semantics on three surfaces (purple is canon),
   "Jobs" in the nav but "roles" in page copy, and 13px inputs that make iOS zoom
   the page on focus.

Severity counts after verification: **50 P1, 76 P2, 30 P3** (no P0s — nothing is
down, but several P1s are data-affecting).

## Headline findings (P1)

### Performance — backend (all verified against code)
| Finding | Where |
|---|---|
| No response compression — multi-MB JSON travels uncompressed to the UAE | `backend/app/main.py` (no GZipMiddleware) |
| `/roles` list embeds job_spec_text, interview-pack templates and full criteria per role; no list consumer uses them | `role_support.py:253` |
| Assessments list ships full CLI transcripts, repo state, git evidence per row | `components/assessments/repository.py` |
| `/analytics/reporting-summary` hydrates every application + decision in the org as ORM rows to count them in Python | `analytics_routes.py:422-620` |
| Role workspace loads up to 2×2000 full application rows per visit | `JobPipelinePage.jsx:426`, apps endpoint |
| Header polls heavy `/roles` + up to 25 per-role status calls every 30s on every page — while `/agent/org-status` already exists | `AgentBar.jsx:102`, `hub_routes.py:221` |

### Performance — frontend
| Finding | Where |
|---|---|
| Landing page statically bundled into the entry chunk, dragging assessment-runtime preview + fixtures into every page load (entry: 452 kB) | `AppShell.jsx:29` |
| cytoscape (455 kB) statically imported by chat `Thread` — every chat surface pays for a graph view that rarely renders | `Thread.jsx:6` |
| recharts vendor chunk (383 kB) downloaded on candidate report open for secondary-tab charts | manualChunks + tab imports |
| Pipeline renders up to 4,000 rows unvirtualized and re-downloads everything after every single-candidate action | `JobPipelinePage.jsx` |
| Candidate report: 2-step request waterfall on load; every action blanks the page to a full spinner and re-runs all 5 requests | `CandidateStandingReportPage.jsx:272-342` |
| Hub queue search fires an uncached request per keystroke (no debounce) | `HomeNow.jsx:164` |
| Settings mounts all 12 tab panels eagerly + permanent 5s poll; requisitions poll the full brief every 5s indefinitely | `RecruiterSettingsPage.jsx`, `RequisitionsPage.jsx` |

### Async-state clarity (top user priority)
- "Run full evaluation" → toast says **"Refresh in a few seconds"**; queued re-scores invisible on the candidate page.
- Submitted-but-unscored assessments read **"Completed"** with dash scores; bounced invites show **"Invited"** forever.
- Failed background jobs show a **success checkmark** in the global toaster; error toasts auto-dismiss in 5s and are the only failure feedback for most mutations.
- Analytics fetch failure renders **all-zero KPIs that look like real data**; scope changes show stale numbers labelled as the new scope.
- Chat dock freezes at "Working…" forever if a run exceeds its 6-minute poll window; assessment runtime claims "Autosave active" but no autosave exists, and "Task submitted" is shown before the server confirms.
- No skeletons anywhere — every cold load is a centered spinner.

### Dangerous interactions
- **Enter bypasses the disabled bulk-approve confirm** and submits with an empty stage map → candidates advance internally with nothing posted to Workable (violates the bulk-actions rule). `HomeNow.jsx:1036`
- **a/t/s shortcuts fire under the Override modal** — can approve/snooze/teach the decision behind an open confirm. `HomeNow.jsx:1015`
- **DecisionRail ignores `rescore_in_flight`/`is_stale`** — one-click approve on a decision that is mid-re-score. `DecisionRail.jsx:69`
- Enter doesn't submit login/register (no `<form>`); requisition edits can silently wipe captured struct-list data; failed chat sends destroy the typed message (requisitions, client intake, assessment chat); switching requisitions shows the previous one's content.

### Consistency, navigation, mobile
- Nav active-states wrong or missing on /analytics, /requisitions, candidate report; no 404 page; route guard misses /chat and /tasks/:id/preview; assessments empty state points to a page that no longer exists.
- Five divergent error helpers; raw technical errors reach recruiter toasts; "HITL"/"kill-switch" jargon in settings; "Jobs" vs "roles" naming split.
- Agent-ON gradient hardcoded as hex in 10 places; 143 raw color literals in 9 files; traffic-light colors on task difficulty chips, demo-lead feed, Workable callback page.
- All inputs are 13–14.5px → iOS Safari zooms the whole page on focus; pipeline kanban keeps 3 columns down to phone width; requisitions below 860px is a dead end; assessment workspace unusable below 1280px.
- Publish with an incomplete brief prints literal "(to be captured)" on the public candidate-facing job page.

## Best-practice research (what "excellent" looks like)
- **Clicks-per-decision is the metric.** Ashby wins on pipeline-first nav + in-context analytics; Greenhouse's top complaint is "too many clicks". Quick-screen side panel with prev/next beats full page navigations.
- **Skeletons for content, named-stage progress for AI work** ("Parsing CV → Scoring → Writing rationale"), spinners only for sub-second actions, nothing under ~300ms.
- **Stale-while-revalidate everywhere** is the single highest-leverage pattern for far-from-server users (already proven on the hub — extend it).
- **AI-job visibility**: one persistent global task surface (GitHub Copilot Agents-panel model); >10s jobs start foreground, offer background, always notify on completion.
- **Trust vocabulary**: mark AI output, per-criterion evidence everywhere a score appears, "you decide — the agent never advances or rejects on its own" on-surface.
- **Empty states** = why it's empty + exactly one CTA. **Errors** = what happened + what to do next, in recruiter language.
- CWV targets: LCP ≤2.5s, INP <200ms, CLS <0.1, measured on soft navigations too.

## Execution — 5 PRs, all merged 2026-07-10
| PR | Scope | Outcome |
|---|---|---|
| **#879 — backend payload & round-trip diet** | gzip, slim /roles + assessments lists, SQL aggregation for reporting-summary, header fan-out → single /agent/org-status call | 26→1 header requests per poll; multi-KB blob fields off every list row |
| **#878 — pipeline + candidate report** | row-patch instead of 4,000-row refetch after actions, windowed table, parallel loads, `refreshing` mode (no full-page blanks), re-score in-flight polling, DecisionRail staleness guard, shared Skeleton | candidate-report first open ~138 → ~23 kB gz |
| **#877 — bundle & global chatter** | lazy Landing/GraphView/recharts, hidden-tab poll pauses, settings lazy tabs + gated poll, chat scroll-hijack + re-render storm, demo iframes on demand | entry JS 452 → 183 kB (gzip 136 → 56 kB) |
| **#880 — interaction correctness** | bulk-approve Enter gate, modal shortcut leaks, auth `<form>` submits, chat/requisitions data-loss bugs, route guard + real 404, assessment-runtime submit/timeout/autosave honesty, 60s HTTP timeout | all 16 P1s closed |
| **#896 — consistency, copy, mobile** | honest assessment statuses (Scoring… / bounced invites), analytics error states, persistent error toasts, ONE shared error helper, gradient/token cleanup, traffic-lights → purple, nav active states, jargon sweep, 16px inputs on touch, marketing dead-ends, Stripe portal session, publish validation | manual lint-ui inventory 230 → 187 (historical; superseded by the machine baseline below) |

### UI guardrail baseline (2026-07-15)

The previous ~187 figure was a manual inventory and was not an enforceable CI
baseline. The old walker later reported 720 findings because it treated tests,
vendored/generated files, and independently-tokenized public deck JavaScript as
React components; it also reported valid `var(--optional, fallback)` calls as
undefined and missed custom properties declared in inline React styles.

`npm run lint:ui` now keeps unresolved CSS variables, structural rules, and
component-token findings at **zero**. It checks each static public experience
against its own CSS-token scope, while production palettes live in named CSS
custom properties rather than component literals. The temporary 244-finding
ratchet was fully paid down on 2026-07-15; `frontend/scripts/lint-ui-baseline.json`
is intentionally empty, so every new finding fails as a regression.

### Deliberately not done (future work)
- Per-feature button implementations → shared Button consolidation (L effort).
- Blanket "roles"→"jobs" copy sweep (only the clearest surfaces changed; "roles" is correct in agent/decision contexts).
- Named-stage AI progress ("Parsing CV → Scoring → Writing rationale") and a persistent global background-work panel (Copilot-Agents-panel model) — recommended by research, needs design.
- Most P3 polish items. UI token/structural guardrail debt is now zero and cannot grow without failing `npm run lint:ui`.
- `cv_text` column deferral on application lists (would introduce a per-row N+1 with the current serializer; gzip covers the transfer cost).

Full machine-readable findings (156 items with file:line evidence, severity, and
proposed fixes) are archived with the session transcript; each PR lists the
findings it closes.
