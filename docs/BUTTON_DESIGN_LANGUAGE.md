# Taali button design language

Taali uses one action-button grammar across product, marketing, public, assessment, and agent surfaces. Feature context changes the wording and placement of an action, not its geometry.

The core distinction is simple:

- Rounded rectangles are actions.
- Pills are choices: tabs, filters, toggle chips, and segmented controls.

## Canonical component

Use `Button` from `frontend/src/shared/ui/TaaliPrimitives.jsx` for actions. Do not rebuild action buttons with feature CSS, inline style objects, or utility-class combinations.

```jsx
<Button variant="primary" size="md">Save changes</Button>
<Button variant="secondary" size="sm">Cancel</Button>
<Button variant="danger" loading loadingLabel="Deleting role">Delete role</Button>
<Button variant="ghost" size="xs" iconOnly aria-label="Settings">
  <Settings2 aria-hidden="true" />
</Button>
```

Native buttons default to `type="button"`. Set `type="submit"` deliberately. Links that navigate retain link semantics through the polymorphic `as` prop.

## Semantic variants

| Variant | Visual role | Use |
| --- | --- | --- |
| `primary` | Solid purple | The main action in a page, panel, or dialog action group |
| `secondary` | Neutral surface and border | Alternative, cancel, edit, or supporting actions |
| `ghost` | Transparent, low emphasis | Quiet actions that must not compete with the main action |
| `soft` | Lavender surface | Supportive review or contextual actions; use sparingly |
| `danger` | Solid danger red | Immediate or confirmed destructive actions only |
| `agent` | Static dark agent gradient | Explicit AI recommendations or agent activation only |
| `inverse` | Translucent light treatment | Actions on dark agent surfaces |

Use at most one `primary` in an action group. Do not use colour as a feature identifier. Ink/black CTAs, ad hoc warning buttons, and a generic `purple` variant are not part of the language.

## Sizes and shape

| Size | Height | Typical use |
| --- | ---: | --- |
| `xs` | 28px | Dense rows and compact utilities |
| `sm` | 32px | Cards, toolbars, and composers |
| `md` | 40px | Default forms, dialogs, and page actions |
| `lg` | 48px | Public, marketing, and high-prominence CTAs |

All action buttons use `var(--taali-button-radius)`, which resolves to the 10px control radius. Icon-only actions are square and use the selected size for both height and width. Full-width buttons use layout width without inventing another size or variant.

## Interaction states

- Hover, active, focus-visible, disabled, and reduced-motion behaviour are owned by the shared system.
- Focus-visible uses the shared purple ring and must never be removed by a feature stylesheet.
- Disabled buttons suppress hover and active treatments and use the shared disabled opacity.
- `loading` disables the action, sets `aria-busy`, and renders the shared spinner. Supply `loadingLabel` when the operation benefits from a specific accessible name.
- Agent buttons use a static gradient. Animation indicates real activity or progress, never decoration.
- Icon-only actions require an `aria-label`; decorative icons are hidden from assistive technology.
- Leading icons describe the action or object. Directional arrows trail the label. Normally use no more than one icon.

## Legacy family mapping

The compatibility layer keeps older selectors visually aligned during migration. New code must use `Button`.

| Audit family | Legacy selectors | Canonical destination |
| --- | --- | --- |
| A | `.taali-btn-*` | Source component: all seven variants and four sizes |
| B | `.btn-primary`, `.btn-purple`, `.btn-outline`, `.btn-ghost`, `.btn.danger` | `primary`, `primary`, `secondary`, `ghost`, `danger` |
| C | `.mc-auth-cta`, `.mc-auth-cta-outline` | Full-width `primary md`, `secondary md` |
| D | `.rq-btn`, `.rq-approve`, `.rq-teach`, `.rq-override`, `.rq-defer` | `secondary`, `agent`, `primary`, `secondary`, `ghost` at `sm` |
| E | `.ac-btn-primary`, `.ac-btn-soft`, `.ac-btn-ghost` | `primary`, `soft`, `ghost` at `sm` |
| F | `.cp-btn-*`, `.cp-send-btn`, `.tk-send-btn`, `.tk-stop-btn` | `primary`, `ghost`, `danger`, or `secondary` at `sm` |
| G | `.rq-new-btn`, `.rq-publish-btn`, `.rq-btn-sm` | `primary`, `secondary`, or `soft` at `sm/md` |
| H | `.src-btn`, `.src-btn-ghost` | `primary sm`, `secondary sm` |
| I | `.mc-show-btn` | `secondary sm`, `primary sm`, or `primary lg` |
| J | `.dr-rec-btn`, `.dr-btn` | `agent lg`, `secondary/soft sm` |
| K | `.pjp-*`, `.ci-*`, `.cl-*`, `.tasks-*` CTA selectors | `primary` or `secondary` at the appropriate shared size |
| L | `.ce-btn`, `.icon-btn`, text-action selectors | `secondary/ghost xs`, shared icon action, or shared text action |
| M | `.ab-btn`, `.mc-agent-btn` | Inverse actions at `sm`; solid inverse for the group primary |
| N | Assessment utility-only action combinations | Shared `primary`, `secondary`, `danger`, `ghost`, and `inverse` buttons |

## Deliberate exclusions

These elements may be implemented with `<button>` but are selection or navigation controls, not action buttons:

- Segmented choices such as `.seg`, `.evidence-seg`, and analytics windows.
- Toggle chips such as `.ac-chip-toggle`, `.ac-bulk-toggle`, and demo chips.
- Tabs, assessment dock switches, and assessment file-tree rows.
- Microphone, theme, and other persistent on/off controls.

Use `aria-pressed` for toggles, `aria-selected` for tabs/tree selections, and the relevant grouping semantics. Their selected state uses pill or row-selection geometry and must not masquerade as a page primary action.

## Guardrails

- New action code imports `Button`; it does not introduce another `*-btn` family.
- Feature styles may control placement and width, but not button colour, padding, radius, typography, focus, loading, or disabled behaviour.
- Navigation uses a link, even when styled as a CTA. Mutations use a button.
- Destructive actions use `danger` only when the action is actually destructive; warnings belong in the surrounding message or status surface.
- The live reference is `/dev/buttons`. It must show every variant, size, shared state, and legacy mapping in both themes.
