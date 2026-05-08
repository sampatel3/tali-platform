# Home — agent-first landing page

**Status:** Design + first implementation (PR 1–4 in this branch).
**Audience:** Engineers and product. Read before touching anything under `/home`.
**Replaces:** The standalone `Reporting` tab (route `/reporting` → 301 to `/home`).

---

## 1. What this is

`/home` is the new default landing route for authenticated recruiters. It replaces
`/reporting` as the place a recruiter ends up when they sign in, and folds three
previously-separate surfaces into one:

1. **The agent's pending-decision queue** (was: implicit, scattered across role pages).
2. **The org-wide reporting dashboard** (was: `/reporting`, narrator + funnel + histogram).
3. **The training-signal trace** — proof that human corrections fed back into the agent's behaviour (was: not surfaced anywhere).

The page is structured so a recruiter can answer three questions without
clicking away:

| Question | Section |
|---|---|
| What does Taali need from me right now? | **NOW** (V4 hybrid) |
| Where is Taali working, and how hard? | **ROLES** + KPI strip |
| Is Taali learning from us, and what changed? | **SIGNAL** |
| Where can I see literally everything it did? | **EVERYTHING** (history + analytics) |

The framing follows the agent-native positioning: the agent is the first-class
actor, the recruiter is the human in the loop, and the page makes both halves
of that loop visible.

---

## 2. Page anatomy

```
┌─────────────────────────────────────────────────────────────┐
│ HomeHero (purple slab) — "Good morning, Sam."               │
│   kicker · greeting · time-range pill · agent-state chip    │
├─────────────────────────────────────────────────────────────┤
│ KPI strip — 4 cards                                         │
│   awaiting you · decisions today · org budget · override 7d │
├─────────────────────────────────────────────────────────────┤
│ NOW — V4 hybrid                                             │
│   ┌────────────┬────────────────────────────────────────┐   │
│   │ Pending    │ Detail of selected pending decision     │   │
│   │ sidebar    │  (candidate summary · trace · actions)  │   │
│   └────────────┴────────────────────────────────────────┘   │
│   Activity feed (full width, reverse-chronological)         │
├─────────────────────────────────────────────────────────────┤
│ ROLES — per-role breakdown                                  │
│   pending · today · 7d · budget · override · paused/flagged │
├─────────────────────────────────────────────────────────────┤
│ SIGNAL — learning trace                                     │
│   recent feedback events · resulting rubric revisions ·     │
│   realised outcomes (interviewed / hired / rejected_conf.)  │
│   pending co-sign tray for org-scope teach feedback         │
├─────────────────────────────────────────────────────────────┤
│ EVERYTHING — history table + analytics drill-ins            │
│   filter, sort, export · score histogram · funnel           │
└─────────────────────────────────────────────────────────────┘
```

### Section descriptions

**HomeHero.** Same `AgentHeader` slab used elsewhere (kicker / title / subtitle /
right-hand pill row). Subtitle is a single sentence: *"Every decision the agent
makes that needs you. Approve, override, or teach it — your calls become its
training signal."* Right-hand actions: Live | 24h | 7d | 30d toggle (purely
filters the KPI strip, history table, and SIGNAL — never NOW).

**KPI strip.** Four cards. The first ("awaiting you") is emphasised — bigger
number, purple accent — because it is the only KPI that maps to an action.

**NOW.** The V4 hybrid layout from the handoff, unchanged in structure:
left rail = pending sidebar; right column = detail panel above + decision
feed below. `?pending=:id` deep-links select a specific item.

**ROLES.** Sortable table, one row per active role, deep-link to `/jobs/:id`.
The "Override 7d" column is split into two pills (`OVR n%` / `TEACH n%`) so the
operator can see which roles the team is correcting *vs.* which they're
*teaching* — high override + low teach is a smell.

**SIGNAL.** Three subsections:

1. *Pending co-sign* — org-scope teach events awaiting a second admin. Tray with one-click cosign.
2. *Recent feedback* — last ~20 teach events from `decision_feedback`. Reviewer · failure_mode · scope · 1h revert button.
3. *Realised outcomes* — last ~10 entries from `role.agent_calibration["outcomes"]` (advance → interviewed/hired, reject → rejected_confirmed). The *other* observation loop — what actually happened to candidates downstream.

What deliberately is **not** in SIGNAL: any "rubric revisions" / "retune queued"
copy. Improving the agent's scoring is a separate workstream (§8); the Hub
captures input but doesn't promise output.

