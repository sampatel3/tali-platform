# TAALI Brand Assets

This folder collects the core TAALI visual assets in one place so they are easy to find, review, and share.

## Included files

- `taali.svg`
  Primary square TAALI mark. Best default logo file for product/app use.
- `taali-logo-variants.svg`
  Overview sheet showing the main logo variants and usage intent.
- `taali-mark-circle.svg`
  Circular mark for avatars, profile chips, and round treatments.
- `taali-mark-lines-purple.svg`
  Standalone tally-lines mark in accent purple.
- `taali-mark-lines-purple-deep.svg`
  Standalone tally-lines mark in deep brand purple.
- `taali-mark-lines-purple-soft.svg`
  Standalone tally-lines mark in soft brand purple.

## Brand basics

- Brand name: `TAALI`
- Domain: `taali.ai`
- Product tagline: `Technical assessments for AI-native engineering teams.`
- App title: `AI Technical Assessments That Tally Real Skill`

## Color reference

Brand purple v8 — lifted out of the agent-quiet anchor.

v7 sampled the literal `--grad-dark-vert` start (`#2A1854`), but at
21% lightness that crossed into "looks black," not purple. v8 keeps
the same hue axis as the gradient stops (H≈263°, the same axis as
`#2A1854` / `#3A1D6E` / `#B450FF`) and lifts lightness to ≈44% — the
brand still reads as deep and calm, but unmistakably purple. It sits
perceptually midway between the agent-off anchor (calm, deep) and
the agent-on glow (vivid, live). The bright violet stays reserved
for the agent-running hero — its brightness is the signal that the
agent is working *right now*, not the brand identity.

- Primary square/logo fill: `#5E3AA8`
- Accent purple lines: `#9D00FF`  *(unchanged — bright accent variant for high-energy contexts)*
- Deep purple lines: `#5E3AA8`  *(matches primary; variants stack: deep `#5E3AA8` → accent `#9D00FF` → soft `#B06BFF`)*
- Soft purple lines: `#B06BFF`
- Inverse line color: `#FFFFFF`

### CSS tokens

The frontend reads brand purples from CSS custom properties on `:root`
in `frontend/src/index.css`. Touch `--purple` to retune the brand —
buttons, kickers, accents, the marketing CTA pill text, focus rings
all cascade from this one token.

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--purple` | `#5E3AA8` | `#8867C4` | Brand purple. Buttons, kickers, accents, focus rings. Dark mode lifted into the same hue family for AA contrast on `#0E0A18`. |
| `--purple-2` | `#4A2D80` | `#6E4BA8` | Hover state on `.btn-purple`, `.btn-primary:hover`. |
| `--purple-soft` | `#EDE5F8` | `#2E1A4E` | Background washes on info chips, soft surfaces. |
| `--purple-lav` | `#C4A5FD` | `#C4A5FD` | Lavender accent — the hero top-right glow, terminal cursor, anywhere a *visible* purple is needed on a dark surface. |
| `--purple-glow` | `rgba(196, 165, 253, 0.45)` | (same) | Atmospheric blob in dark slabs. |

### Agent-on vs. agent-off vs. brand

The two hero variants are intentionally *not* anchored on `--purple`
— each has its own job:

- **`.agent-header.agent-running`** (vivid) — hard-coded
  `linear-gradient(180deg, #3A1D6E → #251248)` plus `rgba(180,80,255)`
  and `rgba(196,165,253)` radial glows. The brightness is the
  signal: *the agent is live, working right now.* Don't reuse this
  recipe for ambient brand surfaces.
- **`.agent-header.agent-quiet`** (calm) — uses `--grad-dark-vert`
  (`#2A1854 → #1D1130`). Default state for every page hero. Calmer
  cousin of the brand `--purple`; same hue axis (H≈263°), darker
  lightness so the hero reads as a still surface, not a button.
- **Brand `--purple` (`#5E3AA8`)** — the readable middle. Same hue
  axis as both heroes; lightness sits between them. Use for every
  surface that should *be* purple: buttons, kickers, accents, the
  marketing CTA pill text, focus rings, the soft Pending count.

### Visibility exceptions

A handful of consumers render to canvas / xterm, where they can't
read CSS custom properties or sit on a fixed dark surface that the
brand `#5E3AA8` would still under-contrast. These keep a hard-coded
bright purple by design — scoped exceptions, not the brand:

- `frontend/src/features/chat/GraphView.jsx` — Cytoscape `Person`
  node colour (`#B450FF`). Canvas, no CSS-var resolution.
- `frontend/src/features/assessment_runtime/AssessmentTerminal.jsx`
  — xterm cursor and selection. Always renders against the dark
  terminal bg.
- `frontend/src/features/chat/chat.css` — CSS-var fallback literals
  (`var(--purple, #b450ff)`) — only kick in if `--purple` is
  undefined, which never happens in practice. Cosmetic.

When changing the brand purple, leave these alone — they're not
brand surfaces.

## Implementation references

- Frontend brand config: [frontend/src/config/brand.js](/Users/sampatel/tali-platform/frontend/src/config/brand.js)
- Frontend logo components: [frontend/src/shared/ui/Branding.jsx](/Users/sampatel/tali-platform/frontend/src/shared/ui/Branding.jsx)
- Assessment glyph usage: [frontend/src/features/assessment_runtime/AssessmentBrandGlyph.jsx](/Users/sampatel/tali-platform/frontend/src/features/assessment_runtime/AssessmentBrandGlyph.jsx)
- Backend brand strings: [backend/app/platform/brand.py](/Users/sampatel/tali-platform/backend/app/platform/brand.py)

## Notes

- The app currently serves its runtime SVG files from `frontend/public/`.
- These files are copied here for easier human access and sharing.
- If you update a logo asset, mirror the same change in `frontend/public/` until we centralize runtime references to this folder.
