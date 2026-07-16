# Taali chat design system

Status: canonical product and engineering contract
Scope: Search Chat, Chat > Agents, Home agent dock, candidate workspace, and future chat-native workflows
Last updated: 16 July 2026

## 1. Product promise

Taali should let a recruiter complete work end to end in conversation: ask, inspect evidence, give direction, approve a material action, see the receipt, and continue. On agent surfaces, one durable timeline is presented through two deliberately separate lanes:

1. **Chat** contains recruiter turns, direct agent replies, requested work products, and the failure or receipt that is causally part of the active exchange.
2. **Agent Feed** contains unsolicited operational activity: warnings and errors, proactive help, needs-input requests, and workflow decisions.

This separation protects the conversation from background automation without fragmenting the audit trail. Both lanes derive from the same durable timeline; they are presentation filters, not different sources of truth. Structured results are **artifacts** attached to the turn or event that produced them. They are not a third notification language.

This contract governs presentation and interaction. Domain policy, tool authorization, and server-side validation remain owned by their features.

## 2. Core principles

- **One durable timeline, two presentation lanes.** Preserve source ordering and auditability while routing conversational work to Chat and unsolicited operational work to Agent Feed.
- **Causality decides the lane.** A result or failure caused by the user's active request stays in Chat. Background run state, unsolicited help, blockers, and review-queue work belong in Agent Feed.
- **Ask in context.** An agent request explains what is blocked, why it matters, and what the answer will unlock.
- **Conversation before chrome.** Normal suggestions look like assistant turns. Reserve framed attention UI for genuine blockers.
- **Progressive disclosure.** Show the useful summary first. Put IDs, run metadata, and technical detail behind an accessible disclosure.
- **Never hijack the conversation.** An unresolved recruiter request may update a quiet attention badge, but Feed arrivals never switch lanes, steal focus, move chat scroll, or overwrite a draft.
- **Attention is not unread.** The Agent Feed badge counts explicit open recruiter requests. Durable warnings and errors do not have an acknowledgement state and must not count forever or masquerade as unread.
- **User intent stays editable.** Suggested prompts prefill the composer; they do not execute consequential work.
- **Consequence before confirmation.** Material or irreversible actions show the proposed change, impact, cost, and scope before approval.
- **Durable receipts.** After an action, replace the preview with a concise, timestamped result that remains in history.
- **Motion creates awareness.** Motion explains arrival, state change, and focus. It must never be decorative noise.
- **Same semantics at every density.** Full-page and dock surfaces may differ in spacing, never in status meaning or action behavior.

## 3. System ownership and import boundary

The canonical kit lives in `src/shared/chat` and is imported through `src/shared/chat/index.js`. Feature code may lay out a page and provide domain renderers, but it must not redefine shared chat primitives or style their `.tk-*` implementation classes.

### Canonical primitives

| Primitive | Responsibility |
| --- | --- |
| `ChatSurface` | Applies semantic tokens and explicit `comfortable` or `compact` density. |
| `ChatMessage` | User and assistant turn anatomy, identity, timestamp, and markdown slot. |
| `ChatComposer` | Draft, send/stop, keyboard rules, voice, and contextual reply mode. |
| `ChatMarkdown` | Safe, consistent assistant formatting. |
| `ChatEmptyState` | First-use framing and prompt suggestions. |
| `ThinkingDots` | Short pending-response status, not durable history. |
| `NewMessageNotice` | Announces updates when the user is away from the bottom. |
| `ChatActivity` | Flat durable ledger row with severity, metadata, details, and follow-up actions. |
| `ChatArtifact` | Density-aware shell for inspectable results, evidence, previews, comparisons, and reports. |
| `AgentPromptCard` | Genuine needs-input request and its resolved/dismissed receipt state. |
| `AgentHelperPromptCard` | Proactive non-blocking suggestion whose actions prefill chat. |
| `AgentStreamTabs` | Accessible Chat/Agent Feed lane selection and attention badge. |
| `AgentFeedTimeline` | Filters and renders compact operational rows from the shared durable timeline. |
| `CandidateDecisionReference` | Compact workflow-decision summary linking to the canonical review queue. |
| `RoleAgentTimeline` | Shared chronological mapping for Home and Chat > Agents. |
| `useAgentUpdateAwareness` | Near-bottom pinning, unseen-update count, and focus restoration. |
| `useAgentRequestReply` | Moves a free-form agent answer into the composer without losing the user's draft. |

