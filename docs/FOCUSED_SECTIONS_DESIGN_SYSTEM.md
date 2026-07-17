# Focused sections navigation

Focused sections is Taali’s standard page-level navigation pattern for durable task areas. It replaces long horizontal tab strips and one-page card stacks with a compact section index and one focused content region.

## Navigation taxonomy

### Focused sections

Use when all of the following are true:

- The page has three or more durable sections.
- Each section represents a distinct user task or body of content.
- Only one section needs to be visible at a time.
- The section should be addressable after refresh or from a copied link.

Desktop uses a sticky left rail. At 980px and below, the same items become a horizontally scrollable selector above the content. Do not render a second desktop rail inside a focused content region.

Examples: Workspace Settings, Job → Agent settings, and the Developer Portal section index.

### Peer views

Use the horizontal `bar` variant for equal work modes that users switch between frequently and that benefit from preserving the full page width.

Examples: Candidates / Pipeline / Job spec / Agent settings on a role; Analytics lenses; Candidate report views; Decision policy views; Job spec / Brief inside the requisition workspace.

### Segmented controls

Use `SegmentedControl` for filters and modes, not page navigation.

Examples: date window, pipeline stage filter, Manual / Agent-managed threshold, Ask me / Act automatically.

### Local content tabs

Use `TabBar` only for two or three compact panels inside an already focused task, such as Evidence / Timeline in a drawer. It implements the complete horizontal ARIA tab interaction: one tab stop, Arrow Left / Right, Home / End, linked panel ids, and automatic activation. It is not a substitute for a page URL.

## Component contract

```jsx
import {
  FocusedSectionLayout,
  SegmentedControl,
  TabBar,
} from '../../shared/ui/TaaliPrimitives';

const sections = [
  {
    id: 'guidance',
    label: 'Guidance',
    description: 'Criteria and feedback',
    meta: '9 criteria',
    Icon: Target,
    group: 'Agent & communication',
    to: '?section=guidance',
  },
  {
    id: 'budget',
    label: 'Budget & limits',
    group: 'Finance & operations',
    badge: { label: '84%', ariaLabel: '84% of budget used', tone: 'warning' },
    tone: 'warning',
    to: '?section=budget',
  },
];

<FocusedSectionLayout
  items={sections}
  activeId={activeSection}
  onChange={setActiveSection}
  ariaLabel="Agent settings sections"
>
  {activeContent}
</FocusedSectionLayout>
```

Compact panel tabs and filters use separate primitives:

```jsx
<TabBar
  ariaLabel="Candidate evidence"
  tabs={[
    { id: 'evidence', label: 'Evidence', panelId: 'evidence-panel' },
    { id: 'timeline', label: 'Timeline', panelId: 'timeline-panel' },
  ]}
  activeTab={panel}
  onChange={setPanel}
/>

<SegmentedControl
  ariaLabel="Pipeline stage"
  options={stages.map((stage) => ({
    value: stage.id,
    label: stage.label,
    meta: stage.count,
  }))}
  value={stageFilter}
  onChange={setStageFilter}
/>
```

`TabBar` options may include `disabled`, `panelId`, `tabId`, `ariaLabel`, `meta`, and `className`. `SegmentedControl` options may include `disabled`, `ariaLabel`, `title`, `meta`, and `className`; it exposes a labelled button group with `aria-pressed`, so every enabled option remains directly keyboard reachable. Use `density="compact"` only in constrained drawers or toolbars and `fullWidth` only when equal-width options improve scanning.

Focused-section item properties:

