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

- Primary square/logo fill: `#7F39FB`
- Accent purple lines: `#9D00FF`
- Deep purple lines: `#7F39FB`
- Soft purple lines: `#B06BFF`
- Inverse line color: `#FFFFFF`

## Implementation references

- Frontend brand config: [frontend/src/config/brand.js](/Users/sampatel/tali-platform/frontend/src/config/brand.js)
- Frontend logo components: [frontend/src/shared/ui/Branding.jsx](/Users/sampatel/tali-platform/frontend/src/shared/ui/Branding.jsx)
- Assessment glyph usage: [frontend/src/features/assessment_runtime/AssessmentBrandGlyph.jsx](/Users/sampatel/tali-platform/frontend/src/features/assessment_runtime/AssessmentBrandGlyph.jsx)
- Backend brand strings: [backend/app/platform/brand.py](/Users/sampatel/tali-platform/backend/app/platform/brand.py)

## Notes

- The app currently serves its runtime SVG files from `frontend/public/`.
- These files are copied here for easier human access and sharing.
- If you update a logo asset, mirror the same change in `frontend/public/` until we centralize runtime references to this folder.
