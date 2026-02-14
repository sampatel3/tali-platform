# ADR 0001: Frontend Main-Chunk Warning Waiver

- Date: 2026-02-14
- Status: Implemented then retired on 2026-02-14
- Owner: @sampatel

## Context
At decision time, the frontend build emitted a chunk-size warning:

- `dist/assets/index-*.js` ≈ `635.6 kB` (gzip ≈ `182 kB`)

Route-level code splitting is now active for major feature pages, and domain API clients were split under `src/shared/api/*`. The remaining oversize chunk is dominated by the still-large monolith content in `src/App.jsx` (marketing/auth/dashboard shell components that have not yet been fully extracted).

## Decision
Temporarily waive the Vite chunk-size warning while continuing incremental decomposition in PR-sized steps.

## Guardrails
1. Keep route-level lazy loading enabled for extracted feature modules.
2. Keep `src/shared/api/*` as canonical API client layout.
3. Any new feature page must be shipped as a lazy-loaded module under `src/features/*`.
4. Re-evaluate chunk-size after each extraction PR.

## Resolution (2026-02-14)
Manual chunking and route-level lazy loading were implemented in `/Users/sampatel/tali-platform/frontend/vite.config.js`, and the warning no longer reproduces.

Current representative chunks:
- `index-*.js` ≈ `101 kB`
- `charts_vendor-*.js` ≈ `411 kB`
- `react_vendor-*.js` ≈ `151 kB`

This ADR remains as historical record and does not require further waiver action.
