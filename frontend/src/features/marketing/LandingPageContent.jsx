import React, { useEffect } from 'react';

import { AgentScene } from './landing_preview/variant_g/AgentScene';
import { FUNNEL, DDS, COMPOSITE } from './landing_preview/variant_g/variantG.data';
import {
  consumePendingMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';
import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';
import { AgentLoop } from '../../shared/motion';
import '../../shared/motion/reveal.css';
import './heroAgentScene.css';
import './landingVariantGSections.css';

// The production homepage — the ORIGINAL agentic-first landing restored (the
// hero with the animated agent-ON <AgentScene>, the closing CTA, the real
// footer) with variant G's two strongest product sections grafted in from the
// /landing-preview variant G experiment, replacing the earlier 3-step + live
// decision-feed and IDE-walkthrough bands:
//   1. Refined, inclusive copy + the "screens, assesses, and decides — with
//      you" headline; phrasing that covers engineering AND knowledge work.
//   2. The animated agent-ON <AgentScene> (job flips OFF→ON on first scroll,
//      candidates flow into the decision lane, verdicts stamp) as the hero's
//      product graphic (styles in heroAgentScene.css).
//   3. Variant G's 5-step FUNNEL ("One agent, your whole funnel." — Source /
//      Screen / Assess / Decide / Hand back) in the #how-it-works band.
//   4. Variant G's 5-Ds AI-fluency scorecard ("Measure how people actually work
//      with AI." — Delegation / Description / Discernment / Diligence /
//      Deliverable) in the #platform band — the single assessment section.
// The funnel + scorecard markup is a plain-JSX re-render of variant G's sections
// driven by the SAME data model (variantG.data.js), styled by
// landingVariantGSections.css (variant G's CSS re-scoped `.lvg` → `.mc-vg`), and
// revealed by the shared production reveal.css .reveal, while agent-state flow
// uses the shared Motion system. Chrome is the shared <MarketingNav> and a footer whose
// every link resolves. CTAs route through `onNavigate`.

const containerClass = 'mx-auto max-w-[85rem] px-6 md:px-10 xl:px-16';

// Glimpse chip(s) pinned to each funnel card's foot — rendered from the `viz`
// data model on each FUNNEL step (no dangerouslySetInnerHTML).
const FunnelViz = ({ viz }) => {
  if (viz.kind === 'evidence') {
    return (
      <div className="evid-row"><span className="tick">✓</span><span>{viz.text}</span></div>
    );
  }
  if (viz.kind === 'score') {
    return <div className="mini-score">{viz.value}<small>{viz.unit}</small></div>;
  }
  return (
    <div className="fchip-row">
      {viz.chips.map((c) => (
        <span key={c.label} className={`fchip${c.variant === 'plain' ? ' plain' : c.variant === 'ok' ? ' ok' : ''}`}>{c.label}</span>
      ))}
    </div>
  );
};

// Footer link columns. Every destination is real and resolves: `section`
// scrolls to an in-page anchor that EXISTS on this page (#how-it-works,
// #platform); `page` is a real route through onNavigate; `href` is a literal
// mailto or static guide page. No dead links.
const footerColumns = [
  {
    title: 'Product',
    items: [
      { label: 'How it works', section: 'how-it-works' },
      { label: 'AI-native assessment', section: 'platform' },
      { label: 'Product walkthrough', page: 'showcase' },
      { label: 'Developers / API', page: 'developers' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Blog', page: 'blog' },
      { label: 'Book a demo', page: 'demo-lead' },
      { label: 'Contact', href: 'mailto:hello@taali.ai' },
    ],
  },
  {
    title: 'Guides',
    items: [
      { label: 'What is agentic hiring?', href: '/agentic-hiring' },
      { label: 'AI-native hiring', href: '/ai-native-hiring' },
      { label: 'AI-native assessments', href: '/ai-native-assessments' },
    ],
  },
];

export const LandingPage = ({ onNavigate }) => {
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sectionId = consumePendingMarketingSection() || window.location.hash.replace(/^#/, '');
    if (!sectionId) return undefined;

    const timer = window.setTimeout(() => {
      scrollToMarketingSection(sectionId, { behavior: 'smooth' });
    }, 40);

    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <MarketingNav onNavigate={onNavigate} />

      {/* HERO — two columns: refined copy left, the animated agent-ON scene
          (variant G's <AgentScene>) right. */}
      <section className="relative overflow-hidden pb-16 pt-12 md:pb-24 md:pt-16">
        <div className={containerClass}>
          <div className="grid items-center gap-12 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16">
            <div className="reveal">
              <div className="mc-kicker" style={{ display: 'inline-flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
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
                AGENT-NATIVE HIRING
              </div>
              <h1
                className="font-[var(--font-display)] font-semibold"
                style={{
                  fontSize: 'clamp(40px,5.6vw,64px)',
                  lineHeight: 1.04,
                  letterSpacing: '-0.04em',
                  margin: '0 0 22px',
                  maxWidth: 720,
                }}
              >
                The hiring agent that screens, assesses, and{' '}
                <em className="not-italic text-[var(--purple)]">decides — with you.</em>
              </h1>
              <p className="text-[1.125rem] leading-[1.55] text-[var(--ink-2)]" style={{ maxWidth: 560, margin: '0 0 22px' }}>
                Taali is the agentic hiring platform — one governed agent runs screening,
                AI-fluency assessment, and defensible decisions end to end, across
                <em className="not-italic font-medium text-[var(--ink)]"> engineering and knowledge work</em>.
                It paces itself within your budget and asks you on every call that matters.
                Every consequential decision still goes through you.
              </p>
              <div className="flex flex-wrap gap-3 text-[0.8125rem] text-[var(--ink-2)]" style={{ marginBottom: 30 }}>
                {[
                  { k: 'AGENTIC', v: 'Runs your pipeline 24/7 — pauses for your judgment' },
                  { k: 'AI-NATIVE', v: 'The only platform that scores how people work with AI' },
                ].map((badge) => (
                  <div
                    key={badge.k}
                    className="inline-flex items-center gap-2.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3.5 py-2"
                  >
                    <span className="font-[var(--font-mono)] text-[0.65625rem] font-semibold tracking-[0.08em] text-[var(--purple)]">
                      {badge.k}
                    </span>
                    <span>{badge.v}</span>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ height: 46, padding: '0 22px', fontSize: 14 }}
                  onClick={() => onNavigate('demo-lead')}
                >
                  Book a demo
                </button>
                <button
                  type="button"
                  className="btn btn-outline"
                  style={{ height: 46, padding: '0 22px', fontSize: 14 }}
                  onClick={() => onNavigate('showcase')}
                >
                  See it live <span className="arrow">→</span>
                </button>
              </div>
            </div>

            {/* The agent-ON scene: job card flips OFF→ON on first scroll, three
                candidates flow into the decision lane, each verdict stamps
                (Maya 88 Advance / Jordan 84 Advance / Tariq 41 Reject). Scoped
                under .lvg-scene so heroAgentScene.css styles it. */}
            <div className="reveal lvg-scene" style={{ '--reveal-delay': '0.12s' }}>
              <AgentScene />
            </div>
          </div>
        </div>
      </section>

      {/* HOW THE AGENT WORKS — variant G's 5-step funnel ("One agent, your
          whole funnel."), re-rendered as plain JSX from the FUNNEL data model
          and styled by landingVariantGSections.css (scoped `.mc-vg`). */}
      <section id="how-it-works" className="mc-vg border-t border-[var(--line)] bg-[var(--bg-2)]">
        <div className={`${containerClass} py-20`}>
          <div className="reveal section-head">
            <span className="eyebrow">AGENTIC HIRING</span>
            <h2 className="display mt-3">One agent, <span className="grad-text">your whole funnel.</span></h2>
            <p className="lede">
              It sources, reads every CV, runs the assessment, and puts a decision in front of you with
              the evidence attached. You approve. It executes.
            </p>
          </div>

          <div className="reveal-stagger funnel mt-4">
            {FUNNEL.map((s, i) => (
              <div key={s.n} className="fstep" style={{ '--i': i }}>
                {i < FUNNEL.length - 1 ? <span className="fflow-track" aria-hidden="true" /> : null}
                <span className="fnum">{s.n}</span>
                <h3>{s.key}</h3>
                <p>{s.body}</p>
                <div className="fviz"><FunnelViz viz={s.viz} /></div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* AI-NATIVE ASSESSMENT — variant G's single 5-Ds scorecard ("Measure how
          people actually work with AI." — Delegation / Description /
          Discernment / Diligence / Deliverable), the one assessment section. */}
      <section id="platform" className="mc-vg border-t border-[var(--line)] bg-white">
        <div className={`${containerClass} py-20`}>
          <div className="reveal section-head">
            <span className="eyebrow">AI-NATIVE ASSESSMENTS</span>
            <h2 className="display mt-3">Measure how people <span className="grad-text">actually work with AI.</span></h2>
            <p className="lede">
              Five dimensions, scored from the real session. Planted traps they should catch. Same
              rubric, every candidate — engineering or knowledge work.
            </p>
          </div>

          <div className="reveal scorecard">
            <div className="sc-head">
              <div className="who">
                <div className="avatar">MC</div>
                <div>
                  <div className="sc-title">Maya Chen · AI-fluency</div>
                  <div className="sc-sub">SCORED FROM SESSION · AI ENGINEER #312</div>
                </div>
              </div>
              <div className="sc-total">
                <div className="big">{COMPOSITE}</div>
                <div className="lbl">Composite / 100</div>
              </div>
            </div>
            {DDS.map((d) => (
              <div className="dd-row" key={d.name}>
                <div>
                  <div className="dd-name">{d.name}</div>
                  <div className="dd-def">{d.def}</div>
                </div>
                <div className="dd-track"><AgentLoop kind="flow" className="dd-fill" style={{ width: `${d.val}%` }} /></div>
                <div className="dd-val">{d.val}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* BOTTOM CTA — softened, token-based purple gradient */}
      <section className="bg-[var(--bg)]">
        <div className={`${containerClass} py-16`}>
          <div
            className="reveal relative overflow-hidden rounded-[18px] px-12 py-14"
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
                  className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[0.875rem] font-semibold text-[var(--purple)]"
                  style={{ boxShadow: '0 10px 28px -8px rgba(0,0,0,0.3)' }}
                  onClick={() => onNavigate('showcase')}
                >
                  See it live →
                </button>
                <button
                  type="button"
                  className="inline-flex h-12 items-center gap-2 rounded-full px-7 text-[0.875rem] font-semibold text-white"
                  style={{ border: '1px solid rgba(255,255,255,0.55)', background: 'transparent' }}
                  onClick={() => onNavigate('demo-lead')}
                >
                  Book a demo
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-[var(--line)] bg-[var(--ink)] text-[var(--bg)]">
        <div className={`${containerClass} py-14`}>
          <div className="grid gap-10 lg:grid-cols-[1.1fr_.9fr_.9fr_.9fr]">
            <div>
              <TaaliLogo onClick={() => onNavigate('landing')} wordmarkClassName="!text-[var(--bg)]" />
              <p className="mt-5 max-w-[17.5rem] text-[0.9375rem] leading-7 text-[var(--taali-inverse-text)] opacity-70">
                The agentic hiring platform. One governed agent runs your funnel — you decide every call that matters.
              </p>
            </div>

            {footerColumns.map((column) => (
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
                          scrollToMarketingSection(item.section);
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
            <div>© 2026 Taali</div>
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
    </div>
  );
};

export default LandingPage;
