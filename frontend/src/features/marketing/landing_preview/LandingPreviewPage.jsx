import React from 'react';
import { useSearchParams } from 'react-router-dom';

import { LandingVariantA } from './LandingVariantA';
import { LandingVariantB } from './LandingVariantB';
import { LandingVariantC } from './LandingVariantC';
import { LandingVariantD } from './variant_d/LandingVariantD';
import { LandingVariantE } from './variant_e/LandingVariantE';
import { LandingVariantF } from './variant_f/LandingVariantF';

const DEFAULT_VARIANT = 'f';
const VARIANTS = {
  a: { label: 'A · Value-abstract', Component: LandingVariantA },
  b: { label: 'B · One live artifact', Component: LandingVariantB },
  c: { label: 'C · Turn hiring on', Component: LandingVariantC },
  d: { label: 'D · Watch it work', Component: LandingVariantD },
  e: { label: 'E · Watch it work', Component: LandingVariantE },
  f: { label: 'F · Vivid', Component: LandingVariantF },
};

// Small floating variant-switcher chip. Fixed bottom-centre, updates the ?v=
// query param in place (replace, so the back button still leaves the preview).
// Internal-only affordance so Sam can flip A/B while eyeballing in prod.
const VariantSwitcher = ({ active, onPick }) => (
  <div
    role="group"
    aria-label="Landing preview variant"
    style={{
      position: 'fixed',
      bottom: 18,
      left: '50%',
      transform: 'translateX(-50%)',
      zIndex: 60,
    }}
    className="flex items-center gap-1 rounded-full border border-[var(--line)] bg-[var(--bg-2)] p-1 shadow-[0_12px_32px_-12px_rgba(0,0,0,0.35)]"
  >
    <span className="px-2 font-[var(--font-mono)] text-[0.625rem] uppercase tracking-[0.12em] text-[var(--mute)]">
      Preview
    </span>
    {Object.entries(VARIANTS).map(([key, { label }]) => (
      <button
        key={key}
        type="button"
        aria-pressed={active === key}
        onClick={() => onPick(key)}
        className={`rounded-full px-3 py-1.5 text-[0.75rem] font-semibold transition ${
          active === key
            ? 'bg-[var(--purple)] text-white'
            : 'text-[var(--ink-2)] hover:bg-[var(--bg)]'
        }`}
      >
        {label}
      </button>
    ))}
  </div>
);

// Public, no-auth preview route at /landing-preview. `?v=d` (default) renders the
// pinned scroll-scrubbed "Watch it work" concept; `?v=c` the "Turn hiring on"
// agent-switch concept; `?v=a` the value-abstract variant; `?v=b` the narrative +
// one-live-artifact variant. All render logged-out. onNavigate is passed from
// AppShell so the shared nav/footer/CTAs route to the same marketing pages as the
// live landing.
export const LandingPreviewPage = ({ onNavigate }) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = (searchParams.get('v') || DEFAULT_VARIANT).toLowerCase();
  const active = VARIANTS[raw] ? raw : DEFAULT_VARIANT;
  const { Component } = VARIANTS[active];

  const pick = (key) => {
    const next = new URLSearchParams(searchParams);
    next.set('v', key);
    setSearchParams(next, { replace: true });
  };

  return (
    <>
      <Component onNavigate={onNavigate} />
      <VariantSwitcher active={active} onPick={pick} />
    </>
  );
};

export default LandingPreviewPage;
