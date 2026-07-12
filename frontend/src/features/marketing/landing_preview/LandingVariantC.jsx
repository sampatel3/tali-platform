import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { TaaliLogo } from '../../../shared/layout/TaaliLayout';
import { scrollToMarketingSection } from '../../../lib/marketingScroll';
import { AgentLoop, prefersReducedMotion } from '../../../shared/motion';
import { VARIANT_C_CSS } from './landingVariantC.styles';

// ---------------------------------------------------------------------------
// VARIANT C — "Turn the agent on". A LIGHT concept where the page itself is an
// agent-ON switch. It loads OFF (desaturated grey-on-white, inert) and, after
// ~1.4s (or on click / keyboard), the toggle flips ON: purple saturates in,
// motion begins.
//
// All colour lives on a scoped `.lvc` root as CSS custom properties, using the
// Taali light purple palette directly (hardcoded, not the brand token) so the
// look holds regardless of the app's active brand/theme. The OFF→ON flood is a
// single `filter` (grayscale) + custom-property transition on `.lvc`, driven by
// one `data-on` attribute — every child animation keys off it.
//
// Constraints honoured: no new deps (shared Motion + CSS transitions — no
// <canvas>, no rAF), lazy-loaded route, prefers-reduced-motion renders straight
// to ON with static composition, robust at 80% zoom / 1024–1600 widths,
// purple-family accents, fixture data only. Nothing depends on
// IntersectionObserver for correctness — reveals have fallbacks and stay visible
// even if they never fire.
// ---------------------------------------------------------------------------

// ── Dot lattice (hero background) ──────────────────────────────────────────
// A loose grid of ~120 small dots. OFF: static, grey, low opacity. On flip a
// radial pulse ripples outward from the toggle (bottom-centre): each dot's
// colour/scale transition is delayed by its distance to the toggle, so the
// ripple visibly propagates. After it settles the lattice holds as a calm
// background. Positions and per-dot delays
// are computed once at render (deterministic seed) — no rAF, CSS only.
const LATTICE_COLS = 14;
const LATTICE_ROWS = 9; // 14 × 9 = 126 dots ≈ "~120"
// The toggle lives at bottom-centre of the hero, so the ripple origin is the
// bottom-middle of the lattice in normalised (0–1) space.
const RIPPLE_ORIGIN = { x: 0.5, y: 1 };

const buildLattice = () => {
  const dots = [];
  let i = 0;
  for (let r = 0; r < LATTICE_ROWS; r += 1) {
    for (let c = 0; c < LATTICE_COLS; c += 1) {
      // Deterministic pseudo-random offsets so the field is stable per render.
      const s1 = ((i * 9301 + 49297) % 233280) / 233280;
      const s2 = ((i * 4099 + 7919) % 233280) / 233280;
      const s3 = ((i * 6151 + 1033) % 233280) / 233280;
      // Base grid position (0–1), nudged by a small random offset.
      const gx = (c + 0.5) / LATTICE_COLS;
      const gy = (r + 0.5) / LATTICE_ROWS;
      const x = Math.min(0.99, Math.max(0.01, gx + (s1 - 0.5) * 0.05));
      const y = Math.min(0.99, Math.max(0.01, gy + (s2 - 0.5) * 0.06));
      // Distance to the ripple origin → drives the staggered transition-delay.
      const dx = x - RIPPLE_ORIGIN.x;
      const dy = y - RIPPLE_ORIGIN.y;
      const dist = Math.sqrt(dx * dx + dy * dy); // 0 … ~1.1
      dots.push({
        id: i,
        x: +(x * 100).toFixed(2),
        y: +(y * 100).toFixed(2),
        size: +(2.4 + s3 * 1.4).toFixed(2), // 3px ± slight variance
        delay: +(dist * 0.55).toFixed(3), // seconds — ripple sweep
      });
      i += 1;
    }
  }
  return dots;
};

const DotLattice = () => {
  const dots = useMemo(buildLattice, []);
  return (
    <div className="lvc-lattice" aria-hidden="true">
      {dots.map((d) => (
        <span
          key={d.id}
          className="lvc-dot"
          style={{
            left: `${d.x}%`,
            top: `${d.y}%`,
            width: `${d.size}px`,
            height: `${d.size}px`,
            '--d': `${d.delay}s`,
          }}
        />
      ))}
    </div>
  );
};

