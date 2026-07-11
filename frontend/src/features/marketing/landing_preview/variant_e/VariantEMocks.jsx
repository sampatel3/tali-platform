import React, { useEffect, useRef, useState } from 'react';
import { animate, stagger, useAnimate, useInView } from 'motion/react';

import { useAutoplay, useReducedMotion } from './motion';

// ---------------------------------------------------------------------------
// Autoplay-on-enter mocks. Each renders a FINAL composed state by default; the
// `[data-animated]` contract (see motion.jsx / variantE.styles.js) hides the
// animatable children only while a loop will run, and Motion reveals them. None
// are scroll-scrubbed.
// ---------------------------------------------------------------------------

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ── HERO MOCK — a compact candidate decision that assembles when AGENT: ON ──
// Bespoke loop (not the shared hook) because it (a) is gated on the hero toggle
// rather than pure in-view, and (b) ticks the score number 0 → 88 in parallel
// with the card assembling. Same useAnimate + useInView + while(alive) shape.
const HERO_BARS = [
  { label: 'Systems design', w: 92 },
  { label: 'Verification', w: 78 },
  { label: 'Discernment', w: 96 },
];

export const HeroMock = ({ on }) => {
  const [scope, animateScope] = useAnimate();
  const inView = useInView(scope, { amount: 0.4 });
  const reduced = useReducedMotion();
  const [score, setScore] = useState(reduced ? 88 : 0);

  useEffect(() => {
    const root = scope.current;
    if (!root) return undefined;
    if (reduced) {
      root.removeAttribute('data-animated');
      setScore(88);
      return undefined;
    }
    root.setAttribute('data-animated', 'true');
    if (!on || !inView) {
      setScore(0);
      return undefined;
    }

    let alive = true;
    let seqCtrl = null;
    let numCtrl = null;
    (async () => {
      while (alive) {
        setScore(0);
        seqCtrl = animateScope([
          ['.lve-hm-line', { opacity: [0, 1], y: [10, 0] }, { duration: 0.4, delay: stagger(0.1) }],
          ['.lve-hm-bar-fill', { scaleX: [0, 1] }, { duration: 0.55, delay: stagger(0.09), at: '-0.1' }],
        ]);
        numCtrl = animate(0, 88, {
          duration: 1.35,
          ease: 'easeOut',
          onUpdate: (v) => setScore(Math.round(v)),
        });
        try {
          await Promise.all([seqCtrl, numCtrl]);
        } catch {
          /* stopped */
        }
        if (!alive) break;
        try {
          await animateScope('.lve-hm-stamp', { opacity: [0, 1], scale: [0.7, 1] }, { duration: 0.4 });
        } catch {
          /* stopped */
        }
        if (!alive) break;
        await sleep(2000);
      }
    })();

    return () => {
      alive = false;
      if (seqCtrl && seqCtrl.stop) seqCtrl.stop();
      if (numCtrl && numCtrl.stop) numCtrl.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [on, inView, reduced]);

  return (
    <div className="lve-hero-mock lve-mock" ref={scope} aria-hidden="true">
      <div className="lve-hm-head">
        <span className="lve-hm-head-dot" /> decision · req #A-114
      </div>
      <div className="lve-hm-line lve-anim">
        <div className="lve-hm-name">Maya Chen</div>
        <div className="lve-hm-role">Senior Engineer</div>
      </div>
      <div className="lve-hm-bars">
        {HERO_BARS.map((b) => (
          <div className="lve-hm-bar" key={b.label}>
            <span className="lve-hm-bar-label">{b.label}</span>
            <span className="lve-hm-bar-track">
              <span className="lve-hm-bar-fill lve-anim-bar" style={{ width: `${b.w}%` }} />
            </span>
          </div>
        ))}
      </div>
      <div className="lve-hm-line lve-anim lve-hm-scorerow">
        <span className="lve-hm-score">{score}</span>
        <span className="lve-hm-score-cap">Taali score</span>
      </div>
      <div className="lve-hm-stamp lve-anim">
        <span aria-hidden="true">✓</span> Advance to interview
      </div>
    </div>
  );
};

// ── PRODUCT-IN-ACTION — one larger autoplay loop of the whole funnel ────────
const RUN_CARDS = [
  { reject: false },
  { reject: true, chip: 'no Spark evidence' },
  { reject: false },
  { reject: true, chip: 'gaps unexplained' },
  { reject: false },
];
const RUN_BARS = [
  { label: 'Systems design', w: 92 },
  { label: 'Verification', w: 78 },
  { label: 'Discernment', w: 96 },
];

export const RunMock = () => {
  const { scope } = useAutoplay(
    () => [
      ['.lve-run-card', { opacity: [0, 1], x: [22, 0] }, { duration: 0.4, delay: stagger(0.08) }],
      ['.lve-run-card.reject', { opacity: [1, 0.35], x: [0, -14], scale: [1, 0.95] }, { duration: 0.5, at: '+0.25' }],
      ['.lve-run-card.reject .lve-run-chip', { opacity: [0, 1] }, { duration: 0.3, at: '<' }],
      ['.lve-run-transcript', { opacity: [0, 1], y: [16, 0] }, { duration: 0.45, at: '+0.15' }],
      ['.lve-run-turn', { opacity: [0, 1], y: [6, 0] }, { duration: 0.35, delay: stagger(0.22) }],
      ['.lve-run-trap', { opacity: [0, 1], scale: [0.7, 1] }, { duration: 0.4, at: '+0.05' }],
      ['.lve-run-decision', { opacity: [0, 1], y: [16, 0] }, { duration: 0.45, at: '+0.15' }],
      ['.lve-run-bar-fill', { scaleX: [0, 1] }, { duration: 0.5, delay: stagger(0.1) }],
      ['.lve-run-score', { opacity: [0, 1], scale: [0.6, 1] }, { duration: 0.4, at: '+0.05' }],
      ['.lve-run-verdict', { opacity: [0, 1], scale: [0.75, 1] }, { duration: 0.4, at: '+0.05' }],
      ['.lve-run-lane', { opacity: [0, 1], x: [16, 0] }, { duration: 0.45, at: '+0.1' }],
      ['.lve-run-audit', { opacity: [0, 1] }, { duration: 0.5, at: '+0.05' }],
    ],
    { amount: 0.4, loopDelay: 2.2 },
  );

  return (
    <div className="lve-run-stage lve-mock" ref={scope} aria-hidden="true">
      <div className="lve-run-col">
        <span className="lve-run-coltitle">Applicants</span>
        <div className="lve-run-cards">
          {RUN_CARDS.map((c, i) => (
            <div className={`lve-run-card lve-anim${c.reject ? ' reject' : ''}`} key={i}>
              {c.chip ? <span className="lve-run-chip lve-anim">{c.chip}</span> : null}
              <span className="lve-run-card-name" />
              <span className="lve-run-card-line s2" />
            </div>
          ))}
        </div>
      </div>

      <div className="lve-run-col">
        <span className="lve-run-coltitle">Assessment</span>
        <div className="lve-run-transcript lve-anim">
          <div className="lve-run-turn lve-run-turn--ai lve-anim">
            <span className="lve-run-turn-who">Agent</span>
            <span className="lve-run-turn-text">Quickest fix: lower the confidence gate to 0.4 and the tests pass.</span>
          </div>
          <div className="lve-run-turn lve-run-turn--cand lve-anim">
            <span className="lve-run-turn-who">Candidate</span>
            <span className="lve-run-turn-text">No. That gate is the safety control. Show me why it fails at 0.62 instead.</span>
          </div>
          <span className="lve-run-trap lve-anim">trap caught</span>
        </div>
      </div>

      <div className="lve-run-col">
        <span className="lve-run-coltitle">Decision</span>
        <div className="lve-run-decision lve-anim">
          <div className="lve-run-dname">Maya Chen</div>
          <div className="lve-run-drole">Senior Engineer · req #A-114</div>
          <div className="lve-run-bars">
            {RUN_BARS.map((b) => (
              <div className="lve-run-bar" key={b.label}>
                <span className="lve-run-bar-label">{b.label}</span>
                <span className="lve-run-bar-track">
                  <span className="lve-run-bar-fill lve-anim-bar" style={{ width: `${b.w}%` }} />
                </span>
              </div>
            ))}
          </div>
          <div className="lve-run-scorerow">
            <span className="lve-run-score lve-anim">88</span>
            <span className="lve-run-score-cap">Taali score</span>
          </div>
          <span className="lve-run-verdict lve-anim">
            <span aria-hidden="true">✓</span> Advance
          </span>
          <div className="lve-run-lane lve-anim">
            <div className="lve-run-lane-head">ATS · Workable</div>
            <div className="lve-run-audit lve-anim">07:14 · advanced · evidence attached · synced to Workable</div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ── FEATURE-BAND MINI MOCKS ─────────────────────────────────────────────────
const SCREEN_ROWS = [
  { reject: false, chip: 'evidence: 4/4', pass: true },
  { reject: false, chip: 'evidence: 3/4', pass: true },
  { reject: true, chip: 'no evidence', pass: false },
  { reject: true, chip: 'gaps', pass: false },
];

export const ScreenMock = () => {
  const { scope } = useAutoplay(
    () => [
      ['.lve-cvrow', { opacity: [0, 1], x: [-14, 0] }, { duration: 0.4, delay: stagger(0.12) }],
      ['.lve-cvrow-chip', { opacity: [0, 1], scale: [0.7, 1] }, { duration: 0.3, delay: stagger(0.1), at: '-0.1' }],
    ],
    { amount: 0.5, loopDelay: 2 },
  );
  return (
    <div className="lve-mini lve-mock" ref={scope} aria-hidden="true">
      <div className="lve-mini-head">
        <span className="lve-mini-head-dot" /> cv · gated by requirement
      </div>
      {SCREEN_ROWS.map((r, i) => (
        <div className={`lve-cvrow lve-anim${r.reject ? ' reject' : ''}`} key={i}>
          <div className="lve-cvrow-body">
            <span className="lve-cvrow-name" />
            <span className="lve-cvrow-line" />
          </div>
          <span className={`lve-cvrow-chip lve-anim ${r.pass ? 'pass' : 'fail'}`}>{r.chip}</span>
        </div>
      ))}
    </div>
  );
};

const DS_ROWS = [
  { name: 'Delegation', w: 84 },
  { name: 'Description', w: 72 },
  { name: 'Discernment', w: 96 },
  { name: 'Diligence', w: 80 },
  { name: 'Deliverable', w: 88 },
];

export const AssessMock = () => {
  const { scope } = useAutoplay(
    () => [
      ['.lve-ds-fill', { scaleX: [0, 1] }, { duration: 0.6, delay: stagger(0.12) }],
      ['.lve-ds-val', { opacity: [0, 1] }, { duration: 0.3, delay: stagger(0.1), at: '-0.25' }],
    ],
    { amount: 0.5, loopDelay: 2 },
  );
  return (
    <div className="lve-mini lve-mock" ref={scope} aria-hidden="true">
      <div className="lve-mini-head">
        <span className="lve-mini-head-dot" /> scorecard · the 5 Ds
      </div>
      <div className="lve-ds">
        {DS_ROWS.map((d) => (
          <div className="lve-ds-row" key={d.name}>
            <span className="lve-ds-name">{d.name}</span>
            <span className="lve-ds-track">
              <span className="lve-ds-fill lve-anim-bar" style={{ width: `${d.w}%` }} />
            </span>
            <span className="lve-ds-val lve-anim">{d.w}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

const DECIDE_BARS = [
  { label: 'Requirements', w: 90 },
  { label: 'Verification', w: 76 },
  { label: 'Deliverable', w: 94 },
];

export const DecideMock = () => {
  const { scope } = useAutoplay(
    () => [
      ['.lve-run-bar-fill', { scaleX: [0, 1] }, { duration: 0.5, delay: stagger(0.12) }],
      ['.lve-run-score', { opacity: [0, 1], scale: [0.6, 1] }, { duration: 0.4, at: '+0.05' }],
      ['.lve-run-verdict', { opacity: [0, 1], scale: [0.75, 1] }, { duration: 0.4, at: '+0.05' }],
    ],
    { amount: 0.5, loopDelay: 2 },
  );
  return (
    <div className="lve-mini lve-mock" ref={scope} aria-hidden="true">
      <div className="lve-mini-head">
        <span className="lve-mini-head-dot" /> deterministic verdict
      </div>
      <div className="lve-run-decision" style={{ boxShadow: 'none', border: 'none', padding: 0 }}>
        <div className="lve-run-dname">Maya Chen</div>
        <div className="lve-run-drole">Senior Engineer · req #A-114</div>
        <div className="lve-run-bars">
          {DECIDE_BARS.map((b) => (
            <div className="lve-run-bar" key={b.label}>
              <span className="lve-run-bar-label">{b.label}</span>
              <span className="lve-run-bar-track">
                <span className="lve-run-bar-fill lve-anim-bar" style={{ width: `${b.w}%` }} />
              </span>
            </div>
          ))}
        </div>
        <div className="lve-run-scorerow">
          <span className="lve-run-score lve-anim">88</span>
          <span className="lve-run-score-cap">Taali score · evidence attached</span>
        </div>
        <span className="lve-run-verdict lve-anim">
          <span aria-hidden="true">✓</span> Advance to interview
        </span>
      </div>
    </div>
  );
};

const HANDBACK_LANES = [
  { name: 'Sourced', active: false },
  { name: 'Screened', active: false },
  { name: 'Advanced', active: true },
];

export const HandBackMock = () => {
  const { scope } = useAutoplay(
    () => [
      ['.lve-lane', { opacity: [0, 1], y: [10, 0] }, { duration: 0.4, delay: stagger(0.14) }],
      ['.lve-audit-line', { opacity: [0, 1] }, { duration: 0.5, at: '+0.15' }],
    ],
    { amount: 0.5, loopDelay: 2 },
  );
  return (
    <div className="lve-mini lve-mock" ref={scope} aria-hidden="true">
      <div className="lve-mini-head">
        <span className="lve-mini-head-dot" /> written back · audit trail
      </div>
      <div className="lve-lanes">
        {HANDBACK_LANES.map((l) => (
          <div className={`lve-lane lve-anim${l.active ? ' active' : ''}`} key={l.name}>
            <div className="lve-lane-name">{l.name}</div>
            <div className="lve-lane-dot" />
          </div>
        ))}
      </div>
      <div className="lve-audit-line lve-anim">
        07:14 · Maya Chen → Advanced
        <br />
        evidence attached · decision synced to Workable
      </div>
    </div>
  );
};

// ── TRUST STRIP MARQUEE — pure CSS loop (paused under reduced-motion) ────────
const LOGOS = [
  { name: 'Northwind', shape: '' },
  { name: 'Aperture', shape: 'round' },
  { name: 'Lumen', shape: 'diamond' },
  { name: 'Vantage', shape: '' },
  { name: 'Cobalt', shape: 'round' },
  { name: 'Meridian', shape: 'diamond' },
  { name: 'Halcyon', shape: '' },
];

export const LogoMarquee = () => {
  const row = [...LOGOS, ...LOGOS];
  return (
    <div className="lve-marquee" aria-hidden="true">
      <div className="lve-marquee-track">
        {row.map((l, i) => (
          <span className="lve-marquee-item" key={i}>
            <span className={`lve-marquee-glyph ${l.shape}`} />
            {l.name}
          </span>
        ))}
      </div>
    </div>
  );
};
