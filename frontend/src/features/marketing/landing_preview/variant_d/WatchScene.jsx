import React, { useCallback, useEffect, useRef } from 'react';

import {
  beatLocal,
  beatVis,
  clamp,
  easeOut,
  useScrollProgress,
} from './sceneProgress';

// ---------------------------------------------------------------------------
// The centrepiece: a PINNED, scroll-scrubbed scene. A ~500vh wrapper holds a
// `position: sticky; top: 0; height: 100vh` stage. `useScrollProgress` turns
// scroll into one value `p` (0→1); onFrame writes CSS vars (--p, --b1..--b5,
// --v1..--v5) + a few textContents onto the stage — no React state, so scrubbing
// is transform/opacity only. `p` splits into 5 beats (0.2 wide, crossfaded):
//   1 SOURCE · 2 SCREEN · 3 ASSESS · 4 DECIDE · 5 HAND BACK
// Typed text (transcript, counter, score, audit) is derived from local beat
// progress, so scrubbing BACK un-types it. Under staticMode the same markup is
// rendered as 5 stacked, labelled panels in their final state (see styles).
// ---------------------------------------------------------------------------

const RAIL_STEPS = ['Source', 'Screen', 'Assess', 'Decide', 'Hand back'];

const CAPTIONS = [
  { eyebrow: '01 · Source', text: 'Plugs into your ATS. Every candidate, role and JD flows in.' },
  {
    eyebrow: '02 · Screen',
    text: "Reads every CV against the role's real requirements. Weak fits gated with evidence, not guesswork.",
  },
  {
    eyebrow: '03 · Assess',
    text: 'Candidates pair with Claude on real work — engineering or knowledge work. We watch how they work, not just what they hand in.',
  },
  {
    eyebrow: '04 · Decide',
    text: 'A deterministic verdict on every candidate, the evidence attached. You approve, override, or teach it back.',
  },
  {
    eyebrow: '05 · Hand back',
    text: 'Decisions, notes and reports written back to your ATS. The audit trail comes free.',
  },
];

const TRANSCRIPT = {
  ai: 'Quickest fix: lower the confidence gate to 0.4 and the tests pass.',
  cand: 'No. That gate is the safety control. Show me why it fails at 0.62 instead.',
};
const AUDIT_LINE = '07:14 · advanced · evidence attached · synced to Workable';

const SOURCE_CARDS = [0, 1, 2, 3, 4, 5];
// Beat-2 fan: 5 cards; rejects (index 1 & 3 → 40%) carry an evidence chip and
// slide aside as they leave. --y stacks them; --x fans; --dir picks exit side.
const SCREEN_CARDS = [
  { i: 0, x: -14, y: -132, reject: 0 },
  { i: 1, x: 18, y: -66, reject: 1, dir: 1, chip: 'no Spark evidence' },
  { i: 2, x: -8, y: 0, reject: 0 },
  { i: 3, x: 16, y: 66, reject: 1, dir: -1, chip: 'gaps unexplained' },
  { i: 4, x: -12, y: 132, reject: 0 },
];
const REQS = [
  { label: 'Systems design', w: 0.92 },
  { label: 'Verification', w: 0.78 },
  { label: 'Discernment', w: 0.96 },
];

const AbstractCard = ({ className, style, chip, ...rest }) => (
  <div className={`lvd-card ${className || ''}`} style={style} {...rest}>
    {chip ? <span className="lvd-card-chip">{chip}</span> : null}
    <span className="lvd-card-name" />
    <span className="lvd-card-line s2" />
    <span className="lvd-card-line s3" />
  </div>
);

// ── The five beat visuals (shared by scrub + static) ──────────────────────
const Beat1 = ({ countRef, staticMode }) => (
  <>
    <div className="lvd-b1-queue">
      {SOURCE_CARDS.map((i) => (
        <AbstractCard key={i} className="lvd-b1-card" style={{ '--i': i }} />
      ))}
    </div>
    <div className="lvd-b1-counter">
      <div className="lvd-b1-count" ref={countRef}>
        {staticMode ? '1,240' : '0'}
      </div>
      <div className="lvd-b1-count-cap">candidates sourced</div>
    </div>
  </>
);

const Beat2 = () => (
  <div className="lvd-b2-col">
    {SCREEN_CARDS.map((c) => (
      <AbstractCard
        key={c.i}
        className="lvd-b2-card"
        data-reject={c.reject}
        chip={c.chip}
        style={{ '--x': c.x, '--y': c.y, '--dir': c.dir || 0 }}
      />
    ))}
  </div>
);