// ── The switch — reuses the product's dark-purple agent-ON vocabulary ──────
const AgentSwitch = ({ on, pressing, onToggle }) => (
  <div className="lvc-switch-wrap">
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={on ? 'Agent on. Turn hiring off.' : 'Agent off. Turn hiring on.'}
      className={`lvc-switch${on ? ' is-on' : ''}${pressing ? ' is-pressing' : ''}`}
      onClick={onToggle}
    >
      <AgentLoop kind="flow" active={on} className="lvc-switch-track" aria-hidden="true">
        <span className="lvc-switch-glow" />
        <span className="lvc-switch-knob">
          <AgentLoop kind="ring" active={on} className="lvc-switch-ring" />
        </span>
      </AgentLoop>
    </button>
    <span className="lvc-switch-caption" aria-hidden="true">
      agent: <b>{on ? 'on' : 'off'}</b>
    </span>
  </div>
);

// ── Section header — shared design language (centred eyebrow + H2 + sub) ────
// Mirrors the hero: a mono eyebrow (dot + uppercase label), an H2 whose last
// word is purple-accented, and a one-sentence sub in --lvc-ink-2.
const SectionHeader = ({ eyebrow, headParts, sub, revealRef }) => (
  <header className="lvc-sechead" ref={revealRef} data-reveal>
    <div className="lvc-eyebrow lvc-eyebrow--center">
      <span className="lvc-eyebrow-dot" /> {eyebrow}
    </div>
    <h2 className="lvc-h2">
      {headParts[0]}
      {headParts[1] ? <em className="lvc-h2-accent"> {headParts[1]}</em> : null}
    </h2>
    <p className="lvc-sechead-sub">{sub}</p>
  </header>
);

// ── Section 1 · HERO ───────────────────────────────────────────────────────
const HeroSection = ({ on, pressing, onToggle, onNavigate, onHowItWorks }) => (
  <section className="lvc-hero">
    <DotLattice />
    <div className="lvc-hero-inner">
      <div className="lvc-kicker">
        <span className="lvc-kicker-dot" /> AGENT-NATIVE HIRING
      </div>

      <h1 className="lvc-h1" aria-live="polite">
        <span className="lvc-h1-off" aria-hidden={on}>
          Hiring runs on guesswork.
        </span>
        <span className="lvc-h1-on" aria-hidden={!on}>
          {['Turn', 'the', 'agent', 'on.'].map((w, i) => (
            <React.Fragment key={w}>
              <span className="lvc-word" style={{ transitionDelay: `${0.12 + i * 0.09}s` }}>
                {w}
              </span>
              {i < 3 ? ' ' : ''}
            </React.Fragment>
          ))}
        </span>
      </h1>

      <p className="lvc-sub">
        Taali works your pipeline end to end and measures the one thing every CV now hides: how well
        this person actually works with AI.
      </p>

      <div className="lvc-cta-row">
        <button type="button" className="lvc-btn lvc-btn--primary" onClick={() => onNavigate('demo-lead')}>
          See it live <span aria-hidden="true">→</span>
        </button>
        <button type="button" className="lvc-btn lvc-btn--ghost" onClick={onHowItWorks}>
          How it works
        </button>
      </div>
    </div>

    <AgentSwitch on={on} pressing={pressing} onToggle={onToggle} />
  </section>
);

// ── Section 2 · THE PROBLEM (kinetic typography) ───────────────────────────
const ProblemSection = ({ reveal }) => (
  <section className="lvc-problem">
    <p
      ref={reveal('problem-0')}
      data-reveal
      className="lvc-problem-line"
      style={{ transitionDelay: '0s' }}
    >
      Everyone works with AI now.
    </p>
    <p
      ref={reveal('problem-1')}
      data-reveal
      className="lvc-problem-line has-strike"
      style={{ transitionDelay: '0.05s' }}
    >
      <span className="lvc-strike">The&nbsp;CV</span> can’t prove it.{' '}
      <span className="lvc-strike">The&nbsp;interview</span> can’t catch it.
    </p>
    <p
      ref={reveal('problem-2')}
      data-reveal
      className="lvc-problem-line"
      style={{ transitionDelay: '0.1s' }}
    >
      You need to watch them work.
    </p>
  </section>
);

