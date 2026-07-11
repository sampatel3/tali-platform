import React from 'react';

import { TaaliLogo } from '../../../../shared/layout/TaaliLayout';
import { scrollToMarketingSection } from '../../../../lib/marketingScroll';

// ---------------------------------------------------------------------------
// Closing CTA + production dark footer. Copied from the production landing
// treatment (via variant C) so variant E ends on the exact same note the live
// site does — the token-based purple gradient CTA and the dark full footer
// (logo, three link columns, giant faded wordmark, contact row). These use the
// GLOBAL brand tokens (var(--purple)/--ink/--line/--font-display) on purpose so
// the footer matches production, not the scoped `.lve` palette.
// ---------------------------------------------------------------------------

const containerClass = 'mx-auto max-w-[85rem] px-6 md:px-10 xl:px-16';

const FOOTER_COLUMNS = [
  {
    title: 'Product',
    items: [
      { label: 'Book a demo', page: 'demo-lead' },
      { label: 'AI collab score', section: 'lve-pillars' },
      { label: 'Question bank', section: 'lve-bands' },
      { label: 'Integrations', section: 'lve-integrations' },
      { label: 'Developers / API', page: 'developers' },
      { label: 'Product walkthrough', page: 'showcase' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Manifesto', section: 'lve-control' },
      { label: 'Careers', href: 'mailto:hello@taali.ai?subject=Careers%20at%20Taali' },
      { label: 'Blog', page: 'blog' },
      { label: 'Contact', href: 'mailto:hello@taali.ai' },
    ],
  },
  {
    title: 'Guides',
    items: [
      { label: 'What is agentic hiring?', href: '/agentic-hiring' },
      { label: 'AI-native hiring', href: '/ai-native-hiring' },
      { label: 'AI-native assessments', href: '/ai-native-assessments' },
      { label: 'Product walkthrough', page: 'showcase' },
    ],
  },
];

export const ClosingCta = ({ onNavigate }) => (
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
              Ready to put the agent to work?
            </h2>
            <p className="mt-3 max-w-[35rem] text-[1rem] leading-[1.55] opacity-85">
              Take the full product walkthrough — pre-loaded with a real role, no card, no install.
              Or tell us what you&apos;re hiring for and we&apos;ll follow up by email.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="inline-flex h-12 items-center gap-2 rounded-full px-7 text-[0.875rem] font-semibold text-white"
              style={{ border: '1px solid rgba(255,255,255,0.55)', background: 'transparent' }}
              onClick={() => onNavigate('showcase')}
            >
              Open walkthrough →
            </button>
            <button
              type="button"
              className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[0.875rem] font-semibold text-[var(--purple)]"
              style={{ boxShadow: '0 10px 28px -8px rgba(0,0,0,0.3)' }}
              onClick={() => onNavigate('demo-lead')}
            >
              Get in touch →
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
);

export const ProductionFooter = ({ onNavigate }) => (
  <footer className="border-t border-[var(--line)] bg-[var(--ink)] text-[var(--bg)]">
    <div className={`${containerClass} py-14`}>
      <div className="grid gap-10 lg:grid-cols-[1.1fr_.9fr_.9fr_.9fr]">
        <div>
          <TaaliLogo onClick={() => onNavigate('landing')} wordmarkClassName="!text-[var(--bg)]" />
          <p className="mt-5 max-w-[17.5rem] text-[0.9375rem] leading-7 text-[var(--taali-inverse-text)] opacity-70">
            AI-native technical assessments that{' '}
            <span className="font-[var(--font-display)] text-[var(--purple)]">tally</span> real skill.
          </p>
        </div>

        {FOOTER_COLUMNS.map((column) => (
          <div key={column.title}>
            <h4 className="font-[var(--font-display)] text-[1.25rem] tracking-[-0.02em]">{column.title}</h4>
            <div className="mt-4 flex flex-col gap-3">
              {column.items.map((item) => (
                <button
                  key={item.label}
                  type="button"
                  className="w-fit text-left text-[0.875rem] text-[var(--taali-inverse-text)] opacity-70 transition hover:opacity-100"
                  onClick={() => {
                    if (item.href) {
                      window.location.href = item.href;
                      return;
                    }
                    if (item.section) {
                      const el = document.getElementById(item.section);
                      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                      else scrollToMarketingSection(item.section);
                      return;
                    }
                    if (item.page) {
                      onNavigate(item.page);
                    }
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-12 font-[var(--font-display)] text-[clamp(72px,12vw,164px)] leading-none tracking-[-0.08em] text-[var(--taali-inverse-text)] opacity-[0.08]">
        taali<em className="text-[var(--purple)] not-italic">.</em>
      </div>

      <div
        className="mt-6 flex flex-col gap-3 border-t pt-5 text-[0.8125rem] text-[var(--taali-inverse-text)] md:flex-row md:items-center md:justify-between"
        style={{
          borderColor: 'color-mix(in oklab, var(--taali-inverse-text) 10%, transparent)',
          color: 'color-mix(in oklab, var(--taali-inverse-text) 52%, transparent)',
        }}
      >
        <div>© 2026 Taali, Inc. · San Francisco</div>
        <button
          type="button"
          className="w-fit text-left text-[var(--taali-inverse-text)] opacity-70 transition hover:opacity-100"
          onClick={() => {
            window.location.href = 'mailto:hello@taali.ai';
          }}
        >
          hello@taali.ai
        </button>
      </div>
    </div>
  </footer>
);