Feature components such as candidate grids, comparison tables, decision evidence, and assessment queues are domain artifacts. They should use the shared artifact anatomy and tokens but keep their feature-specific data logic.

### Boundary rules

- Import primitives from the shared barrel, never a concrete shared file.
- `src/shared/chat` must not import from `src/features`.
- Search Chat and Home agent chat must not import each other's implementations. Move shared behavior into the kit.
- Feature CSS may position a surface. It must not override `.tk-*`; add an explicit primitive prop or shared variant.
- Use shared `Button`, `Input`, and `Textarea`; do not add `.ac-btn` or `.cp-btn-*` aliases.
- A direct route must load all styles it needs. Rendering may not depend on another route having been visited first.

`scripts/check-chat-system.mjs` enforces these boundaries and contains a short, explicit allowlist for current migration seams. Every removed seam should remove its allowlist entry in the same change.

## 4. Foundations

### Semantic tokens

Chat consumes global design tokens through the semantic `--chat-*` layer in `chat-kit.css`. New components use semantic tokens rather than literal brand colors.

| Intent | Token examples | Use |
| --- | --- | --- |
| Surface | `--chat-surface`, `--chat-surface-subtle` | Transcript, composer, artifacts |
| Content | `--chat-text`, `--chat-text-secondary`, `--chat-muted` | Primary, supporting, metadata copy |
| Structure | `--chat-line`, `--chat-shadow` | Separators and floating composer elevation |
| Agent | `--chat-agent`, `--chat-agent-soft` | Identity, selected state, focus affordance |
| Attention | `--chat-attention`, `--chat-attention-soft` | Actionable blocker, never ordinary status |
| Success | `--chat-success`, `--chat-success-soft` | Completed or saved receipt |
| Danger | `--chat-danger`, `--chat-danger-soft` | Failed or destructive consequence |
| Focus | `--chat-focus` | Keyboard focus ring |

Do not encode state through color alone. Every status needs visible text and, where useful, an icon.

### Geometry and density

- Full-page transcript: `comfortable`, content width no wider than `--chat-content-max`.
- Home and side dock: `compact` through `ChatSurface density="compact"`.
- User turns may occupy up to `--chat-user-max`; assistant turns and artifacts use the transcript width.
- Controls use `--chat-control-h`; artifact, turn, and control radii use their named tokens.
- Density changes spacing and type scale only. Do not hide status, consequence, or recovery controls in compact mode.

### Tone and severity

`info` means neutral progress. `success` means completed. `warning` means work can continue but needs attention. `error` means the attempted step failed. `needs-input` is a workflow state, not a synonym for error.

Avoid large tinted backgrounds for ordinary warnings. In the ledger, the rail marker, label, and copy carry severity. Use an attention surface only for the active needs-you request.

## 5. End-to-end surface anatomy

A complete agent chat surface has these regions in order:

1. **Context header**: conversation title, role/scope, connection or run state, and only essential controls.
2. **Optional context rail**: threads or agents. Rows share the same identity/status anatomy across Home and Chat.
3. **Lane control**: an accessible `Chat` / `Agent Feed` tab pair. Feed may show a quiet attention count.
4. **Chat panel**: recruiter turns, direct agent replies, requested artifacts, and causally related action states.
5. **Agent Feed panel**: compact operational rows with `All`, `Needs you`, `Issues`, and `Decisions` filters.
6. **Away-from-bottom notice**: scoped to conversational updates and shown above the composer when Chat updates arrive out of view.
7. **Composer**: sticky to the surface edge, with contextual reply/confirmation state inside it.

Both panels remain mounted so each keeps its own scroll position and switching lanes never discards the user's composer draft. Chat is the default whenever the role changes. A Feed arrival must not auto-select Agent Feed, focus its row, display the Chat new-message notice, or move the Chat panel.

### Lane routing contract