**EVERYTHING.** The full filterable history. Filters compose (role × type ×
status × text) and persist in URL query params. Below the table, the
analytics drill-ins folded in from the retired `/reporting`: score histogram,
funnel, narrator paragraph (collapsed by default in an accordion).

---

## 3. Data flow

### 3.1 The decision lifecycle

```
                ┌──────────────────────────────────────────────────┐
                │                AgentDecision                     │
                │  status enum:                                    │
                │    pending → approved | overridden |             │
                │              reverted_for_feedback | discarded   │
                │              | expired                           │
                │  human_disposition (NEW):                        │
                │    null | approved | overridden | taught         │
                │  feedback_id (NEW): FK → decision_feedback       │
                │  snoozed_until (NEW): TIMESTAMPTZ | null         │
                └──────────────────────────────────────────────────┘
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
         POST /approve POST /override POST /agent/feedback
              │             │              │
              ▼             ▼              ▼
         resolved      resolved      decision_feedback row
         status=app    status=ovr    status=reverted_for_feedback
         dispos=app    dispos=ovr    dispos=taught
                                     enqueue retune (if scope ∈ {role, org})
                                     org-scope → cosign_required=true
```

### 3.2 The teach-and-log loop

```
┌──────────────────┐    POST /agent/feedback   ┌─────────────────────┐
│ Reviewer hits    │ ────────────────────────▶ │ decision_feedback   │
│ "Send back &     │                           │ scope ∈ {decision,  │
│  teach"          │                           │   role, org}        │
└──────────────────┘                           └──────────┬──────────┘
                                                          │
                                                          ▼
                                          ┌──────────────────────────────┐
                                          │ Decision back to Pending     │
                                          │ Note attached for next       │
                                          │   reviewer                   │
                                          │ Org-scope: needs 2nd admin   │
                                          │   co-sign to be accepted     │
                                          └──────────────────────────────┘

Improvements to how Taali scores candidates and chooses decisions are a
separate, deeper rework — see §8.
```

Two complementary observation loops on the same agent (no auto-retraining
yet for either):

- **Teach loop** (this design) — *human says agent was wrong*. Captures the
  correction in `decision_feedback`. Visible in the Hub's SIGNAL section.
- **Outcome loop** ([outcome_learning.py](../backend/app/agent_runtime/outcome_learning.py)) —
  *world says agent was right or wrong*. Tracked in
  `role.agent_calibration["outcomes"]` (FIFO list); already surfaces a
  "track record" line in the next agent prompt.

Both surface in SIGNAL, side by side. High teach-rate = the agent's
*judgements* are off; high "interviewed but not hired" = the agent's
*advances* aren't holding up downstream. Different problems — but both
visible from one place.

---

## 4. URL contract

| Route | Behaviour |
|---|---|
| `/home` | The page. Default landing for authenticated recruiters. |
| `/home?pending=D-1234` | Selects a specific pending decision in NOW. |
| `/home?role=42&type=advance&status=pending&q=maya` | Filters compose; persisted in URL. |
| `/home#signal` | Anchors to the SIGNAL section. |
| `/home#analytics` | Anchors to the analytics drill-ins (open the accordion). |
| `/dashboard` | 301 → `/home`. |
| `/reporting` | 301 → `/home`. |
| `/analytics` | 301 → `/home`. |

Reverse deep-links shipped:
- Role detail page: an "{N} pending → Home" button appears next to the role
  hero actions whenever the role has pending agent decisions. Click → `/home?role=:id&status=pending`.
- Candidate detail: deferred. A reverse link from the candidate report
  would need a per-application pending-agent-decision lookup that the page
  doesn't already do; we'd rather not add a button that does a wasted
  fetch for the 99% of candidates with no pending decision. Revisit when
  the candidate audit timeline gets a refactor.

---

## 5. Backend changes

### 5.1 Migration `063_add_decision_feedback.py`

