import React, { useCallback, useEffect, useRef, useState } from 'react';

import { LandingPreviewFooter } from './LandingPreviewChrome';
import { VARIANT_C_CSS } from './landingVariantC.styles';

// ---------------------------------------------------------------------------
// VARIANT C — "Turn hiring on". A LIGHT concept where the page itself is an
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
// Constraints honoured: no new deps (CSS keyframes only — no <canvas>, no rAF),
// lazy-loaded route, prefers-reduced-motion renders straight to ON with static
// composition, robust at 80% zoom / 1024–1600 widths, purple-family accents,
// fixture data only. Nothing depends on IntersectionObserver for correctness —
// reveals have fallbacks and stay visible even if they never fire.
// ---------------------------------------------------------------------------

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── Falling-CV field (hero background, OFF state) ──────────────────────────
// A denser, calmer field of abstract light-grey "paper" cards drifting down.
// On flip they accelerate and stream toward the toggle (a translate + scale
// toward a shared vanishing point), then fade — the "sucked in" beat.
// Positions are deterministic so the scene is stable across renders.
const CV_CARDS = Array.from({ length: 18 }, (_, i) => {
  const seed = (i * 9301 + 49297) % 233280;
  const rnd = seed / 233280;
  const rnd2 = ((i * 4099 + 7919) % 233280) / 233280;
  return {
    id: i,
    left: 2 + rnd * 92, // vw %
    delay: -(rnd2 * 18), // negative → mid-flight on mount
    dur: 18 + rnd * 12,
    scale: 0.6 + rnd2 * 0.6,
    tilt: (rnd - 0.5) * 10,
  };
});

const FallingCvField = ({ on }) => (
  <div className="lvc-cvfield" aria-hidden="true" data-on={on ? 'true' : 'false'}>
    {CV_CARDS.map((c) => (
      <div
        key={c.id}
        className="lvc-cv"
        style={{
          left: `${c.left}%`,
          transform: `scale(${c.scale}) rotate(${c.tilt}deg)`,
          animationDelay: `${c.delay}s`,
          animationDuration: `${c.dur}s`,
        }}
      >
        <span className="lvc-cv-line lvc-cv-line--head" />
        <span className="lvc-cv-line" />
        <span className="lvc-cv-line" />
        <span className="lvc-cv-line lvc-cv-line--short" />
      </div>
    ))}
  </div>
);

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
      <span className="lvc-switch-track" aria-hidden="true">
        <span className="lvc-switch-glow" />
        <span className="lvc-switch-knob">
          <span className="lvc-switch-ring" />
        </span>
      </span>
    </button>
    <span className="lvc-switch-caption" aria-hidden="true">
      agent: <b>{on ? 'on' : 'off'}</b>
    </span>
  </div>
);