// ── Section 3 · THE PIPELINE (abstract CSS ribbon + stage cards) ───────────
// The ribbon is pure CSS: a horizontal rail with five glowing nodes and small
// dots flowing along it via keyframes. It animates unconditionally when ON;
// off-screen pausing is a nice-to-have driven by animation-play-state only.
const RIBBON_NODES = ['Source', 'Screen', 'Assess', 'Decide', 'Hand back'];
const PipelineRibbon = () => (
  <div className="lvc-ribbon" aria-hidden="true">
    <AgentLoop kind="flow" className="lvc-ribbon-rail" />
    <div className="lvc-ribbon-nodes">
      {RIBBON_NODES.map((n, i) => (
        <span key={n} className="lvc-ribbon-node" style={{ '--n': i }}>
          <AgentLoop kind="pulse" delay={i * 0.4} className="lvc-ribbon-node-core" />
        </span>
      ))}
    </div>
  </div>
);

const PIPELINE_STAGES = [
  {
    n: '01',
    t: 'Source',
    d: 'Plugs into your ATS. Candidates, roles and JDs sync in; nothing to set up.',
    meta: 'workable · bullhorn · api',
  },
  {
    n: '02',
    t: 'Screen',
    d: "Reads every CV against the role's real requirements. Weak fits are gated with evidence, not vibes.",
    meta: 'requirement-by-requirement evidence',
  },
  {
    n: '03',
    t: 'Assess',
    d: 'A task authored from your JD, battle-tested in a sandbox, sent automatically. Candidates pair with Claude on real work — engineering or not.',
    meta: '30 minutes · real repo · full transcript',
  },
  {
    n: '04',
    t: 'Decide',
    d: 'A deterministic verdict on every candidate, with the evidence attached. You approve, override, or teach it back.',
    meta: 'deterministic verdict · audit trail',
  },
  {
    n: '05',
    t: 'Hand back',
    d: 'Decisions, notes and reports written back to your ATS. The audit trail comes free.',
    meta: 'notes, reports & stage moves synced',
  },
];

const PIPELINE_STATS = [
  { big: 'Every task', cap: 'battle-tested before use' },
  { big: 'Every decision', cap: 'carries its evidence' },
  { big: 'Every session', cap: 'captured turn by turn' },
  { big: 'Zero', cap: 'webcams or lockdown browsers' },
];

const PipelineSection = ({ reveal, pipelineRef }) => (
  <section className="lvc-pipeline" ref={pipelineRef}>
    <SectionHeader
      revealRef={reveal('pipe-copy')}
      eyebrow="THE PIPELINE"
      headParts={['An agent that runs the', 'funnel.']}
      sub="It finds candidates, reads every CV, runs the assessment, and puts a decision in front of you with the evidence attached. You approve. It executes."
    />

    <PipelineRibbon />

    <div className="lvc-stage-grid" ref={reveal('pipe-stages')} data-reveal>
      {PIPELINE_STAGES.map((s, i) => (
        <div className="lvc-stage" key={s.t} style={{ '--i': i }}>
          <span className="lvc-stage-n">{s.n}</span>
          <h3 className="lvc-stage-t">{s.t}</h3>
          <p className="lvc-stage-d">{s.d}</p>
          <span className="lvc-stage-meta">{s.meta}</span>
        </div>
      ))}
    </div>

    <div className="lvc-stats" ref={reveal('pipe-stats')} data-reveal>
      {PIPELINE_STATS.map((s, i) => (
        <div className="lvc-stat" key={s.big} style={{ '--i': i }}>
          <span className="lvc-stat-big">{s.big}</span>
          <span className="lvc-stat-cap">{s.cap}</span>
        </div>
      ))}
    </div>
  </section>
);

// ── Section 4 · THE STANDARD (five Ds + statically composed trap vignette) ──
const CHAT_TURNS = [
  { who: 'AI', text: 'Quickest fix: lower the confidence gate to 0.4 and the tests pass.' },
  {
    who: 'Candidate',
    text: 'No. That gate is the safety control. Show me why the test fails at 0.62 instead.',
  },
];