```sql
-- 1. Extend agent_decisions
ALTER TABLE agent_decisions
  ADD COLUMN feedback_id BIGINT REFERENCES decision_feedback(id),
  ADD COLUMN human_disposition VARCHAR(32),  -- approved|overridden|taught|null
  ADD COLUMN snoozed_until TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_agent_decisions_org_status_created
  ON agent_decisions (organization_id, status, created_at DESC);

-- status values are stored as a string (no enum type), so the
-- AGENT_DECISION_STATUSES tuple in the model gains 'reverted_for_feedback'.

-- 2. decision_feedback
CREATE TABLE decision_feedback (
  id BIGSERIAL PRIMARY KEY,
  decision_id BIGINT NOT NULL REFERENCES agent_decisions(id),
  reviewer_id INTEGER NOT NULL REFERENCES users(id),
  organization_id INTEGER NOT NULL REFERENCES organizations(id),
  role_id INTEGER REFERENCES roles(id),  -- nullable when scope='org'
  failure_mode VARCHAR(32) NOT NULL,
  correction_text TEXT NOT NULL,
  scope VARCHAR(16) NOT NULL,  -- decision|role|org
  cosign_required BOOLEAN NOT NULL DEFAULT FALSE,
  cosigned_by_user_id INTEGER REFERENCES users(id),
  cosigned_at TIMESTAMP WITH TIME ZONE,
  applied_at TIMESTAMP WITH TIME ZONE,
  applied_revision_id BIGINT REFERENCES rubric_revisions(id),
  reverted_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_decision_feedback_org_created
  ON decision_feedback (organization_id, created_at DESC);
CREATE INDEX ix_decision_feedback_role_id ON decision_feedback (role_id);
CREATE INDEX ix_decision_feedback_decision_id ON decision_feedback (decision_id);

-- 3. rubric_revisions
CREATE TABLE rubric_revisions (
  id BIGSERIAL PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations(id),
  role_id INTEGER REFERENCES roles(id),  -- null = org-wide
  parent_revision_id BIGINT REFERENCES rubric_revisions(id),
  cause VARCHAR(32) NOT NULL,  -- human_edit|feedback_retune|manual_rollback
  feedback_ids BIGINT[] NOT NULL DEFAULT '{}',
  weights_diff JSONB,
  threshold_diff JSONB,
  notes TEXT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_rubric_revisions_org_created
  ON rubric_revisions (organization_id, created_at DESC);
CREATE INDEX ix_rubric_revisions_role_id ON rubric_revisions (role_id);
```

### 5.2 New / extended endpoints (under `/api/v1`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/agent/org-status` | Org-wide poll: pending count, today, budget, override-rate, paused-roles. Drives the live tab badge + KPI strip. |
| `GET` | `/agent/kpis?range=` | Time-windowed KPI strip values. |
| `GET` | `/agent/roles/breakdown` | Per-role table rows (pending/today/7d/budget/override/teach/paused). |
| `GET` | `/agent-decisions` | **Extended.** New optional filters: `type`, `q`, `since`. Returns the same shape. |
| `POST` | `/agent-decisions/{id}/snooze` | Body: `{}` (1h hard-coded; UI keeps it simple). Sets `snoozed_until = now + 1h`; the row is hidden from `status=pending` filters until then. |
| `POST` | `/agent/feedback` | The "Send back & teach" action. Body: `{ decision_id, failure_mode, correction_text, scope, role_id? }`. |
| `POST` | `/agent/feedback/{id}/cosign` | Second-admin co-sign for `scope=org` feedback. Required before the correction is "accepted" (the future scoring rework will consume it as authoritative input). |
| `POST` | `/agent/feedback/{id}/revert` | Undo within 1h grace window. Restores prior decision status. |
| `GET` | `/agent/feedback?role_id=&since=&limit=` | Backs SIGNAL section. |

`GET /agent/rubric-revisions` is **not** part of this ship — see §8.

### 5.3 Status enum extension

`AGENT_DECISION_STATUSES` in [`backend/app/models/agent_decision.py`](../backend/app/models/agent_decision.py) gains `reverted_for_feedback`. Existing pending-list semantics: a decision with `status='reverted_for_feedback'` shows up in the pending sidebar with a `+ FEEDBACK` pill and the prior reviewer's note attached.

### 5.4 Action layer

New file [`backend/app/actions/teach_decision.py`](../backend/app/actions/teach_decision.py) mirrors the shape of [`approve_decision.py`](../backend/app/actions/approve_decision.py) and [`override_decision.py`](../backend/app/actions/override_decision.py):

