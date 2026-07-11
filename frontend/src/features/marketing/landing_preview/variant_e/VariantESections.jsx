import React, { useRef } from 'react';
import { m, useScroll, useTransform } from 'motion/react';

import { Reveal, Stagger, StaggerItem, Ticker } from './motion';
import {
  DecideMock,
  HandBackMock,
  LogoMarquee,
} from './VariantEMocks';
import {
  HeroAgentSwitch,
  HeroDecisionArtifact,
  FunnelFeedArtifact,
  ScreenFeedArtifact,
  ScorecardArtifact,
  ScreenPillarVisual,
  AssessPillarVisual,
  DecidePillarVisual,
} from './VariantERealMocks';

// ---------------------------------------------------------------------------
// Content sections for landing variant E. Each carries the section-header triad
// (mono eyebrow → short verb-led H2 → one-line sub) and a <Reveal>/<Stagger>
// entrance. The signature autoplay mocks live in VariantEMocks.jsx.
// ---------------------------------------------------------------------------

const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
    <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const SectionHead = ({ eyebrow, children, sub, center = true }) => (
  <div className={center ? 'lve-sechead' : undefined}>
    <div className="lve-eyebrow">
      <span className="lve-eyebrow-dot" /> {eyebrow}
    </div>
    <h2 className="lve-h2">{children}</h2>
    {sub ? <p className="lve-sub">{sub}</p> : null}
  </div>
);