| Timeline item | Presentation lane | Reason |
| --- | --- | --- |
| Recruiter message or direct agent reply | Chat | It is part of the active conversation. |
| Requested artifact, including `candidate_evidence` / Top-X results | Chat | It answers the recruiter's request and must retain its grounded evidence. |
| Failure or receipt for the user's active send | Chat | It is causally necessary to understand that exchange. |
| Background run warning, error, pause, stop, or receipt | Agent Feed | It is operational awareness, not conversational prose. |
| Unsolicited proactive helper prompt | Agent Feed | It may be useful without interrupting the active conversation. |
| Open needs-input / steer request | Agent Feed | It needs attention but should not break dialogue flow. |
| Candidate workflow decision | Agent Feed | The review queue is the canonical place to inspect and act. |

Lane selection is derived from stable item type and causality, not severity alone. Persisted history and live arrivals must use the same classifier.

## 6. Conversation turns

Use a conversation turn for:

- recruiter instructions and questions;
- agent explanations and summaries;
- agent help or suggested next steps requested in the active exchange;
- one to three low-risk quick replies;
- the natural-language lead-in to an artifact.

User text is right-aligned and visually bounded. Agent text is borderless, left-aligned, and identified by label/avatar when context is otherwise ambiguous. Timestamps are secondary and use a real `<time>` value.

Do not place normal agent prose inside warning cards. Do not repeat an agent label both in `ChatMessage` and in feature markup. The target `ChatMessage` contract should accept explicit identity and density instead of requiring `.cp-agent-say` or `.ac-agent-say` wrappers.

Unsolicited proactive help belongs in Agent Feed; help directly requested by the recruiter stays in Chat. In either lane, suggestions are safe only when they place editable text in the composer. A suggestion labeled “Retry unfinished work” may prepare the retry request; it must not start work unless the action is already an explicit, low-risk control with clear semantics.

## 7. Agent requests and composer reply mode

### When to interrupt

An item belongs in needs-you only if the agent cannot safely continue without a human answer, authorization, or missing resource. Open requests appear as one-line Agent Feed rows and reveal the full request only when expanded. A useful request contains:

- a direct title;
- the blocked fact in plain language;
- why the answer is necessary;
- one to three typed options, or a request to reply in chat;
- the effect of answering;
- a dismiss option only when dismissal is valid.

Avoid “Agent needs a steer” as the only heading. Prefer the decision: “Choose an assessment task” or “Make these CVs readable.”

### Request states

```text
open -> saving -> answered
  |       |          |
  |       +-> error -+ (retry preserves the answer)
  +-> dismissed
  +-> auto-resolved
```

- Disable sibling actions while saving and set `aria-busy`.
- Preserve typed text after an error.
- Announce save failures with `role="alert"`.
- Replace an answered request with its receipt in place; do not delete it from history.
- Auto-resolution must say what was detected, not imply the user answered.

### Quick choices versus typed answers

- Render up to three short, mutually exclusive options inline.
- If an answer needs prose, number validation, or “something else,” switch the shared composer into reply mode.
- Reply mode identifies the request, shows a two-line prompt summary, changes the accessible label, and allows Escape to cancel.
- Choosing a reply action from Agent Feed switches to Chat intentionally, prefills or enters composer reply mode, and focuses the composer only as a direct result of that user action.
- Preserve the user's pre-existing draft when reply mode begins; restore it on cancel and after a successful answer.
- Validate numeric minimum, maximum, integer, and finite-number constraints before submit. Server validation remains authoritative.
- Failed submission keeps reply mode, answer, and focus.

## 8. Agent Feed and flat activity ledger

Agent Feed is the operational view of the durable timeline. Use `ChatActivity` inside an expanded Feed row for machine or system history:

- background agent tool called, working, completed, or failed;
- agent cycle started, paused, stopped, or retried;
- action receipt, threshold changed, invite sent, or role created;
- warning that does not require a blocking answer.

The default Feed presentation is a single line: state icon, concise title, essential metadata or time, and a disclosure affordance. Summary, source/run metadata, recovery actions, and human-readable details are not mounted or shown until the recruiter expands the row. Do not render warnings, errors, or requests as permanently open cards. The outer row already owns title, severity, and time; expanded detail must not repeat that header or wrap itself in another large card.

