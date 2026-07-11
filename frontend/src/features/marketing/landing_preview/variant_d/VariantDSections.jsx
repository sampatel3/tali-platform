import React, { useCallback, useRef } from 'react';

import { useScrollProgress } from './sceneProgress';

// Content reused verbatim from variant C v3 (the approved copy), restyled to sit
// around the pinned scene. Purple only; "works with AI" never "ship/build with AI".

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

const STATS = [
  { big: 'Every task', cap: 'battle-tested before use' },
  { big: 'Every decision', cap: 'carries its evidence' },
  { big: 'Every session', cap: 'captured turn by turn' },
  { big: 'Zero', cap: 'webcams or lockdown browsers' },
];

// The 5-Ds as a STICKY RAIL: the list pins briefly while each D highlights in
// turn as you scroll past. Same sticky+progress technique as the scene, lighter
// — active row driven imperatively (class toggles), no React re-render.
export const StandardSection = ({ staticMode }) => {
  const wrapRef = useRef(null);
  const rowRefs = useRef([]);

  const onFrame = useCallback((dp) => {
    const active = dp >= 0.999 ? FIVE_DS.length - 1 : Math.min(FIVE_DS.length - 1, Math.floor(dp * FIVE_DS.length));
    rowRefs.current.forEach((el, i) => {
      if (!el) return;
      el.classList.toggle('is-active', i === active);
      el.classList.toggle('is-dim', i !== active);
    });
  }, []);

  useScrollProgress(wrapRef, !staticMode, onFrame);

  return (
    <section className="lvd-standard">
      <header className="lvd-sechead">
        <div className="lvd-eyebrow">
          <span className="lvd-eyebrow-dot" /> THE STANDARD
        </div>
        <h2 className="lvd-h2">
          We&rsquo;re making AI fluency <em className="lvd-h2-accent">measurable.</em>
        </h2>
        <p className="lvd-sechead-sub">
          Five dimensions. Planted traps. Scored verification. A transcript instead of a webcam.
          When a Taali score says they can work with AI, they can.
        </p>
      </header>

      <div
        className="lvd-ds-wrap"
        ref={wrapRef}
        style={staticMode ? undefined : { height: 'calc(100vh + 240vh)' }}
      >
        <div className="lvd-ds-sticky">
          <div className="lvd-ds-rows">
            {FIVE_DS.map((row, i) => (
              <div
                className={`lvd-ds-row${i === 0 && !staticMode ? ' is-active' : ''}`}
                key={row.d}
                ref={(el) => {
                  rowRefs.current[i] = el;
                }}
              >
                <span className="lvd-ds-name">{row.d}</span>
                <div className="lvd-ds-body">
                  <span className="lvd-ds-def">{row.def}</span>
                  <span className="lvd-ds-evidence">{row.evidence}</span>
                </div>
                <span className="lvd-ds-chip">{row.chip}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="lvd-claims">
        {CLAIMS.map((c) => (
          <span className="lvd-claim" key={c}>
            {c}
          </span>
        ))}
      </div>
    </section>
  );
};

// Compact stats row (reused from variant C v3).
export const StatsRow = () => (
  <section className="lvd-stats">
    <div className="lvd-stats-grid">
      {STATS.map((s) => (
        <div className="lvd-stat" key={s.big}>
          <span className="lvd-stat-big">{s.big}</span>
          <span className="lvd-stat-cap">{s.cap}</span>
        </div>
      ))}
    </div>
  </section>
);
