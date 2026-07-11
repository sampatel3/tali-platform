// Skeleton — the one shared shimmer primitive for cold-load states.
//
// Why this exists: every page-level load in the app currently drops a
// centred <Spinner>, which conveys zero structure and makes the app feel
// slower than it is over the UAE→us-east4 round-trip. A skeleton that
// echoes the real layout reads as "content is arriving" instead of
// "something is spinning". This is deliberately minimal — a shimmer block
// plus two thin layout presets (table / report) that the two heaviest
// surfaces (role pipeline, candidate report) cold-load into. Other PRs
// adopt it later.
//
// Purple-tinted neutral, rem-based, no CSS zoom. The shimmer keyframes are
// injected once via a module-scoped <style> so the component stays
// self-contained (no stylesheet edit required to land it).
import React from 'react';

const STYLE_ID = 'taali-skeleton-shimmer';

// Inject the keyframes + base rule exactly once. Runs on first render of
// any Skeleton; a no-op on the server (no document) and on repeat mounts.
const ensureShimmerStyle = () => {
  if (typeof document === 'undefined') return;
  if (document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = `
@keyframes taali-skel-shimmer { 0% { background-position: -12rem 0; } 100% { background-position: 12rem 0; } }
.taali-skel {
  border-radius: 0.5rem;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--purple) 6%, var(--surface-soft, rgba(120,120,140,0.08))) 25%,
    color-mix(in srgb, var(--purple) 12%, var(--surface-soft, rgba(120,120,140,0.14))) 37%,
    color-mix(in srgb, var(--purple) 6%, var(--surface-soft, rgba(120,120,140,0.08))) 63%
  );
  background-size: 24rem 100%;
  animation: taali-skel-shimmer 1.4s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) { .taali-skel { animation: none; } }
`;
  document.head.appendChild(el);
};

// A single shimmer block. `width`/`height` accept any CSS length (rem
// preferred); `radius` overrides the default pill/round corner.
export const Skeleton = ({ width = '100%', height = '1rem', radius, className = '', style = {} }) => {
  ensureShimmerStyle();
  return (
    <div
      aria-hidden="true"
      className={`taali-skel ${className}`.trim()}
      style={{ width, height, ...(radius != null ? { borderRadius: radius } : {}), ...style }}
    />
  );
};

// Layout preset: a candidate-table cold load — header bar + N shimmer rows.
// Mirrors the .ctable rhythm (a wide name cell + a few short cells) so the
// swap-in to real rows doesn't jump.
export const SkeletonTable = ({ rows = 8, className = '' }) => (
  <div className={`ctable-wrap ${className}`.trim()} aria-busy="true" aria-label="Loading candidates">
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '0.75rem 0' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <Skeleton width="1.25rem" height="1.25rem" radius="0.25rem" />
          <div style={{ flex: 2, display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
            <Skeleton width="60%" height="0.9rem" />
            <Skeleton width="40%" height="0.7rem" />
          </div>
          <Skeleton width="3rem" height="1.5rem" radius="0.5rem" />
          <Skeleton width="5rem" height="0.9rem" />
          <Skeleton width="4rem" height="0.9rem" />
        </div>
      ))}
    </div>
  </div>
);

// Layout preset: the candidate-report dossier cold load — the left decision
// rail (identity + score ring + facts) beside the main pane's tab strip and
// a few content blocks. Uses the real .dossier grid so the shape lands in
// place.
export const SkeletonReport = ({ className = '' }) => (
  <div className={`dossier ${className}`.trim()} aria-busy="true" aria-label="Loading candidate report">
    <aside className="dossier-rail">
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem' }}>
        <Skeleton width="3rem" height="3rem" radius="50%" />
        <Skeleton width="70%" height="1rem" />
        <Skeleton width="6.5rem" height="6.5rem" radius="50%" />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem', marginTop: '1.25rem' }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} width="100%" height="1.1rem" />
        ))}
      </div>
    </aside>
    <main className="dossier-main">
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem' }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} width="5rem" height="1.5rem" radius="0.5rem" />
        ))}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <Skeleton width="100%" height="6rem" />
        <Skeleton width="90%" height="1rem" />
        <Skeleton width="80%" height="1rem" />
        <Skeleton width="100%" height="9rem" />
      </div>
    </main>
  </div>
);

export default Skeleton;