In `All`, aggregate pending candidate workflow decisions into one “N candidate decisions ready” queue row so a large agent cycle cannot bury steers or issues. Show individual candidate references only after the recruiter chooses `Decisions`.

### Ledger rules

- One durable row per meaningful event, not per polling tick or token.
- Update an in-progress row in place when it completes.
- Title describes the outcome: “Candidate search completed,” not “Success.”
- Summary explains useful scope: “138 candidates scored against AI Engineer.”
- Keep the collapsed row to one visual line at standard desktop density; truncate long copy with an accessible full label.
- Metadata may show run number and timestamp; internal payloads and stack traces stay out of recruiter UI.
- “Details” is keyboard-operable and reveals human-readable fields, never raw production JSON.
- Actions are text-forward, small, and adjacent to the event they affect.
- Consequential retry shows scope and any estimated cost before execution.
- Feed filters alter presentation only; they never mutate, mark read, or remove the underlying timeline item.

### Candidate workflow decisions

Do not hydrate or embed the full candidate decision card in either lane. In Agent Feed, show a compact `CandidateDecisionReference` with candidate name, recommendation/status, score when available, and a link to `/home?role=<role>&pending=<decision>`. Home's review queue remains the canonical action surface and owns the full decision evidence and controls.

This rule does not apply to a recruiter-requested shortlist or Top-X search. Those are grounded `candidate_evidence` artifacts and remain in Chat with their ranking, criteria, provenance, and shareable report.

## 9. Structured artifacts

Artifacts are inspectable work products attached immediately after the turn or activity that produced them. Use one shared artifact anatomy:

- optional eyebrow and title;
- concise summary;
- domain body;
- provenance or evidence;
- primary and secondary actions;
- loading, empty, partial, error, and stale states.

Artifacts should not copy ledger severity chrome. A result can be attached to an error row when partial data remains usable. Keep candidate evidence, comparisons, graphs, decisions, and previews domain-specific.

### Agent action mapping

| Current action type | Target presentation |
| --- | --- |
| `helper_prompt` | Agent Feed row when unsolicited; normal Chat turn when directly requested. Actions prefill the composer. |
| `agent_event` | One-line Agent Feed row with expandable activity detail. |
| `operation_preview` | Confirmation request plus structured preview artifact. |
| `decision_action_preview` | Confirmation request plus consequence/evidence artifact. |
| `operation_receipt` | Compact success/error ledger receipt. |
| `threshold_change` | Compact ledger receipt with old/new value. |
| `related_role_created` | Compact receipt linking the created role. |
| `constraint_change` | Structured change artifact. |
| `job_spec_change` | Structured diff/change artifact. |
| threshold recommend/simulate | Structured recommendation artifact with evidence and impact. |
| related-role preview | Structured preview artifact. |
| draft task | Structured editable artifact. |
| workflow `decision` | Compact Agent Feed reference linking to the canonical Home review queue; never the full candidate card. |
| `candidate_evidence` / Top-X result | Grounded Chat artifact with ranking, provenance, coverage, and report link. |

## 10. Search Chat tool lifecycle

Search Chat is streamed and tool-rich. Migration must preserve the protocol emitted by `useChatStream.js` and the ordered `parts` rendered by `Thread.jsx`.

### Grounded candidate-search contract

Grounding is the default presentation and reasoning contract for candidate discovery in every chat surface. Grounded shortlist and Top-X results always remain in Chat because they answer a recruiter request; never route them to Agent Feed or replace them with a workflow-decision reference. A result must make it possible for the recruiter to distinguish what Taali found in deterministic data, what it verified against candidate evidence, what is missing, and what was not checked.

Every candidate-search artifact exposes, when applicable:

- the interpreted request or criteria echo;
- per-criterion status (`met`, `partially met`, `not met`, or `not stated`);
- the source and verbatim evidence for qualitative claims;
- coverage (`database matches`, attempted `deep checked`, `evidence succeeded`/failed, `qualified`, `returned`, and whether verification was capped);
- exclusions and warnings without silently treating unchecked candidates as failures;
- an unguessable, time-limited bearer report link for a ranked shortlist.