// ── Section 1 · HERO ───────────────────────────────────────────────────────
const HeroSection = ({ on, pressing, onToggle, onNavigate, onHowItWorks }) => (
  <section className="lvc-hero">
    <FallingCvField on={on} />
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
        Taali works your pipeline end to end and measures the one thing every CV now hides: can this
        person actually build with AI.
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
      Everyone ships with AI now.
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
const RIBBON_DOTS = Array.from({ length: 6 }, (_, i) => ({ id: i, delay: i * 1.1 }));

const PipelineRibbon = () => (
  <div className="lvc-ribbon" aria-hidden="true">
    <div className="lvc-ribbon-rail" />
    <div className="lvc-ribbon-flow">
      {RIBBON_DOTS.map((d) => (
        <span key={d.id} className="lvc-ribbon-dot" style={{ animationDelay: `${-d.delay}s` }} />
      ))}
    </div>
    <div className="lvc-ribbon-nodes">
      {RIBBON_NODES.map((n, i) => (
        <span key={n} className="lvc-ribbon-node" style={{ '--n': i }}>
          <span className="lvc-ribbon-node-core" />
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
  },
  {
    n: '02',
    t: 'Screen',
    d: "Reads every CV against the role's real requirements. Weak fits are gated with evidence, not vibes.",
  },
  {
    n: '03',
    t: 'Assess',
    d: 'A task authored from your JD, battle-tested in a sandbox, sent automatically. Candidates pair with Claude on real work.',
  },
  {
    n: '04',
    t: 'Decide',
    d: 'A deterministic verdict on every candidate, with the evidence attached. You approve, override, or teach it back.',
  },
  {
    n: '05',
    t: 'Hand back',
    d: 'Decisions, notes and reports written back to your ATS. The audit trail comes free.',
  },
];

const PipelineSection = ({ reveal, pipelineRef }) => (
  <section className="lvc-pipeline" ref={pipelineRef}>
    <div className="lvc-pipe-copy" ref={reveal('pipe-copy')} data-reveal>
      <div className="lvc-eyebrow">THE PIPELINE</div>
      <h2 className="lvc-h2">An agent that runs the funnel.</h2>
      <p className="lvc-body">
        It finds candidates, reads every CV, runs the assessment, and puts a decision in front of
        you with the evidence attached. You approve. It executes.
      </p>
    </div>

    <PipelineRibbon />

    <div className="lvc-stage-grid" ref={reveal('pipe-stages')} data-reveal>
      {PIPELINE_STAGES.map((s, i) => (
        <div className="lvc-stage" key={s.t} style={{ '--i': i }}>
          <span className="lvc-stage-n">{s.n}</span>
          <h3 className="lvc-stage-t">{s.t}</h3>
          <p className="lvc-stage-d">{s.d}</p>
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
  },
  {
    d: 'Description',
    def: 'Directing it — clear prompts, the right context.',
    chip: 'prompt quality, scored',
  },
  {
    d: 'Discernment',
    def: 'Catching what the AI gets wrong.',
    chip: 'planted traps, scored',
  },
  {
    d: 'Diligence',
    def: 'Verifying before calling it done.',
    chip: 'verification events, counted',
  },
  {
    d: 'Deliverable',
    def: 'What actually shipped, on its merits.',
    chip: 'tests + rubric, graded',
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
    <div className="lvc-standard-copy" ref={reveal('std-copy')} data-reveal>
      <div className="lvc-eyebrow">THE STANDARD</div>
      <h2 className="lvc-h2">We’re making AI fluency measurable.</h2>
      <p className="lvc-body">
        Five dimensions. Planted traps. Scored verification. A transcript instead of a webcam. When
        a Taali score says they can ship with AI, they can.
      </p>
      <div className="lvc-ds-rows">
        {FIVE_DS.map((row, i) => (
          <div className="lvc-ds-row" key={row.d} style={{ '--i': i }}>
            <span className="lvc-ds-name">{row.d}</span>
            <span className="lvc-ds-def">{row.def}</span>
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

    <div className="lvc-claims" ref={reveal('std-claims')} data-reveal>
      {CLAIMS.map((c) => (
        <span className="lvc-claim" key={c}>
          {c}
        </span>
      ))}
    </div>
  </section>
);

// ── Section 5 · CTA BAND ───────────────────────────────────────────────────
const CtaBand = ({ onNavigate }) => (
  <section className="lvc-ctaband">
    <div className="lvc-ctaband-inner">
      <h2 className="lvc-ctaband-h2">Watch it decide in three minutes.</h2>
      <button type="button" className="lvc-btn lvc-btn--primary lvc-btn--lg" onClick={() => onNavigate('demo-lead')}>
        See it live <span aria-hidden="true">→</span>
      </button>
    </div>
  </section>
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
      <CtaBand onNavigate={onNavigate} />

      <div className="lvc-footer">
        <LandingPreviewFooter onNavigate={onNavigate} />
      </div>
    </div>
  );
};

export default LandingVariantC;