const FIVE_DS = [
  {
    d: 'Delegation',
    def: 'Deciding what to own and what to hand to the agent.',
    chip: 'decision points, interrogated',
    evidence:
      'Planted decision points the agent refuses to make for them — we score how they take them.',
  },
  {
    d: 'Description',
    def: 'Directing it — clear prompts, the right context.',
    chip: 'prompt quality, scored',
    evidence: 'Prompt quality and context discipline, graded from the actual transcript.',
  },
  {
    d: 'Discernment',
    def: 'Catching what the AI gets wrong.',
    chip: 'planted traps, scored',
    evidence: 'We plant a plausible-but-wrong suggestion. Catching it is worth real points.',
  },
  {
    d: 'Diligence',
    def: 'Verifying before calling it done.',
    chip: 'verification events, counted',
    evidence: 'Test runs, re-checks and edits-after-verification, counted from telemetry.',
  },
  {
    d: 'Deliverable',
    def: 'What actually shipped, on its merits.',
    chip: 'tests + rubric, graded',
    evidence: "The artifact itself, graded against the role's rubric — code or document.",
  },
];

const CLAIMS = [
  'Every task battle-tested',
  'Verification scored, not assumed',
  'Full transcript, no webcam',
  'Same rubric for every candidate',
];

// Both turns are rendered statically and revealed with a CSS stagger — no JS
// typewriter, no observer gating for correctness. The dial fills and the
// "trap caught" badge stamps in via CSS keyed off the section reveal.
const StandardSection = ({ reveal }) => (
  <section className="lvc-standard">
    <SectionHeader
      revealRef={reveal('std-head')}
      eyebrow="THE STANDARD"
      headParts={['We’re making AI fluency', 'measurable.']}
      sub="Five dimensions. Planted traps. Scored verification. A transcript instead of a webcam. When a Taali score says they can work with AI, they can."
    />

    <div className="lvc-standard-body">
      <div className="lvc-standard-copy" ref={reveal('std-copy')} data-reveal>
        <div className="lvc-ds-rows">
          {FIVE_DS.map((row, i) => (
            <div className="lvc-ds-row" key={row.d} style={{ '--i': i }}>
              <span className="lvc-ds-name">{row.d}</span>
              <div className="lvc-ds-body">
                <span className="lvc-ds-def">{row.def}</span>
                <span className="lvc-ds-evidence">{row.evidence}</span>
              </div>
              <span className="lvc-ds-chip">{row.chip}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="lvc-chat" ref={reveal('std-chat')} data-reveal>
        <div className="lvc-chat-head">
          <span className="lvc-chat-dot" /> assessment · live transcript
        </div>
        {CHAT_TURNS.map((turn, i) => (
          <div
            key={turn.text}
            className={`lvc-turn lvc-turn--${turn.who === 'AI' ? 'ai' : 'cand'}`}
            style={{ '--i': i }}
          >
            <span className="lvc-turn-who">{turn.who}</span>
            <span className="lvc-turn-text">{turn.text}</span>
          </div>
        ))}
        <div className="lvc-dial" aria-hidden="true">
          <span className="lvc-dial-label">Discernment</span>
          <span className="lvc-dial-track">
            <span className="lvc-dial-fill" />
          </span>
          <span className="lvc-trap-badge">trap caught</span>
        </div>
      </div>
    </div>

    <div className="lvc-claims" ref={reveal('std-claims')} data-reveal>
      {CLAIMS.map((c) => (
        <span className="lvc-claim" key={c}>
          {c}
        </span>
      ))}
    </div>
  </section>
);

// ── Section 5 · CLOSING CTA + FOOTER ───────────────────────────────────────
// Replicated from the production landing (LandingPageContent.jsx) — the founder
// prefers its closing treatment. We reproduce the same token-based purple
// gradient CTA and the dark full footer (logo, three link columns, giant faded
// wordmark, contact row), adapted minimally to the `.lvc` scope. Links/CTAs
// route through the same onNavigate prop / marketing scroll the production
// footer uses. Kept as JSX (not an import) because the production versions live
// inside LandingPage's body, not as standalone exported components.
const containerClass = 'mx-auto max-w-[85rem] px-6 md:px-10 xl:px-16';

const FOOTER_COLUMNS = [
  {
    title: 'Product',
    items: [
      { label: 'Book a demo', page: 'demo-lead' },
      { label: 'AI collab score', section: 'platform' },
      { label: 'Question bank', section: 'platform' },
      { label: 'Integrations', section: 'platform' },
      { label: 'Developers / API', page: 'developers' },
      { label: 'Product walkthrough', page: 'showcase' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Manifesto', section: 'problem' },
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

const ClosingCta = ({ onNavigate }) => (
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
              className="taali-btn taali-btn-inverse taali-btn-lg"
              onClick={() => onNavigate('showcase')}
            >
              Open walkthrough →
            </button>
            <button
              type="button"
              className="taali-btn taali-btn-primary taali-btn-lg"
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

const ProductionFooter = ({ onNavigate }) => (
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

// ---------------------------------------------------------------------------
// Root — owns the ON/OFF state, the auto-flip timer, the scroll-reveal
// observer, and the reduced-motion branch.
// ---------------------------------------------------------------------------
export const LandingVariantC = ({ onNavigate }) => {
  const reduced = prefersReducedMotion();
  const [on, setOn] = useState(reduced); // reduced-motion → straight to ON
  const [pressing, setPressing] = useState(false);
  const userToggledRef = useRef(reduced);
  const pipelineRef = useRef(null);
  const revealRefs = useRef(new Map());

  const toggle = useCallback(() => {
    userToggledRef.current = true;
    if (reduced) {
      setOn((v) => !v);
      return;
    }
    // Physical press: scale down for 200ms, then flip.
    setPressing(true);
    window.setTimeout(() => {
      setOn((v) => !v);
      setPressing(false);
    }, 200);
  }, [reduced]);

  // Auto-flip ON ~1.4s after mount, unless the visitor already toggled.
  useEffect(() => {
    if (reduced || userToggledRef.current) return undefined;
    const t = window.setTimeout(() => {
      if (userToggledRef.current) return;
      setPressing(true);
      window.setTimeout(() => {
        if (userToggledRef.current) return;
        setOn(true);
        setPressing(false);
      }, 200);
    }, 1400);
    return () => window.clearTimeout(t);
  }, [reduced]);

  // Scroll-reveal: registers refs and reveals them as they enter view.
  const reveal = useCallback((key) => (node) => {
    if (node) revealRefs.current.set(key, node);
    else revealRefs.current.delete(key);
  }, []);

  useEffect(() => {
    if (reduced) {
      revealRefs.current.forEach((node) => node.setAttribute('data-shown', 'true'));
      return undefined;
    }
    if (typeof IntersectionObserver === 'undefined') {
      revealRefs.current.forEach((node) => node.setAttribute('data-shown', 'true'));
      return undefined;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.setAttribute('data-shown', 'true');
            obs.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.2 },
    );
    revealRefs.current.forEach((node) => obs.observe(node));
    // Belt and braces: if the observer never fires (blocked, broken, or
    // never intersecting because of an ancestor quirk), content must still
    // appear — an invisible marketing page is the one unacceptable failure.
    const fallback = window.setTimeout(() => {
      revealRefs.current.forEach((node) => {
        if (node.getAttribute('data-shown') !== 'true') {
          node.setAttribute('data-shown', 'true');
        }
      });
    }, 2600);
    return () => {
      window.clearTimeout(fallback);
      obs.disconnect();
    };
  }, [reduced]);

  const scrollToPipeline = useCallback(() => {
    pipelineRef.current?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  }, [reduced]);

  return (
    <div className={`lvc${on ? ' is-on' : ''}${reduced ? ' is-reduced' : ''}`} data-on={on ? 'true' : 'false'}>
      <style>{VARIANT_C_CSS}</style>

      <HeroSection
        on={on}
        pressing={pressing}
        onToggle={toggle}
        onNavigate={onNavigate}
        onHowItWorks={scrollToPipeline}
      />
      <ProblemSection reveal={reveal} />
      <PipelineSection reveal={reveal} pipelineRef={pipelineRef} />
      <StandardSection reveal={reveal} />

      <div className="lvc-footer">
        <ClosingCta onNavigate={onNavigate} />
        <ProductionFooter onNavigate={onNavigate} />
      </div>
    </div>
  );
};

export default LandingVariantC;