Use the smallest honest search mode:

| Recruiter request | Search contract | Presentation |
| --- | --- | --- |
| “Find/show/best/top candidates with …” or any bounded qualitative discovery | Ground and rank the named criteria, even when the recruiter did not say “top” | Shared candidate-evidence artifact plus the report link |
| Exact name, score, pipeline stage, outcome, or other canonical field | Deterministic database filter | Candidate grid with the canonical fields that caused the match |
| Explicit “all/every” structural search | Exhaustive person-deduplicated database retrieval | Candidate grid plus coverage; do not imply deep verification |
| Explicit “all/every” qualitative search | Exhaustive retrieval followed by bounded evidence verification | Coverage plus grounded evidence; clearly identify the unchecked remainder |
| Relationship/graph question | Ground in graph facts and hydrate matching applications | Candidate grid and graph artifact; distinguish graph evidence from CV evidence |

The shareable report is the immutable companion to the in-chat artifact. It uses an unguessable bearer token, expires after 30 days, and recursively scrubs contact/identity fields, credentials and tokens, embedded URLs, and live/internal ATS links on both write and read. It shows the same ranking, criteria, verdicts, evidence, warnings, and coverage the recruiter inspected in chat. Display its link as soon as the search completes, including score-only or degraded results with their honest label. The snapshot is read-only and must not be described as current live candidate state; anyone with the link can view it until expiry, so the UI and agent should say that plainly.

“Grounded by default” does not mean pretending every search was model-verified. Exact canonical fields are grounded in the database; qualitative claims require cited candidate evidence; bounded verification must disclose its coverage. If grounding is unavailable, retain the result only with a visible warning and do not present an unsupported criterion as satisfied.

### State and ordering contract

```text
assistant text delta(s)
tool_call: streaming -> awaiting_result -> complete | error
tool result artifact(s)
assistant text delta(s)
```

- Preserve the exact interleaving of text and tool calls/results. Never collect all tools below all prose.
- A tool activity row may replace `ToolCallCard`, but its result artifact renders immediately after that row.
- A tool result object containing only `error` becomes the tool's error state.
- `hydrateMessage` plus `stitchToolResults` must continue dissolving synthetic user tool-result rows into the preceding assistant calls for persisted-history compatibility.
- Raw arguments and raw results remain development-only. Never expose recruiter-facing payload JSON.
- Streaming token deltas update the current turn without entrance animation. Only a newly created turn or activity row enters.
- Keep Stop available during streaming and retry the last user turn after a friendly failure.
- Friendly errors retain quota, authentication, rate-limit, and generic mappings.
- Pin to bottom only while the user is already near it. Otherwise increment and announce `NewMessageNotice` without moving their reading position.

### Tool renderer mapping

| Tool | Result artifact contract |
| --- | --- |
| `find_top_candidates` | `CandidateEvidenceCard` |
| `screen_pool_against_requirement` | `CandidateEvidenceCard` |
| `compare_applications` | `ComparisonTable` |
| `search_applications` | `CandidateGrid` |
| `get_recruiting_overview` | Recruiting overview artifact |
| `list_assessments` | Assessment queue artifact |
| `nl_search_candidates` | Candidate grid and graph when data exists, plus `SearchCoverage` |
| `graph_search_candidates` | Candidate grid and graph when data exists |

Do not collapse multi-output tools to a single result. `nl_search_candidates` may legitimately render the candidate grid, graph, and coverage together. Preserve `GraphView` as a lazy boundary because Cytoscape is a large dependency (approximately 455 kB).

## 11. Motion system

All chat motion uses `src/shared/motion` and Motion 12. Do not import Motion directly in feature chat code when a shared primitive already expresses the behavior.

