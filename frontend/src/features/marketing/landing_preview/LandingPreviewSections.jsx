import React, { useRef } from 'react';

import {
  MOTION_DURATION,
  MOTION_EASE,
  MotionLoop,
  m,
  useInView,
  useReducedMotionSync,
} from '../../../shared/motion';
import { containerClass } from './LandingPreviewChrome';

// ---------------------------------------------------------------------------
// Abstract line-art motifs. Pure CSS/SVG — no screenshots, no product chrome.
// Each is a small geometric mark drawn from the purple palette that gestures at
// its pillar (a flowing pipeline, a verified checkmark-through-a-lens, a shield
// /ledger). Decorative only, so aria-hidden.
// ---------------------------------------------------------------------------

const PillarPipeline = () => (
  <svg viewBox="0 0 72 72" width="72" height="72" aria-hidden="true" fill="none">
    <circle cx="12" cy="14" r="4.5" stroke="var(--purple)" strokeWidth="2" />
    <circle cx="12" cy="36" r="4.5" stroke="var(--purple)" strokeWidth="2" />
    <circle cx="12" cy="58" r="4.5" stroke="var(--purple)" strokeWidth="2" />
    <path d="M16.5 14H40a10 10 0 0 1 10 10v0a10 10 0 0 0 10 10" stroke="var(--purple)" strokeWidth="2" strokeLinecap="round" />
    <path d="M16.5 36H50" stroke="var(--purple)" strokeWidth="2" strokeLinecap="round" opacity="0.55" />
    <path d="M16.5 58H40a10 10 0 0 0 10-10" stroke="var(--purple)" strokeWidth="2" strokeLinecap="round" opacity="0.55" />
    <circle cx="60" cy="34" r="5" fill="var(--purple)" />
  </svg>
);

const PillarProof = () => (
  <svg viewBox="0 0 72 72" width="72" height="72" aria-hidden="true" fill="none">
    <circle cx="32" cy="32" r="18" stroke="var(--purple)" strokeWidth="2" opacity="0.55" />
    <path d="M45 45l14 14" stroke="var(--purple)" strokeWidth="2" strokeLinecap="round" />
    <path d="M24 32l6 6 12-13" stroke="var(--purple)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const PillarDefensible = () => (
  <svg viewBox="0 0 72 72" width="72" height="72" aria-hidden="true" fill="none">
    <path d="M36 8l22 8v16c0 17-11 27-22 32C25 59 14 49 14 32V16l22-8z" stroke="var(--purple)" strokeWidth="2" />
    <path d="M26 30h20M26 38h20M26 46h13" stroke="var(--purple)" strokeWidth="2" strokeLinecap="round" opacity="0.6" />
  </svg>
);

const PILLARS = [
  {
    Motif: PillarPipeline,
    title: 'An agent that works the pipeline',
    body: 'A governed hiring agent screens, assesses, and moves on-policy reversible steps automatically. Ambiguous, off-policy, and irreversible outcomes come to you with deterministic, evidence-linked recommendations.',
  },
  {
    Motif: PillarProof,
    title: 'Proof they can ship with AI',
    body: 'Candidates pair with Claude in a chat-first workspace, and every prompt, edit and test run is captured. We score how they actually work with AI — not just the code they hand in.',
  },
  {
    Motif: PillarDefensible,
    title: 'Defensible by design',
    body: 'Every decision carries a full audit trail and links to the evidence behind it. The agent warns, never blocks, and never acts on protected characteristics.',
  },
];