- Validates the decision belongs to the actor's org.
- Inserts a `decision_feedback` row.
- Flips the decision: `status='reverted_for_feedback'`, `human_disposition='taught'`, `feedback_id=<new>`, `resolved_by_user_id=<actor>`, `resolved_at=now()`.
- For `scope ∈ {role, org}`, sets `cosign_required = (scope=='org')`. No background job is enqueued — see §8 for the rationale.
- Returns `(feedback, decision)`.

The action's only side effect is on the `decision_feedback` and `agent_decisions` rows. There is no "retune the agent" hook here, by design.

---

## 6. Frontend changes

### 6.1 Routing

[`frontend/src/AppShell.jsx`](../frontend/src/AppShell.jsx):
- `defaultRecruiterRoute` flips from `/jobs` to `/home`.
- New `<Route path="/home" element={<HomePage …/>} />`.
- `<Route path="/dashboard" />`, `<Route path="/reporting" />`, `<Route path="/analytics" />` all `<Navigate replace to="/home" />`.
- `isProtectedRecruiterPath` gains `/home`.

[`frontend/src/app/routing.js`](../frontend/src/app/routing.js):
- New `case 'home'` returning `/home`.
- `case 'reporting'` and `case 'analytics'` redirect to `/home`.

### 6.2 Nav

[`frontend/src/shared/layout/Shell.jsx`](../frontend/src/shared/layout/Shell.jsx):
- New first tab: `{ id: 'home', label: 'Home', Icon: Home, badge: <pendingCount> }`. The badge is reactive — polled from `GET /agent/org-status` every 30s when authenticated.
- `Reporting` tab is removed. The `LineChart` icon and label are no longer used.

### 6.3 Page

New file [`frontend/src/features/home/HomePage.jsx`](../frontend/src/features/home/HomePage.jsx). Component tree:

```
HomePage
├── HomeHero (AgentHeader variant)
├── HomeKpis
├── HomeNow                  // V4 hybrid
│   ├── HomeNowToolbar       // role/type/status filters, search
│   ├── PendingSidebar
│   ├── DecisionDetail
│   │   ├── CandidateSummary // deep-link rows
│   │   ├── DecisionTrace
│   │   └── ActionBar        // Approve / Override / Teach / Snooze
│   └── ActivityFeed         // reverse-chronological, full width below detail
├── HomeRoles
├── HomeSignal
│   ├── PendingCosignTray    // when scope='org' feedback awaits cosign
│   ├── RecentFeedbackList
│   └── RealisedOutcomesList
├── HomeEverything
│   ├── HistoryTable
│   └── AnalyticsDrillIns    // accordion: score histogram + funnel + narrator
└── TeachModal               // mounted on demand
```

### 6.4 API client

[`frontend/src/shared/api/agentClient.js`](../frontend/src/shared/api/agentClient.js) gains:

```js
orgStatus: () => api.get('/agent/org-status'),
kpis: (params) => api.get('/agent/kpis', { params }),
rolesBreakdown: () => api.get('/agent/roles/breakdown'),
snoozeDecision: (id) => api.post(`/agent-decisions/${id}/snooze`, {}),
sendFeedback: (body) => api.post('/agent/feedback', body),
cosignFeedback: (id) => api.post(`/agent/feedback/${id}/cosign`, {}),
revertFeedback: (id) => api.post(`/agent/feedback/${id}/revert`, {}),
listFeedback: (params) => api.get('/agent/feedback', { params }),
listRubricRevisions: (params) => api.get('/agent/rubric-revisions', { params }),
```

### 6.5 Styling

The V4 handoff uses ~340 lines of `.rq-*` classes. They live in a dedicated
file [`frontend/src/features/home/home.css`](../frontend/src/features/home/home.css)
imported once from `index.css` (or directly from `HomePage.jsx`). Tokens are
already in `:root`; no new colours.

---

## 7. Co-sign flow (org-scope teach)

The handoff backend spec calls for this; we ship it in PR 2 rather than
deferring. Without it, a single reviewer can unilaterally re-tune the org-wide
rubric on the strength of one bad call.

```
1. Reviewer A: opens teach modal, picks scope=org → submits.
   Backend: decision_feedback row created with cosign_required=true.
            agent_decisions row flipped to reverted_for_feedback.
            NO retune queued yet.
   Frontend: confirmation toast — "Submitted. A second admin must
            co-sign before this re-tunes the org rubric."

2. Hub SIGNAL section shows a "Pending co-sign" tray for any admin in
   the same org. Each row: original decision summary, reviewer A's
   correction, "Co-sign" button.

3. Reviewer B (different user, must be admin): clicks Co-sign.
   Backend: cosigned_by_user_id + cosigned_at set; retune is enqueued.

4. Reviewer A or any admin can revert within 1h grace via /revert.
```