| Moment | Shared behavior | Intent |
| --- | --- | --- |
| New turn/activity | `MotionChatItem` | Make a newly arrived unit discoverable. |
| A short set of quick actions | `MotionStagger` | Reveal choices as one coherent response. |
| Request state changes | `PresenceSwap` | Connect open, saving, receipt, and error states. |
| Details/rationale | `MotionDisclosure` | Preserve spatial continuity while expanding. |
| Feed attention count | `MotionAttentionBadge` | Call attention once when unresolved attention changes; never imply unread state. |
| Chat/Feed selection | `MotionTabs` / `MotionTab` | Preserve orientation while the recruiter explicitly changes lanes. |
| Active run/tool | `AgentLoop`, `MotionLoop`, or spinner | Signal genuinely active work. |
| Composer reply mode | shared layout motion | Show that the composer is now answering a specific request. |
| Updates off-screen | `NewMessageNotice` | Create awareness without stealing scroll. |

### Motion rules

- Loaded history uses `initial={false}` and does not replay entrance animations.
- Never animate each streamed token, polling refresh, or timestamp update.
- Animate a semantic phase change once; do not pulse static warnings.
- An open recruiter-request arrival may animate the attention badge once. Informational Feed history never increments the badge without a durable acknowledgement model. Neither may auto-switch the selected lane, expand a row, steal focus, move Chat scroll, or trigger Chat's `NewMessageNotice`.
- Indefinite loops are allowed only while work is genuinely active and must stop on complete, error, pause, or cancellation.
- Use `motionSafeScrollBehavior`; preserve the reader's scroll unless they request the jump.
- Respect reduced motion everywhere. Reduced motion changes transitions to instant and removes scale/translation loops while retaining state text and announcements.
- Keep entry motion subtle: short opacity plus small y-shift. Avoid layout bounce, parallax, or celebratory motion in operational chat.

## 12. Accessibility contract

- The transcript has an accessible name. Do not make the entire history a noisy live region.
- Chat and Agent Feed use a real tablist with named tabs, `aria-selected`, and associated tabpanels. Hidden panels must not expose interactive controls to keyboard or assistive technology.
- Each Feed row is a button or disclosure control with `aria-expanded`; its compact title remains understandable without color or icon alone.
- New assistant text uses a polite, atomic announcement at turn completion or a debounced meaningful chunk, not on every token.
- Errors use `role="alert"`; saved receipts use polite status.
- Every icon-only action has an accessible name and tooltip/title where helpful.
- Use actual buttons, links, forms, `details/summary`, headings, lists, and `<time>` elements.
- Visible focus meets contrast requirements and is never clipped by sticky containers.
- Quick-choice groups have a question-specific accessible label.
- Busy states expose `aria-busy`, disable duplicate submissions, and retain focus context.
- Escape cancels composer reply mode and restores the draft. Enter behavior is always shown near the composer.
- IME composition must finish before Enter can send. Keep the `isComposing`/key-code guard.
- Minimum pointer target is 32 px in dense desktop UI and 44 px where the mobile layout permits.
- Do not rely on hover to reveal the only route to details or recovery.
- Updating the Feed attention badge must not move focus. Focus moves from Feed to the Chat composer only after the recruiter activates a reply or prefill action.
- At 200% zoom and a 320 px viewport, content reflows without horizontal page scrolling.

## 13. Responsive, themes, and resilience

- At narrow widths, stack timestamp under title, make artifacts one column, and keep primary actions reachable without horizontal scrolling.
- The composer remains visible above safe-area insets and the on-screen keyboard.
- Long role names, candidate names, IDs, and URLs wrap or truncate with an accessible full label.
- Dark mode comes from semantic tokens. Do not add component-specific hard-coded light backgrounds.
- A cold direct load of `/chat/agents` and `/chat` must be visually complete.
- Offline, load error, stalled turn, poll limit, and retry states need human-readable recovery.
- Returning persisted history must produce the same chronology as the live turn.

## 14. Prompt examples for product QA

These prompts test both capability and presentation. Use roles with realistic data and record whether the expected pattern appears.

### Search and evidence

- “Find the top five AI engineers with Azure Databricks and explain the evidence for each.”
- “Find candidates with payments experience and stakeholder leadership.” (No “top” wording; this should still use the grounded shortlist.)
- “Give me a shareable report for the top ten data engineers with banking experience.”
- “Compare the strongest three candidates side by side on must-haves, gaps, and confidence.”
- “Show everyone with Python and graph databases, then visualise how their skills connect.”
- “Screen the pool against this requirement: five years of Snowflake and stakeholder leadership.”
- “What is our recruiting overview today, and which stage is the bottleneck?”
- “List the open assessments and tell me which candidates are waiting longest.”

