# Taali motion system

Taali uses Motion.dev through `src/shared/motion`. Application code must not
import `motion/react` directly or create CSS keyframes. The system separates
meaningful product state from decorative feedback so motion stays consistent,
accessible, and inexpensive to render.

## Product language

### Agent motion

Continuous agent motion is reserved for an agent that is active, working, or
authoring a recommendation. Use `AgentLoop` or `AgentFlowButton`:

- `flow` — active agent surfaces and agent-authored actions.
- `glow` — the active agent shell or header.
- `pulse` — a live agent signal or work heartbeat.
- `ring` — work in flight around a glyph.
- `ambient` — a large active-agent surface.

Never use agent loops as generic decoration or as the only indication of a
state. Keep a text label such as “Agent on”, “Screening”, or “Recommendation”.

### General motion

- `Reveal` — one-time narrative entrance.
- `MotionStagger` — a small group entering together; later inserts animate
  without replaying or hiding existing children.
- `MotionList` / `MotionListItem` — inserted, removed, or reordered rows.
- `PresenceSwap` — keyed loading, tab, or selected-detail continuity.
- `MotionDisclosure` — measured expand/collapse.
- `MotionTabs` / `MotionTab` — accessible tabs with a shared layout marker.
- `MotionNumber` — data interpolation without a React render per frame.
- `MotionSpinner` — indeterminate loading.
- `MotionSkeleton` — loading placeholders.
- `MotionProgress` — bars, rails, scores, and plots using transform scale.
- `MotionLoop` — non-agent continuous feedback (`spin`, `pulse`, `signal`,
  `bob`, or `shimmer`).

## Timing and movement

Use values from `tokens.js`; do not introduce page-local duration/easing
scales. The shared vocabulary is:

| Intent | Duration |
| --- | ---: |
| Instant settlement | 80 ms |
| Fast feedback | 140 ms |
| Base state change | 200 ms |
| Spatial movement | 280 ms |
| Content reveal | 480 ms |
| Data/progress | 750 ms |

Dense lists stagger by 35 ms and normal groups by 60 ms, capped at eight
items. Entrances use 12 px by default. Layout changes use the shared spring.

## Performance policy

- Animate `transform` and `opacity` for continuous or repeated motion.
- Agent gradients move on clipped transform layers; agent glow crossfades a
  static shadow layer. Do not animate `background-position`, `box-shadow`,
  width, or height in a continuous loop.
- `MotionProgress` uses `scaleX`/`scaleY`; do not animate bar width or height.
- Loops pause when offscreen, when the document is hidden, and for reduced
  motion. They remain static until viewport visibility is confirmed.
- Do not animate layout for very large collections. Paginate or progressively
  render first, then use motion only on the visible working set.
- Keep CSS for cheap hover/focus color, border, and shadow transitions. Use the
  shared CSS timing variables. Stateful, spatial, entrance/exit, layout, data,
  and continuous motion belongs to Motion.dev.

## Accessibility policy

`MotionSystemProvider` applies the user’s reduced-motion preference globally.
Every shared primitive also has a deterministic reduced/static state for
imperative timelines and native scrolling. Content must never depend on an
animation completing. `Reveal` uses a near-zero viewport threshold so tall
surfaces cannot remain hidden, and immediately reveals when focus enters its
content so keyboard users never land on an invisible control. Loading
indicators need an accessible label when they are the only status announcement.

## Usage

```jsx
import {
  AgentLoop,
  MotionList,
  MotionListItem,
  MotionProgress,
  Reveal,
} from '../../shared/motion';

<Reveal as="section">…</Reveal>

<MotionList as="ol">
  {items.map((item, index) => (
    <MotionListItem as="li" key={item.id} index={index}>
      {item.label}
    </MotionListItem>
  ))}
</MotionList>

<MotionProgress style={{ width: `${score}%` }} />

<AgentLoop kind="flow" className="agent-action">
  Approve recommendation
</AgentLoop>
```

Run `npm run lint:motion` after changing animation behavior. The architecture
gate also runs this check in CI.
