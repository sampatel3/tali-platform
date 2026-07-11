import React from 'react';

import { containerClass, HeroCtas } from './LandingPreviewChrome';

// Shared hero for both value-led variants. No product UI chrome — value-abstract
// per the brief. Variant B may pass a slightly different H1 via `headline`.
export const LandingPreviewHero = ({ onNavigate, headline }) => (
  <section className="relative overflow-hidden pb-16 pt-14 md:pb-24 md:pt-20">
    <div className={containerClass}>
      <div
        className="mc-kicker"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 10, marginBottom: 18 }}
      >
        <span
          aria-hidden="true"
          style={{
            display: 'inline-flex',
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: 'var(--purple)',
            boxShadow: '0 0 0 4px var(--purple-soft)',
          }}
        />
        THE RECRUITER&apos;S AGENT
      </div>

      {headline || (
        <h1
          className="font-[var(--font-display)] font-semibold"
          style={{
            fontSize: 'clamp(44px,6.6vw,78px)',
            lineHeight: 1.0,
            letterSpacing: '-0.045em',
            margin: '0 0 24px',
            maxWidth: 1040,
          }}
        >
          Hiring has an AI-fluency problem.
          <br />
          <span className="text-[var(--purple)]">
            We measure it — and we work your pipeline while we&apos;re at it.
          </span>
        </h1>
      )}

      <p
        className="text-[1.125rem] leading-[1.55] text-[var(--ink-2)]"
        style={{ maxWidth: 680, margin: '0 0 30px' }}
      >
        Taali screens, assesses and decides across your funnel with a governed agent — and it&apos;s
        the only platform that scores how candidates actually work with AI.
      </p>

      <HeroCtas onNavigate={onNavigate} />
    </div>
  </section>
);