// ── HERO ────────────────────────────────────────────────────────────────
// The signature beat: variant-D's clean pill toggle flips OFF → ON — grey knob
// slides purple — and the hero product card (the real, compact AgentDecisionCard)
// reveals + comes alive alongside it. Simple and bold, like variant D.
export const HeroSection = ({ on, pressing, onToggle, onNavigate }) => {
  const ref = useRef(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end start'] });
  const glowY = useTransform(scrollYProgress, [0, 1], [0, 90]);

  return (
    <section className="lve-hero" ref={ref}>
      <m.div className="lve-hero-glow" style={{ y: glowY }} aria-hidden="true" />
      <div className="lve-wrap">
        <div className="lve-hero-grid">
          <Reveal className="lve-hero-copy" amount={0.2}>
            <div className="lve-hero-kicker">
              <span className="lve-hero-kicker-dot" /> AGENT-NATIVE HIRING
            </div>
            <h1 className="lve-h1">
              Taali is the hiring agent that screens, assesses, and decides — <em>with you.</em>
            </h1>
            <p className="lve-hero-sub">
              An agentic recruiting platform that runs screening, AI-fluency assessment, and defensible
              decisions end to end. You stay in control of every call that matters.
            </p>
            <div className="lve-hero-cta">
              <button type="button" className="lve-btn lve-btn--primary" onClick={() => onNavigate('demo-lead')}>
                See it live <span aria-hidden="true">→</span>
              </button>
              <button type="button" className="lve-btn lve-btn--ghost" onClick={() => onNavigate('demo-lead')}>
                Book a demo
              </button>
            </div>
            <HeroAgentSwitch on={on} pressing={pressing} onToggle={onToggle} />
          </Reveal>

          <Reveal className={`lve-hero-mock-wrap${on ? ' is-on' : ''}`} amount={0.2} delay={0.1} y={26}>
            <HeroDecisionArtifact />
          </Reveal>
        </div>
      </div>
    </section>
  );
};

// ── TRUST STRIP ──────────────────────────────────────────────────────────
export const TrustStrip = () => (
  <section className="lve-trust">
    <div className="lve-wrap">
      <div className="lve-trust-label">Built for modern talent teams</div>
      <LogoMarquee />
    </div>
  </section>
);

// ── PRODUCT IN ACTION ─────────────────────────────────────────────────────
export const ProductInAction = () => (
  <section className="lve-section lve-run" id="lve-product">
    <div className="lve-wrap">
      <Reveal>
        <SectionHead
          eyebrow="WATCH IT WORK"
          sub="Applicants come in, weak fits drop with evidence, the strong ones get assessed, and a decision lands in your ATS. You approve every one."
        >
          Watch the agent run your <em>funnel</em>.
        </SectionHead>
      </Reveal>
      <Reveal amount={0.2} y={26}>
        <FunnelFeedArtifact />
        <p className="lve-run-caption">
          The real Home queue — every call the agent made overnight, waiting for your approval. Nothing moves without you.
        </p>
      </Reveal>
    </div>
  </section>
);

// ── VALUE PILLARS ─────────────────────────────────────────────────────────
// Each pillar is backed by a real product MICRO-VISUAL built from the live
// atoms (feed rows, the 5-Ds axes, a decision-card header) — not a stock
// icon+heading card. Unmistakably Taali.
const PILLARS = [
  {
    eyebrow: 'SCREEN',
    h: 'Screen every applicant in hours',
    p: "The agent reads every CV against the role's real requirements and gates weak fits with evidence. You review the shortlist.",
    Visual: ScreenPillarVisual,
  },
  {
    eyebrow: 'ASSESS',
    h: 'Measure how they work with AI',
    p: 'A 30-minute assessment scores candidates across the 5 Ds from the actual transcript. You read the same rubric every time.',
    Visual: AssessPillarVisual,
  },
  {
    eyebrow: 'DECIDE',
    h: 'Defensible decisions, evidence attached',
    p: 'Every verdict is deterministic and cites the evidence behind it. You approve, override, or teach it back.',
    Visual: DecidePillarVisual,
  },
];

export const ValuePillars = () => (
  <section className="lve-section" id="lve-pillars">
    <div className="lve-wrap">
      <Reveal>
        <SectionHead
          eyebrow="WHAT YOU GET"
          sub="Put the busywork on the agent — every applicant screened, every candidate assessed, every decision backed by evidence, with you signing off."
        >
          Give the agent the <em>busywork</em>.
        </SectionHead>
      </Reveal>
      <Stagger className="lve-pillars-grid">
        {PILLARS.map(({ eyebrow, h, p, Visual }) => (
          <StaggerItem className="lve-pillar" key={h}>
            <div className="lve-pillar-visual">
              <Visual />
            </div>
            <div className="lve-pillar-eyebrow">
              <span className="lve-eyebrow-dot" /> {eyebrow}
            </div>
            <h3 className="lve-pillar-h">{h}</h3>
            <p className="lve-pillar-p">{p}</p>
          </StaggerItem>
        ))}
      </Stagger>
    </div>
  </section>
);

// ── DEEP FEATURE BANDS ─────────────────────────────────────────────────────
const BANDS = [
  {
    eyebrow: 'SCREEN',
    h: "Screen every CV against the role's real requirements.",
    p: "The agent checks each requirement and shows the evidence it found — or didn't. You never wonder why someone was passed over.",
    Mock: ScreenFeedArtifact,
    flip: false,
  },
  {
    eyebrow: 'ASSESS AI FLUENCY',
    h: 'Measure how candidates work with AI — engineering or knowledge work.',
    p: 'A task authored from your role, scored across five dimensions from the actual transcript. You read the same rubric for every candidate.',
    Mock: ScorecardArtifact,
    flip: true,
  },
  {
    eyebrow: 'DECIDE',
    h: 'A deterministic verdict on every candidate, evidence attached.',
    p: 'Same inputs, same decision, every time — with the requirement scores and transcript behind it. You make the final call.',
    Mock: DecideMock,
    flip: false,
  },
  {
    eyebrow: 'HAND BACK',
    h: 'Written back to your ATS. The audit trail comes free.',
    p: 'Decisions, notes and reports sync to Workable, Bullhorn or Greenhouse. Every move is logged for you to review.',
    Mock: HandBackMock,
    flip: true,
  },
];

export const FeatureBands = () => (
  <section className="lve-section" id="lve-bands">
    <div className="lve-wrap">
      <div className="lve-bands">
        {BANDS.map(({ eyebrow, h, p, Mock, flip }) => (
          <div className={`lve-band${flip ? ' flip' : ''}`} key={eyebrow}>
            <Reveal className="lve-band-copy" amount={0.4}>
              <SectionHead eyebrow={eyebrow} sub={p} center={false}>
                {h}
              </SectionHead>
            </Reveal>
            <Reveal className="lve-band-visual" amount={0.3} y={26} delay={0.05}>
              <Mock />
            </Reveal>
          </div>
        ))}
      </div>
    </div>
  </section>
);

// ── HOW IT WORKS ───────────────────────────────────────────────────────────
const STEPS = [
  {
    n: '01',
    h: 'Connect your ATS',
    p: 'Plug into Workable, Bullhorn or Greenhouse. Candidates, roles and briefs sync in. Nothing to set up.',
  },
  {
    n: '02',
    h: 'The agent screens & assesses',
    p: 'It reads every CV, sends the assessment, and scores how each candidate works with AI.',
  },
  {
    n: '03',
    h: 'You approve with evidence',
    p: 'A decision lands for every candidate with the evidence attached. You approve, override, or teach it back.',
  },
];

export const HowItWorks = () => (
  <section className="lve-section" id="lve-how">
    <div className="lve-wrap">
      <Reveal>
        <SectionHead eyebrow="HOW IT WORKS" sub="Three steps from connecting your ATS to approving your first shortlist.">
          Live in an <em>afternoon</em>.
        </SectionHead>
      </Reveal>
      <Stagger className="lve-steps">
        {STEPS.map((step) => (
          <StaggerItem className="lve-step" key={step.n}>
            <span className="lve-step-badge">{step.n}</span>
            <h3 className="lve-step-h">{step.h}</h3>
            <p className="lve-step-p">{step.p}</p>
          </StaggerItem>
        ))}
      </Stagger>
    </div>
  </section>
);

// ── TRUST / CONTROL ────────────────────────────────────────────────────────
const CONTROL_POINTS = [
  {
    h: 'It advises, never acts alone',
    p: 'The agent surfaces a recommendation and the evidence. Advancing, rejecting and hiring stay your call.',
  },
  {
    h: 'Evidence behind every score',
    p: "Each verdict cites the requirements, transcript and rubric it's built on. Nothing is a black box.",
  },
  {
    h: 'Fair by design',
    p: 'The same task and rubric for every candidate. The agent never scores on protected characteristics.',
  },
  {
    h: 'A full audit trail',
    p: 'Every decision, note and stage move is logged and written back to your ATS for review.',
  },
];

export const TrustControl = () => (
  <section className="lve-section" id="lve-control">
    <div className="lve-wrap">
      <Reveal className="lve-control" amount={0.2}>
        <div className="lve-control-grid">
          <div>
            <SectionHead
              eyebrow="HUMAN IN THE LOOP"
              sub="Every recommendation is yours to accept or reject, and every one shows its work."
              center={false}
            >
              The agent advises. <em>You decide.</em>
            </SectionHead>
          </div>
          <Stagger className="lve-control-points" amount={0.3}>
            {CONTROL_POINTS.map((point) => (
              <StaggerItem className="lve-control-point" key={point.h}>
                <span className="lve-control-check">
                  <CheckIcon />
                </span>
                <div>
                  <div className="lve-control-point-h">{point.h}</div>
                  <div className="lve-control-point-p">{point.p}</div>
                </div>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </Reveal>
    </div>
  </section>
);

// ── STATS BAND (number tickers) ─────────────────────────────────────────────
export const StatsBand = () => (
  <section className="lve-section" id="lve-stats">
    <div className="lve-wrap">
      <Reveal>
        <SectionHead
          eyebrow="BY THE NUMBERS"
          sub="Capability, not vanity metrics — what every assessment and decision guarantees."
        >
          Built to be <em>defensible</em>.
        </SectionHead>
      </Reveal>
      <Reveal className="lve-stats" amount={0.3} y={26}>
        <div className="lve-stats-grid">
          <div className="lve-stat">
            <div className="lve-stat-big">
              <Ticker value={5} />
            </div>
            <div className="lve-stat-cap">dimensions scored on every assessment</div>
          </div>
          <div className="lve-stat">
            <div className="lve-stat-big">
              <Ticker value={30} />
              <em> min</em>
            </div>
            <div className="lve-stat-cap">assessment — no take-home marathon</div>
          </div>
          <div className="lve-stat">
            <div className="lve-stat-big">
              <Ticker value={100} />
              <em>%</em>
            </div>
            <div className="lve-stat-cap">of decisions linked to evidence</div>
          </div>
          <div className="lve-stat">
            <div className="lve-stat-big">Zero</div>
            <div className="lve-stat-cap">webcams or lockdown browsers</div>
          </div>
        </div>
      </Reveal>
    </div>
  </section>
);

// ── INTEGRATIONS ────────────────────────────────────────────────────────────
const INTEGRATIONS = [
  { name: 'Workable', sub: 'Two-way candidate & decision sync', shape: '' },
  { name: 'Bullhorn', sub: 'Two-way candidate & decision sync', shape: 'round' },
  { name: 'Greenhouse', sub: 'Two-way candidate & decision sync', shape: 'diamond' },
];

export const Integrations = () => (
  <section className="lve-section" id="lve-integrations">
    <div className="lve-wrap">
      <Reveal>
        <SectionHead eyebrow="INTEGRATIONS" sub="Two-way sync with the systems your team already runs.">
          Works with your <em>ATS</em>.
        </SectionHead>
      </Reveal>
      <Stagger className="lve-integrations-row">
        {INTEGRATIONS.map((it) => (
          <StaggerItem className="lve-integration" key={it.name}>
            <span className={`lve-integration-glyph ${it.shape}`} aria-hidden="true" />
            <div className="lve-integration-body">
              <div className="lve-integration-name">{it.name}</div>
              <div className="lve-integration-sub">
                <span className="lve-integration-sync" aria-hidden="true">⇄</span> {it.sub}
              </div>
            </div>
            <span className="lve-integration-status">
              <span className="lve-integration-dot" aria-hidden="true" /> Connected
            </span>
          </StaggerItem>
        ))}
      </Stagger>
    </div>
  </section>
);