Expected: ordered text/tool ledger, one or more attached artifacts, human-readable details, stop during streaming, and no raw JSON. A grounded shortlist shows its interpreted criteria, coverage, per-candidate evidence, and secure report link in both Search Chat and Agent Chat. Opening the link reproduces the same read-only evidence snapshot without direct contact PII.

### Agent helper behavior

- “What should I do next on this role?”
- “Help me improve this role before you screen anyone.”
- “Tell me where you are blocked and give me the fastest safe options.”
- “Suggest how to handle these unreadable CVs.”

Expected: a direct answer stays in Chat; an unsolicited helper appears as a compact Agent Feed row. In either case, suggestions prefill the composer rather than execute.

### Needs-you and reply mode

- Start a role with no assessment task.
- Start a role with an ambiguous threshold.
- Start a role with a missing job description.
- Trigger an unreadable or missing CV request.
- Choose “Something else,” type an invalid number, correct it, then send.
- Enter reply mode while a draft already exists, cancel, and confirm the draft returns.

Expected: decision-specific one-line Agent Feed row, expandable request, quick choices or an explicit jump to composer reply mode, preserved text on error, and durable receipt after success.

### Lane separation and feed density

- Continue a multi-turn conversation while a background run emits a warning and an error.
- Trigger a needs-input request while Chat has an unsent draft.
- Produce a candidate workflow decision, then a grounded Top-X shortlist.
- Switch between `All`, `Needs you`, `Issues`, and `Decisions` in Agent Feed.

Expected: Chat remains selected and its scroll, focus, and draft do not move when Feed items arrive. Only an explicit unresolved steer changes the attention badge. Warning, error, and steer rows are one line until explicitly expanded. `All` aggregates pending candidate decisions; `Decisions` exposes compact individual references linking to the Home review queue without ever rendering the full candidate card. The grounded Top-X artifact and report remain in Chat.

### Consequence and receipt

- “Change the pass threshold from 55 to 70.”
- “Reject these 20 candidates and explain exactly who will be affected first.”
- “Retry only the unfinished work from the stopped run.”
- “Create a related Senior Data Engineer role from this one.”

Expected: preview before a material action, scope/cost where relevant, explicit approval, then a compact durable receipt.

### Awareness and motion

- Scroll up while a streamed answer continues.
- Leave a role open while its agent posts a new event.
- Turn on reduced motion and repeat request resolution, details expansion, and retry.
- Reload a long thread and confirm old rows do not animate in.

Expected: no forced scroll, Chat notices announce only conversational updates, the Feed attention badge announces unresolved attention without claiming unread state, only new semantic units animate, and reduced motion is instant.

## 15. Migration map

### Phase 1: stabilize the shared contract

- Make `ChatSurface`, `ChatActivity`, `RoleAgentTimeline`, `AgentStreamTabs`, `AgentFeedTimeline`, reply mode, and update awareness canonical exports.
- Add explicit identity and density to `ChatMessage`; remove feature-owned agent-say wrappers.
- Keep all shared primitive styles colocated or loaded by the barrel so direct routes are safe.
- Add unit contracts for message, empty state, new-update notice, update awareness, reply draft preservation, activity details, and reduced motion.

### Phase 2: unify agent conversations

- Keep fetching, polling, permissions, and feature layout in `AgentChatDock.jsx` and `AgentConversation.jsx`.
- Partition the shared durable timeline with one canonical lane classifier; keep live and persisted items consistent.
- Render operational events as one-line Agent Feed disclosures and keep direct-turn failure states in Chat.
- Route unsolicited helper prompts and genuine blockers to Agent Feed; their explicit actions may prefill or enter Chat composer reply mode.
- Replace full candidate decision cards with compact Feed references to the Home review queue while preserving grounded `candidate_evidence` artifacts in Chat.
- Extract a shared agent list row; retain Home/Chat wrappers for grouping and bulk controls.
- Establish parity tests for load errors, stalled turns, poll caps, lane isolation, attention badges, draft/scroll preservation, decision references, and action rendering.

