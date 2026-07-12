import React from 'react';

import { MarketingNav, TaaliLogo } from '../../../shared/layout/TaaliLayout';

// Shared page container width — mirrors LandingPageContent's containerClass so
// the preview variants line up exactly with the production landing gutters.
export const containerClass = 'mx-auto max-w-[85rem] px-6 md:px-10 xl:px-16';

// Top nav shared by both variants. We reuse the production <MarketingNav /> so
// the logo, "Book a demo" CTA, sign-in link, and mobile menu all match the live
// landing exactly. `onNavigate` routes to the same marketing pages (/demo-lead
// for "See it live").
export const LandingPreviewNav = ({ onNavigate }) => <MarketingNav onNavigate={onNavigate} />;

// Primary + secondary hero CTAs, shared by both variants. "See it live" is the
// primary and routes to /demo-lead (the demo-lead capture form); "Talk to us"
// is the secondary and also routes there (single capture surface, framed two
// ways — matches how the production landing funnels both buttons to intake).
export const HeroCtas = ({ onNavigate }) => (
  <div className="flex flex-wrap gap-3" style={{ marginBottom: 8 }}>
    <button
      type="button"
      className="btn btn-primary"
      style={{ height: 48, padding: '0 24px', fontSize: 14 }}
      onClick={() => onNavigate('demo-lead')}
    >
      See it live <span className="arrow">→</span>
    </button>
    <button
      type="button"
      className="btn btn-outline"
      style={{ height: 48, padding: '0 24px', fontSize: 14 }}
      onClick={() => onNavigate('demo-lead')}
    >
      Talk to us
    </button>
  </div>
);

// Closing CTA band — the softened purple gradient block shared by both
// variants. Same token-based gradient as the production landing's bottom CTA.
export const ClosingCtaBand = ({ onNavigate }) => (
  <section className="bg-[var(--bg)]">
    <div className={`${containerClass} py-16`}>
      <div
        className="relative overflow-hidden rounded-[18px] px-8 py-14 md:px-12"
        style={{
          background:
            'linear-gradient(135deg, color-mix(in oklab, var(--purple) 75%, #000) 0%, var(--purple) 60%, var(--purple-lav) 100%)',
          color: '#fff',
        }}
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{ background: 'radial-gradient(600px 280px at 80% 20%, rgba(255,255,255,0.18), transparent 60%)' }}
        />
        <div className="relative flex flex-wrap items-center justify-between gap-8">
          <div>
            <h2 className="font-[var(--font-display)] text-[clamp(28px,3.6vw,40px)] font-semibold leading-[1.05] tracking-[-0.025em]">
              See the agent work your pipeline.
            </h2>
            <p className="mt-3 max-w-[35rem] text-[1rem] leading-[1.55] opacity-85">
              Book a 20-minute demo with a founder and we&apos;ll run Taali on a role of yours —
              screening, assessment, and an evidence-linked decision on every candidate.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="taali-btn taali-btn-primary taali-btn-lg"
              onClick={() => onNavigate('demo-lead')}
            >
              See it live →
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
);

// Footer — a trimmed version of the production landing footer (logo, tagline,
// contact). Kept lean because these are internal mockups, not the live page.
export const LandingPreviewFooter = ({ onNavigate }) => (
  <footer className="border-t border-[var(--line)] bg-[var(--ink)] text-[var(--bg)]">
    <div className={`${containerClass} py-14`}>
      <div className="flex flex-wrap items-start justify-between gap-10">
        <div>
          <TaaliLogo onClick={() => onNavigate('landing')} wordmarkClassName="!text-[var(--bg)]" />
          <p className="mt-5 max-w-[20rem] text-[0.9375rem] leading-7 text-[var(--taali-inverse-text)] opacity-70">
            The recruiter&apos;s agent — screens, assesses, and decides across your pipeline, and the
            only platform that scores how candidates actually work with{' '}
            <span className="font-[var(--font-display)] text-[var(--purple)]">AI</span>.
          </p>
        </div>
        <button
          type="button"
          className="text-left text-[0.875rem] text-[var(--taali-inverse-text)] opacity-70 transition hover:opacity-100"
          onClick={() => {
            window.location.href = 'mailto:hello@taali.ai';
          }}
        >
          hello@taali.ai
        </button>
      </div>
      <div className="mt-12 font-[var(--font-display)] text-[clamp(72px,12vw,164px)] leading-none tracking-[-0.08em] text-[var(--taali-inverse-text)] opacity-[0.08]">
        taali<em className="text-[var(--purple)] not-italic">.</em>
      </div>
      <div
        className="mt-6 border-t pt-5 text-[0.8125rem] text-[var(--taali-inverse-text)]"
        style={{
          borderColor: 'color-mix(in oklab, var(--taali-inverse-text) 10%, transparent)',
          color: 'color-mix(in oklab, var(--taali-inverse-text) 52%, transparent)',
        }}
      >
        © 2026 Taali, Inc. · Internal design preview
      </div>
    </div>
  </footer>
);