const Beat3 = ({ aiRef, candRef, aiCaretRef, candCaretRef, staticMode }) => (
  <div className="lvd-b3-panel">
    <div className="lvd-b3-head">
      <span className="lvd-b3-dot" /> assessment · live transcript
    </div>
    <div className="lvd-turn lvd-turn--ai">
      <span className="lvd-turn-who">Agent</span>
      <span className="lvd-turn-text">
        <span ref={aiRef}>{staticMode ? TRANSCRIPT.ai : ''}</span>
        <span className="lvd-caret" ref={aiCaretRef} style={{ display: 'none' }} />
      </span>
    </div>
    <div className="lvd-turn lvd-turn--cand">
      <span className="lvd-turn-who">Candidate</span>
      <span className="lvd-turn-text">
        <span ref={candRef}>{staticMode ? TRANSCRIPT.cand : ''}</span>
        <span className="lvd-caret" ref={candCaretRef} style={{ display: 'none' }} />
      </span>
    </div>
    <div className="lvd-dial" aria-hidden="true">
      <span className="lvd-dial-label">Discernment</span>
      <span className="lvd-dial-track">
        <span className="lvd-dial-fill" />
      </span>
      <span className="lvd-trap-badge">trap caught</span>
    </div>
  </div>
);

const Beat4 = ({ scoreRef, staticMode }) => (
  <div className="lvd-b4-card">
    <div className="lvd-b4-name">Maya Chen</div>
    <div className="lvd-b4-role">Senior Engineer · req #A-114</div>
    <div className="lvd-b4-reqs">
      {REQS.map((r, i) => (
        <div className="lvd-b4-req" key={r.label}>
          <span className="lvd-b4-req-label">{r.label}</span>
          <span className="lvd-b4-req-track">
            <span className="lvd-b4-req-fill" style={{ '--i': i, '--w': r.w }} />
          </span>
        </div>
      ))}
    </div>
    <div className="lvd-b4-scorerow">
      <span className="lvd-b4-score" ref={scoreRef}>
        {staticMode ? '88' : '0'}
      </span>
      <span className="lvd-b4-score-cap">Taali score</span>
    </div>
    <div className="lvd-b4-verdict">
      <span aria-hidden="true">✓</span> Advance to interview
    </div>
  </div>
);

const Beat5 = ({ auditRef, auditCaretRef, staticMode }) => (
  <div className="lvd-b5-wrap">
    <div className="lvd-b5-card" style={{ '--slide': '0px' }}>
      <div className="lvd-b5-card-name">Maya Chen</div>
      <div className="lvd-b5-card-verdict">Advance to interview</div>
    </div>
    <div className="lvd-b5-lane">
      <div className="lvd-b5-lane-head">ATS · Workable</div>
      <div className="lvd-b5-audit">
        <span ref={auditRef}>{staticMode ? AUDIT_LINE : ''}</span>
        <span className="lvd-caret" ref={auditCaretRef} style={{ display: 'none' }} />
      </div>
    </div>
  </div>
);

