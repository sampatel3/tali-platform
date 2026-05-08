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

Brand purple v6 — re-anchored on the agent-on hero gradient so every
purple in the product (buttons, kickers, .btn-purple, marketing CTA,
the agent-running hero, the agent-quiet hero) reads as the same hue.
Was `#7F39FB` (a more blue indigo); now `#B450FF` — the same bright
violet that's already the dominant glow in the agent-on hero
(`rgba(180, 80, 255)` sans alpha).

- Primary square/logo fill: `#B450FF`
- Accent purple lines: `#9D00FF`  *(unchanged — kept as the brighter, more saturated lines variant)*
- Deep purple lines: `#B450FF`  *(now matches primary; the variants stack in saturation order: deep `#B450FF` → accent `#9D00FF` → soft `#B06BFF`)*
- Soft purple lines: `#B06BFF`
- Inverse line color: `#FFFFFF`

### CSS tokens

The frontend reads brand purples from CSS custom properties on `:root`
in `frontend/src/index.css`. Touch `--purple` to retune everything:

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--purple` | `#B450FF` | `#C78AFF` | Buttons, kickers, accents, agent-on hero anchor |
| `--purple-2` | `#9D3EFF` | `#D4A8FF` | Hover state on `.btn-purple` |
| `--purple-soft` | `#F4E8FF` | `#2E1A4E` | Background washes on info chips, soft surfaces |
| `--purple-lav` | `#C4A5FD` | `#C4A5FD` | Lavender accent on the hero top-right glow |
| `--purple-glow` | `rgba(196, 165, 253, 0.45)` | (same) | Atmospheric blob in dark slabs |

The agent-on hero gradient (`.agent-header.agent-running`) and the
agent-quiet hero (`--grad-dark-vert`) are intentionally hard-coded
sibling recipes anchored on the same purple — they look different
(vivid vs. muted) but read as the same hue family.

## Implementation references

- Frontend brand config: [frontend/src/config/brand.js](/Users/sampatel/tali-platform/frontend/src/config/brand.js)
- Frontend logo components: [frontend/src/shared/ui/Branding.jsx](/Users/sampatel/tali-platform/frontend/src/shared/ui/Branding.jsx)
- Assessment glyph usage: [frontend/src/features/assessment_runtime/AssessmentBrandGlyph.jsx](/Users/sampatel/tali-platform/frontend/src/features/assessment_runtime/AssessmentBrandGlyph.jsx)
- Backend brand strings: [backend/app/platform/brand.py](/Users/sampatel/tali-platform/backend/app/platform/brand.py)

## Notes

- The app currently serves its runtime SVG files from `frontend/public/`.
- These files are copied here for easier human access and sharing.
- If you update a logo asset, mirror the same change in `frontend/public/` until we centralize runtime references to this folder.
