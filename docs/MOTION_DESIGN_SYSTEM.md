# Taali motion design system

Taali motion is calm, directional, and explanatory. It should make a state
change easier to follow without making hiring decisions, assessments, or data
review feel playful or unstable.

The system uses Motion for React for presence, layout, gestures, and sequenced
animation. CSS remains the right tool for simple colour/focus transitions and
essential loading indicators, but it consumes the same timing and easing
tokens.

## Principles

1. **Explain change.** Animate an item leaving, moving, expanding, or being
   replaced when that movement helps the user understand cause and effect.
2. **Prefer continuity over entrances.** Layout and presence transitions are
   more useful than replaying page reveals.
3. **Keep work surfaces calm.** Assessment timers, proctoring states, streamed
   tokens, and large data tables do not bounce, pulse, or continuously reflow.
   The deliberate exception is a restrained live-agent signal: an enabled
   agent, agent-authored recommendation, or work-in-flight indicator may use
   the shared flow, glow, pulse, ring, or ambient loop.
4. **Animate once unless the user caused it.** Polling, background refreshes,
   filter changes, and route remounts must not replay narrative staggers.
5. **Reduced motion is a first-class state.** Critical content always has a
   deterministic final state. Decorative loops and smooth scrolling stop.
6. **Use the shared API.** Product code imports from `src/shared/motion`, never
   directly from `motion/react`.

## Tokens

| Token | JavaScript | CSS | Purpose |
|---|---:|---:|---|
| instant | `0.08` | `80ms` | Press and icon feedback |
| fast | `0.14` | `140ms` | Hover, focus, popover exit |
| base | `0.20` | `200ms` | Tabs, validation, popovers, disclosures |
| spatial | `0.28` | `280ms` | Drawers, sheets, list reflow |
| reveal | `0.48` | `480ms` | One-shot marketing entrances |
| data | `0.75` | `750ms` | First-settle charts and metrics |

Active-agent loops have a separate cadence because they communicate an ongoing
state rather than interaction latency:

| Loop | JavaScript | Purpose |
|---|---:|---|
| pulse | `1.6s` | Small live/working dot |
| ring | `1.6s` | Work actively executing now |
| glow | `3.6s` | One major agent-on container |
| flow | `7s` | Agent-on identity and agent-authored primary action |
| ambient | `18s` | Large agent background, used sparingly |

Easing:

- Enter: `[0.16, 1, 0.3, 1]`
- Standard: `[0.2, 0, 0, 1]`
- Exit: `[0.4, 0, 1, 1]`
- Emphasized marketing: `[0.2, 0.7, 0.2, 1]`
- Positive confirmation: `[0.2, 1.3, 0.4, 1]`
- Continuous loop: `[0.45, 0, 0.55, 1]`
- Layout spring: stiffness `420`, damping `36`, mass `0.8`

Distance:

- Micro: `2px`
- Small: `4px`
- Medium: `12px`
- Large: `24px`

Stagger is `35ms` for dense content and `60ms` by default, capped at eight
items. Exits should normally be faster than entrances. Overshoot is reserved
for positive confirmation and authored marketing scenes; destructive actions
and assessment UI never overshoot.

## Ownership

Use Motion for:

- mounting and unmounting dialogs, sheets, menus, toasts, and alerts;
- tab indicators and restrained panel swaps;
- measured accordions and disclosures;
- user-triggered filtering, insertion, removal, and reordering;
- shared-element or layout continuity;
- authored, one-shot marketing timelines;
- first-settle data visualisation.
- all continuous live-agent signals through `AgentLoop`.

Use tokenised CSS for:

- colour, border, shadow, and focus transitions;
- simple button press states that do not need orchestration;
- genuinely essential loading indicators;
- static gradient, border, and shadow styling underneath an `AgentLoop`.

## Shared primitives

- `MotionSystemProvider` — global Motion configuration and reduced-motion
  policy.
- `Reveal` — a true, one-shot in-view reveal for narrative content.
- `PresenceSwap` — keyed loading, empty, error, tab, and detail transitions.
- `MotionDisclosure` — measured expand/collapse with an immediate reduced-motion
  state.
