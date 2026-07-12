import React from 'react';

import { Reveal, useReducedMotionSync } from '../../../../shared/motion';
import { NumberTicker } from '../../../../shared/motion/previewMotion';
import { HeroScene } from './VariantEHeroScene';
import { FunnelScene } from './VariantEFunnelScene';
import { ScorecardArtifact, ControlDecisionArtifact } from './VariantERealMocks';

// ---------------------------------------------------------------------------
// The six-section narrative spine for landing variant E. One story, told once:
// turn a job on, the agent works your whole funnel — and it's the only one that
// measures how people actually work with AI. Every section-header triad (mono
// eyebrow → short verb-led H2 → one-line sub) enters via the shared, one-shot
// once-only shared <Reveal>. The
// two autoplay SCENES — the hero job-on loop and the funnel advance — own their
// own useAnimate/useInView timelines in their component files.
// ---------------------------------------------------------------------------

const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
    <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const SectionHead = ({ eyebrow, children, sub, center = true, reduced }) => (
  <Reveal className={center ? 'lve-sechead' : undefined} reduced={reduced}>
    <div className="lve-eyebrow">
      <span className="lve-eyebrow-dot" /> {eyebrow}
    </div>
    <h2 className="lve-h2">{children}</h2>
    {sub ? <p className="lve-sub">{sub}</p> : null}
  </Reveal>
);

// ── 1 · HERO — the product's core loop, live ─────────────────────────────
export const HeroSection = ({ onNavigate }) => {
  const reduced = useReducedMotionSync();
  return (
    <section className="lve-hero">
      <span className="lve-hero-glow" aria-hidden="true" />
      <div className="lve-wrap">
        <div className="lve-hero-grid">
          <Reveal className="lve-hero-copy" reduced={reduced}>
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
          </Reveal>

          <Reveal className="lve-hero-scene-wrap" delay={0.12} y={26} reduced={reduced}>
            <HeroScene />
          </Reveal>
        </div>
      </div>
    </section>
  );
};

// ── 2 · THE PROBLEM — one tight beat, mostly type ────────────────────────
export const ProblemSection = () => {
  const reduced = useReducedMotionSync();
  return (
    <section className="lve-section lve-problem" id="lve-problem">
      <div className="lve-wrap">
        <Reveal className="lve-problem-inner" reduced={reduced}>
          <div className="lve-eyebrow">
            <span className="lve-eyebrow-dot" /> THE PROBLEM
          </div>
          <p className="lve-problem-lead">
            Everyone works with AI now. <em>The CV can&apos;t prove it. The interview can&apos;t catch it.</em>
          </p>
          <p className="lve-problem-tail">
            Hiring still screens for the old job. The one skill that decides output today — how well a
            person actually works with AI — goes unmeasured, and slips straight past you.
          </p>
        </Reveal>
      </div>
    </section>
  );
};

// ── 3 · THE FUNNEL — shown once ──────────────────────────────────────────
export const FunnelSection = () => {
  const reduced = useReducedMotionSync();
  return (
    <section className="lve-section lve-funnel" id="lve-funnel">
      <div className="lve-wrap">
        <SectionHead
          eyebrow="WATCH IT WORK"
          reduced={reduced}
          sub="Sourced from your ATS, screened against the role's real requirements, assessed on AI fluency, decided with evidence, and handed back — one candidate, one continuous pass."
        >
          One agent, your <em>whole funnel</em>.
        </SectionHead>
        <Reveal className="lve-funnel-stage" delay={0.08} y={26} reduced={reduced}>
          <FunnelScene />
        </Reveal>
      </div>
    </section>
  );
};

// ── 4 · THE WEDGE — AI fluency (the differentiator) ──────────────────────
export const WedgeSection = () => {
  const reduced = useReducedMotionSync();
  return (
    <section className="lve-section lve-wedge" id="lve-wedge">
      <div className="lve-wrap">
        <SectionHead
          eyebrow="THE DIFFERENTIATOR"
          reduced={reduced}
          sub="Five dimensions, planted traps, scored from the actual transcript — real verification of how someone works with AI on engineering or knowledge work. No one else measures this."
        >
          Measure how people <em>actually work with AI</em>.
        </SectionHead>
        <Reveal className="lve-wedge-stage" delay={0.08} y={26} reduced={reduced}>
          <ScorecardArtifact />
        </Reveal>
      </div>
    </section>
  );
};

// ── 5 · YOU STAY IN CONTROL — the credibility keystone ───────────────────
const CONTROL_POINTS = [
  {
    h: 'Deterministic and evidence-linked',
    p: 'Same inputs, same call, every time — each one citing the requirements, transcript and rubric behind it.',
  },
  {
    h: 'Yours to approve, override, or teach',
    p: 'Every consequential call waits for you. Override it and your call becomes the agent’s next training signal.',
  },
  {
    h: 'A full audit trail',
    p: 'Every decision, note and stage move is logged and written back to your ATS for review.',
  },
  {
    h: 'Never on protected characteristics',
    p: 'The same task and rubric for every candidate. The agent advises on evidence, never on who someone is.',
  },
];

export const ControlSection = () => {
  const reduced = useReducedMotionSync();
  return (
    <section className="lve-section lve-control-sec" id="lve-control">
      <div className="lve-wrap">
        <Reveal className="lve-control" reduced={reduced}>
          <div className="lve-control-grid">
            <div>
              <div className="lve-eyebrow">
                <span className="lve-eyebrow-dot" /> HUMAN IN THE LOOP
              </div>
              <h2 className="lve-h2">The agent advises. <em>You decide.</em></h2>
              <p className="lve-sub">
                Every consequential call is deterministic, evidence-linked, and yours to approve, override, or
                teach. Pair every AI claim with human control.
              </p>
              <div className="lve-control-points">
                {CONTROL_POINTS.map((point) => (
                  <div className="lve-control-point" key={point.h}>
                    <span className="lve-control-check">
                      <CheckIcon />
                    </span>
                    <div>
                      <div className="lve-control-point-h">{point.h}</div>
                      <div className="lve-control-point-p">{point.p}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="lve-control-artifact">
              <ControlDecisionArtifact />
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
};

// ── 6 · PROOF + CLOSE — a tight stats row (the ClosingCta + footer follow) ─
const STATS = [
  { to: 5, suffix: '', cap: 'dimensions scored on every assessment' },
  { to: 30, suffix: ' min', cap: 'assessment — every task battle-tested before use' },
  { to: 100, suffix: '%', cap: 'of decisions evidence-linked' },
  { word: 'Zero', cap: 'webcams or lockdown browsers' },
];

export const ProofSection = () => {
  const reduced = useReducedMotionSync();
  return (
    <section className="lve-section lve-proof" id="lve-proof">
      <div className="lve-wrap">
        <SectionHead
          eyebrow="BY THE NUMBERS"
          reduced={reduced}
          sub="Capability, not vanity metrics — what every assessment and decision guarantees."
        >
          Built to be <em>defensible</em>.
        </SectionHead>
        <Reveal className="lve-proof-band" delay={0.08} y={26} reduced={reduced}>
          <div className="lve-proof-grid">
            {STATS.map((s) => (
              <div className="lve-stat" key={s.cap}>
                <div className="lve-stat-big">
                  {s.word
                    ? s.word
                    : <><NumberTicker to={s.to} reduced={reduced} />{s.suffix ? <em>{s.suffix}</em> : null}</>}
                </div>
                <div className="lve-stat-cap">{s.cap}</div>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
};