export const WatchScene = ({ wrapperRef, staticMode }) => {
  const stageRef = useRef(null);
  const countRef = useRef(null);
  const aiRef = useRef(null);
  const candRef = useRef(null);
  const aiCaretRef = useRef(null);
  const candCaretRef = useRef(null);
  const scoreRef = useRef(null);
  const auditRef = useRef(null);
  const auditCaretRef = useRef(null);
  const stepRefs = useRef([]);

  // Per-frame writer. Sets CSS vars + a handful of textContents. No setState.
  const onFrame = useCallback((p) => {
    const stage = stageRef.current;
    if (!stage) return;
    stage.style.setProperty('--p', p.toFixed(4));
    for (let n = 0; n < 5; n += 1) {
      stage.style.setProperty(`--b${n + 1}`, beatLocal(p, n).toFixed(4));
      stage.style.setProperty(`--v${n + 1}`, beatVis(p, n).toFixed(4));
    }

    // Beat 1 — counter tics 0 → 1,240.
    if (countRef.current) {
      const v = Math.round(1240 * easeOut(beatLocal(p, 0)));
      countRef.current.textContent = v.toLocaleString('en-US');
    }

    // Beat 3 — transcript types from local progress (scrub back un-types).
    const b3 = beatLocal(p, 2);
    const tp = clamp((b3 - 0.08) / 0.72);
    const total = TRANSCRIPT.ai.length + TRANSCRIPT.cand.length;
    const shown = Math.round(tp * total);
    if (aiRef.current) aiRef.current.textContent = TRANSCRIPT.ai.slice(0, Math.min(shown, TRANSCRIPT.ai.length));
    if (candRef.current)
      candRef.current.textContent = TRANSCRIPT.cand.slice(0, clamp(shown - TRANSCRIPT.ai.length, 0, TRANSCRIPT.cand.length));
    const typingAi = tp > 0 && shown < TRANSCRIPT.ai.length;
    const typingCand = shown >= TRANSCRIPT.ai.length && shown < total;
    if (aiCaretRef.current) aiCaretRef.current.style.display = typingAi ? 'inline-block' : 'none';
    if (candCaretRef.current) candCaretRef.current.style.display = typingCand ? 'inline-block' : 'none';

    // Beat 4 — Taali score counts up to 88.
    if (scoreRef.current) {
      const s = Math.round(88 * clamp((beatLocal(p, 3) - 0.4) / 0.4));
      scoreRef.current.textContent = String(s);
    }

    // Beat 5 — audit line writes itself character by character.
    const b5 = beatLocal(p, 4);
    const ap = clamp((b5 - 0.2) / 0.6);
    const chars = Math.round(ap * AUDIT_LINE.length);
    if (auditRef.current) auditRef.current.textContent = AUDIT_LINE.slice(0, chars);
    if (auditCaretRef.current) auditCaretRef.current.style.display = ap > 0 && chars < AUDIT_LINE.length ? 'inline-block' : 'none';

    // Rail — active step tracks p.
    const active = p >= 0.999 ? 4 : Math.min(4, Math.floor(p / 0.2));
    stepRefs.current.forEach((el, i) => {
      if (el) el.classList.toggle('is-active', i === active);
    });
  }, []);

  useScrollProgress(wrapperRef, !staticMode, onFrame);

  // In static mode there is no scroll loop, so paint the rail's first step once.
  useEffect(() => {
    if (staticMode) return;
    onFrame(0);
  }, [staticMode, onFrame]);

  const beatText = { countRef, aiRef, candRef, aiCaretRef, candCaretRef, scoreRef, auditRef, auditCaretRef, staticMode };

  // ── Static / reduced-motion: 5 stacked labelled panels, final state ──────
  if (staticMode) {
    return (
      <div className="lvd-scene-wrap" ref={wrapperRef}>
        <div className="lvd-stage">
          <div className="lvd-stage-inner">
            {[Beat1, Beat2, Beat3, Beat4, Beat5].map((Beat, i) => (
              <div className={`lvd-beat lvd-beat--${i + 1}`} key={i}>
                <div className="lvd-beat-static-copy">
                  <span className="lvd-cap-eyebrow">{CAPTIONS[i].eyebrow}</span>
                  <p className="lvd-cap-text">{CAPTIONS[i].text}</p>
                </div>
                <div className="lvd-beat-visual">
                  <Beat {...beatText} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Scrub: pinned stage ──────────────────────────────────────────────────
  return (
    <div className="lvd-scene-wrap" ref={wrapperRef}>
      <div className="lvd-stage" ref={stageRef}>
        <div className="lvd-rail" aria-hidden="true">
          <div className="lvd-rail-track">
            <div className="lvd-rail-fill" />
          </div>
          <div className="lvd-rail-steps">
            {RAIL_STEPS.map((s, i) => (
              <span
                key={s}
                className={`lvd-rail-step${i === 0 ? ' is-active' : ''}`}
                ref={(el) => {
                  stepRefs.current[i] = el;
                }}
              >
                {s}
              </span>
            ))}
          </div>
        </div>

        <div className="lvd-stage-inner">
          <div className="lvd-beat lvd-beat--1" style={{ opacity: 'var(--v1)' }}>
            <Beat1 {...beatText} />
          </div>
          <div className="lvd-beat lvd-beat--2" style={{ opacity: 'var(--v2)' }}>
            <Beat2 />
          </div>
          <div className="lvd-beat lvd-beat--3" style={{ opacity: 'var(--v3)' }}>
            <Beat3 {...beatText} />
          </div>
          <div className="lvd-beat lvd-beat--4" style={{ opacity: 'var(--v4)' }}>
            <Beat4 {...beatText} />
          </div>
          <div className="lvd-beat lvd-beat--5" style={{ opacity: 'var(--v5)' }}>
            <Beat5 {...beatText} />
          </div>
        </div>

        <div className="lvd-caps">
          {CAPTIONS.map((c, i) => (
            <div className="lvd-cap" key={c.eyebrow} style={{ opacity: `var(--v${i + 1})` }}>
              <span className="lvd-cap-eyebrow">{c.eyebrow}</span>
              <p className="lvd-cap-text">{c.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default WatchScene;