- `MotionTabs` / `MotionTab` — shared moving indicator and keyboard-safe tab
  markup.
- `MotionList` / `MotionListItem` — capped entrance choreography plus layout and
  exit continuity.
- `MotionNumber` — previous-to-next interpolation, never repeated zero-to-value
  theatre after polling or a filter change.
- `AgentLoop` — the only continuous-motion primitive. `flow`, `glow`, `pulse`,
  `ring`, and `ambient` share tokens, pause outside the viewport, and settle
  explicitly under reduced motion.

Agent loop semantics are strict:

| Kind | Use | Do not use |
|---|---|---|
| flow | Agent ON, agent recommendation, agent-controlled pipeline rail | Generic gradients or settled data |
| glow | One primary agent-on container | Every agent card on a page |
| pulse | Tiny live/working indicator | Whole rows or readable text |
| ring | A run is currently in flight | Merely enabled or completed states |
| ambient | Large hero/agent background | Dense product cards |

Paused, idle, off, completed, and error states stay static. A surface may have
one persistent base loop plus one conditional in-flight ring; it should not
stack multiple competing pulses.

Shared `Dialog`, `Sheet`, and toast primitives build on these contracts. A
feature should not add a bespoke overlay animation.

The interactive reference surface lives at `/dev/motion` behind the existing
developer token gate. It demonstrates the live tokens, tabs, keyed presence,
disclosures, list reflow, number interpolation, dialogs, sheets, and toasts.

## Implemented coverage

- Global provider and reduced-motion policy in the application shell.
- Shared navigation drawer, account/search popovers, marketing mobile menu,
  keyboard-shortcut dialog, Sheet, Dialog, and toast presence.
- Home decision-detail continuity, Jobs card filter/reflow, Analytics tabs and
  panels, and candidate-triage tabs/disclosure.
- Agent recommendation actions, agent-on headers and role pills, fleet/activity
  signals, agent chat, and marketing agent scenes on the shared loop contract.
- True once-in-view marketing reveals and centralized authored-scene easing.
- Motion-safe native scrolling throughout product, assessment, chat, settings,
  requisition, demo, and marketing surfaces.

## Reduced-motion policy

The provider respects `prefers-reduced-motion`. Under reduced motion:

- transform, layout, parallax, smooth scrolling, and every `AgentLoop` stop;
- count-ups and charts render their final value immediately;
- a short opacity transition may remain when it preserves context;
- small essential progress indicators may continue, without scale pulsing;
- videos and authored timelines do not autoplay;
- nothing meaningful depends on an intersection observer or animation
  callback becoming visible.

The preference must be read through the shared hook for imperative logic and
scroll behaviour. Do not create local `matchMedia` hooks.

## Product rules

- **Home:** animate a reviewed decision leaving and the next decision taking its
  place. Do not animate reordering caused only by polling.
- **Jobs:** lay filtered cards into their new positions. Do not replay the full
  entrance stagger after each filter.
- **Candidate reports:** use the shared tab and disclosure motion. Avoid motion
  across long evidence tables.
- **Analytics:** draw charts on first settle; interpolate previous data to new
  data when shapes are compatible. Do not restart every metric at zero.
- **Chat:** animate a new message container once. Never animate streamed tokens
  or smooth-scroll on every token.
- **Assessment runtime:** animate explicit panel open/close actions only. Do not
  animate countdowns, urgency milestones, pointer-driven resizes, or editor
  activity.
- **Marketing:** narrative motion is one-shot, bounded, and in-view. Avoid
  parallax and scroll-scrubbed critical content. Continuous flow is reserved
  for an explicit agent-on scene and still pauses offscreen.

## Verification and guardrails

Every new motion primitive or migrated interaction needs:

1. a normal-motion behavior test;
2. a reduced-motion final-state test;
3. an exit/unmount test when presence is involved;
4. focus-restoration coverage for overlays;
5. a production build/bundle check;
6. desktop and mobile visual verification.

The UI lint rejects new direct `motion/react` imports outside the shared motion
module and rejects retired CSS agent keyframes. Literal animation timing in
feature components should be treated as a design-system exception and
documented when unavoidable.