| Property | Purpose |
|---|---|
| `id` | Stable section identifier. Required. |
| `label` | Short task-oriented label. Required. |
| `description` | Optional one-line explanation; hidden in the mobile selector. |
| `meta` | Optional count or current state, such as `55%` or `4 overrides`. |
| `badge` | Optional compact status. Accepts content or `{ label, ariaLabel, tone }`. |
| `tone` | Optional `neutral`, `info`, `success`, `warning`, or `danger` emphasis. |
| `Icon` / `icon` | Optional explicit icon component or compact marker. The rail generates a number when omitted. |
| `marker` | Set to `false` to suppress the rail’s generated number for a specific item. |
| `group` | Optional rail heading. Accepts a label or `{ id, label }`; headings collapse on mobile and in the bar variant. |
| `to` | React Router destination. Prefer this for full pages. |
| `disabled` / `hidden` | Availability state. |

The rail generates a numbered marker only when no icon is supplied. The horizontal `bar` variant does not generate numbers because peer views are not a sequence; it shows a marker only when the item explicitly supplies `Icon` or `icon`.

`idPrefix` is optional. The component uses React-generated, DOM-safe identifiers by default and sanitizes domain ids before connecting the current item to its content region. Pass a stable prefix only when tests or external controls need predictable ids.

Use `variant="rail"` (default) for focused page sections and `variant="bar"` for horizontal peer views. `FocusedSectionLayout` renders the navigation and one labelled content region; the parent remains responsible for rendering the selected section.

For forms with local drafts, mount a section the first time it is visited and keep that visited panel mounted with inactive panels hidden. This retains in-progress input without loading every heavy panel on first render. Read-only sections can render only the active panel.

## Interaction rules

- Use URL-backed sections on full pages. Back, Forward, refresh, copied links, and open-in-new-tab must preserve the section.
- Navigational links push browser history. Filter and sorting controls may replace the current history entry; do not use filter-state helpers for section navigation.
- Use task labels: “Budget & limits,” not implementation nouns such as “BillingPanel.”
- Show current state in `meta`; do not repeat paragraphs in the rail.
- The active item uses `aria-current="page"`; the content region is labelled by that item.
- A controlled button item also exposes `aria-pressed`.
- Do not use `role="tab"` unless implementing the full arrow-key tab pattern.
- A missing, hidden, or disabled `activeId` falls back to the first visible enabled item.
- URL-backed items render as router links inside the app and ordinary links when rendered without a Router provider.
- Keep destructive reset actions inside the relevant section, not in the navigation rail.
- One rail per page. Nested two- or three-option views use local tabs or a segmented control according to meaning.

## Visual rules

- Rail width: 224px by default.
- Active surface: `--purple-soft`; active content: `--purple-2`.
- Rail remains sticky below the global navigation; content scrolls naturally.
- Mobile items must remain horizontally scrollable without document-level overflow.
- When selection changes, an off-screen mobile item scrolls into the selector viewport.
- Labels remain visible; descriptions collapse first, then metadata at the narrowest breakpoint.
- Supporting copy uses the normal text contrast tokens rather than low-contrast decorative text.

## Migration policy

Page-level indexes should migrate to `FocusedSectionLayout`; equal page views use `FocusedSectionNav variant="bar"`. Compact content panels use `TabBar`, while filters and work modes use `SegmentedControl`. Global product navigation, radio choices in forms, multi-select chips, and one-off action toggles are intentionally outside this pattern. New bespoke tab or segmented-control CSS is not permitted once a shared primitive covers the use case.

### Platform adoption

| Pattern | Target surfaces |
|---|---|
| Focused rail | Workspace Settings; Job → Agent settings; Developer Portal |
| Peer bar | Job role views; Candidate standing report; Analytics; Decision policy; Requisition Job spec / Brief; product walkthroughs |
| Local `TabBar` | Candidate assessment evidence; candidate triage actions |
| `SegmentedControl` | Job stage and source filters; Home decision filter; Analytics time window; Tasks role filter; candidate search view; Chat Ask / Agents; threshold mode; connection modes |

The legacy `HomeMonitoring` component is not mounted by the current application and is excluded from the live-surface migration. If it is restored, its local tab bars must use the same primitives before release.