export const ValuePillars = ({ condensed = false }) => (
  <section className="border-t border-[var(--line)] bg-[var(--bg-2)]">
    <div className={`${containerClass} ${condensed ? 'py-16' : 'py-20 md:py-24'}`}>
      {!condensed ? (
        <div className="mb-12 max-w-[46rem]">
          <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
            WHY TAALI
          </div>
          <h2 className="mt-3 font-[var(--font-display)] text-[clamp(30px,4vw,46px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]">
            Three things no other platform does at once.
          </h2>
        </div>
      ) : null}
      <div className="grid gap-8 md:grid-cols-3">
        {PILLARS.map(({ Motif, title, body }) => (
          <div
            key={title}
            className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg)] p-7 shadow-[var(--shadow-sm)]"
          >
            <Motif />
            <h3 className="mt-5 font-[var(--font-display)] text-[1.5rem] font-semibold leading-[1.1] tracking-[-0.02em] text-[var(--ink)]">
              {title}
            </h3>
            <p className="mt-3 text-[0.9375rem] leading-[1.6] text-[var(--ink-2)]">{body}</p>
          </div>
        ))}
      </div>
    </div>
  </section>
);

// ---------------------------------------------------------------------------
// The 5 Ds band — an abstract pentagon/radial motif drawn in SVG with a subtle
// Motion-native draw-in + slow, offscreen-aware rotation of the outer guides.
// The five Ds sit around it, each with a one-line definition.
// ---------------------------------------------------------------------------

const FIVE_DS = [
  { d: 'Delegation', def: 'Deciding what to own versus hand to the agent.' },
  { d: 'Description', def: 'Directing the agent — clear prompts, the right context.' },
  { d: 'Discernment', def: "Catching and overriding what the agent gets wrong." },
  { d: 'Diligence', def: 'Verifying before claiming done; owning the result.' },
  { d: 'Deliverable', def: 'The correctness and quality of what actually shipped.' },
];

// Five vertices of a regular pentagon, top-first, on a 200×200 viewBox.
const pentagonPoints = (cx, cy, r) =>
  FIVE_DS.map((_, i) => {
    const angle = (-90 + i * 72) * (Math.PI / 180);
    return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
  });