UI rules:

- The reviewer who submitted org-scope feedback **cannot** co-sign their own
  submission — backend enforces, frontend hides the button if `reviewer_id == current_user.id`.
- An admin role flag is required on both reviewer and cosigner. (Today every
  workspace user is effectively an admin; the check is forward-compatible.)
- The 1h `revert` window starts from `created_at` of the feedback row, not
  from cosign time, to keep the semantics simple.

---

## 8. Deliberately deferred — Taali scoring & decision-making rework

The Hub captures every "Send back & teach" submission as a `decision_feedback`
row. It deliberately **does not** advertise any automated retune to the user,
and the modal copy avoids any "rubric weights will be re-tuned overnight"
language.

The reason: improving how Taali scores candidates and decides actions is a
**deep, IP-load-bearing piece of the platform** that should be designed end-to-end
as its own initiative — not bolted onto a single button click. We don't want
to make a public promise from one corner of the product that we can't yet
keep, and we don't want to ship a half-finished retune pipeline that gets
treated as the long-term architecture.

What we keep building today:

- Every reviewer correction is logged in `decision_feedback` with full scope,
  failure mode, and free text. This is the input the future rework will need.
- The **org-scope two-admin co-sign** stays as a policy guardrail — even
  without retunes, an org-wide correction is a strong signal and shouldn't be
  unilateral.
- The **1-hour revert** stays — reviewers can still undo a teach action
  while it's fresh.
- The audit trail is visible in the Hub's SIGNAL section.

What we explicitly do not ship today:

- No nightly retune job.
- No `GET /agent/rubric-revisions` endpoint (the table + model stay as
  quiet infrastructure for the future rework).
- No "expected impact" / "decisions to retune" copy in the modal.
- No "rubric revisions" sub-block in the Hub's SIGNAL section.

Other items still parked for the future rework:

- **Active learning** — agent proactively flagging borderline cases for human
  review.
- **Reviewer-trust weighting** — feedback from reviewers with high
  agreement-with-peers weighted higher.
- **Rubric versioning UI for non-admins** — read-only view of revisions.
- **Cross-workspace federated training** — explicitly out of scope, probably forever.

---

## 9. Acceptance for the first ship

- [x] Authenticated `/` redirects to `/home`.
- [x] `/reporting` and `/analytics` 301 to `/home`.
- [x] Home tab shows live pending count badge from `GET /agent/org-status`.
- [x] NOW section shows pending decisions, supports approve / override / teach.
- [x] Teach modal posts to `/agent/feedback`; org-scope shows the cosign warning copy and the resulting feedback shows up in the SIGNAL "pending cosign" tray.
- [x] Co-sign click records who co-signed and when.
- [x] Revert within 1h restores the original decision status.
- [x] Snooze hides the row from pending for 1h.
- [x] Filters compose and persist in URL (`?role=`, `?type=`, `?status=`, `?q=`, `?pending=`).
- [x] Score histogram + funnel render inside the EVERYTHING accordion.
- [x] Backend tests cover: feedback create (action + HTTP), cosign, revert, snooze, status enum extension.
- [x] Frontend builds clean; existing tests still pass.
- [x] No copy anywhere implies an automated retune. The Hub captures input;
      improving the agent's scoring is a separate, deeper rework.

---

## 10. Why this layout, not just porting V4 verbatim

The V4 handoff page is a Review Queue — it answers "what needs me?" and "what
already happened?" extremely well. Three things were missing for an
agent-native landing:

1. **A reason to land here over `/jobs`.** The V4 page assumes the recruiter
   navigated to it deliberately. As a *home page* it has to also answer "what
   should I do next?" and "is this thing earning its keep?" The KPI strip and
   ROLES table do that.
2. **Visible learning.** "Tali learns from your decisions" is the single
   highest-stakes claim in the product. Without a SIGNAL section that *shows*
   feedback events tied to rubric revisions, the claim is unfalsifiable.
3. **Continuity with retired pages.** Deleting `/reporting` without absorbing
   its histogram + funnel would erase muscle memory for current users. The
   accordion at the bottom keeps that affordance one click away.

The V4 hybrid stays as-is at the centre (NOW). The page wraps it.
