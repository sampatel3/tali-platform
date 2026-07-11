import React from 'react';
import { stagger } from 'motion/react';

import { useAutoplay } from './motion';

// ---------------------------------------------------------------------------
// Autoplay-on-enter mocks. Each renders a FINAL composed state by default; the
// `[data-animated]` contract (see motion.jsx / variantE.styles.js) hides the
// animatable children only while a loop will run, and Motion reveals them. None
// are scroll-scrubbed.
//
// The hero, product-in-action and assess-band surfaces now embed the REAL
// production components (see VariantERealMocks.jsx). These remaining autoplay
// mocks back the SCREEN / DECIDE / HAND BACK feature bands.
// ---------------------------------------------------------------------------

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