### Phase 3: migrate Search Chat

- Replace `ToolCallCard` chrome with ledger rows without changing `parts` ordering.
- Attach result artifacts immediately after their tool row and preserve all multi-output mappings.
- Add Search update awareness while streaming.
- Delete dead `.cp-msg-*`, `.cp-composer`, and `.cp-btn-*` rules only after consumers move to the kit.
- Add a cold-route test and a complete stream/error/stop/retry lifecycle test.

### Phase 4: finish artifacts and remove seams

- Standardize the artifact shell across candidate evidence, comparisons, operations, and decisions.
- Move shared domain-neutral cards out of feature-to-feature imports.
- Delete legacy `.ac-btn` aliases and feature `.tk-*` overrides.
- Remove each matching allowlist entry from `check-chat-system.mjs`.
- Add mobile, dark, 200% zoom, keyboard, screen-reader, and reduced-motion visual/interaction coverage.

## 16. Required test matrix

Every chat surface must cover:

| Area | Required checks |
| --- | --- |
| Lifecycle | empty, loading, first turn, streaming/working, complete, partial, error, retry, cancel/stop |
| Ordering | text before/between/after tools; artifact immediately follows producer; persisted hydration matches live order; stable Chat/Feed classification |
| Input | mouse, keyboard, IME, multiline, reply cancel, draft restore, failed-submit preservation, voice when supported |
| Awareness | near-bottom pin, Chat-only away-from-bottom count, Feed attention count, no auto-switch/focus/scroll theft, no duplicate announcement |
| Requests | collapsed Feed row, expansion, options, typed, numeric validation, dismiss, auto-resolve, save error, answered receipt |
| Actions | preview, confirm, cancel, permission failure, partial failure, receipt, idempotent retry |
| Layout | full page, dock, 320 px, 200% zoom, long content, soft keyboard |
| Theme/motion | light, dark, reduced motion, history does not replay |
| Accessibility | headings, labels, focus order, focus visibility, live regions, contrast, target size |
| Routes | cold direct `/chat` and `/chat/agents`; no style dependency on Home; decision references deep-link to the canonical queue |

## 17. Definition of done

A chat change is complete when:

- every timeline item uses the Chat/Agent Feed causality contract intentionally;
- live and persisted ordering match;
- Feed arrivals never steal Chat selection, focus, scroll, draft, or new-message notice;
- operational rows are one line until expanded and candidate decisions remain compact queue references;
- grounded Top-X and `candidate_evidence` artifacts remain in Chat with their evidence and report;
- user text and drafts survive recoverable failures;
- material actions have preview and receipt;
- new updates are noticeable without scroll theft;
- keyboard, IME, screen-reader, narrow viewport, dark mode, and reduced motion behavior are verified;
- it introduces no feature-owned shared primitive, `.tk-*` override, or new Search/Home cross-import;
- `npm run check:architecture`, relevant unit tests, and the production build pass;
- a direct cold load of the changed route is visually verified.

## 18. Known migration risks

- Chat > Agents currently reaches into Home agent chat for card implementations. Their styles historically came from `agentchat.css`, so a cold direct route can be incomplete. Shared prompt/activity ownership removes that dependency; keep a cold-route regression test.
- `AgentChatDock.jsx` and `AgentConversation.jsx` have duplicated fetching/timeline behavior with differences in load errors, stalled-turn handling, polling caps, and update awareness. Shared rendering alone is not enough; parity tests must precede orchestration consolidation.
- Legacy `AgentDecisionTimelineCard` styles are scoped to the Home stream. Agent chat must not import or hydrate that full card; keep the compact decision reference in shared chat and the full card in the Home review queue.
- `chat.css` and `agentchat.css` still override shared kit density and contain legacy controls. Treat the architecture allowlist as deletion work, not an extension point.
- Shared chat primitives also serve Requisitions, Client Intake, Assessment runtime, Home Showcase, Job Spec, and Public Job surfaces. Use explicit variants and test consumers; never silently change global `.tk-*` behavior for one feature.