export const FiveDsBand = () => {
  const motifRef = useRef(null);
  const motifInView = useInView(motifRef, { amount: 0.25, once: true });
  const reduced = useReducedMotionSync();
  const motifVisible = reduced || motifInView;
  const outer = pentagonPoints(100, 100, 78);
  const inner = pentagonPoints(100, 100, 46);
  const outerPoly = outer.map((p) => p.join(',')).join(' ');
  const innerPoly = inner.map((p) => p.join(',')).join(' ');
  return (
    <section className="border-t border-[var(--line)] bg-[var(--bg)]" aria-labelledby="five-ds-heading">
      <div className={`${containerClass} py-20 md:py-24`}>
        <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
          <div className="mx-auto w-full max-w-[320px]">
            <m.svg
              ref={motifRef}
              viewBox="0 0 200 200"
              width="100%"
              height="auto"
              role="img"
              aria-label="The five Ds, arranged as a pentagon"
            >
              <MotionLoop
                as="g"
                kind="spin"
                duration={48}
                reduced={reduced}
                opacity="0.4"
                style={{ transformOrigin: '100px 100px' }}
                aria-hidden="true"
              >
                <polygon points={outerPoly} fill="none" stroke="var(--purple)" strokeWidth="1" strokeDasharray="3 5" />
                {outer.map((p) => (
                  <line key={`spoke-${p.join()}`} x1="100" y1="100" x2={p[0]} y2={p[1]} stroke="var(--purple)" strokeWidth="0.75" />
                ))}
              </MotionLoop>
              <m.polygon
                points={innerPoly}
                fill="var(--purple-soft)"
                stroke="var(--purple)"
                strokeWidth="2"
                strokeDasharray="620"
                initial={reduced ? false : { strokeDashoffset: 620 }}
                animate={{ strokeDashoffset: motifVisible ? 0 : 620 }}
                transition={reduced
                  ? { duration: 0 }
                  : { duration: MOTION_DURATION.data, ease: MOTION_EASE.enter }}
                data-motion-draw="pentagon"
              />
              {inner.map((p, i) => (
                <circle key={`v-${p.join()}`} cx={p[0]} cy={p[1]} r="4.5" fill="var(--purple)">
                  <title>{FIVE_DS[i].d}</title>
                </circle>
              ))}
              <circle cx="100" cy="100" r="5" fill="var(--bg)" stroke="var(--purple)" strokeWidth="2" />
            </m.svg>
          </div>

          <div>
            <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
              HOW WE SCORE
            </div>
            <h2
              id="five-ds-heading"
              className="mt-3 font-[var(--font-display)] text-[clamp(28px,3.6vw,42px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]"
            >
              The 5 Ds of working with AI.
            </h2>
            <p className="mt-4 max-w-[42rem] text-[0.96875rem] leading-[1.6] text-[var(--ink-2)]">
              Every assessment rolls up into five dimensions — anchored on Anthropic&apos;s AI-fluency
              framework — so how a candidate works with AI is scored as a first-class result.
            </p>
            <dl className="mt-8 grid gap-x-10 gap-y-5 sm:grid-cols-2">
              {FIVE_DS.map(({ d, def }) => (
                <div key={d} className="border-l-2 border-[var(--purple)] pl-4">
                  <dt className="font-[var(--font-display)] text-[1.125rem] font-semibold text-[var(--ink)]">{d}</dt>
                  <dd className="mt-1 text-[0.875rem] leading-[1.5] text-[var(--ink-2)]">{def}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Evidence strip — four hard claims as big-type stat-style items.
// ---------------------------------------------------------------------------

const EVIDENCE = [
  'Every task battle-tested before a candidate sees it',
  'Verification scored, not assumed',
  'A verdict on every candidate — automatically',
  'The transcript is the record. No webcam.',
];

export const EvidenceStrip = () => (
  <section className="border-t border-[var(--line)] bg-[var(--bg-2)]">
    <div className={`${containerClass} py-20 md:py-24`}>
      <div className="grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
        {EVIDENCE.map((claim, i) => (
          <div key={claim} className="border-t-2 border-[var(--purple)] pt-5">
            <div className="font-[var(--font-mono)] text-[0.6875rem] text-[var(--purple)]">{`0${i + 1}`}</div>
            <p className="mt-3 font-[var(--font-display)] text-[clamp(20px,2vw,26px)] font-semibold leading-[1.15] tracking-[-0.02em] text-[var(--ink)]">
              {claim}
            </p>
          </div>
        ))}
      </div>
    </div>
  </section>
);

// ---------------------------------------------------------------------------
// How it works — three plain-language steps, no UI.
// ---------------------------------------------------------------------------

const STEPS = [
  {
    n: '01',
    t: 'Connect your ATS',
    d: 'Point Taali at your open roles. It reads each job description and calibrates to the bar you actually hire for.',
  },
  {
    n: '02',
    t: 'The agent screens & assesses',
    d: 'It works the pipeline — pre-screens, sends assessments where they earn a seat, and scores how each candidate works with AI.',
  },
  {
    n: '03',
    t: 'You approve with evidence',
    d: 'Every decision arrives with its reasoning and the evidence behind it. You approve, override, or teach it back — in one click.',
  },
];

export const HowItWorks = () => (
  <section className="border-t border-[var(--line)] bg-[var(--bg)]">
    <div className={`${containerClass} py-20 md:py-24`}>
      <div className="mb-12 max-w-[44rem]">
        <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
          HOW IT WORKS
        </div>
        <h2 className="mt-3 font-[var(--font-display)] text-[clamp(30px,4vw,46px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]">
          Three steps. You stay in charge.
        </h2>
      </div>
      <div className="grid gap-8 md:grid-cols-3">
        {STEPS.map((step) => (
          <div key={step.n} className="border-t border-[var(--ink)] pt-7">
            <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.1em] text-[var(--purple)]">
              STEP {step.n}
            </div>
            <h3 className="mt-2.5 font-[var(--font-display)] text-[1.625rem] font-semibold tracking-[-0.015em] text-[var(--ink)]">
              {step.t}
            </h3>
            <p className="mt-2.5 text-[0.90625rem] leading-[1.55] text-[var(--ink-2)]">{step.d}</p>
          </div>
        ))}
      </div>
    </div>
  </section>
);
